"""
ui_main.py — SteamShelf 主界面

整合了：
- 软件 A (ui_intro.py): Steam 库管理助手 — 账号选择 + CEF 连接
- 软件 B (ui_app.py): Steam 笔记管理器 — 笔记管理功能

使用标签页结构，两个功能共享同一个账号系统。
"""

import json
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import ssl

# ═══════════════════════════════════════════════════════════════════════════════
#  导入依赖模块（统一后）
# ═══════════════════════════════════════════════════════════════════════════════

from account_manager import SteamAccount, SteamAccountScanner
from cef_bridge import CEFBridge

# 导入全局补丁（模块导入时自动执行）
import ui_utils  # noqa: F401
from ui_utils import ProgressWindow, set_window_icon

from core_notes import SteamNotesManager
from ai_generator import SteamAIGenerator
from ui_notes_viewer import NotesViewerMixin
from ui_import_export import ImportExportMixin
from ui_settings import SettingsMixin
from ui_library import LibraryMixin
from ui_cloud import CloudMixin
from ui_collection_ops import CollectionOpsMixin
from ui_curator import CuratorMixin
from ui_recommend import RecommendMixin
from ui_steamdb import SteamDBMixin
from ui_backup import BackupMixin
from ui_ai_inline_gen import InlineAIGenMixin
from ui_ai_search import AISearchMixin
from ui_updater import UpdaterMixin
from ui_sharing import SharingMixin
from ui_intro import SteamToolboxIntro

from steam_data import get_game_name_from_steam, get_app_name_and_type, get_review_summary
from config_manager import ConfigManager
from vdf_parser import parse_remotecache_syncstates as _vdf_parse_syncstates




# ═══════════════════════════════════════════════════════════════════════════════
#  整合后的主界面（标签页结构）
# ═══════════════════════════════════════════════════════════════════════════════

