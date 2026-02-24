"""SteamShelf — 库管理：收藏夹加载/渲染/操作/事件（LibraryCollectionsMixin）

从 ui_library.py 拆分。包含收藏夹相关的所有逻辑：
加载、渲染、筛选、右键菜单、来源更新、拖拽、排序。

依赖 self 属性（由其他模块提供）：
  .root: tk.Tk                          — ui_main
  .current_account: SteamAccount        — ui_main
  ._config_mgr: ConfigManager           — ui_main
  ._collections_core: CollectionsCore   — ui_main
  ._cef_bridge: CEFBridge               — ui_main
  ._game_name_cache: dict               — ui_main
  .manager: SteamNotesManager           — ui_main
  ._coll_tree: ttk.Treeview             — ui_library (_build_library_tab)
  ._lib_tree: ttk.Treeview              — ui_library (_build_library_tab)
  ._lib_all_games: list                 — ui_library
  ._lib_all_games_backup: list|None     — ui_library
  ._lib_status: tk.Label                — ui_library
  ._viewing_collections: bool           — ui_library
  ._coll_filter_states: dict            — ui_library
  ._coll_filter_var: tk.StringVar       — ui_library
  ._coll_data_cache: dict               — ui_library
  ._toolbar_context: str                — ui_library
  ._selection_updating: bool            — ui_library
  ._prev_tree_selection: set            — ui_library
  ._sort_columns: dict                  — ui_library
  ._sort_order: list                    — ui_library
  ._sort_key_cache: dict                — ui_library
  ._type_filter: set                    — ui_library
  ._ALL_TYPES: tuple                    — ui_library
  ._img_coll_plus/minus/default         — ui_library
  ._viewing_collections: bool           — ui_library (查看状态跟踪)
"""

import json
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog

from account_manager import SteamAccountScanner
from utils import (steam_sort_key, STORE_TYPE_MAP,
                   ETYPE_GAME, ETYPE_DLC, ETYPE_DEMO, ETYPE_TOOL, ETYPE_MUSIC)
from ui_utils import ProgressWindow, bg_thread
from ui_library_expression import LibraryExpressionMixin

try:
    from core_collections import CollectionsCore
except ImportError:
    CollectionsCore = None


