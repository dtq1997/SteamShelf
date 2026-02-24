"""SteamShelf — 库管理：筛选表达式分类（LibraryExpressionMixin）

从 ui_library_collections.py 拆分。包含筛选表达式保存为分类、
自动更新、健康检查、CEF 同步验证等逻辑。

依赖 self 属性（由其他模块提供）：
  .root: tk.Tk                          — ui_main
  ._collections_core: CollectionsCore   — ui_main
  ._cef_bridge: CEFBridge               — ui_main
  ._game_name_cache: dict               — ui_main
  .manager: SteamNotesManager           — ui_main
  ._coll_tree: ttk.Treeview             — ui_library
  ._lib_all_games: list                 — ui_library
  ._lib_all_games_backup: list|None     — ui_library
  ._coll_data_cache: dict               — ui_library_collections
  ._coll_filter_states: dict            — ui_library
  ._coll_ops_plus: list                 — ui_library
  ._coll_ops_minus: list                — ui_library
  ._coll_filter_var: tk.StringVar       — ui_library
  ._type_filter: set                    — ui_library
  ._ALL_TYPES: list                     — ui_library
"""

import json
import os
import platform
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog

from ui_utils import bg_thread


class LibraryExpressionMixin:
    """筛选表达式分类相关方法（Mixin，self 指向 SteamToolboxMain 实例）"""

    # ── 筛选表达式保存为分类 ──

    def _save_filter_as_collection(self):
        """将当前筛选表达式保存为 Steam 静态分类（绑定来源，可手动更新）"""
        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return

        source_params = self._build_expression_params()
        app_ids = self._eval_filter_expression(source_params)
        if not app_ids:
            messagebox.showwarning("提示", "当前筛选结果为空，无法创建分类。",
                                   parent=self.root)
            return

        default_name = self._build_expression_display(source_params)
        name = simpledialog.askstring(
            "保存筛选为分类",
            f"当前筛选匹配 {len(app_ids)} 个游戏。\n请输入分类名称：",
            initialvalue=default_name, parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()

        col_id = self._collections_core.add_static_collection(
            data, name, [int(a) for a in app_ids if a.isdigit()])
        self._collections_core.save_collection_source(
            col_id, 'expression', source_params, default_name, 'auto')
        self._save_and_sync(data, backup_description=f"保存筛选表达式为分类「{name}」")
        self._ui_refresh()
        messagebox.showinfo("完成",
            f"已创建分类「{name}」（{len(app_ids)} 个游戏）\n"
            "已绑定筛选表达式，相关分类变化时自动更新。\n"
            "右键「更新上游分类」可刷新引用的绑定来源。",
            parent=self.root)

    def _build_expression_params(self):
        """收集当前所有筛选状态为 source_params dict"""
        return {
            'coll_filter_states': {
                cid: s for cid, s in self._coll_filter_states.items()
                if s != 'default'},
            'coll_ops_plus': list(self._coll_ops_plus),
            'coll_ops_minus': list(self._coll_ops_minus),
            'coll_filter_var': self._coll_filter_var.get(),
            'filters': self._lib_read_filter_state(),
            'type_filter': list(self._type_filter),
            'search_q': self._lib_search_var.get().strip(),
            'search_mode': self._main_search_mode.get(),
        }

    @staticmethod
    def _expr_needs_notes(params):
        """检查表达式参数是否需要笔记数据（AI/来源/确信度等筛选）"""
        f = params.get('filters', {})
        return (f.get('filter_mode') not in (None, '全部')
                or f.get('model_filter') is not None
                or f.get('dirty_only') or f.get('uploading_only')
                or f.get('source_filter', '来源') != '来源'
                or f.get('vol_filter', '信息量') != '信息量'
                or f.get('conf_filter', '确信度') != '确信度'
                or f.get('qual_filter', '质量') != '质量')

    def _build_eval_candidates(self, params, notes_games, ai_map, sync_map):
        """构建筛选表达式的候选 app_id 集合（分类交并 + 显示模式）"""
        coll_cache = getattr(self, '_coll_data_cache', {})
        states = params.get('coll_filter_states', {})
        plus_ids = [c for c, s in states.items() if s == 'plus']
        minus_ids = [c for c, s in states.items() if s == 'minus']
        if (plus_ids or minus_ids) and not coll_cache:
            return set()
        ops_p = params.get('coll_ops_plus', [True] * max(0, len(plus_ids) - 1))
        ops_m = params.get('coll_ops_minus', [True] * max(0, len(minus_ids) - 1))

        plus_o, plus_n = self._eval_coll_expr(plus_ids, ops_p)
        minus_o, minus_n = self._eval_coll_expr(minus_ids, ops_m)

        if plus_ids:
            owned, not_owned = plus_o, plus_n
        else:
            base = self._lib_all_games_backup or self._lib_all_games
            owned = set(str(g['app_id']) for g in base if g.get('owned'))
            not_owned = set()
            # SSOT 对齐点：必须与 ui_library.py:1133-1155 的 merged_games 构建一致
            # notes-only 和 uploading 游戏在 UI 中以 owned=True 加入，此处同步
            for aid in notes_games:
                owned.add(aid)
            for aid, st in sync_map.items():
                if st == 3:
                    owned.add(aid)
        owned -= minus_o
        not_owned -= minus_n

        show = params.get('coll_filter_var', '已入库')
        if show == '已入库':
            return owned
        if show == '全部':
            return owned | not_owned
        return not_owned

    def _eval_filter_expression(self, params, notes_data=None):
        """求值筛选表达式，返回 app_id 字符串列表

        notes_data: 可选 (notes_games, ai_map, sync_map) 元组，
                    传入时复用避免重复 scan_all。
        """
        if not self._expr_needs_notes(params):
            notes_games, ai_map, sync_map = {}, {}, {}
        elif notes_data:
            notes_games, ai_map, sync_map = notes_data
        else:
            notes_games, ai_map, sync_map = self._lib_load_notes_data()

        candidates = self._build_eval_candidates(
            params, notes_games, ai_map, sync_map)
        if not candidates:
            return []

        filters = params.get('filters', {})
        type_f = set(params.get('type_filter', []))
        sq = params.get('search_q', '').lower()
        sm = params.get('search_mode', 'name')

        need_type = type_f and len(type_f) < len(self._ALL_TYPES)
        if need_type:
            type_map = {str(g.get('app_id')): self._get_type_name(
                self._get_app_type(g))
                for g in (self._lib_all_games_backup or self._lib_all_games)}

        result = []
        for aid in candidates:
            has_ai = aid in ai_map
            has_notes = aid in notes_games
            is_dirty = self.manager.is_dirty(aid) if self.manager and has_notes else False
            is_up = sync_map.get(aid) == 3
            name = self._game_name_cache.get(aid, f"AppID {aid}")
            if need_type:
                if type_map.get(aid, "Game") not in type_f:
                    continue
            if not self._lib_should_include_game(
                    aid, has_ai, is_dirty, is_up, ai_map, filters,
                    sq, sm, name):
                continue
            result.append(aid)
        return result

    def _guess_type_for_aid(self, aid):
        """根据缓存猜测 app 类型名"""
        for g in (self._lib_all_games_backup or self._lib_all_games):
            if str(g.get('app_id')) == aid:
                return self._get_type_name(self._get_app_type(g))
        return "Game"

    def _build_expression_display(self, params):
        """生成筛选表达式的简短显示名"""
        cache = getattr(self, '_coll_data_cache', {})
        states = params.get('coll_filter_states', {})
        parts = []
        for sign in ('plus', 'minus'):
            ids = [c for c, s in states.items() if s == sign]
            if not ids:
                continue
            prefix = "＋" if sign == 'plus' else "－"
            names = [cache.get(c, {}).get('name', c)[:8] for c in ids]
            parts.append(f"{prefix}{' '.join(names)}")
        f = params.get('filters', {})
        if f.get('filter_mode') not in (None, '全部'):
            parts.append(f['filter_mode'])
        if params.get('search_q'):
            parts.append(f"🔍{params['search_q'][:6]}")
        return " | ".join(parts) or "筛选表达式"

    def _schedule_expression_update(self):
        """将表达式自动更新调度到后台线程，不阻塞 UI"""
        if getattr(self, '_expression_updating', False):
            return
        if self._collections_core:
            all_src = self._collections_core._get_all_sources()
            if not any(v.get('source_type') == 'expression'
                       for v in all_src.values()):
                return

        def _bg():
            changed = self._auto_update_expression_collections()
            if changed:
                try:
                    self.root.after(0, self._lib_schedule_tree_rebuild)
                except Exception:
                    pass

        threading.Thread(target=bg_thread(_bg), daemon=True).start()

    def _auto_update_expression_collections(self, force_fresh=False):
        """自动更新所有 expression 类型的绑定分类（_lib_load_collections 末尾调用）

        多轮收敛：表达式可能引用其他表达式，单次遍历顺序不确定，
        因此循环直到无变化（最多 5 轮），每轮更新后同步刷新缓存。
        """
        if getattr(self, '_expression_updating', False):
            return
        if not self._collections_core:
            return
        if len(self._lib_all_games) < 100 and not force_fresh:
            return
        all_sources = self._collections_core._get_all_sources()
        expr_sources = {k: v for k, v in all_sources.items()
                        if v.get('source_type') == 'expression'}
        if not expr_sources:
            return

        data = self._collections_core.load_json()
        if data is None:
            return

        # 预加载笔记数据（仅当任一表达式需要时）
        needs_notes = any(
            self._expr_needs_notes(v.get('source_params', {}))
            for v in expr_sources.values())
        if needs_notes:
            if force_fresh:
                self._invalidate_notes_cache()
            nd = self._lib_load_notes_data()
        else:
            nd = ({}, {}, {})

        _dbg_log, _flush_log = self._expr_debug_logger()
        cache = getattr(self, '_coll_data_cache', {})
        _dbg_log(f"=== auto_update START: {len(expr_sources)} expr colls, "
                 f"cache_keys={len(cache)} ===")
        for cid in expr_sources:
            c = cache.get(cid, {})
            _dbg_log(f"  BEFORE {cid}: owned={len(c.get('owned_app_ids',[]))}, "
                     f"name={c.get('name','?')}")

        changed = self._expr_run_convergence(expr_sources, data, nd, _dbg_log)

        for cid in expr_sources:
            c = cache.get(cid, {})
            _dbg_log(f"  AFTER {cid}: owned={len(c.get('owned_app_ids',[]))}")
        _dbg_log(f"=== auto_update END: changed={changed} ===")

        self._expr_ssot_assert(expr_sources, nd, _dbg_log)
        _flush_log()

        if changed:
            self._save_and_sync(
                data, backup_description="自动更新筛选表达式分类")
            self.root.after(0, self._refresh_expr_coll_labels)
            self.root.after(3000, self._verify_expr_sync)
        return changed

    @staticmethod
    def _expr_debug_logger():
        """创建表达式调试日志器，返回 (log_fn, flush_fn)"""
        import datetime
        _dbg = os.environ.get('STEAMSHELF_DEBUG_EXPR')
        _lines = []

        def _log(msg):
            if _dbg:
                _lines.append(f"[{datetime.datetime.now():%H:%M:%S.%f}] {msg}")

        def _flush():
            if _dbg and _lines:
                _p = os.path.join(os.path.expanduser('~'),
                                  '.steam_toolkit', 'expr_debug.log')
                with open(_p, 'a', encoding='utf-8') as f:
                    f.write('\n'.join(_lines) + '\n\n')

        return _log, _flush

    def _expr_run_convergence(self, expr_sources, data, nd, _dbg_log):
        """多轮收敛循环：遍历所有表达式分类，直到无变化（最多5轮）"""
        changed = False
        self._expression_updating = True
        try:
            for _pass in range(5):
                pass_changed = False
                for col_id, src_info in expr_sources.items():
                    params = src_info.get('source_params', {})
                    new_ids = set(self._eval_filter_expression(
                        params, notes_data=nd))
                    _dbg_log(f"  pass{_pass} {col_id}: eval→{len(new_ids)}")
                    if self._apply_expression_update(
                            data, col_id, new_ids, notes_data=nd):
                        pass_changed = True
                        changed = True
                        _dbg_log(f"    → CHANGED")
                if not pass_changed:
                    _dbg_log(f"  pass{_pass}: stable, break")
                    break
        finally:
            self._expression_updating = False
        return changed

    def _expr_ssot_assert(self, expr_sources, nd, _dbg_log):
        """SSOT 回归防线：notes-only 游戏应归为 owned"""
        _dbg = os.environ.get('STEAMSHELF_DEBUG_EXPR')
        if not _dbg or not nd[0]:
            return
        cache = getattr(self, '_coll_data_cache', {})
        ng_keys = set(nd[0].keys())
        for cid in expr_sources:
            c = cache.get(cid, {})
            bad = ng_keys & set(c.get('not_owned_app_ids', []))
            if bad:
                _dbg_log(f"  ⚠️ SSOT ASSERT FAIL: {cid} has "
                         f"{len(bad)} notes-only games as not_owned")

    def _refresh_expr_coll_labels(self):
        """更新表达式分类的树标签（表达式分类不受 CEF owned 拆分影响，始终用 total）"""
        if not self._collections_core:
            return
        all_sources = self._collections_core._get_all_sources()
        cache = getattr(self, '_coll_data_cache', {})
        tree = self._coll_tree
        for col_id, src in all_sources.items():
            if src.get('source_type') != 'expression':
                continue
            if col_id not in cache or not tree.exists(col_id):
                continue
            c = cache[col_id]
            name = c.get('name', col_id)
            total = len(c.get('owned_app_ids', [])) + len(c.get('not_owned_app_ids', []))
            tree.item(col_id, text=f"  {name} ({total})")

    def _health_check_expr_collections(self):
        """三方对账：eval结果 vs JSON存储 vs Steam实际数据"""
        if not self._collections_core:
            messagebox.showinfo("提示", "分类核心未初始化。", parent=self.root)
            return
        all_sources = self._collections_core._get_all_sources()
        expr_sources = {k: v for k, v in all_sources.items()
                        if v.get('source_type') == 'expression'}
        if not expr_sources:
            messagebox.showinfo("提示", "没有表达式分类。", parent=self.root)
            return

        # 1. Re-eval
        nd = self._lib_load_notes_data()
        eval_results = {}
        for cid, src in expr_sources.items():
            params = src.get('source_params', {})
            eval_results[cid] = set(self._eval_filter_expression(params, notes_data=nd))

        # 2. JSON 存储
        json_data = self._collections_core.load_json() or []
        json_results = {}
        for entry in json_data:
            key = entry[0]
            if not key.startswith("user-collections."):
                continue
            cid = key[len("user-collections."):]
            if cid not in expr_sources:
                continue
            meta = entry[1]
            if meta.get("is_deleted") or "value" not in meta:
                continue
            val = json.loads(meta['value'])
            json_results[cid] = set(str(a) for a in val.get('added', []))

        # 3. CEF/Steam 实际数据
        cef_results = {}
        cef_error = ""
        bridge = getattr(self, '_cef_bridge', None)
        if bridge:
            try:
                cef_data = bridge.get_all_collections_with_apps()
                colls = cef_data.get('collections', {})
                for cid in expr_sources:
                    if cid in colls:
                        cef_results[cid] = set(
                            str(a) for a in colls[cid].get('appIds', []))
            except Exception as e:
                cef_error = str(e)

        # 4. 对账
        self._show_health_report(
            expr_sources, eval_results, json_results, cef_results, cef_error)

    def _show_health_report(self, expr_sources, eval_r, json_r, cef_r, cef_err):
        """显示健康检查报告"""
        cache = getattr(self, '_coll_data_cache', {})
        lines = []
        all_ok = True

        for cid in expr_sources:
            c = cache.get(cid, {})
            name = c.get('name', cid)
            ev = eval_r.get(cid, set())
            js = json_r.get(cid, set())
            ce = cef_r.get(cid)

            ev_n, js_n = len(ev), len(js)
            match_ej = (ev == js)
            match_ec = (ev == ce) if ce is not None else None

            if match_ej and match_ec is not False:
                status = "✅"
            else:
                status = "❌"
                all_ok = False

            lines.append(f"{status} {name}")
            lines.append(f"   eval={ev_n}  json={js_n}"
                         + (f"  steam={len(ce)}" if ce is not None else "  steam=N/A"))

            if not match_ej:
                only_eval = ev - js
                only_json = js - ev
                if only_eval:
                    lines.append(f"   ⚠️ eval有/json无: {len(only_eval)}个")
                if only_json:
                    lines.append(f"   ⚠️ json有/eval无: {len(only_json)}个")
            if match_ec is False:
                only_eval = ev - ce
                only_cef = ce - ev
                if only_eval:
                    lines.append(f"   ⚠️ eval有/steam无: {len(only_eval)}个")
                if only_cef:
                    lines.append(f"   ⚠️ steam有/eval无: {len(only_cef)}个")

        self._show_health_report_window(lines, all_ok, cef_err)

    def _show_health_report_window(self, lines, all_ok, cef_err):
        """显示健康检查报告窗口"""
        win = tk.Toplevel(self.root)
        win.title("🔍 表达式分类健康检查")
        win.resizable(True, True)
        win.transient(self.root)

        header = "✅ 全部正常" if all_ok else "❌ 发现不一致"
        tk.Label(win, text=header,
                 font=("", 14, "bold"),
                 fg="#2a7f2a" if all_ok else "#c0392b").pack(pady=(15, 5))

        if cef_err:
            tk.Label(win, text=f"⚠️ CEF查询失败: {cef_err[:60]}",
                     font=("", 9), fg="#888").pack()

        txt = tk.Text(win, width=60, height=min(len(lines) + 2, 25),
                      font=("Monaco" if platform.system() == "Darwin" else "Consolas", 10),
                      wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
        txt.insert(tk.END, "\n".join(lines))
        txt.config(state=tk.DISABLED)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(0, 15))

        def _copy():
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(lines))
            messagebox.showinfo("已复制", "报告已复制到剪贴板。", parent=win)

        def _resync():
            n = self._force_resync_expr_to_cef()
            messagebox.showinfo("完成", f"已强制同步 {n} 个表达式分类到 Steam。",
                                parent=win)
            win.destroy()
            self.root.after(200, self._health_check_expr_collections)

        if not all_ok:
            ttk.Button(btn_frame, text="🔄 强制重新同步到Steam",
                       command=_resync).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="📋 复制报告",
                   command=_copy).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="关闭",
                   command=win.destroy).pack(side=tk.LEFT, padx=4)
        self._center_window(win)

    def _force_resync_expr_to_cef(self):
        """强制将所有表达式分类的当前 JSON 数据推送到 CEF"""
        if not self._collections_core:
            return 0
        all_sources = self._collections_core._get_all_sources()
        data = self._collections_core.load_json() or []
        count = 0
        for entry in data:
            key = entry[0]
            if not key.startswith("user-collections."):
                continue
            cid = key[len("user-collections."):]
            if cid not in all_sources:
                continue
            if all_sources[cid].get('source_type') != 'expression':
                continue
            meta = entry[1]
            if meta.get("is_deleted") or "value" not in meta:
                continue
            val = json.loads(meta['value'])
            int_ids = [int(a) for a in val.get('added', []) if str(a).isdigit()]
            self._collections_core.queue_cef_upsert(
                cid, val.get('name', ''), int_ids)
            count += 1
        if count:
            self._save_and_sync(data, backup_description="强制重新同步表达式分类")
        return count

    def _verify_expr_sync(self):
        """延迟验证：CEF 同步是否成功，不一致则自动重推"""
        bridge = getattr(self, '_cef_bridge', None)
        if not bridge or not self._collections_core:
            return
        all_sources = self._collections_core._get_all_sources()
        expr_cids = [k for k, v in all_sources.items()
                     if v.get('source_type') == 'expression']
        if not expr_cids:
            return

        # 读 JSON
        json_data = self._collections_core.load_json() or []
        json_sets = {}
        for entry in json_data:
            key = entry[0]
            if not key.startswith("user-collections."):
                continue
            cid = key[len("user-collections."):]
            if cid not in expr_cids:
                continue
            meta = entry[1]
            if meta.get("is_deleted") or "value" not in meta:
                continue
            val = json.loads(meta['value'])
            json_sets[cid] = set(str(a) for a in val.get('added', []))

        # 读 CEF
        try:
            cef_data = bridge.get_all_collections_with_apps()
            cef_colls = cef_data.get('collections', {})
        except Exception:
            return

        # 对账
        mismatched = []
        for cid in expr_cids:
            js = json_sets.get(cid)
            ce = cef_colls.get(cid)
            if js is None or ce is None:
                continue
            ce_set = set(str(a) for a in ce.get('appIds', []))
            if js != ce_set:
                mismatched.append(cid)

        if not mismatched:
            return

        _dbg = os.environ.get('STEAMSHELF_DEBUG_EXPR')
        if _dbg:
            _p = os.path.join(os.path.expanduser('~'),
                              '.steam_toolkit', 'expr_debug.log')
            with open(_p, 'a', encoding='utf-8') as f:
                f.write(f"[VERIFY] {len(mismatched)} mismatched, "
                        f"auto-resync: {mismatched}\n")
        self._force_resync_expr_to_cef()

    def _apply_expression_update(self, data, col_id, new_ids,
                                   notes_data=None):
        """更新单个表达式分类的 JSON 数据 + 缓存，返回是否有变化"""
        for entry in data:
            if entry[0] != f"user-collections.{col_id}":
                continue
            meta = entry[1]
            if meta.get("is_deleted") or "value" not in meta:
                return False
            val = json.loads(meta['value'])
            old_ids = set(str(a) for a in val.get('added', []))
            json_changed = (new_ids != old_ids)
            if json_changed:
                int_ids = [int(a) for a in new_ids if a.isdigit()]
                val['added'] = int_ids
                meta['value'] = json.dumps(
                    val, ensure_ascii=False, separators=(',', ':'))
                meta['timestamp'] = int(time.time())
                self._collections_core.queue_cef_upsert(
                    col_id, val.get('name', ''), int_ids)
            # 始终同步缓存
            cache = getattr(self, '_coll_data_cache', {})
            if col_id in cache:
                owned_set = set(
                    str(g['app_id']) for g in
                    (self._lib_all_games_backup or self._lib_all_games)
                    if g.get('owned'))
                # SSOT 对齐：notes-only/uploading 游戏在 UI 中 owned=True
                if notes_data:
                    ng, _, sm = notes_data
                    owned_set.update(ng)
                    owned_set.update(a for a, s in sm.items() if s == 3)
                cache[col_id]['owned_app_ids'] = [
                    a for a in new_ids if a in owned_set]
                cache[col_id]['not_owned_app_ids'] = [
                    a for a in new_ids if a not in owned_set]
            return json_changed
        return False

    def _update_expression_upstream(self, col_id, source_info):
        """对 expression 分类点"更新来源"→ 更新上游 + 重新求值表达式本身"""
        try:
            params = source_info.get('source_params', {})
            states = params.get('coll_filter_states', {})
            upstream_ids = set(states.keys())
            if upstream_ids:
                all_src = self._collections_core._get_all_sources()
                linked = {k for k in upstream_ids if k in all_src
                          and all_src[k].get('source_type') != 'expression'}
                if linked:
                    self._update_all_cached_sources(col_ids=linked)
            changed = self._auto_update_expression_collections(force_fresh=True)
            cache = getattr(self, '_coll_data_cache', {})
            name = cache.get(col_id, {}).get('name', col_id)
            if changed:
                c = cache.get(col_id, {})
                cnt = len(c.get('owned_app_ids', [])) + len(c.get('not_owned_app_ids', []))
                self._refresh_expr_coll_labels()
                messagebox.showinfo("更新完成",
                    f"「{name}」已更新，当前 {cnt} 个游戏。",
                    parent=self.root)
            else:
                messagebox.showinfo("已是最新",
                    f"「{name}」已是最新，无需更新。",
                    parent=self.root)
        except Exception:
            import traceback
            traceback.print_exc()

    # ── 表达式渲染 + 集合运算 ──

    def _render_ops_expr(self, t, names, ops, grp):
        """向 Text 控件插入带括号的表达式"""
        mixed = any(ops) and not all(ops)
        groups, between = [[0]], []
        for i, is_union in enumerate(ops):
            if is_union:
                between.append(i)
                groups.append([i + 1])
            else:
                groups[-1].append(i + 1)
        for gi, group in enumerate(groups):
            paren = mixed and len(group) > 1
            if paren:
                t.insert(tk.END, "(", "paren")
            for j, ni in enumerate(group):
                t.insert(tk.END, names[ni], "txt")
                if j < len(group) - 1:
                    self._insert_op_tag(t, ni, grp)
            if paren:
                t.insert(tk.END, ")", "paren")
            if gi < len(between):
                self._insert_op_tag(t, between[gi], grp)

    def _insert_op_tag(self, t, op_idx, grp):
        """插入一个可点击的运算符到 Text 控件"""
        ops = self._coll_ops_plus if grp == 'plus' else self._coll_ops_minus
        sym = "∪" if ops[op_idx] else "∩"
        tag = f"op_{grp}_{op_idx}"
        t.insert(tk.END, sym, ("op", tag))
        t.tag_bind(tag, "<Button-1>",
                   lambda e, g=grp, i=op_idx: self._toggle_coll_op(g, i))

    def _eval_coll_expr(self, ids, ops):
        """求值收藏夹表达式：按∪切分成∩组，组内交集，组间并集
        已删除的收藏夹（不在 cache 中）会被跳过，而非视为空集。
        """
        if not ids:
            return set(), set()
        cache = self._coll_data_cache
        # 过滤掉已删除的 ID，同时调整 ops
        valid_ids, valid_ops = [], []
        for i, cid in enumerate(ids):
            if cid in cache:
                if valid_ids and i - 1 < len(ops):
                    valid_ops.append(ops[i - 1])
                valid_ids.append(cid)
        if not valid_ids:
            return set(), set()
        # 按∪切分成∩组
        groups = [[0]]
        for i, is_union in enumerate(valid_ops):
            if is_union:
                groups.append([i + 1])
            else:
                groups[-1].append(i + 1)
        result_o, result_n = set(), set()
        for group in groups:
            go = gn = None
            for ni in group:
                data = cache.get(valid_ids[ni], {})
                o = set(data.get('owned_app_ids', []))
                n = set(data.get('not_owned_app_ids', []))
                if go is None:
                    go, gn = o, n
                else:
                    go &= o
                    gn &= n
            result_o |= go
            result_n |= gn
        return result_o, result_n