class SteamToolboxMain(
    LibraryMixin, CloudMixin, NotesViewerMixin,
    InlineAIGenMixin, AISearchMixin,
    ImportExportMixin, SettingsMixin,
    CollectionOpsMixin, CuratorMixin, RecommendMixin,
    SteamDBMixin, BackupMixin, UpdaterMixin, SharingMixin
):
    """
    SteamShelf 主界面（标签页版本）

    标签页 1: 🎮 游戏库（统一游戏列表 + 收藏夹 + 笔记筛选）
    标签页 2: 🛠️ 管理操作（笔记操作 + 收藏夹操作预留）
    """

    # API Key 配置文件路径（跨平台）— 已迁移到 ConfigManager
    _CONFIG_DIR = ConfigManager._CONFIG_DIR
    _CONFIG_FILE = ConfigManager._CONFIG_FILE

    def __init__(self, account: 'SteamAccount', intro_callback):
        """
        初始化主界面
        
        Args:
            account: 统一 SteamAccount 对象（兼容 dict 访问）
            intro_callback: 返回入口界面的回调函数
        """
        self.account_a = account         # SteamAccount 对象（供库管理标签页使用）
        self.current_account = account   # 统一对象，兼容 account['key'] 和 account.attr
        self.accounts = [account]        # 兼容软件 B 的多账号列表
        self.intro_callback = intro_callback
        
        self.manager = None  # SteamNotesManager
        self.cloud_uploader = None  # SteamCloudUploader
        self.root = None
        self._games_data = []
        self._game_name_cache = {}  # {app_id: name} — 缓存在线解析的游戏名
        self._game_name_cache_loaded = False
        self._app_type_cache = {}   # {app_id: type_str} — 缓存 Steam Store API 返回的类型
        self._app_detail_cache = {} # {app_id: detail_dict} — 缓存 Steam Store API 详情
        self._cache_lock = threading.Lock()  # 保护缓存持久化（防止多线程同时写盘）
        self._config_mgr = ConfigManager()
        self._config = self._config_mgr.raw  # 向后兼容：Mixin 直接访问 self._config
        # 性能追踪：每次启动清空旧日志
        from ui_utils import _PERF_ENABLED, _PERF_LOG_PATH, perf_log
        if _PERF_ENABLED:
            try:
                open(_PERF_LOG_PATH, 'w').close()
            except OSError:
                pass
            perf_log('APP_INIT start')

        # 收藏夹核心（来自软件 A）
        self._collections_core = None
        self._cef_bridge = None  # CEFBridge 实例（库管理用）

        # 收藏夹修改追踪（来自软件 A 的 dirty 机制）
        self._pending_data = None
        self._has_pending_changes = False
        self._original_col_ids = set()

        # 提前初始化 CollectionsCore（如果 storage_path 可用）
        self._init_collections_core()

        # 初始化笔记管理器
        self._init_notes_manager()

        # 性能追踪：monkey-patch 关键方法
        from ui_utils import perf_install_hooks
        perf_install_hooks(self)

    def _init_notes_manager(self):
        """初始化笔记管理器"""
        fc = self.current_account.get('friend_code', '')
        hashes = self._config_mgr.get_uploaded_hashes(fc)
        self.manager = SteamNotesManager(
            self.current_account['notes_dir'], self.cloud_uploader,
            uploaded_hashes=hashes)

    def _init_collections_core(self):
        """提前初始化 CollectionsCore（如果 storage_path 可用）"""
        try:
            from core_collections import CollectionsCore
        except ImportError:
            return
        storage_path = getattr(self.current_account, 'storage_path', None)
        if not storage_path:
            return
        try:
            self._collections_core = CollectionsCore(
                self.current_account, self._config_mgr)
        except Exception as e:
            print(f"[桥接] CollectionsCore 初始化失败: {e}")

    # ═══════════════════════════════════════════════════════════════════════════════
    #  收藏夹桥接方法（连接 legacy A Mixin 与统一 UI）
    # ═══════════════════════════════════════════════════════════════════════════════

    def _ensure_collections_core(self):
        """确保 CollectionsCore 已初始化，未初始化时弹提示"""
        if self._collections_core is not None:
            return True
        messagebox.showwarning("提示",
            "收藏夹核心未初始化。\n请先在游戏库标签页加载收藏夹数据。",
            parent=self.root)
        return False

    def _save_and_sync(self, data, backup_description=""):
        """保存收藏夹到本地文件 + CEF 云同步（如果可用）"""
        if not self._collections_core:
            return False
        cef_ops = self._collections_core.pop_pending_cef_ops()
        result = self._collections_core.save_json(
            data, backup_description=backup_description)
        if result and self._cef_bridge and self._cef_bridge.is_connected() and cef_ops:
            self._do_cef_sync(cef_ops)
        elif not result:
            # 保存失败，恢复队列
            self._collections_core._pending_cef_ops = (
                cef_ops + self._collections_core._pending_cef_ops)
        return result

    def _get_owned_app_ids_set(self):
        """CEF 获取已拥有 appid 集合，不可用时返回 None"""
        if not self._cef_bridge or not self._cef_bridge.is_connected():
            return None
        try:
            r = self._cef_bridge.get_all_owned_apps(
                games_only=False, timeout=30)
            if "apps" in r:
                return {a["appid"] for a in r["apps"]}
        except Exception:
            pass
        return None

    def _ask_filter_owned(self, app_ids, *, parent=None):
        """询问是否只保留已入库游戏。None=取消，CEF不可用→原样返回。"""
        owned = self._get_owned_app_ids_set()
        if owned is None:
            return app_ids
        kept = [a for a in app_ids if a in owned]
        removed = len(app_ids) - len(kept)
        if removed == 0:
            return app_ids
        r = messagebox.askyesnocancel(
            "筛选已入库游戏",
            f"共 {len(app_ids)} 个游戏，{len(kept)} 个已入库，"
            f"{removed} 个未入库。\n\n"
            "是否只保留已入库的游戏？\n\n"
            "是 → 只保留已入库\n否 → 保留全部\n取消 → 取消创建",
            parent=parent or self.root)
        if r is None:
            return None
        return kept if r else app_ids

    def _do_cef_sync(self, cef_ops):
        """显示进度窗口并在后台线程执行 CEF 云同步"""
        if not self.root or not cef_ops:
            return

        pw = ProgressWindow(self.root, "☁️ 正在同步到 Steam 云端...",
            "正在将收藏夹同步到 Steam 云端...",
            maximum=len(cef_ops), grab=True, detail=True)
        pw.status_var.set(f"准备同步 {len(cef_ops)} 个收藏夹...")
        self._center_window(pw.win)

        def progress_cb(current, total, name, status_text):
            pw.update(value=current, status=status_text,
                      detail=f"当前: {name[:50]}" if name else None)

        def do_sync():
            bridge = self._cef_bridge
            if not bridge or not bridge.is_connected():
                self.root.after(0, pw.close)
                return
            success, fail, errors = bridge.batch_sync_collections(
                cef_ops, progress_callback=progress_cb)

            def finish():
                pw.close()
                # CEF 同步完成，清缓存并从 CEF 获取最新数据（含准确计数）
                self._cef_collections_cache = None
                self._lib_load_collections(skip_expression_update=True)
                if fail > 0:
                    err_text = "\n".join(errors[:10])
                    messagebox.showwarning("云同步部分失败",
                        f"成功: {success}/{success + fail}\n"
                        f"失败: {fail}\n\n{err_text}")
            self.root.after(0, finish)

        threading.Thread(target=do_sync, daemon=True).start()

    def _ui_mark_dirty(self, data):
        """标记有未保存的收藏夹更改"""
        self._pending_data = data
        self._has_pending_changes = True
        if hasattr(self, '_coll_save_btn'):
            self._coll_save_btn.config(state="normal")
        if hasattr(self, '_coll_save_indicator'):
            self._coll_save_indicator.config(
                text="⚠️ 有未保存的更改", fg="orange")

    def _ui_refresh(self):
        """刷新收藏夹列表，按需刷新游戏列表

        用本地数据渲染分类树（CEF 同步完成后由 _do_cef_sync finish 补刷准确计数）。
        仅在有活跃分类筛选时才重建游戏列表，否则跳过（省 ~500ms）。
        """
        from ui_utils import perf_log
        perf_log('_ui_refresh', extra='START')
        self._cef_collections_cache = None
        self._lib_load_collections(force_local=True)
        if any(v in ('plus', 'minus') for v in self._coll_filter_states.values()):
            self._apply_coll_filters()
        perf_log('_ui_refresh', extra='DONE')

    def _ui_get_selected(self):
        """获取左侧收藏夹树中选中的收藏夹（返回 legacy 格式 list[dict]）"""
        if not hasattr(self, '_coll_tree'):
            return []
        sel = self._coll_tree.selection()
        if not sel or not hasattr(self, '_coll_data_cache'):
            return []
        result = []
        for col_id in sel:
            coll_data = self._coll_data_cache.get(col_id)
            if not coll_data:
                continue
            result.append({
                'id': col_id,
                'name': coll_data['name'],
                'is_dynamic': coll_data['is_dynamic'],
                'added': [int(a) for a in coll_data['owned_app_ids']],
                'app_ids': [int(a) for a in coll_data['owned_app_ids']],
            })
        return result

    def _commit_collection_save(self):
        """储存收藏夹更改：备份 + 写入 + CEF 同步"""
        from ui_utils import perf_log
        perf_log('USER_ACTION', extra='commit_collection_save')
        if not self._has_pending_changes or self._pending_data is None:
            messagebox.showinfo("提示", "没有需要保存的更改。",
                                parent=self.root)
            return
        result = self._save_and_sync(
            self._pending_data, backup_description="储存收藏夹更改")
        if result:
            self._has_pending_changes = False
            self._pending_data = None
            self._original_col_ids.clear()
            if hasattr(self, '_coll_save_btn'):
                self._coll_save_btn.config(state="disabled")
            if hasattr(self, '_coll_save_indicator'):
                self._coll_save_indicator.config(
                    text="✅ 所有更改已保存", fg="green")
            self._ui_refresh()
    # ═══════════════════════════════════════════════════════════════════════════════
    #  配置管理方法（来自软件 B）
    # ═══════════════════════════════════════════════════════════════════════════════

    def _save_config(self, config: dict = None):
        """向后兼容：保存设置到配置文件（委托给 ConfigManager）"""
        self._config_mgr.save()

    def _get_ai_tokens(self) -> list:
        """获取已保存的 AI 令牌列表（含向后兼容）"""
        return self._config_mgr.get_ai_tokens(SteamAIGenerator.PROVIDERS)

    def _save_ai_tokens(self, tokens: list, active_index: int = 0):
        """保存 AI 令牌列表到配置文件"""
        self._config_mgr.save_ai_tokens(tokens, active_index)

    def _get_active_token_index(self) -> int:
        return self._config_mgr.get_active_token_index()

    def _save_uploaded_hashes(self):
        """持久化当前账号的上传哈希到配置文件"""
        if not self.current_account or not self.manager:
            return
        fc = self.current_account.get('friend_code', '')
        self._config_mgr.save_uploaded_hashes(fc, self.manager.get_uploaded_hashes())

    # ═══════════════════════════════════════════════════════════════════════════════
    #  主界面
    # ═══════════════════════════════════════════════════════════════════════════════

    def show_main_window(self):
        """显示主界面（标签页结构）"""
        self.root = tk.Tk()
        self.root.withdraw()  # 隐藏窗口，构建完成后再显示（防止闪烁）
        self.root.title("SteamShelf")
        set_window_icon(self.root)
        self.root.minsize(900, 600)
        root = self.root

        # ── 顶部: 统一状态栏 ──
        acc_frame = tk.Frame(root, bg="#4a90d9", pady=6)
        acc_frame.pack(fill=tk.X)

        # 账号信息 + Steam 状态
        import os as _os
        _logo_bar = _os.path.join(_os.path.dirname(__file__), "logo_24.png")
        if _os.path.exists(_logo_bar):
            self._bar_logo_img = tk.PhotoImage(file=_logo_bar)
            _logo_lbl = tk.Label(acc_frame, image=self._bar_logo_img,
                                 bg="#4a90d9", cursor="hand2")
            _logo_lbl.pack(side=tk.LEFT, padx=(8, 2))
            _logo_lbl.bind("<Button-1>", lambda e: self._ui_show_about())
        steam_info = CEFBridge.detect_steam_process()
        steam_tag = "🟢 运行中" if steam_info['running'] else "⚫ 未运行"
        acc_info = (f"👤 {self.current_account['persona_name']}  |  "
                    f"ID: {self.current_account['friend_code']}  |  "
                    f"Steam {steam_tag}")
        self._acc_label = tk.Label(
            acc_frame, text=acc_info, font=("", 9, "bold"),
            bg="#4a90d9", fg="white")
        self._acc_label.pack(side=tk.LEFT, padx=(10, 6))

        # CEF / Cloud 状态标签 + 设置 / 切换账号
        _cef_init_text = ("CEF: 🟢已连接" if self._cef_bridge is not None
                          else "CEF: 未连接")
        _cef_init_fg = "white" if self._cef_bridge is not None else "#aac8ee"
        self._cef_status_label = tk.Label(
            acc_frame, text=_cef_init_text,
            font=("", 8), bg="#4a90d9", fg=_cef_init_fg)
        self._cef_status_label.pack(side=tk.LEFT, padx=(2, 6))

        # 代理状态指示（动态更新）
        self._proxy_status_label = tk.Label(
            acc_frame, text="", font=("", 8),
            bg="#4a90d9", fg="#aac8ee")
        self._proxy_status_label.pack(side=tk.LEFT, padx=(2, 6))
        self._update_proxy_status()

        # AI 模型指示（动态更新）
        self._ai_model_label = tk.Label(
            acc_frame, text="", font=("", 8),
            bg="#4a90d9", fg="#aac8ee")
        self._ai_model_label.pack(side=tk.LEFT, padx=(2, 6))
        self._update_ai_model_label()

        # Cloud 上传状态（非阻塞进度显示，在 AI 模型右边）
        self._cloud_upload_label = tk.Label(
            acc_frame, text="", font=("", 8, "bold"),
            bg="#4a90d9", fg="#aac8ee")
        self._cloud_upload_label.pack(side=tk.LEFT, padx=(2, 6))

        def switch_account():
            root.destroy()
            self.intro_callback()
        ttk.Button(acc_frame, text="🔄 切换账号", width=8,
                   command=switch_account).pack(side=tk.RIGHT, padx=(2, 6))
        ttk.Button(acc_frame, text="⚙️ 设置", width=7,
                   command=self._open_unified_settings).pack(side=tk.RIGHT, padx=2)

        # 更新提示标签（初始隐藏，有更新时显示）
        self._update_label = tk.Label(
            acc_frame, text="", font=("", 8, "bold"),
            bg="#4a90d9", fg="#ffeb3b")

        # ── 全局：预加载游戏名称缓存（两个标签页共享） ──
        # 先同步加载持久化缓存（瞬时完成），确保两个标签页都能立刻使用
        self._ensure_game_name_cache_fast()

        # ── 主内容区（状态栏已集成到 body grid 中，保证分隔线对齐） ──
        library_frame = tk.Frame(root)
        library_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._build_library_tab(library_frame)

        # 启动 Steam 进程监控定时器
        self._steam_monitor_id = None
        self._start_steam_monitor()

        # 自动尝试连接 CEF（如果可用）
        if self._cef_bridge is not None and self._cef_bridge.is_connected():
            # bridge 已从 intro 传入，立即使用
            self.root.after(0, self._apply_cef_bridge)
        else:
            self.root.after(500, self._auto_connect_cef)

        # 自动更新：清理残留 + 后台检查
        import updater
        updater.cleanup_update()
        self.root.after(2000, self._check_update_bg)

        # 窗口关闭时检查未保存的收藏夹更改 + 未上传笔记
        def _on_close():
            # 1. 检查未保存的收藏夹更改
            if self._has_pending_changes:
                ans = messagebox.askyesnocancel(
                    "未保存的更改",
                    "您有未保存的收藏夹更改。\n\n是否在退出前保存？",
                    parent=root)
                if ans is None:
                    return
                if ans:
                    self._commit_collection_save()

            # 2. 检查未上传到 Steam Cloud 的笔记
            dirty_n = self.manager.dirty_count() if self.manager else 0
            if dirty_n > 0:
                ans = messagebox.askyesnocancel(
                    "☁️ 未上传的笔记",
                    f"有 {dirty_n} 个游戏的笔记尚未上传到 Steam Cloud。\n\n"
                    "「是」→ 上传后关闭\n"
                    "「否」→ 直接关闭（本地文件已保存）\n"
                    "「取消」→ 返回",
                    parent=root)
                if ans is None:
                    return
                if ans:
                    # 事务性上传：连接→上传→断开→关闭
                    self._upload_and_close()
                    return

            # 确定关闭 → 停止后台线程 + 持久化缓存
            self._resolve_thread_running = False
            if getattr(self, '_app_detail_cache', None):
                self._persist_all_caches()
            root.destroy()
        root.protocol("WM_DELETE_WINDOW", _on_close)

        self._center_window(root, width=1000, height=700)
        root.deiconify()  # 构建完成，显示窗口
        root.mainloop()

    # ═══════════════════════════════════════════════════════════════════════════════
    #  标签页 2: 笔记管理（来自软件 B）
    # ═══════════════════════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════════════════════
    #  以下是从软件 B 移植的方法（笔记管理相关）
    # ═══════════════════════════════════════════════════════════════════════════════

    # ────────────────────── Steam 进程监控 ──────────────────────

    def _ensure_game_name_cache(self, force=False, progress_callback=None):
        """确保游戏名称缓存已加载 — 持久化 + 全量列表 + 本地扫描 + 后台补全"""
        if self._game_name_cache_loaded and not force:
            return
        # 1. 从配置文件加载已持久化的名称缓存
        persisted = self._config.get("game_name_cache", {})
        self._game_name_cache.clear()
        self._game_name_cache.update(persisted)
        # 2. 尝试从 ISteamApps/GetAppList/v2/ 获取全量名称列表（无需 API Key）
        bulk_cache_ts = self._config.get("game_name_bulk_cache_ts", 0)
        now = time.time()
        # 每 24 小时更新一次全量列表
        if now - bulk_cache_ts > 86400 or not persisted:
            try:
                est_total = len(persisted) if persisted else 0
                bulk_names = SteamAccountScanner.fetch_all_steam_app_names(
                    api_key=self._config.get("steam_web_api_key", ""),
                    progress_callback=progress_callback,
                    estimated_total=est_total)
                if bulk_names:
                    self._game_name_cache.update(bulk_names)
                    self._config["game_name_bulk_cache_ts"] = now
                    print(f"[游戏名称] 全量列表已更新: {len(bulk_names)} 条")
            except Exception as e:
                print(f"[游戏名称] 全量列表获取失败: {e}")
        # 3. 本地扫描（已安装游戏，可能有更准确的本地化名称）
        try:
            library_games = SteamAccountScanner.scan_library(
                self.current_account['steam_path'])
            for g in library_games:
                self._game_name_cache[g['app_id']] = g['name']
        except Exception:
            pass
        # 4. 持久化合并后的缓存
        self._persist_name_cache()
        self._game_name_cache_loaded = True

    def _update_ai_model_label(self):
        """刷新顶部栏 AI 模型指示"""
        tokens = self._get_ai_tokens()
        idx = self._get_active_token_index()
        if tokens and 0 <= idx < len(tokens):
            t = tokens[idx]
            self._ai_model_label.config(
                text=f"🤖 {t.get('model', '?')}", fg="white")
        else:
            self._ai_model_label.config(text="🤖 未配置", fg="#aac8ee")

    def _update_proxy_status(self):
        """刷新顶部栏代理状态指示（结果缓存到 self._has_proxy / _proxy_country）"""
        import urllib.request as _ur
        proxies = _ur.getproxies()
        proxy_url = proxies.get('https') or proxies.get('http') or ''
        self._has_proxy = bool(proxy_url)

        if not proxy_url:
            self._proxy_country = ""
            self._proxy_url_cache = ""
            self._set_proxy_label("🌐 直连")
            self._sync_curator_proxy()
            return

        # 代理 URL 未变 → 用缓存
        if proxy_url == getattr(self, '_proxy_url_cache', ''):
            country = getattr(self, '_proxy_country', '')
            self._set_proxy_label(f"🌐 代理: {country}" if country else "🌐 代理: ✅")
            return

        # 代理 URL 变了 → 后台检测国家
        self._proxy_url_cache = proxy_url
        self._set_proxy_label("🌐 代理: …")
        import threading
        threading.Thread(target=self._detect_proxy_country, daemon=True).start()

    def _detect_proxy_country(self):
        """后台检测代理出口 IP 国家"""
        try:
            import urllib.request, json
            resp = urllib.request.urlopen(
                "http://ip-api.com/json/?fields=countryCode", timeout=5)
            data = json.loads(resp.read().decode())
            cc = data.get('countryCode', '')
            if cc:
                # 国旗 emoji: 区域指示符 A=🇦(U+1F1E6)
                flag = chr(0x1F1E6 + ord(cc[0]) - ord('A')) + \
                       chr(0x1F1E6 + ord(cc[1]) - ord('A'))
                self._proxy_country = f"{flag} {cc}"
            else:
                self._proxy_country = "✅"
        except Exception:
            self._proxy_country = "✅"
        self.root.after(0, lambda: (
            self._set_proxy_label(f"🌐 代理: {self._proxy_country}"),
            self._sync_curator_proxy()))

    def _set_proxy_label(self, text):
        try:
            self._proxy_status_label.config(text=text)
        except Exception:
            pass

    def _sync_curator_proxy(self):
        refresh = getattr(self, '_curator_refresh_cap', None)
        if refresh:
            try:
                refresh()
            except Exception:
                self._curator_refresh_cap = None

    def _ensure_game_name_cache_fast(self):
        """仅从持久化缓存快速加载游戏名称（不做任何网络请求）"""
        if self._game_name_cache_loaded or self._game_name_cache:
            return
        self._config_mgr.load_caches()  # 延迟加载缓存文件
        self._game_name_cache.clear()
        self._game_name_cache.update(self._config.get("game_name_cache", {}))
        self._app_type_cache.clear()
        self._app_type_cache.update(self._config.get("app_type_cache", {}))
        self._app_detail_cache.clear()
        self._app_detail_cache.update(self._config.get("app_detail_cache", {}))
        try:
            library_games = SteamAccountScanner.scan_library(
                self.current_account['steam_path'])
            for g in library_games:
                self._game_name_cache[g['app_id']] = g['name']
        except Exception:
            pass

    def _bg_init_game_names(self):
        """后台线程：完整加载游戏名称缓存（含网络请求），完成后刷新所有列表"""
        def _on_progress(fetched, page, is_done, estimated_total=0):
            try:
                self.root.after(0, lambda: self._update_name_progress(
                    fetched, page, is_done, estimated_total))
            except Exception:
                pass
        try:
            old_size = len(self._game_name_cache)
            self._ensure_game_name_cache(force=False, progress_callback=_on_progress)
            names_changed = len(self._game_name_cache) != old_size
            try:
                self.root.after(0, lambda: self._hide_name_progress())
                # 仅在名称缓存实际更新时才刷新 UI（避免冗余 rebuild）
                if names_changed:
                    self.root.after(0, lambda: self._lib_load_collections(
                        skip_expression_update=True))
                    self.root.after(0, self._lib_schedule_tree_rebuild)
            except Exception:
                pass
            self._bg_resolve_missing_names()
        except Exception as e:
            print(f"[后台] 游戏名称初始化失败: {e}")
            try:
                self.root.after(0, lambda: self._hide_name_progress())
            except Exception:
                pass

    def _update_name_progress(self, fetched, page, is_done, estimated_total=0):
        """更新游戏名称获取进度条"""
        try:
            if is_done:
                self._name_progress_label.config(
                    text=f"✅ 已获取 {fetched} 个游戏名称（已缓存到本地）")
                self._name_progress_bar.stop()
                self._name_progress_bar.config(mode='determinate', value=100)
            else:
                if estimated_total > 0:
                    pct = min(int(fetched / estimated_total * 100), 99)
                    self._name_progress_label.config(
                        text=f"📥 正在获取游戏名称... {fetched} / ~{estimated_total}（第 {page} 页）")
                    self._name_progress_bar.stop()
                    self._name_progress_bar.config(mode='determinate', value=pct)
                else:
                    self._name_progress_label.config(
                        text=f"📥 正在获取游戏名称... 已获取 {fetched} 个（第 {page} 页）")
            self._name_progress_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0))
        except Exception:
            pass

    def _hide_name_progress(self):
        """隐藏游戏名称获取进度条"""
        try:
            self._name_progress_frame.pack_forget()
        except Exception:
            pass

    def _persist_name_cache(self):
        """将游戏名称缓存持久化到配置文件"""
        with self._cache_lock:
            self._config_mgr.save_name_cache(dict(self._game_name_cache))

    def _persist_type_cache(self):
        """将游戏类型缓存持久化到配置文件"""
        self._config_mgr.save_type_cache(dict(self._app_type_cache))

    def _persist_detail_cache(self):
        """将游戏详情缓存持久化到配置文件"""
        self._config_mgr.save_detail_cache(dict(self._app_detail_cache))

    def _bg_resolve_missing_names(self):
        """后台线程：解析仍显示为 AppID 的游戏名称"""
        games = self.manager.list_all_games()
        missing = [g['app_id'] for g in games
                   if g['app_id'] not in self._game_name_cache]
        if not missing:
            return
        resolved_any = False
        bulk_names = SteamAccountScanner.fetch_all_steam_app_names(
            api_key=self._config.get("steam_web_api_key", ""))
        if bulk_names:
            for aid in missing:
                if aid in bulk_names:
                    self._game_name_cache[aid] = bulk_names[aid]
                    resolved_any = True
            missing = [aid for aid in missing
                       if aid not in self._game_name_cache]
        for aid in missing:
            try:
                name = get_game_name_from_steam(aid)
                if name and not name.startswith("AppID "):
                    self._game_name_cache[aid] = name
                    resolved_any = True
                time.sleep(0.3)
            except Exception:
                pass
        if resolved_any:
            self._persist_name_cache()
            try:
                self.root.after(0, lambda: self._refresh_games_list())
            except Exception:
                pass

    def _get_game_name(self, app_id: str) -> str:
        """获取游戏名称，优先缓存，否则返回 AppID"""
        return self._game_name_cache.get(app_id, f"AppID {app_id}")

    def _bg_resolve_visible_names(self):
        """已被 _bg_resolve_all_unowned_types 取代，保留为空壳避免调用报错"""
        pass

    def _bg_resolve_all_unowned_types(self):
        """后台静默获取所有未入库 app 的 name+type+detail，可断点续传"""
        if getattr(self, '_resolve_thread_running', False):
            return
        cache = getattr(self, '_coll_data_cache', {})
        if not cache:
            return
        all_unowned = set()
        for data in cache.values():
            all_unowned.update(data.get('not_owned_app_ids', []))
        # 断点续传：只跳过已有详情缓存的（最完整的判据）
        need = [aid for aid in all_unowned
                if aid not in self._app_detail_cache]
        if not need:
            print(f"[库管理] 未入库详情: 全部已缓存 ({len(all_unowned)} 个)")
            return
        print(f"[库管理] 未入库详情: 需获取 {len(need)}/{len(all_unowned)} 个 (5线程并发)")
        self._resolve_thread_running = True
        self._resolve_progress = (0, len(need))
        threading.Thread(target=self._resolve_worker,
                         args=(need,), daemon=True).start()

    def _resolve_worker(self, need):
        """后台 worker：5 线程并发获取游戏详情"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(need)
        done = persist = 0
        WORKERS = 5
        CHUNK = WORKERS * 4

        def fetch_one(aid):
            if not self._resolve_thread_running:
                return None
            for attempt in range(3):
                if not self._resolve_thread_running:
                    return None
                name, type_str, detail = get_app_name_and_type(aid)
                if detail == "rate_limited":
                    time.sleep(min(3.0 * (2 ** attempt), 15.0))
                    continue
                if detail is not None:
                    # 追加评测摘要（轻量 API，不影响主流程）
                    try:
                        rv = get_review_summary(aid)
                        if isinstance(rv, dict):
                            detail.update(rv)
                    except Exception:
                        pass
                    return (aid, name, type_str, detail)
                time.sleep(min(1.0 * (2 ** attempt), 5.0))
            return None

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for start in range(0, total, CHUNK):
                if not self._resolve_thread_running:
                    break
                chunk = need[start:start + CHUNK]
                futs = [pool.submit(fetch_one, aid) for aid in chunk]
                for f in as_completed(futs):
                    done += 1
                    self._resolve_progress = (done, total)
                    try:
                        result = f.result()
                    except Exception:
                        continue
                    if result is None:
                        continue
                    aid, name, type_str, detail = result
                    if name and not name.startswith("AppID "):
                        self._game_name_cache[aid] = name
                    self._app_type_cache[aid] = type_str or ""
                    self._app_detail_cache[aid] = detail or {"_removed": True}
                    persist += 1
                    if persist % 200 == 0:
                        self._persist_all_caches()
        self._persist_all_caches()
        print(f"[库管理] 后台详情获取完成: {done}/{total}")
        self._resolve_thread_running = False
        try:
            self.root.after(0, self._lib_schedule_tree_rebuild)
        except Exception:
            pass
        # 接力：补查已入库游戏的发行日期
        self.root.after(500, self._bg_resolve_owned_release_dates)

    def _bg_resolve_owned_release_dates(self):
        """后台补查已入库游戏中 rt_release=0 的发行日期"""
        if getattr(self, '_resolve_thread_running', False):
            return
        games = getattr(self, '_lib_all_games', [])
        need = [str(g['app_id']) for g in games
                if not g.get('rt_release')
                and str(g['app_id']) not in self._app_detail_cache]
        if not need:
            return
        print(f"[库管理] 后台补查 {len(need)} 个已入库游戏的发行日期")
        self._resolve_thread_running = True
        self._resolve_progress = (0, len(need))
        threading.Thread(target=self._resolve_worker,
                         args=(need,), daemon=True).start()

    def _persist_all_caches(self):
        """一次性持久化所有游戏缓存（单次写盘）"""
        with self._cache_lock:
            self._config_mgr.raw["game_name_cache"] = dict(self._game_name_cache)
            self._config_mgr.raw["app_type_cache"] = dict(self._app_type_cache)
            self._config_mgr.raw["app_detail_cache"] = dict(self._app_detail_cache)
            self._config_mgr.save_caches()

    def _parse_remotecache_syncstates(self) -> dict:
        """解析 remotecache.vdf 获取每个笔记文件的 syncstate（mtime 缓存）"""
        if not self.current_account:
            return {}
        notes_dir = self.current_account.get('notes_dir', '')
        vdf_path = os.path.join(os.path.dirname(notes_dir), 'remotecache.vdf')
        try:
            mtime = os.path.getmtime(vdf_path)
        except OSError:
            return {}
        cache = getattr(self, '_vdf_cache', None)
        if cache and cache[0] == mtime:
            return cache[1]
        result = _vdf_parse_syncstates(notes_dir)
        self._vdf_cache = (mtime, result)
        return result

    def is_app_uploading(self, app_id: str) -> bool:
        """判断指定 app_id 的笔记是否正在上传中"""
        syncstates = self._parse_remotecache_syncstates()
        return syncstates.get(app_id) == 3

    def _refresh_games_list(self, force_cache=False):
        """刷新游戏列表 — 数据变化时使用，会失效 L4 缓存触发全量重建"""
        if force_cache:
            self._ensure_game_name_cache(force=True)
        elif not self._game_name_cache_loaded:
            self._ensure_game_name_cache_fast()
        self._tree_rebuild_cache = None
        if getattr(self, '_viewing_collections', False):
            self._apply_coll_filters()
        else:
            self._lib_populate_tree()

    def _force_refresh_games_list(self):
        """刷新按钮：强制重建游戏名称缓存"""
        self._game_name_cache_loaded = False
        self._name_progress_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0))
        self._name_progress_bar.config(mode='indeterminate')
        self._name_progress_bar.start(15)
        self._name_progress_label.config(text="📥 正在刷新游戏名称...")
        self._refresh_games_list()

        def _on_progress(fetched, page, is_done, estimated_total=0):
            try:
                self.root.after(0, lambda: self._update_name_progress(
                    fetched, page, is_done, estimated_total))
            except Exception:
                pass

        def _bg():
            try:
                self._ensure_game_name_cache(force=True, progress_callback=_on_progress)
                try:
                    self.root.after(0, lambda: self._hide_name_progress())
                    self.root.after(0, lambda: self._refresh_games_list())
                except Exception:
                    pass
                self._bg_resolve_missing_names()
            except Exception as e:
                print(f"[后台] 强制刷新游戏名称失败: {e}")
                try:
                    self.root.after(0, lambda: self._hide_name_progress())
                except Exception:
                    pass

        threading.Thread(target=_bg, daemon=True).start()

    def _on_main_search_changed(self):
        """主界面搜索框内容或模式变化时刷新列表"""
        if hasattr(self, '_search_debounce_id') and self._search_debounce_id:
            self.root.after_cancel(self._search_debounce_id)
        delay = 300 if (hasattr(self, '_main_search_mode')
                        and self._main_search_mode.get() == "content") else 100
        self._search_debounce_id = self.root.after(delay, self._lib_populate_tree)

    def _on_filter_changed(self):
        """AI 筛选器变更时，重置所有子筛选器并更新可见性"""
        self._source_filter_var.set("来源")
        self._vol_filter_var.set("信息量")
        self._conf_filter_var.set("确信度")
        self._qual_filter_var.set("质量")
        self._update_sub_filter_visibility()
        self._lib_populate_tree()

    def _on_vol_filter_changed(self):
        """信息量筛选器变更时，控制质量筛选器可见性（信息过少时隐藏质量）"""
        self._update_sub_filter_visibility()
        self._lib_populate_tree()

    @staticmethod
    def _iid_to_app_id(iid: str) -> str:
        """从 Treeview iid 提取 app_id（兼容 'aid::n::nid' 和 'aid::lazy'）"""
        return iid.split("::")[0] if "::" in iid else iid

    def _on_tree_double_click(self):
        """Treeview 双击 → 查看笔记"""
        sel = self._games_tree.selection()
        if sel:
            self._open_notes_viewer(self._iid_to_app_id(sel[0]))

    def _open_ai_notes_preview(self, app_id):
        """双击 AI信息 列时弹出 AI 笔记预览窗口（支持上下键切换游戏）"""
        # 如果预览窗口已存在，直接刷新内容
        if hasattr(self, '_ai_preview_win') and self._ai_preview_win:
            try:
                self._ai_preview_win.winfo_exists()
                self._fill_ai_preview_content(app_id)
                return
            except tk.TclError:
                self._ai_preview_win = None

        data = self.manager.read_notes(app_id)
        if not data.get("notes"):
            messagebox.showinfo("无笔记",
                f"{self._get_game_name(app_id)} (AppID {app_id}) 暂无笔记。",
                parent=self.root)
            return

        preview = tk.Toplevel(self.root)
        preview.title(f"🤖 AI 笔记预览")
        preview.transient(self.root)
        preview.grab_set()

        self._ai_preview_win = preview

        def _close():
            preview.grab_release()
            preview.destroy()
            self._ai_preview_win = None

        hdr = tk.Frame(preview, padx=10, pady=5)
        hdr.pack(fill=tk.X)
        self._ai_preview_lbl_name = tk.Label(hdr, text="",
                 font=("微软雅黑", 12, "bold"))
        self._ai_preview_lbl_name.pack(side=tk.LEFT)
        self._ai_preview_lbl_id = tk.Label(hdr, text="商店页面",
                 font=("微软雅黑", 9, "underline"), fg="#1a73e8", cursor="hand2")
        self._ai_preview_lbl_id.pack(side=tk.RIGHT)
        self._ai_preview_lbl_id.bind("<Button-1>", lambda e: __import__('webbrowser').open(
            f"https://store.steampowered.com/app/{self._ai_preview_aid}"))

        btn_f = tk.Frame(preview, padx=10, pady=8)
        btn_f.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btn_f, text="关闭", command=_close).pack(side=tk.RIGHT)
        preview.protocol("WM_DELETE_WINDOW", _close)

        txt_frame = tk.Frame(preview)
        txt_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))
        self._ai_preview_txt = tk.Text(txt_frame, font=("微软雅黑", 10),
                                        wrap=tk.CHAR, padx=10, pady=10)
        scrollbar = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL,
                                   command=self._ai_preview_txt.yview)
        self._ai_preview_txt.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._ai_preview_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        preview.bind("<Up>", lambda e: self._navigate_ai_preview(-1))
        preview.bind("<Down>", lambda e: self._navigate_ai_preview(1))

        preview.geometry("700x450")
        self._center_window(preview)
        self._fill_ai_preview_content(app_id)

    def _navigate_ai_preview(self, delta):
        """上下键在当前筛选列表中切换游戏并刷新预览"""
        tree = self._lib_tree
        all_items = tree.get_children("")  # 顶层游戏行
        if not all_items:
            return
        cur_aid = getattr(self, '_ai_preview_aid', None)
        try:
            idx = list(all_items).index(cur_aid)
        except ValueError:
            idx = 0
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(all_items):
            return
        new_aid = all_items[new_idx]
        tree.selection_set(new_aid)
        tree.see(new_aid)
        self._fill_ai_preview_content(new_aid)

    def _fill_ai_preview_content(self, app_id):
        """填充/刷新 AI 预览窗口的内容"""
        import re as _re
        from core_notes import (
            is_ai_note, extract_ai_confidence_from_note,
            extract_ai_info_volume_from_note, extract_ai_info_source_from_note,
            extract_ai_quality_from_note, is_insufficient_info_note,
            CONFIDENCE_EMOJI, QUALITY_EMOJI, INFO_VOLUME_EMOJI,
        )
        self._ai_preview_aid = app_id
        game_name = self._get_game_name(app_id)
        self._ai_preview_lbl_name.config(text=f"🎮 {game_name}")
        self._ai_preview_win.title(f"🤖 AI 笔记预览 — {game_name}")

        txt = self._ai_preview_txt
        txt.config(state=tk.NORMAL)
        txt.delete("1.0", tk.END)

        data = self.manager.read_notes(app_id)
        notes_list = data.get("notes", [])
        if not notes_list:
            txt.insert(tk.END, "暂无笔记。")
            txt.config(state=tk.DISABLED)
            return

        ai_notes = [n for n in notes_list if is_ai_note(n)]
        display_notes = ai_notes if ai_notes else notes_list

        def _strip_ai_prefix(content):
            m = _re.match(
                r'🤖AI:\s*(?:⛔信息过少\s*)?'
                r'(?:(?:📡联网检索|📚训练数据与Steam评测)\s*\|\s*)?'
                r'(?:相关信息量[：:]\s*(?:相当多|较多|中等|较少|相当少)[🟢🔵🟡🟠🔴]?\s*(?:\|\s*)?)?'
                r'(?:游戏总体质量[：:]\s*(?:相当好|较好|中等|较差|相当差)[💎✨➖👎💀]?\s*)?'
                r'(?:⚠️\s*)?'
                r'(?:以下内容由.+?确信程度[：:]\s*(?:很高|较高|中等|较低|很低)[🟢🔵🟡🟠🔴]?[。.]\s*)?',
                content)
            if m and m.end() > 0:
                return content[m.end():]
            return content

        for i, note in enumerate(display_notes):
            if i > 0:
                txt.insert(tk.END, "\n" + "─" * 36 + "\n\n")
            content = note.get("content", note.get("title", ""))
            note_is_ai = is_ai_note(note)
            tag_prefix = f"note_{i}"

            if note_is_ai:
                is_insuf = is_insufficient_info_note(note)
                conf = extract_ai_confidence_from_note(note)
                vol = extract_ai_info_volume_from_note(note)
                src = extract_ai_info_source_from_note(note)
                emoji = CONFIDENCE_EMOJI.get(conf, "🤖")
                if is_insuf:
                    txt.insert(tk.END, "⛔ 信息过少", f"{tag_prefix}_header")
                else:
                    txt.insert(tk.END, f"{emoji} AI 笔记", f"{tag_prefix}_header")
                if conf:
                    txt.insert(tk.END, f"（确信度: {conf}）", f"{tag_prefix}_header")
                meta_parts = []
                if src == "web":
                    meta_parts.append("📡联网")
                elif src == "local":
                    meta_parts.append("📚本地")
                if vol:
                    vol_emoji = INFO_VOLUME_EMOJI.get(vol, "")
                    meta_parts.append(f"信息量:{vol}{vol_emoji}")
                qual = extract_ai_quality_from_note(note)
                if qual:
                    q_emoji = QUALITY_EMOJI.get(qual, "")
                    meta_parts.append(f"质量:{qual}{q_emoji}")
                if meta_parts:
                    txt.insert(tk.END, f" [{' | '.join(meta_parts)}]",
                               f"{tag_prefix}_meta")
                    txt.tag_config(f"{tag_prefix}_meta",
                                   foreground="#888", font=("微软雅黑", 9))
                txt.insert(tk.END, "\n")
                txt.tag_config(f"{tag_prefix}_header",
                               foreground="#cc3333" if is_insuf else "#1a73e8",
                               font=("微软雅黑", 10, "bold"))
            else:
                txt.insert(tk.END, "📝 手动笔记\n", f"{tag_prefix}_header")
                txt.tag_config(f"{tag_prefix}_header",
                               foreground="#333", font=("微软雅黑", 10, "bold"))

            display_content = _re.sub(
                r'\[/?[a-z0-9*]+(?:=[^\]]*)?\]', '', content).strip()
            if note_is_ai:
                display_content = _strip_ai_prefix(display_content)
                display_content = _re.sub(r'\s*(⚔️|⚠️|📅|📌)', '\n\n\u3000\u3000\\1', display_content)
                display_content = '\u3000\u3000' + display_content.strip()
            txt.insert(tk.END, display_content + "\n")

        txt.config(state=tk.DISABLED)

    def _on_tree_right_click(self, event):
        """右键弹出菜单"""
        region = self._games_tree.identify_region(event.x, event.y)
        if region == "heading":
            self._show_column_visibility_menu(event)
            return
        iid = self._games_tree.identify_row(event.y)
        if not iid:
            # 空白区域右键：只显示展开/收起
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="📂 展开全部笔记", command=self._expand_all_notes)
            menu.add_command(label="📁 收起全部笔记", command=self._collapse_all_notes)
            menu.add_separator()
            menu.add_command(label="🔄 刷新库列表", command=self._lib_refresh)
            if any(v in ('plus', 'minus') for v in self._coll_filter_states.values()):
                menu.add_command(label="↩️ 还原库列表", command=self._lib_reset_coll_filters)
            self._smart_popup(menu, event.x_root, event.y_root)
            return
        current_sel = self._games_tree.selection()
        if iid not in current_sel:
            self._games_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        sel = self._games_tree.selection()
        # 提取去重后的 app_id 列表
        app_ids = []
        seen = set()
        for s in sel:
            aid = self._iid_to_app_id(s)
            if aid not in seen:
                seen.add(aid)
                app_ids.append(aid)
        if len(app_ids) == 1:
            aid = app_ids[0]
            menu.add_command(label="📋 查看笔记", command=lambda: self._open_notes_viewer(aid))
            menu.add_command(label="📋 复制 AppID", command=lambda: self._copy_appid_silent(aid))
            menu.add_separator()
            menu.add_command(label="📤 导出笔记", command=self._ui_export_dialog)
            if self.manager.is_dirty(aid):
                menu.add_separator()
                menu.add_command(label="☁️ 上传到 Steam Cloud",
                                 command=lambda: self._cloud_upload_single(aid))
        else:
            menu.add_command(label=f"📤 导出 ({len(app_ids)} 个游戏)",
                             command=self._ui_export_dialog)
            # 大量选中时用总 dirty count 避免逐个检查
            dirty_n = self.manager.dirty_count() if self.manager else 0
            if dirty_n > 0:
                menu.add_command(label=f"☁️ 上传选中的改动",
                                 command=self._cloud_upload_selected)
        # 展开/收起
        menu.add_separator()
        menu.add_command(label="📝 新建笔记", command=self._ui_create_note)
        menu.add_command(label="📥 导入笔记", command=self._ui_import)
        menu.add_command(label="🗑 删除笔记", command=self._ui_delete_notes)
        # 展开/收起：根据当前状态决定是否显示
        has_closed = has_open = False
        for iid in self._lib_tree.get_children():
            if self._lib_tree.get_children(iid):
                if self._lib_tree.item(iid, "open"):
                    has_open = True
                else:
                    has_closed = True
                if has_closed and has_open:
                    break
        if has_closed or has_open:
            menu.add_separator()
        if has_closed:
            menu.add_command(label="📂 展开全部笔记", command=self._expand_all_notes)
        if has_open:
            menu.add_command(label="📁 收起全部笔记", command=self._collapse_all_notes)
        menu.add_separator()
        menu.add_command(label="🔄 刷新库列表", command=self._lib_refresh)
        if any(v in ('plus', 'minus') for v in self._coll_filter_states.values()):
            menu.add_command(label="↩️ 还原库列表", command=self._lib_reset_coll_filters)
        self._smart_popup(menu, event.x_root, event.y_root)

    def _get_selected_app_id(self):
        """获取 Treeview 选中的第一个 AppID（兼容笔记子节点）"""
        sel = self._games_tree.selection()
        return self._iid_to_app_id(sel[0]) if sel else None

    def _get_selected_app_ids(self):
        """获取 Treeview 选中的所有 AppID（去重，兼容笔记子节点）"""
        result = []
        for iid in self._games_tree.selection():
            aid = self._iid_to_app_id(iid)
            if aid not in result:
                result.append(aid)
        return result

    def _copy_selected_appid(self):
        """复制选中游戏的 AppID"""
        aids = self._get_selected_app_ids()
        if aids:
            self._copy_appid_silent(",".join(aids))
        else:
            messagebox.showinfo("提示", "请先在列表中选择游戏。")

    def _copy_appid_silent(self, app_id: str):
        """复制 AppID 到剪贴板（带短暂反馈）"""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(app_id)
            self.root.update()
            self._flash_status(f"✅ 已复制 AppID: {app_id}")
        except Exception:
            pass

    def _flash_status(self, text, duration=2000):
        """在状态栏短暂显示提示文字，duration 毫秒后恢复"""
        lbl = getattr(self, '_lib_status', None)
        if not lbl:
            return
        old_text = lbl.cget("text")
        lbl.config(text=text, fg="#2a7f2a")
        self.root.after(duration, lambda t=old_text: lbl.config(text=t, fg="#666"))

    def _select_all_games(self):
        """全选/取消全选当前筛选页下的所有游戏及其可见笔记"""
        tree = self._lib_tree if hasattr(self, '_lib_tree') else self._games_tree
        all_games = list(tree.get_children())
        if not all_games:
            return
        self._selection_updating = True
        try:
            current_sel = set(tree.selection())
            # 收集所有游戏行 + 所有可见子笔记
            all_items = []
            for game_iid in all_games:
                all_items.append(game_iid)
                for child in tree.get_children(game_iid):
                    all_items.append(child)
            # 判断是否已全选
            if set(all_items) <= current_sel:
                tree.selection_set([])
                self.root.update_idletasks()
                self._prev_tree_selection = set()
                for game_iid in all_games:
                    self._set_partial_select(game_iid, False)
            else:
                tree.selection_set(all_items)
                self.root.update_idletasks()
                # 判断每个游戏行是否所有笔记都可见，否则标记 partial_select
                final_sel = set(tree.selection())
                self._prev_tree_selection = final_sel
                for game_iid in all_games:
                    visible_children = tree.get_children(game_iid)
                    if not visible_children:
                        # 没有子笔记，正常选中
                        self._set_partial_select(game_iid, False)
                        continue
                    # 比较可见子笔记数量与游戏实际总笔记数
                    total = self._sort_key_cache.get(game_iid, {}).get('notes', 0)
                    if len(visible_children) < total:
                        self._set_partial_select(game_iid, True)
                    else:
                        self._set_partial_select(game_iid, False)
            self._prev_tree_selection = set(tree.selection())
        finally:
            self._selection_updating = False

    def _on_game_double_click(self, event):
        app_id = self._get_selected_app_id()
        if app_id:
            self._open_notes_viewer(app_id)

    # ────────────────────── UI 辅助方法 ──────────────────────

    def _ui_view_selected(self):
        """查看选中游戏的笔记"""
        aid = self._get_selected_app_id()
        if aid:
            self._open_notes_viewer(aid)
        else:
            messagebox.showinfo("提示", "请先在列表中选择游戏。")

    def _ui_backfill_ai_dates(self):
        """为所有缺少生成日期的 AI 笔记补上日期"""
        if not self.manager:
            return
        ans = messagebox.askyesno("📅 补充生成日期",
            "将为所有缺少生成日期的 AI 笔记补上日期。\n"
            "日期来源于笔记的创建时间戳。\n\n"
            "确认执行？", parent=self.root)
        if not ans:
            return
        apps, notes = self.manager.backfill_ai_note_dates()
        if notes > 0:
            messagebox.showinfo("✅ 完成",
                f"已为 {apps} 个游戏的 {notes} 条 AI 笔记补充了生成日期。",
                parent=self.root)
            self._refresh_games_list()
        else:
            messagebox.showinfo("提示",
                "所有 AI 笔记都已有生成日期，无需补充。",
                parent=self.root)

    def _last_dir(self, key):
        """获取功能对应的上次使用目录"""
        d = self._config_mgr.get('last_dirs', {}).get(key, '')
        return d if d and os.path.isdir(d) else os.path.expanduser('~')

    def _save_dir(self, key, path):
        """保存功能使用的目录（接受文件路径或目录路径）"""
        if not path:
            return
        d = path if os.path.isdir(path) else os.path.dirname(path)
        dirs = self._config_mgr.get('last_dirs', {})
        dirs[key] = d
        self._config_mgr.set('last_dirs', dirs)

    @staticmethod
    def _smart_popup(menu, x, y):
        """弹出右键菜单，底部空间不足时向上弹出（底边对齐鼠标）"""
        menu.update_idletasks()
        menu_h = menu.winfo_reqheight()
        screen_h = menu.winfo_screenheight()
        if menu_h and y + menu_h > screen_h:
            y = y - menu_h
        menu.tk_popup(x, y)

    @staticmethod
    def _center_window(win, width=None, height=None):
        """居中窗口（Toplevel 先隐藏再显示，避免闪到左上角）"""
        is_toplevel = isinstance(win, tk.Toplevel)
        if is_toplevel:
            win.withdraw()
        win.update_idletasks()
        if width and height:
            cw, ch = width, height
        else:
            cw, ch = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{cw}x{ch}+{int((sw - cw) / 2)}+{int((sh - ch) / 2)}")
        if is_toplevel:
            win.deiconify()


# ═══════════════════════════════════════════════════════════════════════════════
#  程序入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """程序入口"""
    intro = SteamToolboxIntro()
    intro.intro_ui()


if __name__ == "__main__":
    main()
