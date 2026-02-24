"""SteamShelf — 库管理：收藏夹来源绑定/更新（LibrarySourceUpdateMixin）

从 ui_library_collections.py 拆分。包含收藏夹来源的绑定、解绑、
单个更新和批量更新逻辑。

依赖 self 属性（由其他模块提供）：
  .root: tk.Tk                          — ui_main
  ._collections_core: CollectionsCore   — ui_main
  ._coll_data_cache: dict               — ui_library
  ._ensure_collections_core()           — ui_collection_ops
  ._save_and_sync()                     — ui_collection_ops
  ._ui_refresh()                        — ui_collection_ops
  ._center_window()                     — ui_main
  ._lib_load_collections()              — ui_library_collections
"""

import json
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from ui_utils import ProgressWindow, bg_thread


class LibrarySourceUpdateMixin:
    """收藏夹来源更新方法（Mixin，self 指向 SteamToolboxMain 实例）"""

    def _ask_bind_source(self, col_id, source_type, source_params,
                         display_name, update_mode='incremental',
                         parent=None):
        """更新完成后询问是否绑定来源"""
        mode_labels = {"incremental_aux": "增量+辅助",
                       "incremental": "增量", "replace": "替换"}
        mode_label = mode_labels.get(update_mode, update_mode)
        ans = messagebox.askyesno("绑定来源",
            f"是否将此来源绑定到该分类？\n\n"
            f"来源：{display_name}\n"
            f"更新模式：{mode_label}\n\n"
            f"绑定后可右键一键更新。",
            parent=parent or self.root)
        if ans:
            self._collections_core.save_collection_source(
                col_id, source_type, source_params,
                display_name, update_mode)
            self._lib_load_collections()

    def _unbind_collection_source(self, col_id, col_name):
        """解绑收藏夹的来源"""
        if not messagebox.askyesno("解绑来源",
                f"确定解绑「{col_name}」的更新来源？\n解绑后无法一键更新。",
                parent=self.root):
            return
        self._collections_core.remove_collection_source(col_id)
        self._lib_load_collections()

    def _update_all_cached_sources(self, col_ids=None):
        """批量更新有缓存来源的收藏夹。col_ids 非空时只更新指定的。"""
        if not self._ensure_collections_core():
            return

        all_sources = self._collections_core._get_all_sources()
        if col_ids is not None:
            all_sources = {k: v for k, v in all_sources.items()
                          if k in col_ids}
        if not all_sources:
            messagebox.showinfo("提示", "没有任何分类绑定了来源。",
                                parent=self.root)
            return

        data = self._collections_core.load_json()
        if data is None:
            return

        tasks = []
        for col_id, source_info in all_sources.items():
            for entry in data:
                if entry[0] == f"user-collections.{col_id}":
                    meta = entry[1]
                    if meta.get("is_deleted") or "value" not in meta:
                        break
                    val_obj = json.loads(meta['value'])
                    tasks.append((col_id, source_info, entry,
                                  val_obj.get('name', '未知')))
                    break

        if not tasks:
            messagebox.showinfo("提示", "没有需要更新的来源。",
                                parent=self.root)
            return

        pw = ProgressWindow(self.root, "🔄 批量更新所有来源",
            f"即将更新 {len(tasks)} 个分类",
            maximum=len(tasks), detail=True)
        self._center_window(pw.win)

        def batch_thread():
            results = []
            disclaimer = self._collections_core.disclaimer

            # 表达式分类单独处理（用已验证的 _auto_update_expression_collections）
            expr_ids = [col_id for col_id, si, _, _ in tasks
                        if si.get('source_type') == 'expression']
            non_expr_tasks = [(cid, si, e, n) for cid, si, e, n in tasks
                              if si.get('source_type') != 'expression']

            for idx, (col_id, source_info, target_entry, name) in \
                    enumerate(non_expr_tasks):
                src_type = source_info.get('source_type', '')
                src_params = source_info.get('source_params', {})
                update_mode = source_info.get('update_mode',
                                              'incremental')
                src_display = source_info.get('source_display_name', '')

                def _up_status(n=name, i=idx):
                    pw.update(value=i,
                              status=f"[{i + 1}/{len(tasks)}] 正在更新「{n}」...")
                try:
                    pw.win.after(0, _up_status)
                except Exception:
                    pass

                def progress_cb(fetched, total, phase, detail):
                    pw.update(detail=phase or detail or "")

                ids, error = self._fetch_source_ids(
                    src_type, src_params, progress_cb)

                if error or not ids:
                    results.append(f"❌ {name}: {error or '无数据'}")
                    time.sleep(0.3)
                    continue

                if update_mode == 'replace':
                    old_c, new_c = \
                        self._collections_core.perform_replace_update(
                            data, target_entry, ids)
                    results.append(
                        f"🔄 {name}: {old_c} → {new_c}")
                else:
                    create_aux = (update_mode == 'incremental_aux')
                    a, r, t, updated = \
                        self._collections_core \
                        .perform_incremental_update(
                            data, target_entry, ids, name,
                            create_aux=create_aux)
                    if updated:
                        results.append(
                            f"✅ {name}: +{a}, -{r}, 共{t}")
                    else:
                        results.append(f"⏭️ {name}: 已是最新")

                self._collections_core.save_collection_source(
                    col_id, src_type, src_params, src_display,
                    update_mode)

                time.sleep(0.3)

            # 表达式分类用已验证的自动更新路径
            if expr_ids:
                # auto_update 内部会 pop_pending_cef_ops，先保存非表达式的
                _saved_cef = (self._collections_core.pop_pending_cef_ops()
                              if non_expr_tasks else [])
                if non_expr_tasks:
                    self._collections_core.save_json(
                        data, backup_description="批量更新外部来源")
                try:
                    pw.update(status="正在更新筛选表达式分类...")
                except Exception:
                    pass
                changed = self._auto_update_expression_collections()
                if changed:
                    results.append(
                        f"🔄 {len(expr_ids)} 个筛选表达式分类已更新")
                else:
                    results.append(
                        f"⏭️ {len(expr_ids)} 个筛选表达式分类已是最新")
                # 恢复非表达式 CEF ops 供 finish() 同步
                if _saved_cef:
                    self._collections_core._pending_cef_ops.extend(
                        _saved_cef)

            def finish():
                pw.update(value=len(tasks))
                if non_expr_tasks:
                    if not expr_ids:
                        self._save_and_sync(
                            data, backup_description="批量更新所有来源")
                    else:
                        # 数据已保存，只需同步恢复的 CEF ops
                        cef_ops = self._collections_core.pop_pending_cef_ops()
                        if (cef_ops and self._cef_bridge
                                and self._cef_bridge.is_connected()):
                            self._do_cef_sync(cef_ops)
                pw.close()
                self._ui_refresh()

                result_text = "\n".join(results)
                messagebox.showinfo("批量更新完成",
                    f"已处理 {len(tasks)} 个分类：\n\n"
                    f"{result_text}" + disclaimer,
                    parent=self.root)

            self.root.after(0, finish)

        threading.Thread(target=bg_thread(batch_thread), daemon=True).start()

    def _update_from_cached_source(self, col_id, source_info):
        """根据缓存的来源信息一键更新收藏夹"""
        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return

        target_entry = None
        target_name = None
        for entry in data:
            if entry[0] == f"user-collections.{col_id}":
                meta = entry[1]
                if meta.get("is_deleted") or "value" not in meta:
                    break
                val_obj = json.loads(meta['value'])
                target_name = val_obj.get('name', '未知')
                target_entry = entry
                break

        if not target_entry:
            messagebox.showwarning("错误", "未找到该分类，可能已被删除。",
                                   parent=self.root)
            return

        src_type = source_info.get('source_type', '')
        src_params = source_info.get('source_params', {})
        update_mode = source_info.get('update_mode', 'incremental')
        src_display = source_info.get('source_display_name', '未知来源')

        mode_labels = {"incremental_aux": "增量+辅助",
                       "incremental": "增量", "replace": "替换"}

        prog_win = tk.Toplevel(self.root)
        prog_win.title(f"🔄 更新「{target_name}」")
        prog_win.resizable(False, False)
        prog_win.transient(self.root)

        tk.Label(prog_win, text=f"来源：{src_display}",
                 font=("", 10)).pack(padx=20, pady=(15, 5))
        tk.Label(prog_win, text=f"模式：{mode_labels.get(update_mode, update_mode)}",
                 font=("", 9), fg="#666").pack(padx=20)

        status_var = tk.StringVar(value="正在连接...")
        tk.Label(prog_win, textvariable=status_var,
                 font=("", 9), fg="gray").pack(padx=20, pady=(8, 0))

        progress_bar = ttk.Progressbar(prog_win, length=350,
                                        mode='indeterminate')
        progress_bar.pack(padx=20, pady=(5, 0))
        progress_bar.start(15)

        detail_var = tk.StringVar(value="")
        detail_label = tk.Label(prog_win, textvariable=detail_var,
                                font=("", 8), fg="#888")
        detail_label.pack(padx=20, anchor="w")

        self._center_window(prog_win)

        def fetch_thread():
            def progress_cb(fetched, total, phase, detail):
                def _up():
                    status_var.set(f"正在获取: {phase}")
                    if detail:
                        detail_var.set(detail)
                try:
                    prog_win.after(0, _up)
                except Exception:
                    pass

            ids, error = self._fetch_source_ids(
                src_type, src_params, progress_cb)

            def finish():
                if not prog_win.winfo_exists():
                    return
                progress_bar.stop()
                if error or not ids:
                    prog_win.destroy()
                    messagebox.showerror("更新失败",
                        f"❌ 获取来源数据失败：\n{error or '未获取到任何游戏'}",
                        parent=self.root)
                    return

                disclaimer = self._collections_core.disclaimer
                if update_mode == 'replace':
                    old_count, new_count = \
                        self._collections_core.perform_replace_update(
                            data, target_entry, ids)
                    result_msg = (f"🔄 替换更新完成\n"
                                  f"   {old_count} → {new_count}")
                else:
                    create_aux = (update_mode == 'incremental_aux')
                    a, r, t, updated = \
                        self._collections_core.perform_incremental_update(
                            data, target_entry, ids, target_name,
                            create_aux=create_aux)
                    if not updated:
                        prog_win.destroy()
                        messagebox.showinfo("已是最新",
                            f"「{target_name}」已是最新，无需更新。",
                            parent=self.root)
                        return
                    result_msg = (f"✅ 增量更新完成\n"
                                  f"   新增: {a}, 移除: {r}, 总计: {t}")

                self._collections_core.save_collection_source(
                    col_id, src_type, src_params, src_display, update_mode)
                self._save_and_sync(
                    data, backup_description=f"从缓存来源更新: {target_name}")
                prog_win.destroy()
                self._ui_refresh()
                messagebox.showinfo("更新完成",
                    result_msg + disclaimer, parent=self.root)

            try:
                prog_win.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=bg_thread(fetch_thread), daemon=True).start()

    def _fetch_source_ids(self, src_type, src_params, progress_cb):
        """根据来源类型获取游戏 ID 列表（公共提取，供两个更新方法复用）

        Returns:
            (ids, error): ids 为 list，error 为 str 或 None
        """
        ids = []
        error = None
        try:
            if src_type == 'steam250':
                ids, error = self._collections_core.fetch_steam250_ids(
                    src_params.get('url', ''), progress_cb)
            elif src_type == 'curator':
                url = src_params.get('url', '')
                page_type, identifier = \
                    self._collections_core.extract_steam_list_info(url)
                if page_type and identifier:
                    login_cookies = None
                    saved_cookie = \
                        self._collections_core.get_saved_cookie()
                    if saved_cookie:
                        login_cookies = \
                            f"steamLoginSecure={saved_cookie}"
                    ids, _, error, _ = \
                        self._collections_core.fetch_steam_list(
                            page_type, identifier, progress_cb,
                            login_cookies)
                else:
                    error = f"无法解析来源 URL: {url}"
            elif src_type == 'igdb_category':
                dim = src_params.get('dimension', '')
                item_id = src_params.get('item_id', 0)
                item_name = src_params.get('item_name', '')
                ids, error = \
                    self._collections_core.fetch_igdb_games_by_dimension(
                        dim, item_id, item_name, progress_cb,
                        force_refresh=True)
            elif src_type == 'igdb_company':
                company_id = src_params.get('company_id', 0)
                company_name = src_params.get('company_name', '')
                ids, error = \
                    self._collections_core.fetch_igdb_games_by_company(
                        company_id, company_name, progress_cb)
            elif src_type == 'expression':
                ids = self._eval_filter_expression(src_params)
                if not ids:
                    error = "表达式求值结果为空（相关分类可能已删除）"
            elif src_type == 'community_share':
                from ui_sharing import _supabase_get
                share_id = src_params.get('share_id', '')
                col_name = src_params.get('collection_name', '')
                rows = _supabase_get(
                    "shared_collections",
                    f"id=eq.{share_id}&limit=1")
                if rows:
                    for c in rows[0].get("collections", []):
                        if c.get("name") == col_name:
                            ids = [int(a) for a in c.get("added", [])
                                   if str(a).isdigit()]
                            break
                    if not ids:
                        error = f"远端分享中未找到分类「{col_name}」"
                else:
                    error = "分享已被删除或无法访问"
            else:
                error = f"未知的来源类型: {src_type}"
        except Exception as e:
            error = str(e)
        return ids, error