class LibraryCollectionsMixin(LibraryExpressionMixin):
    """收藏夹相关方法（Mixin，self 指向 SteamToolboxMain 实例）"""

    def _coll_is_empty(self, col_id):
        """当前筛选模式下该分类游戏数是否为 0"""
        data = getattr(self, '_coll_data_cache', {}).get(col_id)
        if not data:
            return False
        mode = getattr(self, '_coll_filter_var', None)
        mode = mode.get() if mode else "已入库"
        if mode == "已入库":
            return data.get('owned_count', 0) == 0
        if mode == "未入库":
            return data.get('not_owned_count', 0) == 0
        return data.get('total_count', 0) == 0

    def _coll_item_tags(self, col_id, state):
        """根据筛选状态 + 是否为空，返回 (image, tags)"""
        if state == 'plus':
            img = self._img_coll_plus
        elif state == 'minus':
            img = self._img_coll_minus
        else:
            img = self._img_coll_default
        if self._coll_is_empty(col_id):
            return img, ("coll_empty",)
        if state == 'plus':
            return img, ("coll_plus",)
        if state == 'minus':
            return img, ("coll_minus",)
        return img, ()

    def _lib_load_collections(self, prefetched_cef=None,
                              skip_expression_update=False, force_local=False):
        """加载 Steam 收藏夹

        数据来源优先级：
        1. prefetched_cef 已提供 → 直接使用（后台预取，零阻塞）
        2. CEF 缓存 / 实时获取（force_local=True 时跳过）
        3. 本地 JSON 文件（回退方案）
        """
        if not hasattr(self, '_coll_tree'):
            return
        coll_tree = self._coll_tree
        coll_tree.delete(*coll_tree.get_children())

        # CollectionsCore：复用已有实例，仅首次创建
        if self._collections_core is None:
            storage_path = getattr(self.current_account, 'storage_path', None)
            if CollectionsCore is not None and storage_path:
                try:
                    self._collections_core = CollectionsCore(
                        self.current_account, self._config_mgr)
                except Exception as e:
                    print(f"[库管理] CollectionsCore 初始化失败: {e}")
        if self._collections_core and self._cef_bridge is not None:
            self._collections_core.cef = self._cef_bridge

        # ── CEF 数据（预取 > 缓存 > 实时获取 > 本地） ──
        cef_data = None
        if not force_local:
            if prefetched_cef and "collections" in prefetched_cef:
                self._cef_collections_cache = prefetched_cef
            cef_data = prefetched_cef or getattr(self, '_cef_collections_cache', None)
            # 缓存被清除且 CEF 已连接 → 重新获取最新数据
            if cef_data is None and self._cef_bridge and self._cef_bridge.is_connected():
                try:
                    fresh = self._cef_bridge.get_all_collections_with_apps(timeout=20)
                    if fresh and "collections" in fresh:
                        self._cef_collections_cache = fresh
                        cef_data = fresh
                except Exception:
                    pass

        if cef_data and "collections" in cef_data:
            self._lib_render_collections_cef(coll_tree, cef_data["collections"])
        else:
            self._lib_render_collections_local(coll_tree)

        # 表达式分类标签修正（CEF 的 owned/not_owned 拆分对表达式分类无意义）
        self._refresh_expr_coll_labels()

        # 自动更新 expression 类型的绑定分类（后台线程，不阻塞 UI）
        if not skip_expression_update:
            self._schedule_expression_update()

        # 分享者同步：收藏夹变动后检测并上传（debounce 3s）
        self._schedule_sharing_sync()

    def _lib_render_collections_cef(self, coll_tree, cef_collections: dict):
        """使用 CEF 实时数据渲染收藏夹列表

        显示格式：
        - 已入库：📁 名称 (120)
        - 全部：📁 名称 (120/130)
        - 未入库：📁 名称 (10)
        """
        # 同时获取本地收藏夹数据，对比找出"在本地但不在 CEF 结果中"的 AppID
        local_collections = {}
        try:
            userdata_path = self.current_account.get('userdata_path', '')
            local_colls = SteamAccountScanner.get_collections(userdata_path)
            for lc in (local_colls or []):
                col_id = lc.get('id', '')
                if col_id:
                    local_collections[col_id] = lc
        except Exception:
            pass

        if not cef_collections:
            coll_tree.insert("", tk.END, text="（CEF 未返回分类数据）")
            return

        # 获取当前筛选模式
        show_mode = getattr(self, '_coll_filter_var', None)
        show_mode = show_mode.get() if show_mode else "已入库"

        # 预加载来源缓存（用于标记有来源的收藏夹）
        _source_ids = set()
        if self._collections_core:
            _cfg = self._collections_core.load_config()
            _source_ids = set(self._collections_core._get_all_sources(_cfg).keys())

        for col_id, col_info in cef_collections.items():
            coll_name = col_info.get("name", "未命名")
            is_dynamic = col_info.get("isDynamic", False)
            cef_app_ids = col_info.get("appIds", [])
            cef_count = len(cef_app_ids)

            # 对比本地数据找出"不拥有"的 AppID
            local_coll = local_collections.get(col_id, {})
            local_app_ids = set(str(a) for a in local_coll.get('app_ids', []))
            cef_app_ids_str = set(str(a) for a in cef_app_ids)
            not_owned = sorted(local_app_ids - cef_app_ids_str)
            not_owned_count = len(not_owned)
            total_count = cef_count + not_owned_count

            # 判断当前模式下是否为空分类
            _mode_count = (cef_count if show_mode == "已入库"
                           else not_owned_count if show_mode == "未入库"
                           else total_count)
            _is_empty = (_mode_count == 0)

            # 构建标题（简洁格式）
            if is_dynamic:
                icon = "🔄"
            elif col_id in _source_ids:
                icon = "🔗"
            else:
                icon = "📁"

            if show_mode == "已入库":
                label = f"{icon} {coll_name} ({cef_count})"
            elif show_mode == "全部":
                if not_owned_count > 0:
                    label = f"{icon} {coll_name} ({cef_count}/{total_count})"
                else:
                    label = f"{icon} {coll_name} ({cef_count})"
            else:  # 未入库
                label = f"{icon} {coll_name} ({not_owned_count})"

            # 使用 col_id 作为 iid，存储收藏夹数据
            _st = self._coll_filter_states.get(col_id, 'default')
            _img = (self._img_coll_plus if _st == 'plus'
                    else self._img_coll_minus if _st == 'minus'
                    else self._img_coll_default)
            _tag = ["coll_plus"] if _st == 'plus' else ["coll_minus"] if _st == 'minus' else []
            if _is_empty:
                _tag.append("coll_empty")
            node = coll_tree.insert("", tk.END, iid=col_id, text=label,
                                    image=_img, tags=tuple(_tag))

            if not hasattr(self, '_coll_data_cache'):
                self._coll_data_cache = {}
            self._coll_data_cache[col_id] = {
                'name': coll_name,
                'is_dynamic': is_dynamic,
                'owned_app_ids': [str(aid) for aid in cef_app_ids],
                'not_owned_app_ids': not_owned,
                'total_count': total_count,
                'owned_count': cef_count,
                'not_owned_count': not_owned_count,
            }

        # 收藏夹渲染完毕，后台批量查询未入库游戏的 appOverview（避免阻塞 UI）
        def _bg_fetch():
            self._cef_fetch_unowned_overviews()
            self.root.after(0, self._lib_schedule_tree_rebuild)
        threading.Thread(target=bg_thread(_bg_fetch), daemon=True).start()
        # 启动后台获取所有未入库游戏信息（Store API 补充）
        self._bg_resolve_all_unowned_types()


    def _schedule_sharing_sync(self):
        """Debounce 分享者同步：取消上次计时器，3s 后触发"""
        timer = getattr(self, '_sharing_sync_timer', None)
        if timer is not None:
            self.root.after_cancel(timer)
        self._sharing_sync_timer = self.root.after(
            3000, self._sync_published_shares_bg)

    def _lib_render_collections_local(self, coll_tree):
        """使用本地 JSON 渲染收藏夹列表（CEF 未连接时的回退方案）"""
        show_mode = getattr(self, '_coll_filter_var', None)
        show_mode = show_mode.get() if show_mode else "已入库"
        if show_mode != "已入库":
            coll_tree.insert("", tk.END,
                text='⚠️ "全部" 和 "未入库" 筛选需要连接 CEF')
            coll_tree.insert("", tk.END,
                text="（CEF 可区分本地数据中哪些游戏你实际拥有）")

        if not hasattr(self, '_coll_data_cache'):
            self._coll_data_cache = {}

        try:
            userdata_path = self.current_account.get('userdata_path', '')
            collections = SteamAccountScanner.get_collections(userdata_path)
            if not collections:
                coll_tree.insert("", tk.END, text="（暂无分类数据）")
                return

            _source_ids = set()
            if self._collections_core:
                _cfg = self._collections_core.load_config()
                _source_ids = set(self._collections_core._get_all_sources(_cfg).keys())

            for coll in collections:
                coll_id = coll.get('id', '')
                coll_name = coll.get('name', '未命名')
                app_ids = coll.get('app_ids', [])
                is_dynamic = coll.get('is_dynamic', False)

                if is_dynamic:
                    if app_ids:
                        label = f"🔄 {coll_name} (+{len(app_ids)} 手动)"
                    else:
                        label = f"🔄 {coll_name}"
                else:
                    icon = "🔗" if coll_id in _source_ids else "📁"
                    label = f"{icon} {coll_name} ({len(app_ids)})"

                _st = self._coll_filter_states.get(coll_id, 'default') if coll_id else 'default'
                _img = (self._img_coll_plus if _st == 'plus'
                        else self._img_coll_minus if _st == 'minus'
                        else self._img_coll_default)
                _tag = ("coll_plus",) if _st == 'plus' else ("coll_minus",) if _st == 'minus' else ()
                node = coll_tree.insert("", tk.END, iid=coll_id if coll_id else None,
                                        text=label, image=_img, tags=_tag)

                if coll_id:
                    self._coll_data_cache[coll_id] = {
                        'name': coll_name,
                        'is_dynamic': is_dynamic,
                        'owned_app_ids': [str(aid) for aid in app_ids],
                        'not_owned_app_ids': [],
                        'total_count': len(app_ids),
                        'owned_count': len(app_ids),
                        'not_owned_count': 0,
                    }

        except Exception as e:
            coll_tree.insert("", tk.END, text=f"⚠️ 加载分类失败: {e}")

    def _cef_fetch_unowned_overviews(self):
        """CEF 批量查询未入库游戏的 appOverview，结果存入 _cef_unowned_cache"""
        if not self._cef_bridge or not self._cef_bridge.is_connected():
            return
        cache = getattr(self, '_coll_data_cache', {})
        if not cache:
            return
        all_unowned = set()
        for data in cache.values():
            all_unowned.update(data.get('not_owned_app_ids', []))
        # 跳过已有 CEF 数据的（在 _lib_all_games 中的）
        existing = {str(g.get('app_id', '')) for g in self._lib_all_games}
        need = [aid for aid in all_unowned if str(aid) not in existing]
        if not need:
            return
        if not hasattr(self, '_cef_unowned_cache'):
            self._cef_unowned_cache = {}
        result = self._cef_bridge.get_app_overviews_batch(need, timeout=30)
        if result:
            self._cef_unowned_cache.update(result)
            # 同时补充名称缓存
            for aid_str, info in result.items():
                name = info.get('name', '')
                if name and aid_str not in self._game_name_cache:
                    self._game_name_cache[aid_str] = name
            print(f"[CEF] 批量获取 {len(result)}/{len(need)} 个未入库游戏数据")

    def _lib_enhance_name_cache_from_cef(self):
        """用 CEF 获取的游戏列表补充名称缓存"""
        if not self._cef_bridge or not self._cef_bridge.is_connected():
            return
        try:
            data = self._cef_bridge.get_all_owned_apps(games_only=False, timeout=30)
            if "apps" in data:
                updated = 0
                for app in data["apps"]:
                    aid_str = str(app.get("appid", ""))
                    name = app.get("name", "")
                    if aid_str and name and aid_str not in self._game_name_cache:
                        self._game_name_cache[aid_str] = name
                        updated += 1
                if updated > 0:
                    print(f"[库管理] CEF 补充了 {updated} 个游戏名称到缓存")
                    self._persist_name_cache()
        except Exception as e:
            print(f"[库管理] CEF 名称补充失败: {e}")

    def _lib_load_owned_from_cef(self):
        """用 CEF 获取的完整游戏列表替换左侧 Steam 库列表

        解决：本地扫描只能发现已安装游戏，CEF 包含所有入库游戏。
        """
        if not self._cef_bridge or not self._cef_bridge.is_connected():
            return
        try:
            data = self._cef_bridge.get_all_owned_apps(games_only=False, timeout=30)
            if "apps" not in data:
                self._lib_status.config(
                    text=f"⚠️ CEF 获取游戏列表失败: {data.get('error', '未知')}")
                return
            apps = data["apps"]
            cef_games = []
            type_stats = {}
            for app in apps:
                aid = app.get("appid")
                name = app.get("name", "")
                if not aid:
                    continue
                aid_str = str(aid)
                display_name = self._game_name_cache.get(aid_str, name or f"AppID {aid}")
                if name and aid_str not in self._game_name_cache:
                    self._game_name_cache[aid_str] = name

                app_type = self._get_app_type(app)
                type_stats[app_type] = type_stats.get(app_type, 0) + 1

                cef_games.append({
                    'app_id': aid_str,
                    'name': display_name,
                    'installed': app.get('installed', False),
                    'owned': True,
                    'type': app_type,
                    'rt_time_acquired': app.get('rt_time_acquired', 0),
                    'review_pct': app.get('review_pct', 0),
                    'review_score': app.get('review_score', 0),
                    'metacritic': app.get('metacritic', 0),
                    'rt_release': app.get('rt_release', 0),
                    'rt_purchased': app.get('rt_purchased', 0),
                })

            print(f"[库管理] CEF游戏类型统计: {type_stats}")
            cef_games.sort(key=lambda g: steam_sort_key(g['name']))
            # marshal 到主线程（bg 线程直接赋值有迭代器失效风险）
            self.root.after(0, lambda g=cef_games: setattr(self, '_lib_all_games', g))
            # rebuild 由调用方（_bg_load_cef_data）统一调度，此处不再重复触发
        except Exception as e:
            print(f"[库管理] CEF 加载游戏列表失败: {e}")
            self.root.after(0, lambda: self._lib_status.config(
                text=f"⚠️ CEF 游戏列表加载失败: {e}"))

    @staticmethod
    def _guess_type_from_name(name: str) -> int:
        """从游戏名称推断类型（用于没有CEF类型数据的未入库游戏）

        返回 Steam EAppType 枚举值：
        1=Game, 2=App, 4=Tool, 8=Demo, 32=DLC, 0x2000=Music
        """
        if not name:
            return ETYPE_GAME
        nl = name.lower()
        if any(kw in nl for kw in ('soundtrack', ' ost', '- ost', 'original score')):
            return ETYPE_MUSIC
        dlc_keywords = (
            ' dlc', ' - dlc', 'costume', 'skin pack', 'character pack',
            'expansion pack', 'season pass', 'bonus content', 'starter pack',
            'booster pack', 'upgrade pack', 'content pack', 'item pack',
            'map pack', 'weapon pack', 'outfit', 'wallpaper', 'artbook',
            'art book', 'digital art', 'dress -', 'costume -',
        )
        if any(kw in nl for kw in dlc_keywords):
            return ETYPE_DLC
        if nl.endswith(' demo') or ' demo ' in nl or nl.startswith('demo:'):
            return ETYPE_DEMO
        if any(kw in nl for kw in ('dedicated server', 'sdk', 'editor', 'modding tool')):
            return ETYPE_TOOL
        return ETYPE_GAME

    def _show_create_collection_menu(self, event=None):
        """弹出创建分类菜单（统一所有收藏夹创建入口）"""
        from ui_utils import perf_log
        perf_log('USER_ACTION', extra='create_collection_menu')
        menu = tk.Menu(self.root, tearoff=0)
        # 有任何活跃筛选时，显示"保存筛选为分类"
        active_colls = [
            cid for cid, s in self._coll_filter_states.items()
            if s != 'default']
        has_coll_filter = len(active_colls) > 0
        filters = self._lib_read_filter_state()
        _defaults = {'filter_mode': '全部', 'model_filter': None,
                     'dirty_only': False, 'uploading_only': False,
                     'source_filter': '来源', 'conf_filter': '确信度',
                     'vol_filter': '信息量', 'qual_filter': '质量'}
        has_game_filter = any(
            filters.get(k) != v for k, v in _defaults.items())
        has_search = bool(self._lib_search_var.get().strip())
        has_any_filter = has_coll_filter or has_game_filter or has_search
        if has_any_filter:
            menu.add_command(label="📐 将当前筛选保存为分类",
                             command=self._save_filter_as_collection)
            menu.add_separator()
        menu.add_command(label="➕ 新建空分类", command=self._lib_new_collection)
        # 从来源创建（子菜单）
        src_menu = tk.Menu(menu, tearoff=0)
        src_menu.add_command(label="🤖 AI 智能筛选", command=self.ai_search_ui)
        src_menu.add_command(label="⭐ 从推荐来源创建",
            command=lambda: self.personal_recommend_ui(sources='recommend'))
        src_menu.add_command(label="🏆 从 Steam 列表页面创建",
            command=self.curator_sync_ui)
        src_menu.add_command(label="📊 从 IGDB 数据库创建",
            command=lambda: self.personal_recommend_ui(sources='igdb'))
        src_menu.add_command(label="📊 从 SteamDB 创建",
            command=self.steamdb_sync_ui)
        menu.add_cascade(label="🔍 从来源创建", menu=src_menu)
        # 导入（子菜单）
        imp_menu = tk.Menu(menu, tearoff=0)
        imp_menu.add_command(label="📁 从文件导入", command=self.import_collection)
        imp_menu.add_command(label="👤 从其他账号导入",
            command=self.import_from_account)
        imp_menu.add_command(label="👥 从好友游戏库创建",
            command=self.open_friend_sync_ui)
        menu.add_cascade(label="📥 导入", menu=imp_menu)
        # 社区
        menu.add_separator()
        menu.add_command(label="🌐 浏览社区分类", command=self.browse_shared_ui)
        menu.add_command(label="📤 分享我的分类",
            command=self.share_collections_ui)
        if os.environ.get('STEAMSHELF_DEBUG_EXPR'):
            menu.add_separator()
            menu.add_command(label="🔍 检查表达式分类健康",
                             command=self._health_check_expr_collections)
        # 在按钮上方弹出
        btn = self._create_coll_btn
        menu_h = menu.yposition("end") + 30  # 最后一项 y + 项高 + 边距
        menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() - menu_h)

    def _lib_new_collection(self):
        """新建收藏夹"""
        name = simpledialog.askstring("新建分类", "请输入分类名称：", parent=self.root)
        if not name or not name.strip():
            return
        messagebox.showinfo("提示", f"分类 \"{name.strip()}\" 创建功能将在后续版本完善。",
                            parent=self.root)

    # ── 筛选表达式方法已移至 ui_library_expression.py ──

    def _cycle_coll_filter(self, col_id):
        """循环收藏夹筛选状态：default → plus → minus → default"""
        current = self._coll_filter_states.get(col_id, 'default')
        new_state = {'default': 'plus', 'plus': 'minus', 'minus': 'default'}[current]
        self._coll_filter_states[col_id] = new_state

        img, tags = self._coll_item_tags(col_id, new_state)
        try:
            self._coll_tree.item(col_id, image=img, tags=tags)
        except Exception:
            pass

        self._apply_coll_filters()

    def _batch_set_coll_filter(self, state):
        """批量设置选中收藏夹的筛选状态（'plus'/'minus'/'default'）"""
        from ui_utils import perf_log
        sel = self._coll_tree.selection()
        perf_log('USER_ACTION', extra=f'batch_filter state={state} sel={list(sel)}')
        if not sel:
            return
        for col_id in sel:
            self._coll_filter_states[col_id] = state
            img, tags = self._coll_item_tags(col_id, state)
            try:
                self._coll_tree.item(col_id, image=img, tags=tags)
            except Exception:
                pass
        self._apply_coll_filters()

    def _lib_reset_coll_filters(self):
        """还原库列表：清除所有 ＋/－ 筛选状态"""
        for col_id in list(self._coll_filter_states):
            img, tags = self._coll_item_tags(col_id, 'default')
            try:
                self._coll_tree.item(col_id, image=img, tags=tags)
            except Exception:
                pass
        self._coll_filter_states.clear()
        self._viewing_collections = False
        self._viewed_coll_ids = set()
        self._lib_all_games_backup = None
        self._hide_coll_filter_status()
        self._apply_coll_filters()

    def _toggle_coll_op(self, group, index, event=None):
        """点击第 index 个运算符切换 ∪↔∩"""
        ops = self._coll_ops_plus if group == 'plus' else self._coll_ops_minus
        if 0 <= index < len(ops):
            ops[index] = not ops[index]
        self._apply_coll_filters()

    def _update_coll_filter_status(self, plus_ids, minus_ids, game_count):
        """更新筛选状态栏（Text 控件，自动换行，运算符可点击）"""
        t = self._coll_filter_status
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        # 配置 tag 样式
        t.tag_configure("txt", foreground="#555")
        t.tag_configure("op", foreground="#1565c0",
                         font=("微软雅黑", 12, "bold"),
                         background="#e3f2fd")
        t.tag_configure("paren", foreground="#333",
                         font=("微软雅黑", 10, "bold"))
        cache = getattr(self, '_coll_data_cache', {})
        first = True
        for prefix, ids, ops, grp in [
            ("＋", plus_ids, self._coll_ops_plus, "plus"),
            ("－", minus_ids, self._coll_ops_minus, "minus"),
        ]:
            if not ids:
                continue
            names = [cache.get(c, {}).get('name', c) for c in ids]
            if not first:
                t.insert(tk.END, "／", "txt")
            first = False
            t.insert(tk.END, prefix, "txt")
            if len(names) == 1:
                t.insert(tk.END, names[0], "txt")
            else:
                self._render_ops_expr(t, names, ops, grp)
        t.config(state=tk.DISABLED)
        # 先 pack 让控件获得实际宽度，再算显示行数
        if not t.winfo_ismapped():
            t.pack(fill=tk.X, pady=(2, 0), before=self._lib_status)
        t.update_idletasks()
        try:
            dl = t.count("1.0", "end-1c", "displaylines")
            t.config(height=max(1, (dl[0] if dl else 0) + 1))
        except Exception:
            t.config(height=2)

    def _hide_coll_filter_status(self):
        """隐藏筛选状态栏"""
        if self._coll_filter_status.winfo_ismapped():
            self._coll_filter_status.pack_forget()

    def _update_view_btn_text(self):
        """查看/还原状态跟踪（工具条已移除，保留方法避免调用方报错）"""
        pass

    def _on_coll_double_click(self, event):
        """双击收藏夹：独占筛选（仅显示该分类的游戏），再次双击取消"""
        if hasattr(self, '_coll_rename_timer') and self._coll_rename_timer:
            self.root.after_cancel(self._coll_rename_timer)
            self._coll_rename_timer = None
        item = self._coll_tree.identify_row(event.y)
        if not item:
            return
        # 判断是否已经是唯一的 plus（再次双击则取消筛选）
        is_sole_plus = (self._coll_filter_states.get(item) == 'plus'
                        and all(s == 'default' for c, s in self._coll_filter_states.items()
                                if c != item))
        # 重置所有
        for cid in list(self._coll_filter_states):
            self._coll_filter_states[cid] = 'default'
            img, tags = self._coll_item_tags(cid, 'default')
            try:
                self._coll_tree.item(cid, image=img, tags=tags)
            except Exception:
                pass
        # 非独占状态时设为 plus
        if not is_sole_plus:
            self._coll_filter_states[item] = 'plus'
            self._viewed_coll_ids = {item}
            img, tags = self._coll_item_tags(item, 'plus')
            try:
                self._coll_tree.item(item, image=img, tags=tags)
            except Exception:
                pass
        else:
            self._viewed_coll_ids = set()
        self._apply_coll_filters()
        return "break"

    def _coll_filter_reset_view(self):
        """重置收藏夹筛选，恢复正常游戏列表"""
        self._viewing_collections = False
        self._viewed_coll_ids = set()
        self._update_view_btn_text()
        self._hide_coll_filter_status()
        if self._lib_all_games_backup is not None:
            self._lib_all_games = self._lib_all_games_backup
            self._lib_all_games_backup = None
            self._lib_populate_tree(force_rebuild=True)
        elif self._cef_bridge and self._cef_bridge.is_connected():
            self._lib_load_owned_from_cef()
        else:
            self._lib_populate_tree(force_rebuild=True)

    def _coll_filter_build_games(self, all_app_ids, owned_app_ids):
        """从筛选后的 app ID 集合构建游戏列表"""
        existing_games_map = {str(g.get('app_id', '')): g for g in self._lib_all_games_backup}
        games = []
        _cef_cache = getattr(self, '_cef_unowned_cache', {})
        _detail_cache = getattr(self, '_app_detail_cache', {})
        for aid in all_app_ids:
            aid_str = str(aid)
            name = (self._game_name_cache.get(aid_str)
                    or self._game_name_cache.get(aid)
                    or f"AppID {aid}")
            is_owned = aid in owned_app_ids
            existing = existing_games_map.get(aid_str)
            app_type = existing.get('type', existing.get('app_type')) if existing else None
            if app_type is None:
                # 优先用持久化的 type cache（Steam Store API 返回的准确类型）
                cached_type = self._app_type_cache.get(aid_str)
                if cached_type:
                    app_type = STORE_TYPE_MAP.get(cached_type, ETYPE_GAME)
                elif name != f"AppID {aid}":
                    app_type = self._guess_type_from_name(name)
            entry = {
                'app_id': aid_str,
                'name': name,
                'owned': is_owned,
                'type': app_type or 1,
            }
            # 从 CEF / existing 数据复制额外字段（评测/MC/发行/入库时间）
            src = existing
            if not src:
                src = _cef_cache.get(aid_str)
            if src:
                for k in ('review_pct', 'review_score', 'metacritic',
                          'rt_release', 'rt_purchased'):
                    v = src.get(k, 0)
                    if v:
                        entry[k] = v
            # 回退：从 Store API 详情缓存补充 metacritic / release_date
            detail = _detail_cache.get(aid_str)
            if isinstance(detail, dict):
                if detail.get('_removed'):
                    if name == f"AppID {aid}":
                        entry['name'] = f"🚫 AppID {aid}"
                    else:
                        entry['name'] = f"🚫 {name}"
                if not entry.get('metacritic') and detail.get('metacritic'):
                    entry['metacritic'] = detail['metacritic']
                if not entry.get('rt_release') and detail.get('release_date'):
                    entry['release_date_str'] = detail['release_date']
                if not entry.get('review_score') and detail.get('review_score'):
                    entry['review_score'] = detail['review_score']
                if not entry.get('review_pct') and detail.get('review_pct'):
                    entry['review_pct'] = detail['review_pct']
            games.append(entry)
        return games

    def _apply_coll_filters(self):
        """根据所有收藏夹的 ＋/－ 状态筛选游戏列表"""
        plus_ids = [cid for cid, s in self._coll_filter_states.items() if s == 'plus']
        minus_ids = [cid for cid, s in self._coll_filter_states.items() if s == 'minus']

        show_mode = getattr(self, '_coll_filter_var', None)
        show_mode = show_mode.get() if show_mode else "已入库"

        implicit_all = False
        if not plus_ids and not minus_ids:
            if show_mode != "已入库" and hasattr(self, '_coll_data_cache') and self._coll_data_cache:
                plus_ids = list(self._coll_data_cache.keys())
                implicit_all = True
            else:
                self._coll_filter_reset_view()
                return

        if not hasattr(self, '_coll_data_cache'):
            return

        if self._lib_all_games_backup is None:
            self._lib_all_games_backup = self._lib_all_games

        # 同步 ops 列表长度
        for ids, attr in [(plus_ids, '_coll_ops_plus'), (minus_ids, '_coll_ops_minus')]:
            needed = max(0, len(ids) - 1)
            ops = getattr(self, attr)
            if len(ops) != needed:
                setattr(self, attr, [True] * needed)

        plus_owned, plus_not_owned = self._eval_coll_expr(
            plus_ids, self._coll_ops_plus)
        minus_owned, minus_not_owned = self._eval_coll_expr(
            minus_ids, self._coll_ops_minus)

        if plus_ids:
            owned_app_ids = plus_owned
            not_owned_app_ids = plus_not_owned
        else:
            base = self._lib_all_games_backup
            owned_app_ids = set(str(g['app_id']) for g in base if g.get('owned'))
            not_owned_app_ids = set()

        owned_app_ids = owned_app_ids - minus_owned
        not_owned_app_ids = not_owned_app_ids - minus_not_owned

        if show_mode == "已入库":
            all_app_ids = owned_app_ids
        elif show_mode == "全部":
            all_app_ids = owned_app_ids | not_owned_app_ids
        else:
            all_app_ids = not_owned_app_ids

        # "未入库"模式：强制 owned 为空，避免跨收藏夹重叠导致 is_owned 误判
        effective_owned = set() if show_mode == "未入库" else owned_app_ids
        games = self._coll_filter_build_games(all_app_ids, effective_owned)
        from ui_utils import perf_log
        import time as _t; _sort_t0 = _t.perf_counter()
        games.sort(key=lambda g: steam_sort_key(g['name']))
        perf_log('  coll_filters: sort', (_t.perf_counter() - _sort_t0) * 1000,
                 f'{len(games)} games, mode={show_mode}, implicit={implicit_all}')
        self._lib_all_games = games
        self._viewing_collections = True
        self._lib_populate_tree(force_rebuild=True)
        self._bg_resolve_visible_names()

        self._update_view_btn_text()
        if implicit_all:
            self._hide_coll_filter_status()
        else:
            self._update_coll_filter_status(plus_ids, minus_ids, len(games))

    def _lib_toggle_view_collection(self):
        """查看/取消收藏夹筛选

        - 还原模式（选中未变或无选中）：重置所有 ＋/－ → 默认
        - 查看模式：将选中的收藏夹设为包含(＋)，先重置旧筛选
        """
        sel = set(self._coll_tree.selection())
        is_restore = self._viewing_collections and (not sel or sel == self._viewed_coll_ids)

        if is_restore:
            # ── 还原模式：清除＋/－，让 _apply_coll_filters 根据 show_mode 决定 ──
            for col_id in list(self._coll_filter_states):
                self._coll_filter_states[col_id] = 'default'
                img, tags = self._coll_item_tags(col_id, 'default')
                try:
                    self._coll_tree.item(col_id, image=img, tags=tags)
                except Exception:
                    pass
            self._coll_filter_states.clear()
            self._viewed_coll_ids = set()
            # 委托给 _apply_coll_filters：
            # - 已入库模式 → 恢复完整游戏列表
            # - 未入库/全部模式 → 显示所有分类的对应游戏
            self._apply_coll_filters()
        else:
            # ── 查看模式：先重置旧筛选，再应用新选中 ──
            if not sel:
                return
            if self._viewing_collections:
                # 重置旧筛选状态
                for col_id in list(self._coll_filter_states):
                    self._coll_filter_states[col_id] = 'default'
                    img, tags = self._coll_item_tags(col_id, 'default')
                    try:
                        self._coll_tree.item(col_id, image=img, tags=tags)
                    except Exception:
                        pass
                self._coll_filter_states.clear()
            self._viewed_coll_ids = set(sel)
            changed = False
            for col_id in sel:
                if self._coll_filter_states.get(col_id, 'default') == 'default':
                    self._coll_filter_states[col_id] = 'plus'
                    img, tags = self._coll_item_tags(col_id, 'plus')
                    try:
                        self._coll_tree.item(col_id, image=img, tags=tags)
                    except Exception:
                        pass
                    changed = True
            if changed:
                self._apply_coll_filters()
            else:
                self._update_view_btn_text()

    def _on_collection_selection_changed(self, event=None):
        """收藏夹选择变化时：互斥取消游戏选择 + 切换上下文"""
        sel = self._coll_tree.selection()
        if sel and hasattr(self, '_lib_tree'):
            game_sel = self._lib_tree.selection()
            if game_sel:
                self._lib_tree.selection_remove(*game_sel)
            self._toolbar_context = 'coll'
            self._update_toolbar_context()
        self._update_view_btn_text()

    def _on_game_selection_changed(self, event=None):
        """游戏列表选择变化：选中游戏自动选子笔记，部分笔记选中时游戏显示浅高亮"""
        if self._selection_updating:
            return
        self._selection_updating = True
        try:
            tree = self._lib_tree
            current = set(tree.selection())
            prev = self._prev_tree_selection

            added = current - prev
            removed = prev - current
            new_sel = set(current)

            for iid in added:
                if "::" not in iid:
                    for child in tree.get_children(iid):
                        new_sel.add(child)

            for iid in removed:
                if "::" not in iid:
                    try:
                        children = tree.get_children(iid)
                    except Exception:
                        continue
                    for child in children:
                        if child not in current:
                            new_sel.discard(child)

            affected_games = set()
            for iid in added | removed:
                if "::n::" in iid:
                    affected_games.add(iid.split("::n::")[0])
            for game_iid in affected_games:
                try:
                    children = set(tree.get_children(game_iid))
                except Exception:
                    continue
                if not children:
                    continue
                if children <= new_sel:
                    new_sel.add(game_iid)
                else:
                    new_sel.discard(game_iid)

            if new_sel != current:
                tree.selection_set(list(new_sel))
                current = new_sel

            self._prev_tree_selection = set(current)

            # 收集受影响的游戏行（从 added/removed 和 affected_games 推导）
            dirty_games = set(affected_games)
            for iid in added | removed:
                if "::n::" not in iid:
                    dirty_games.add(iid)

            # 防抖：拖动期间只在最后一次触发后执行 partial_select 更新
            if hasattr(self, '_partial_select_timer') and self._partial_select_timer:
                self.root.after_cancel(self._partial_select_timer)
            self._partial_select_dirty = getattr(self, '_partial_select_dirty', set()) | dirty_games
            self._partial_select_timer = self.root.after(
                50, self._flush_partial_select)

            if current and hasattr(self, '_coll_tree'):
                coll_sel = self._coll_tree.selection()
                if coll_sel:
                    self._coll_tree.selection_remove(*coll_sel)
                self._toolbar_context = 'game'
                self._update_toolbar_context()
        finally:
            self._selection_updating = False

    def _set_partial_select(self, game_iid, partial):
        """添加或移除游戏行的 partial_select 标签（浅色高亮）"""
        tree = self._lib_tree
        current_tags = list(tree.item(game_iid, "tags"))
        has = "partial_select" in current_tags
        if partial and not has:
            current_tags.append("partial_select")
            tree.item(game_iid, tags=tuple(current_tags))
        elif not partial and has:
            current_tags.remove("partial_select")
            tree.item(game_iid, tags=tuple(current_tags))

    def _flush_partial_select(self):
        """防抖回调：只更新受影响的游戏行的 partial_select 标签"""
        self._partial_select_timer = None
        dirty = getattr(self, '_partial_select_dirty', set())
        self._partial_select_dirty = set()
        tree = self._lib_tree
        current = set(tree.selection())
        for game_iid in dirty:
            try:
                children = set(tree.get_children(game_iid))
            except Exception:
                continue
            if not children:
                self._set_partial_select(game_iid, False)
                continue
            selected_children = children & current
            if game_iid in current or not selected_children:
                self._set_partial_select(game_iid, False)
            else:
                self._set_partial_select(game_iid, True)

    def _update_toolbar_context(self):
        """更新工具条上下文指示（emoji 已集成到按钮，无需额外更新）"""
        pass

    # ── 双击分发 ──

    def _display_col_to_id(self, col_str):
        """将 identify_column 返回的 '#N' 转换为实际 column ID（兼容 displaycolumns）"""
        if col_str == "#0":
            return "#0"
        idx = int(col_str.replace("#", "")) - 1
        shown = list(self._lib_tree["displaycolumns"])
        if 0 <= idx < len(shown):
            return shown[idx]
        return ""

    def _on_tree_double_click_dispatch(self, event):
        """双击按列分发：📝列→笔记查看器，AI信息列→AI预览"""
        region = self._lib_tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        col_id = self._display_col_to_id(
            self._lib_tree.identify_column(event.x))
        iid = self._lib_tree.identify_row(event.y)
        if not iid:
            return
        aid = iid.split("::n::")[0] if "::n::" in iid else iid
        if col_id == "appid":
            import webbrowser
            webbrowser.open(f"https://store.steampowered.com/app/{aid}")
        elif col_id == "name":
            import webbrowser
            webbrowser.open(f"steam://nav/games/details/{aid}")
        elif col_id == "notes":
            self._open_notes_viewer(aid)
        elif col_id == "source":
            self._open_ai_notes_preview(aid)

    def _show_type_filter_popup(self):
        """单击 Type 列头时弹出类型筛选勾选框"""
        if hasattr(self, '_type_popup') and self._type_popup:
            try:
                self._type_popup.destroy()
            except Exception:
                pass
            self._type_popup = None
            return

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        self._type_popup = popup

        x = self._lib_tree.winfo_rootx()
        y = self._lib_tree.winfo_rooty() + 22
        popup.geometry(f"+{x}+{y}")

        frame = ttk.Frame(popup, padding=6)
        frame.pack()

        vars_ = {}
        for t in self._ALL_TYPES:
            v = tk.BooleanVar(value=(t in self._type_filter))
            vars_[t] = v
            cb = ttk.Checkbutton(frame, text=t, variable=v,
                                 command=lambda: self._on_type_filter_changed(vars_))
            cb.pack(anchor=tk.W)

        sep = ttk.Separator(frame, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=4)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        def select_all():
            for v in vars_.values():
                v.set(True)
            self._on_type_filter_changed(vars_)

        def only_game():
            for t, v in vars_.items():
                v.set(t == "Game")
            self._on_type_filter_changed(vars_)

        ttk.Button(btn_frame, text="全选", width=6, command=select_all).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_frame, text="仅Game", width=8, command=only_game).pack(side=tk.LEFT)

        def _close_popup(e=None):
            try:
                popup.grab_release()
                popup.destroy()
            except Exception:
                pass
            self._type_popup = None

        def _on_popup_click(e):
            if e.widget == popup and (
                e.x < 0 or e.y < 0 or
                e.x > popup.winfo_width() or
                e.y > popup.winfo_height()
            ):
                _close_popup()

        popup.bind("<Button-1>", _on_popup_click)
        popup.bind("<Escape>", _close_popup)
        popup.update_idletasks()
        popup.grab_set()

    def _on_type_filter_changed(self, vars_):
        """类型勾选变化时更新筛选并刷新列表"""
        self._type_filter = {t for t, v in vars_.items() if v.get()}
        self._update_type_header_text()
        self._lib_populate_tree()

    def _update_type_header_text(self):
        """根据类型筛选状态更新 Type 列头文本"""
        base = "Type"
        if "type" in self._sort_columns:
            arrow = " ↑" if self._sort_columns["type"] == 'asc' else " ↓"
            if len(self._sort_order) > 1:
                priority = self._sort_order.index("type") + 1
                base = f"Type{arrow}{priority}"
            else:
                base = f"Type{arrow}"
        if len(self._type_filter) < len(self._ALL_TYPES):
            base += " ▼"
        self._lib_tree.heading("type", text=base)

    # ── 上下文工具条分发方法 ──

    def _ctx_export(self):
        """导出：根据上下文分发到收藏夹导出或笔记导出"""
        if self._toolbar_context == 'coll':
            self.export_static_collection()
        else:
            self._ui_export_dialog()

    def _resolve_sel_note_ids(self, sel):
        """从选中项解析 {aid: [nid, ...]}，已展开取可见子节点，未展开用筛选解析"""
        from collections import defaultdict
        by_app = defaultdict(list)
        sel_set = set(sel)
        for s in sel:
            if "::n::" in s:
                aid, nid = s.split("::n::")
                by_app[aid].append(nid)
                continue
            # 游戏行
            children = self._lib_tree.get_children(s)
            real = [c for c in children if "::n::" in c]
            if real:
                for child in real:
                    if child not in sel_set:
                        aid, nid = child.split("::n::")
                        by_app[aid].append(nid)
            else:
                aid = self._iid_to_app_id(s)
                visible = self._get_visible_note_ids(aid)
                if visible is not None:
                    by_app[aid].extend(visible)
                else:
                    notes = self.manager.read_notes_cached(aid).get("notes", [])
                    by_app[aid].extend(n["id"] for n in notes if n.get("id"))
        return {a: nids for a, nids in by_app.items() if nids}

    def _ctx_delete(self):
        """删除：根据上下文分发到分类删除或笔记删除"""
        if self._toolbar_context == 'coll':
            self._lib_delete_collection()
            return

        sel = self._lib_tree.selection()
        if not sel:
            self._ui_delete_notes()
            return

        by_app = self._resolve_sel_note_ids(sel)
        if not by_app:
            self._ui_delete_notes()
            return

        uploading = [a for a in by_app if self.is_app_uploading(a)]
        if uploading:
            names = ", ".join(self._get_game_name(a) for a in uploading[:5])
            messagebox.showwarning("☁️⬆ 上传中",
                f"以下游戏的笔记正在上传，无法删除：\n{names}",
                parent=self.root)
            for a in uploading:
                del by_app[a]
            if not by_app:
                return

        total = sum(len(nids) for nids in by_app.values())
        if total == 1:
            aid = next(iter(by_app))
            msg = f"确定删除「{self._get_game_name(aid)}」的 1 条笔记？"
        else:
            msg = f"确定删除 {len(by_app)} 个游戏的共 {total} 条笔记？"

        if not messagebox.askyesno("确认删除", f"{msg}\n此操作不可撤销。",
                                    parent=self.root):
            return

        deleted = 0
        for aid, nids in by_app.items():
            deleted += self.manager.delete_notes_by_ids(aid, nids)

        messagebox.showinfo("✅ 成功", f"已删除 {deleted} 条笔记。",
                            parent=self.root)
        self._refresh_games_list()

    # ── 笔记展开/收起 ──

    def _expand_all_notes(self):
        """展开所有有笔记子节点的游戏（触发懒加载）"""
        tree = self._lib_tree
        for item_id in tree.get_children():
            children = tree.get_children(item_id)
            if not children:
                continue
            # 懒加载：如果是占位节点，先加载真实子节点
            if children[0].endswith("::lazy"):
                tree.focus(item_id)
                self._on_tree_open()
            tree.item(item_id, open=True)

    def _collapse_all_notes(self):
        """收起所有展开的游戏"""
        for item_id in self._lib_tree.get_children():
            self._lib_tree.item(item_id, open=False)

    def _on_coll_right_click(self, event):
        """收藏夹树右键菜单"""
        from ui_utils import perf_log
        perf_log('USER_ACTION', extra=f'coll_right_click sel={list(self._coll_tree.selection())}')
        menu = tk.Menu(self.root, tearoff=0)

        sel = self._coll_tree.selection()
        if sel and len(sel) == 1:
            col_id = sel[0]
            coll_data = self._coll_data_cache.get(col_id)
            coll_name = coll_data.get('name', col_id) if coll_data else col_id
            target_col = (col_id, coll_name)

            menu.add_command(label="🔄 从本地来源更新分类", command=self.update_static_collection)
            if coll_data and col_id.startswith("uc-"):
                menu.add_command(label="✏️ 重命名",
                    command=lambda cid=col_id, cn=coll_name:
                        self._rename_collection(cid, cn))

            # 绑定来源：从来源更新 + 解绑（放在第一组）
            if self._collections_core:
                _si = self._collections_core.get_collection_source(col_id)
                if _si:
                    if _si.get('source_type') == 'expression':
                        menu.add_command(
                            label=f"🔄 从来源更新「{coll_name}」",
                            command=lambda cid=col_id, si=_si:
                                self._update_expression_upstream(cid, si))
                    else:
                        _ml = {"incremental_aux": "增量+辅助",
                               "incremental": "增量", "replace": "替换"}
                        _m = _ml.get(_si.get('update_mode', ''), '增量+辅助')
                        menu.add_command(
                            label=f"🔄 从来源更新「{coll_name}」({_m})",
                            command=lambda cid=col_id, si=_si:
                                self._update_from_cached_source(cid, si))
                    menu.add_command(
                        label=f"🔗✂️ 解绑来源「{_si.get('source_display_name', '')[:20]}」",
                        command=lambda cid=col_id, cn=coll_name:
                            self._unbind_collection_source(cid, cn))

            # 从各种来源更新（子菜单）
            upd_menu = tk.Menu(menu, tearoff=0)
            upd_menu.add_command(label="🤖 AI 智能筛选更新",
                command=lambda tc=target_col: self.ai_search_ui(target_col=tc))
            upd_menu.add_command(label="⭐ 从推荐来源更新",
                command=lambda tc=target_col: self.personal_recommend_ui(target_col=tc, sources='recommend'))
            upd_menu.add_command(label="🏆 从 Steam 列表页面更新",
                command=lambda tc=target_col: self.curator_sync_ui(target_col=tc))
            upd_menu.add_command(label="📊 从 IGDB 数据库更新",
                command=lambda tc=target_col: self.personal_recommend_ui(target_col=tc, sources='igdb'))
            upd_menu.add_command(label="📊 从 SteamDB 更新",
                command=lambda tc=target_col: self.steamdb_sync_ui(target_col=tc))
            upd_menu.add_separator()
            upd_menu.add_command(label="📁 从文件更新",
                command=lambda tc=target_col: self.import_collection(target_col=tc))
            menu.add_separator()
            menu.add_cascade(label="🔍 从来源更新", menu=upd_menu)
            menu.add_separator()
            menu.add_command(label="📋 查看分类内容",
                command=self._lib_toggle_view_collection)
            menu.add_command(label="📤 导出分类",
                command=self.export_static_collection)
            menu.add_command(label="🌐 分享到社区",
                command=lambda c=sel[0]: self.share_collections_ui(
                    preselected=[c]))

        # 多选：更新选中的绑定分类 / 导出
        if sel and len(sel) > 1 and self._collections_core:
            sel_set = set(sel)
            all_sources = self._collections_core._get_all_sources()
            linked = {k: v for k, v in all_sources.items() if k in sel_set}
            if linked:
                menu.add_separator()
                menu.add_command(
                    label=f"🔄 更新选中的 {len(linked)} 个绑定分类",
                    command=lambda ids=sel_set:
                        self._update_all_cached_sources(col_ids=ids))
            menu.add_separator()
            menu.add_command(
                label=f"📤 导出选中的 {len(sel)} 个分类",
                command=self.export_static_collection)
            menu.add_command(label="📋 查看分类内容",
                command=self._lib_toggle_view_collection)

        # 一键更新所有有来源的收藏夹
        if self._collections_core:
            all_sources = self._collections_core._get_all_sources()
            if all_sources:
                count = len(all_sources)
                menu.add_separator()
                menu.add_command(
                    label=f"🔄 一键更新所有来源（{count} 个）",
                    command=self._update_all_cached_sources)

        # 删除收藏夹（支持多选）
        if sel:
            menu.add_separator()
            if len(sel) == 1:
                coll_data = self._coll_data_cache.get(sel[0])
                del_label = f"🗑️ 删除「{coll_data['name'] if coll_data else sel[0]}」"
            else:
                del_label = f"🗑️ 删除选中的 {len(sel)} 个分类"
            menu.add_command(label=del_label,
                             command=self._lib_delete_collection)

        menu.add_separator()
        menu.add_command(label="🔄 刷新库列表", command=self._lib_refresh)
        if any(v in ('plus', 'minus') for v in self._coll_filter_states.values()):
            menu.add_command(label="↩️ 还原库列表", command=self._lib_reset_coll_filters)

        self._smart_popup(menu, event.x_root, event.y_root)

    def _rename_collection(self, col_id, current_name):
        """通过 CEF 重命名收藏夹（右键菜单入口 → 内联编辑）"""
        self._coll_begin_inline_rename(col_id)

    def _coll_begin_inline_rename(self, col_id):
        """在收藏夹树上覆盖 Entry 实现内联重命名"""
        # 销毁上一次未完成的 rename Entry（防止快速连续触发导致泄漏）
        old = getattr(self, '_rename_entry', None)
        if old:
            try:
                old.unbind("<FocusOut>")
                old.unbind("<Return>")
                old.unbind("<Escape>")
                old.destroy()
            except Exception:
                pass
            self._rename_entry = None
        if not self._cef_bridge or not self._cef_bridge.is_connected():
            return
        coll_data = self._coll_data_cache.get(col_id)
        if not coll_data:
            return
        tree = self._coll_tree
        try:
            bbox = tree.bbox(col_id, column="#0")
        except Exception:
            return
        if not bbox:
            return

        x, y, w, h = bbox
        current_name = coll_data['name']

        # 动态定位：扫描找到 text 元素的起始 x 坐标
        text_x = x + 28  # 默认值（image 14px + indent 14px）
        mid_y = y + h // 2
        for px in range(x, x + 60, 2):
            if 'text' in str(tree.identify_element(px, mid_y)):
                text_x = px
                break

        entry = tk.Entry(tree, font="TkDefaultFont")
        self._rename_entry = entry
        entry.insert(0, current_name)
        entry.select_range(0, tk.END)
        entry.place(x=text_x, y=y, width=w - text_x + x, height=h)
        entry.focus_set()

        def commit(e=None):
            new_name = entry.get().strip()
            entry.destroy()
            self._rename_entry = None
            if not new_name or new_name == current_name:
                return
            self._cef_rename_collection(col_id, new_name)

        def cancel(e=None):
            entry.destroy()
            self._rename_entry = None

        entry.bind("<Return>", commit)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", commit)

    def _cef_rename_collection(self, col_id, new_name):
        """CEF 执行重命名 + SaveCollection 云同步"""
        from ui_utils import perf_log
        perf_log('USER_ACTION', extra=f'rename_collection {new_name}')
        import json
        result = self._cef_bridge._eval_js(f'''
(async function() {{
    var col = collectionStore.GetCollection({json.dumps(col_id)});
    if (!col) return {{error: "not found"}};
    col.m_strName = {json.dumps(new_name, ensure_ascii=False)};
    try {{
        await collectionStore.SaveCollection(col);
        return {{ok: true}};
    }} catch(e) {{ return {{error: e.message}}; }}
}})()
''', timeout=15)
        if isinstance(result, dict) and result.get('ok'):
            # 局部更新：只改节点文字，不重建 33645 行游戏列表
            cd = self._coll_data_cache.get(col_id)
            if cd:
                old_text = self._coll_tree.item(col_id, 'text')
                new_text = old_text.replace(cd['name'], new_name, 1)
                self._coll_tree.item(col_id, text=new_text)
                cd['name'] = new_name
                # 同步更新 CEF 缓存
                cef_cache = getattr(self, '_cef_collections_cache', None)
                if cef_cache and col_id in cef_cache.get('collections', {}):
                    cef_cache['collections'][col_id]['name'] = new_name
            else:
                self._ui_refresh()
        elif isinstance(result, dict) and result.get('error'):
            messagebox.showerror("重命名失败", result['error'],
                                 parent=self.root)

    def _on_coll_drag_start(self, event):
        """记录拖动起始位置；点击筛选图标时循环状态；慢点击触发重命名"""
        # 取消之前的重命名计时器
        if hasattr(self, '_coll_rename_timer') and self._coll_rename_timer:
            self.root.after_cancel(self._coll_rename_timer)
            self._coll_rename_timer = None
        item = self._coll_tree.identify_row(event.y)
        if not item:
            return
        element = self._coll_tree.identify_element(event.x, event.y)
        if "image" in str(element):
            self._cycle_coll_filter(item)
            return "break"
        self._coll_drag_start = item
        self._coll_drag_moved = False
        # Ctrl/Cmd 拖动：保存已有选择作为基底
        if event.state & 0xC:
            self._coll_drag_base = set(self._coll_tree.selection())
        else:
            self._coll_drag_base = None
        # 慢点击重命名：已选中的 uc- 项再次点击时启动计时器
        sel = self._coll_tree.selection()
        if (len(sel) == 1 and sel[0] == item
                and item.startswith("uc-")):
            self._coll_rename_timer = self.root.after(
                500, lambda: self._coll_begin_inline_rename(item))

    def _drag_autoscroll(self, tree, y):
        """拖动到边缘时启动定时滚动，返回 clamp 后的 y 坐标"""
        h = tree.winfo_height()
        at_edge = y > h - 25 or y < 25
        if not at_edge:
            self._drag_scroll_cancel()
            return y
        self._drag_scroll_tree = tree
        self._drag_scroll_dir = 1 if y > h - 25 else -1
        if not getattr(self, '_drag_scroll_timer', None):
            self._drag_scroll_t0 = time.time()
            self._drag_scroll_tick()
        return h - 26 if y > h - 25 else 26

    def _drag_scroll_tick(self):
        """定时回调：滚动 + 更新选区 + 调度下一次"""
        tree = getattr(self, '_drag_scroll_tree', None)
        if not tree:
            return
        elapsed = time.time() - getattr(self, '_drag_scroll_t0', 0)
        if elapsed < 2:
            speed, delay = 1, 280
        elif elapsed < 4:
            speed, delay = 2, 150
        else:
            speed, delay = 3, 80
        d = getattr(self, '_drag_scroll_dir', 0)
        tree.yview_scroll(d * speed, "units")
        # 重置 last 以强制选区更新，然后用合成事件触发选区逻辑
        if tree is getattr(self, '_lib_tree', None):
            self._game_drag_last = None
        h = tree.winfo_height()
        tree.event_generate("<B1-Motion>", x=50,
                            y=h - 26 if d > 0 else 26)
        self._drag_scroll_timer = self.root.after(
            delay, self._drag_scroll_tick)

    def _drag_scroll_cancel(self, event=None):
        """取消自动滚动定时器"""
        timer = getattr(self, '_drag_scroll_timer', None)
        if timer:
            self.root.after_cancel(timer)
        self._drag_scroll_timer = None
        self._drag_scroll_t0 = 0

    def _on_game_drag_motion(self, event):
        """游戏列表拖动多选（含层级展开：选中游戏行时自动包含子笔记）"""
        if not self._game_drag_start:
            return
        tree = self._lib_tree
        cy = self._drag_autoscroll(tree, event.y)
        item = tree.identify_row(cy)
        if not item or item == getattr(self, '_game_drag_last', None):
            return
        self._game_drag_last = item

        if not getattr(self, '_game_drag_flat', None):
            flat = []
            for game_iid in tree.get_children():
                flat.append(game_iid)
                if tree.item(game_iid, 'open'):
                    for child in tree.get_children(game_iid):
                        flat.append(child)
            self._game_drag_flat = tuple(flat)
            self._game_drag_idx = {iid: i for i, iid in enumerate(flat)}

        idx_map = self._game_drag_idx
        start_idx = idx_map.get(self._game_drag_start)
        end_idx = idx_map.get(item)
        if start_idx is None or end_idx is None:
            return
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx

        range_items = self._game_drag_flat[start_idx:end_idx + 1]
        expanded = list(range_items)
        expanded_set = set(expanded)
        for iid in range_items:
            if "::n::" not in iid:
                for child in tree.get_children(iid):
                    if child not in expanded_set:
                        expanded.append(child)
                        expanded_set.add(child)

        base = getattr(self, '_game_drag_base', None)
        if base:
            expanded_set |= base

        self._selection_updating = True
        try:
            tree.selection_set(list(expanded_set))
            self._prev_tree_selection = expanded_set
        finally:
            self._selection_updating = False

    def _on_coll_drag_motion(self, event):
        """拖动多选"""
        # 拖动时取消重命名计时器
        if hasattr(self, '_coll_rename_timer') and self._coll_rename_timer:
            self.root.after_cancel(self._coll_rename_timer)
            self._coll_rename_timer = None
        if not self._coll_drag_start:
            return
        cy = self._drag_autoscroll(self._coll_tree, event.y)
        item = self._coll_tree.identify_row(cy)
        if not item:
            return
        all_items = self._coll_tree.get_children()
        if not all_items:
            return
        try:
            start_idx = all_items.index(self._coll_drag_start)
            end_idx = all_items.index(item)
            if start_idx > end_idx:
                start_idx, end_idx = end_idx, start_idx
            items_to_select = list(all_items[start_idx:end_idx+1])
            base = getattr(self, '_coll_drag_base', None)
            if base:
                items_to_select = list(base) + items_to_select
            self._coll_tree.selection_set(items_to_select)
        except ValueError:
            pass

    def _show_column_visibility_menu(self, event):
        """右键表头：弹出列可见性切换菜单"""
        menu = tk.Menu(self.root, tearoff=0)
        if self._sort_columns:
            menu.add_command(label="🔄 清空排序", command=self._clear_all_sorts)
            menu.add_separator()
        # 必须存为实例属性，防止 BooleanVar 被 GC 回收导致勾选消失
        self._col_vis_vars = {}
        toggleable = [
            ("appid", "AppID"), ("notes", "📝 笔记数"),
            ("source", "AI信息"), ("date", "最新笔记"),
            ("review_label", "评测等级"), ("review", "好评%"),
            ("release", "发行日期"), ("acquired", "入库时间"),
            ("metacritic", "MC分数"),
        ]
        for col_id, label in toggleable:
            var = tk.BooleanVar(value=col_id in self._visible_columns)
            self._col_vis_vars[col_id] = var
            menu.add_checkbutton(
                label=label, variable=var,
                command=lambda c=col_id: self._toggle_column_visibility(c))
        menu.tk_popup(event.x_root, event.y_root)

    def _toggle_column_visibility(self, col):
        """切换列的显示/隐藏"""
        if col in self._visible_columns:
            self._visible_columns.discard(col)
        else:
            self._visible_columns.add(col)
            w, mw = self._col_defaults.get(col, (60, 40))
            self._lib_tree.column(col, width=w, minwidth=mw)
        self._apply_displaycolumns()
        self._config["visible_columns"] = list(self._visible_columns)
        self._config_mgr.save()

    def _lib_sort_column(self, col):
        """点击表头排序

        排序逻辑：
        - 首次点击：升序（↑）
        - 再次点击同列：降序（↓）
        - 再次点击：取消排序
        - 点击不同列：添加到多列排序
        """
        if col in self._sort_columns:
            if self._sort_columns[col] == 'asc':
                self._sort_columns[col] = 'desc'
            elif self._sort_columns[col] == 'desc':
                del self._sort_columns[col]
                if col in self._sort_order:
                    self._sort_order.remove(col)
        else:
            self._sort_columns[col] = 'asc'
            if col not in self._sort_order:
                self._sort_order.append(col)

        col_names = {"type": "Type", "appid": "AppID", "name": "游戏名称",
                     "notes": "📝", "source": "AI信息", "date": "最新笔记",
                     "review_label": "评测", "review": "好评%",
                     "release": "发行",
                     "acquired": "入库", "metacritic": "MC"}
        for c in col_names:
            text = col_names[c]
            if c in self._sort_columns:
                arrow = " ↑" if self._sort_columns[c] == 'asc' else " ↓"
                if len(self._sort_order) > 1:
                    priority = self._sort_order.index(c) + 1
                    text = f"{col_names[c]}{arrow}{priority}"
                else:
                    text = f"{col_names[c]}{arrow}"
            if c == "type" and len(self._type_filter) < len(self._ALL_TYPES):
                text += " ▼"
            self._lib_tree.heading(c, text=text)

        if self._sort_columns:
            self._apply_sort_order(self._lib_tree)
        else:
            self._lib_populate_tree(force_rebuild=True)

    def _clear_all_sorts(self):
        """清空所有排序状态，恢复默认顺序"""
        self._sort_columns.clear()
        self._sort_order.clear()
        # 重置所有表头文字
        col_names = {"type": "Type", "appid": "AppID", "name": "游戏名称",
                     "notes": "📝", "source": "AI信息", "date": "最新笔记",
                     "review_label": "评测", "review": "好评%",
                     "release": "发行", "acquired": "入库", "metacritic": "MC"}
        for c, text in col_names.items():
            if c == "type" and len(self._type_filter) < len(self._ALL_TYPES):
                text += " ▼"
            self._lib_tree.heading(c, text=text)
        self._lib_populate_tree(force_rebuild=True)

    def _apply_sort_order(self, tree):
        """使用预缓存排序键 + 单次 Tcl 调用重排顶层项顺序（极快）"""
        cache = getattr(self, '_sort_key_cache', {})
        if not cache:
            return
        item_ids = list(tree.get_children())
        if not item_ids:
            return
        sort_order = list(self._sort_order)
        sort_dirs = dict(self._sort_columns)

        def _is_empty(val):
            if isinstance(val, tuple):
                return all(v == 0 for v in val)
            return not val

        def sort_key(item_id):
            cached = cache.get(item_id)
            if not cached:
                return tuple((1,) for _ in sort_order)
            keys = []
            for c in sort_order:
                val = cached.get(c, 0)
                if _is_empty(val):
                    keys.append((1,))  # nulls last
                else:
                    if sort_dirs[c] == 'desc':
                        if isinstance(val, (int, float)):
                            val = -val
                        elif isinstance(val, tuple):
                            val = tuple(-v for v in val)
                        elif isinstance(val, str):
                            val = tuple(-ord(ch) for ch in val[:50])
                    keys.append((0, val))
            return tuple(keys)

        item_ids.sort(key=sort_key)
        tree.tk.call(tree._w, 'children', '', tuple(item_ids))

    def _lib_delete_collection(self):
        """删除选中的收藏夹（支持多选）"""
        print("[DEBUG] _lib_delete_collection CALLED", flush=True)
        from ui_utils import perf_log
        perf_log('USER_ACTION', extra='delete_collection')
        sel = self._coll_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择要删除的分类。",
                                parent=self.root)
            return

        names = []
        for col_id in sel:
            cd = self._coll_data_cache.get(col_id)
            names.append(cd['name'] if cd else col_id)

        if len(names) == 1:
            msg = f"确定删除分类「{names[0]}」？"
        else:
            preview = "\n".join(f"  • {n}" for n in names[:10])
            if len(names) > 10:
                preview += f"\n  …等共 {len(names)} 个"
            msg = f"确定删除以下 {len(names)} 个分类？\n{preview}"

        if not messagebox.askyesno("确认删除",
                f"{msg}\n\n此操作不可撤销。", parent=self.root):
            return

        cef_ok = (self._cef_bridge and self._cef_bridge.is_connected())
        errors = []

        # 本地 JSON 一次性处理
        data = self._collections_core.load_json() if self._collections_core else None
        del_keys = {f"user-collections.{cid}" for cid in sel}

        for col_id in sel:
            # CEF 删除
            if cef_ok:
                ok, err = self._cef_bridge.delete_collection(col_id)
                if not ok:
                    errors.append(f"{col_id}: {err}")
            # 本地标记删除
            if data:
                for entry in data:
                    if entry[0] in del_keys and entry[0] == f"user-collections.{col_id}":
                        entry[1]["is_deleted"] = True
                        entry[1].pop("value", None)
                        break
            # 清理绑定来源
            if self._collections_core:
                self._collections_core.remove_collection_source(col_id)

        if data and self._collections_core:
            self._collections_core.save_json(
                data, backup_description=f"删除 {len(sel)} 个分类")

        # 局部更新：只移除节点，不重建 33645 行游戏列表
        needs_game_rebuild = any(
            self._coll_filter_states.get(cid) in ('plus', 'minus')
            for cid in sel)
        for cid in sel:
            self._coll_filter_states.pop(cid, None)
            self._coll_data_cache.pop(cid, None)
            try:
                self._coll_tree.delete(cid)
            except Exception:
                pass
        # 同步 CEF 缓存
        cef_cache = getattr(self, '_cef_collections_cache', None)
        if cef_cache and 'collections' in cef_cache:
            for cid in sel:
                cef_cache['collections'].pop(cid, None)
        if needs_game_rebuild:
            self._lib_populate_tree(force_rebuild=True)

        if errors:
            messagebox.showwarning("部分删除失败",
                f"已删除 {len(sel) - len(errors)} 个，"
                f"失败 {len(errors)} 个：\n"
                + "\n".join(errors[:5]),
                parent=self.root)
        else:
            messagebox.showinfo("删除完成",
                f"已删除 {len(sel)} 个分类。",
                parent=self.root)

    @staticmethod
    def _build_dlc_set(games):
        """从游戏列表中提取 DLC 类型的 appid 集合"""
        dlc_ids = set()
        for g in games:
            at = self._get_app_type(g)
            if at & ETYPE_DLC and not (at & ETYPE_GAME):
                dlc_ids.add(g.get('app_id') or g.get('appid'))
        return dlc_ids

    def _cleanup_dlc_from_collections(self):
        """从所有分类中移除 DLC 类型的 appid"""
        if not self._collections_core:
            return
        games = getattr(self, '_lib_all_games', None) or []
        if not games:
            messagebox.showwarning("提示",
                "请先连接 CEF 加载游戏库，才能识别 DLC。",
                parent=self.root)
            return
        dlc_ids = self._build_dlc_set(games)
        if not dlc_ids:
            messagebox.showinfo("提示", "未发现 DLC 类型的 appid。",
                                parent=self.root)
            return

        data = self._collections_core.load_json()
        if not data:
            return

        total_removed, affected_cols = self._strip_ids_from_data(
            data, dlc_ids, self._collections_core,
            self._cef_bridge if self._cef_bridge
            and self._cef_bridge.is_connected() else None)

        if total_removed == 0:
            messagebox.showinfo("清理完成",
                f"已识别 {len(dlc_ids)} 个 DLC，但分类中未包含任何 DLC。",
                parent=self.root)
            return

        if not messagebox.askyesno("确认清理",
                f"发现 {len(dlc_ids)} 个 DLC appid，"
                f"将从 {affected_cols} 个分类中移除共 {total_removed} 条。\n\n继续？",
                parent=self.root):
            return

        self._save_and_sync(
            data, backup_description=f"清理DLC: 移除{total_removed}条")
        self._ui_refresh()
        messagebox.showinfo("清理完成",
            f"已从 {affected_cols} 个分类中移除 {total_removed} 条 DLC appid。",
            parent=self.root)

    @staticmethod
    def _strip_ids_from_data(data, bad_ids, core, cef_bridge=None):
        """从所有分类的 added 列表中移除指定 appid，返回 (总移除数, 影响分类数)"""
        import time as _time
        total_removed = affected = 0
        for entry in data:
            if not entry[0].startswith("user-collections."):
                continue
            meta = entry[1]
            if meta.get("is_deleted") or "value" not in meta:
                continue
            try:
                val_obj = json.loads(meta['value'])
            except Exception:
                continue
            added = val_obj.get("added", [])
            cleaned = [a for a in added if a not in bad_ids]
            n = len(added) - len(cleaned)
            if n == 0:
                continue
            total_removed += n
            affected += 1
            val_obj['added'] = cleaned
            meta['value'] = json.dumps(
                val_obj, ensure_ascii=False, separators=(',', ':'))
            meta['timestamp'] = int(_time.time())
            meta['version'] = core.next_version(data)
            if cef_bridge:
                col_id = val_obj.get("id", "")
                if col_id:
                    core.queue_cef_upsert(
                        col_id, val_obj.get('name', ''),
                        cleaned, val_obj.get('removed', []))
        return total_removed, affected
