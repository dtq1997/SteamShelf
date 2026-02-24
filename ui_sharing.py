"""社区分类分享平台 — 上传 / 浏览 / 导入

Mixin: SharingMixin
宿主 Protocol: SharingHost（_protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import json
import threading
import tkinter as tk
import urllib.request
from tkinter import messagebox, ttk

from ui_utils import bg_thread
import utils

if TYPE_CHECKING:
    from _protocols import SharingHost

# ── Supabase 配置（创建项目后替换） ──
_SUPABASE_URL = "https://emaewlzhuzjcrnbjepph.supabase.co"
_SUPABASE_ANON_KEY = "sb_publishable_XIj6tWZ2CMYr7vwzHOA-rw_zmX2g8dk"


def _supabase_request(method, table, *, params="", payload=None, headers=None):
    """Supabase REST API 通用请求"""
    url = f"{_SUPABASE_URL}/rest/v1/{table}"
    if params:
        url += f"?{params}"
    hdrs = {
        "apikey": _SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {_SUPABASE_ANON_KEY}",
    }
    if headers:
        hdrs.update(headers)
    data = None
    if payload is not None:
        hdrs["Content-Type"] = "application/json"
        hdrs["Prefer"] = "return=representation"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    resp = utils.urlopen(req, timeout=30)
    return json.loads(resp.read().decode("utf-8"))


def _supabase_post(table, payload):
    return _supabase_request("POST", table, payload=payload)


def _supabase_get(table, params=""):
    return _supabase_request("GET", table, params=params)


def _supabase_delete(table, params, friend_code):
    """DELETE with friend_code filter in URL (PostgREST row-level filtering)"""
    full = f"{params}&friend_code=eq.{friend_code}" if params else \
           f"friend_code=eq.{friend_code}"
    return _supabase_request("DELETE", table, params=full)


def _supabase_patch(table, params, payload, friend_code):
    """PATCH with friend_code filter in URL (PostgREST row-level filtering)"""
    full = f"{params}&friend_code=eq.{friend_code}" if params else \
           f"friend_code=eq.{friend_code}"
    return _supabase_request("PATCH", table, params=full, payload=payload)


class SharingMixin:
    """社区分类分享 Mixin（上传 + 浏览 + 导入 + 订阅同步）"""

    def _get_sync_engine(self: SharingHost):
        """获取订阅同步引擎（需要 _collections_core 已初始化）"""
        from core_sharing_sync import SharingSyncEngine
        config = self._collections_core.load_config()
        return SharingSyncEngine(config, self.current_account.friend_code)

    # ── 上传流程 ──

    def share_collections_ui(self: SharingHost, preselected=None):
        """打开分享对话框：选择分类 → 填写标题 → 上传到社区"""
        if not _SUPABASE_URL:
            messagebox.showinfo("提示", "社区分享功能尚未配置后端。",
                                parent=self.root)
            return
        cache = getattr(self, '_coll_data_cache', {})
        if not cache:
            messagebox.showwarning("提示", "没有可分享的分类。",
                                   parent=self.root)
            return

        win = tk.Toplevel(self.root)
        win.title("分享我的分类到社区")
        win.transient(self.root)
        win.grab_set()

        # 标题
        tk.Label(win, text="标题：").grid(row=0, column=0, sticky="w",
                                         padx=8, pady=(8, 2))
        title_var = tk.StringVar()
        tk.Entry(win, textvariable=title_var, width=40).grid(
            row=0, column=1, sticky="ew", padx=8, pady=(8, 2))

        # 描述
        tk.Label(win, text="描述（可选）：").grid(row=1, column=0, sticky="w",
                                                padx=8, pady=2)
        desc_var = tk.StringVar()
        tk.Entry(win, textvariable=desc_var, width=40).grid(
            row=1, column=1, sticky="ew", padx=8, pady=2)

        # 分类多选列表（带全选/全不选）
        sel_frame = tk.Frame(win)
        sel_frame.grid(row=2, column=0, columnspan=2, sticky="w", padx=8,
                       pady=(8, 2))
        tk.Label(sel_frame, text="选择要分享的分类：").pack(side="left")
        tk.Button(sel_frame, text="全选", font=("", 9),
                  command=lambda: [v.set(True) for v in check_vars.values()]
                  ).pack(side="left", padx=(8, 2))
        tk.Button(sel_frame, text="全不选", font=("", 9),
                  command=lambda: [v.set(False) for v in check_vars.values()]
                  ).pack(side="left")

        list_frame = tk.Frame(win)
        list_frame.grid(row=3, column=0, columnspan=2, sticky="nsew",
                        padx=8, pady=2)
        win.grid_rowconfigure(3, weight=1)
        win.grid_columnconfigure(1, weight=1)

        pre = set(preselected or [])
        check_vars = {}
        canvas = tk.Canvas(list_frame, height=200)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical",
                                  command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            if event.num == 4 or event.delta > 0:
                canvas.yview_scroll(-3, "units")
            elif event.num == 5 or event.delta < 0:
                canvas.yview_scroll(3, "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Button-4>", _on_mousewheel)
        canvas.bind("<Button-5>", _on_mousewheel)

        for cid, cdata in cache.items():
            if cdata.get('is_dynamic'):
                continue
            var = tk.BooleanVar(value=cid in pre)
            n_games = len(cdata.get('owned_app_ids', []))
            name = cdata.get('name', cid)
            tk.Checkbutton(inner, text=f"{name} ({n_games})",
                           variable=var).pack(anchor="w")
            check_vars[cid] = var

        # 按钮
        btn_frame = tk.Frame(win)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=8)
        self._share_status = tk.StringVar()
        tk.Label(btn_frame, textvariable=self._share_status,
                 fg="gray").pack(side="left", padx=8)
        share_btn = tk.Button(btn_frame, text="分享到社区",
                  command=lambda: self._do_share(
                      win, title_var.get().strip(), desc_var.get().strip(),
                      check_vars, share_btn))
        share_btn.pack(side="right", padx=4)
        def _close_share():
            win.grab_release()
            win.destroy()
        tk.Button(btn_frame, text="取消",
                  command=_close_share).pack(side="right", padx=4)
        win.protocol("WM_DELETE_WINDOW", _close_share)

        self._center_window(win)

    def _do_share(self: SharingHost, win, title, desc, check_vars, btn=None):
        """验证输入 → 构建 payload → 后台上传"""
        selected = [cid for cid, var in check_vars.items() if var.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个分类。", parent=win)
            return
        if not title:
            messagebox.showwarning("提示", "请输入标题。", parent=win)
            return

        if btn:
            btn.config(state=tk.DISABLED)
        payload = self._build_share_payload(title, desc, selected)
        self._share_status.set("正在上传...")

        def _upload():
            try:
                resp = _supabase_post("shared_collections", payload)
                share_id = resp[0]["id"] if resp else None
                try:
                    win.after(0, lambda: self._on_share_done(
                        win, True, btn=btn, share_id=share_id,
                        title=title, col_ids=selected,
                        content_hash=payload.get("content_hash", "")))
                except Exception:
                    pass
            except Exception as e:
                try:
                    win.after(0, lambda: self._on_share_done(
                        win, False, str(e), btn=btn))
                except Exception:
                    pass

        threading.Thread(target=bg_thread(_upload), daemon=True).start()

    def _build_share_payload(self: SharingHost, title, desc, col_ids):
        """从缓存构建上传 payload（含 content_hash）"""
        from core_sharing_sync import SharingSyncEngine
        cache = self._coll_data_cache
        collections = []
        all_games = set()
        for cid in col_ids:
            cdata = cache.get(cid, {})
            aids = [int(a) for a in cdata.get('owned_app_ids', [])
                    if str(a).isdigit()]
            collections.append({"name": cdata.get('name', cid), "added": aids})
            all_games.update(aids)
        return {
            "friend_code": self.current_account.friend_code,
            "persona_name": self.current_account.persona_name,
            "title": title,
            "description": desc,
            "collections": collections,
            "game_count": len(all_games),
            "collection_count": len(collections),
            "content_hash": SharingSyncEngine.compute_content_hash(collections),
        }

    def _on_share_done(self: SharingHost, win, success, error="", btn=None,
                       share_id=None, title="", col_ids=None,
                       content_hash=""):
        """上传完成回调"""
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        if success:
            self._share_status.set("")
            # 记录发布映射（用于自动同步）
            if share_id and self._ensure_collections_core():
                try:
                    engine = self._get_sync_engine()
                    engine.register_published(
                        share_id, title, col_ids or [], content_hash)
                    self._collections_core.save_config()
                except Exception as e:
                    print(f"[sharing] register published failed: {e}")
            messagebox.showinfo("完成", "分享成功！其他用户现在可以浏览你的分类。",
                                parent=win)
            win.grab_release()
            win.destroy()
        else:
            self._share_status.set("")
            if btn:
                btn.config(state=tk.NORMAL)
            messagebox.showerror("上传失败", f"网络错误：{error[:200]}",
                                 parent=win)

    # ── 浏览流程 ──

    def browse_shared_ui(self: SharingHost):
        """打开社区分类浏览对话框"""
        if not _SUPABASE_URL:
            messagebox.showinfo("提示", "社区分享功能尚未配置后端。",
                                parent=self.root)
            return

        win = tk.Toplevel(self.root)
        win.title("浏览社区分类")
        win.transient(self.root)
        win.geometry("600x500")

        # 顶部工具栏
        toolbar = tk.Frame(win)
        toolbar.pack(fill="x", padx=8, pady=(8, 2))
        tk.Button(toolbar, text="刷新",
                  command=lambda: self._fetch_shared_list(win, tree,
                                                         status_var)
                  ).pack(side="left")
        status_var = tk.StringVar(value="正在加载...")
        tk.Label(toolbar, textvariable=status_var, fg="gray").pack(
            side="right")

        # 列表区（带滚动条）
        tree_frame = tk.Frame(win)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("author", "title", "info", "date")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                            height=10)
        tree.heading("author", text="作者")
        tree.heading("title", text="标题")
        tree.heading("info", text="分类/游戏数")
        tree.heading("date", text="日期")
        tree.column("author", width=100, stretch=False)
        tree.column("title", width=200)
        tree.column("info", width=100, stretch=False)
        tree.column("date", width=85, stretch=False)
        tree_sb = ttk.Scrollbar(tree_frame, orient="vertical",
                                command=tree.yview)
        tree.configure(yscrollcommand=tree_sb.set)
        tree.pack(side="left", fill="both", expand=True)
        tree_sb.pack(side="right", fill="y")

        # 详情区（固定高度，可滚动）
        detail_frame = tk.LabelFrame(win, text="详情")
        detail_frame.pack(fill="x", padx=8, pady=(0, 4))
        self._browse_detail_text = tk.Text(
            detail_frame, height=5, wrap=tk.WORD, font=("", 10),
            state=tk.DISABLED, relief=tk.FLAT, bg=detail_frame.cget("bg"))
        self._browse_detail_text.pack(fill="x", padx=4, pady=4)
        self._set_browse_detail("选择一个条目查看详情")

        # 底部按钮
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))

        # 左侧：仅对自己的条目可用
        del_btn = tk.Button(btn_frame, text="删除", state=tk.DISABLED,
                            command=lambda: self._delete_selected_share(
                                win, tree, status_var, del_btn))
        del_btn.pack(side="left", padx=4)
        edit_btn = tk.Button(btn_frame, text="编辑", state=tk.DISABLED,
                             command=lambda: self._edit_selected_share(
                                 win, tree, status_var))
        edit_btn.pack(side="left", padx=4)
        unsub_btn = tk.Button(btn_frame, text="取消订阅", state=tk.DISABLED,
                              command=lambda: self._unsub_selected(
                                  win, tree, unsub_btn))
        unsub_btn.pack(side="left", padx=4)

        # 右侧：通用操作
        tk.Button(btn_frame, text="导入选中分类",
                  command=lambda: self._import_selected_share(
                      win, tree)).pack(side="right", padx=4)
        tk.Button(btn_frame, text="关闭",
                  command=win.destroy).pack(side="right", padx=4)

        # 数据缓存 + 按钮引用
        self._browse_data_cache = {}
        self._browse_owner_btns = (del_btn, edit_btn)
        self._browse_unsub_btn = unsub_btn
        tree.bind("<<TreeviewSelect>>",
                  lambda e: self._on_browse_select(tree))

        self._center_window(win)
        self._fetch_shared_list(win, tree, status_var)

    def _fetch_shared_list(self: SharingHost, win, tree, status_var):
        """后台获取社区分类列表（不含 collections JSONB）"""
        def _fetch():
            try:
                rows = _supabase_get(
                    "shared_collections",
                    "select=id,friend_code,persona_name,title,description,"
                    "collection_count,game_count,created_at"
                    "&order=created_at.desc&limit=50")
                try:
                    win.after(0, lambda: self._populate_browse_tree(
                        tree, status_var, rows))
                except Exception:
                    pass
            except Exception as e:
                try:
                    win.after(0, lambda: status_var.set(f"加载失败: {e}"))
                except Exception:
                    pass

        status_var.set("正在加载...")
        threading.Thread(target=bg_thread(_fetch), daemon=True).start()

    def _populate_browse_tree(self: SharingHost, tree, status_var, rows):
        """填充浏览列表"""
        try:
            if not tree.winfo_exists():
                return
        except Exception:
            return
        tree.delete(*tree.get_children())
        self._browse_data_cache.clear()
        my_code = getattr(self.current_account, 'friend_code', '')
        tree.tag_configure("mine", foreground="#2196f3")
        for row in rows:
            rid = row.get("id", "")
            date_str = row.get("created_at", "")[:10]
            cc = row.get("collection_count", 0)
            gc = row.get("game_count", 0)
            is_mine = row.get("friend_code", "") == my_code
            author = row.get("persona_name", "")
            if is_mine:
                author = f"★ {author}"
            tree.insert("", "end", iid=rid, values=(
                author, row.get("title", ""),
                f"{cc} 分类 / {gc} 游戏", date_str),
                tags=("mine",) if is_mine else ())
            self._browse_data_cache[rid] = row
        if rows:
            status_var.set(f"共 {len(rows)} 条")
        else:
            status_var.set("暂无分享，快来成为第一个吧")

    def _on_browse_select(self: SharingHost, tree):
        """选中条目 → 显示详情（懒加载完整数据）+ 所有权按钮切换"""
        sel = tree.selection()
        if not sel:
            return
        rid = sel[0]
        row = self._browse_data_cache.get(rid, {})

        # 所有权判断 → 启用/禁用 删除/编辑
        is_mine = (row.get("friend_code", "") ==
                   getattr(self.current_account, 'friend_code', ''))
        for btn in getattr(self, '_browse_owner_btns', ()):
            try:
                btn.config(state=tk.NORMAL if is_mine else tk.DISABLED)
            except Exception:
                pass

        # 已有完整数据（含 collections）则直接显示
        if "collections" in row:
            self._show_share_detail(row)
            return

        # 否则后台获取完整数据
        self._set_browse_detail("正在加载详情...")

        def _fetch_detail():
            try:
                full = _supabase_get(
                    "shared_collections", f"id=eq.{rid}&limit=1")
                if full:
                    self._browse_data_cache[rid] = full[0]
                    try:
                        tree.after(0, lambda: self._show_share_detail(full[0]))
                    except Exception:
                        pass
            except Exception as e:
                try:
                    tree.after(0, lambda: self._set_browse_detail(
                        f"加载失败: {e}"))
                except Exception:
                    pass

        threading.Thread(target=bg_thread(_fetch_detail), daemon=True).start()

    def _set_browse_detail(self: SharingHost, text):
        """设置详情区文本（Text widget）"""
        try:
            w = self._browse_detail_text
            if not w.winfo_exists():
                return
        except Exception:
            return
        w.config(state=tk.NORMAL)
        w.delete("1.0", tk.END)
        w.insert("1.0", text)
        w.config(state=tk.DISABLED)

    def _show_share_detail(self: SharingHost, row):
        """显示分享条目的详情（含订阅/发布状态）"""
        try:
            if not self._browse_detail_text.winfo_exists():
                return
        except Exception:
            return
        colls = row.get("collections", [])
        desc = row.get("description", "")
        rid = row.get("id", "")
        lines = []
        if desc:
            lines.append(f"描述：{desc}")
        lines.append(f"作者：{row.get('persona_name', '')}  "
                     f"(ID: {row.get('friend_code', '')})")
        lines.append("")
        for c in colls:
            n = len(c.get("added", []))
            lines.append(f"  - {c.get('name', '?')} ({n} 游戏)")
        # 订阅/发布状态
        if rid and self._ensure_collections_core():
            try:
                engine = self._get_sync_engine()
                if engine.is_subscribed(rid):
                    import time as _time
                    subs = engine.get_subscriptions()
                    ts = subs.get(rid, {}).get("last_synced", 0)
                    date = _time.strftime("%Y-%m-%d", _time.localtime(ts)) if ts else "未知"
                    lines.append(f"\n已订阅 | 上次同步: {date}")
                elif engine.is_published(rid):
                    lines.append(f"\n我的发布 | 自动同步中")
            except Exception:
                pass
        # 更新取消订阅按钮状态
        self._update_unsub_btn(rid)
        self._set_browse_detail("\n".join(lines))

    def _import_selected_share(self: SharingHost, win, tree):
        """导入选中条目的所有分类（可选订阅）"""
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个条目。", parent=win)
            return
        rid = sel[0]
        row = self._browse_data_cache.get(rid, {})
        colls = row.get("collections", [])
        if not colls:
            messagebox.showinfo("提示", "分类数据正在加载中，请稍候再试。",
                                parent=win)
            return

        # 自己的分享不能订阅
        is_mine = (row.get("friend_code", "") ==
                   getattr(self.current_account, 'friend_code', ''))

        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return

        # 收集所有游戏 ID，一次性询问过滤
        all_aids = set()
        parsed_colls = []
        for c in colls:
            name = c.get("name", "社区导入")
            aids = [int(a) for a in c.get("added", []) if str(a).isdigit()]
            if aids:
                parsed_colls.append((name, aids))
                all_aids.update(aids)
        if not parsed_colls:
            messagebox.showinfo("提示", "没有可导入的分类。", parent=win)
            return

        # 订阅复选框（非自己的分享才显示）
        subscribe = not is_mine and messagebox.askyesno(
            "订阅同步",
            "是否订阅此分享？\n订阅后，分享者更新分类时你会自动同步。",
            parent=win)

        owned_filter = self._ask_filter_owned(list(all_aids), parent=win)
        if owned_filter is None:
            return  # 用户取消
        owned_set = set(owned_filter)

        imported = 0
        col_mapping = {}  # remote_name → local_col_id
        for name, aids in parsed_colls:
            filtered = [a for a in aids if a in owned_set]
            if not filtered:
                continue
            new_id = self._collections_core.add_static_collection(
                data, name, filtered)
            col_mapping[name] = new_id
            # 绑定来源（SSOT：统一用 source binding 标记来源）
            author = row.get('persona_name', '社区')
            self._collections_core.save_collection_source(
                new_id, "community_share",
                {"share_id": rid, "collection_name": name},
                f"社区: {author}",
                "replace")
            imported += 1

        if imported:
            author = row.get('persona_name', '社区')
            self._save_and_sync(
                data, backup_description=f"从社区导入 {author} 的分类")
            self._ui_refresh()
            # 记录订阅关系
            if subscribe:
                try:
                    engine = self._get_sync_engine()
                    engine.subscribe(
                        rid, row.get("title", ""),
                        author, col_mapping,
                        row.get("content_hash", ""))
                    self._collections_core.save_config()
                except Exception as e:
                    print(f"[sharing] subscribe failed: {e}")
            msg = f"已导入 {imported} 个分类。"
            if subscribe:
                msg += "\n已订阅，启动时将自动同步更新。"
            messagebox.showinfo("完成", msg, parent=win)
        else:
            messagebox.showinfo("提示", "没有可导入的分类。", parent=win)

    # ── 删除/编辑流程 ──

    def _delete_selected_share(self: SharingHost, win, tree, status_var,
                               btn=None):
        """删除自己的分享条目"""
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个条目。", parent=win)
            return
        rid = sel[0]
        row = self._browse_data_cache.get(rid, {})
        title = row.get("title", "")
        if not messagebox.askyesno(
                "确认删除", f"确定要删除「{title}」吗？\n此操作不可撤销。",
                parent=win):
            return

        if btn:
            btn.config(state=tk.DISABLED)
        my_code = self.current_account.friend_code

        def _do_delete():
            try:
                _supabase_delete("shared_collections",
                                 f"id=eq.{rid}", my_code)
                try:
                    win.after(0, lambda: self._on_delete_done(
                        win, tree, status_var, rid, True))
                except Exception:
                    pass
            except Exception as e:
                try:
                    win.after(0, lambda: self._on_delete_done(
                        win, tree, status_var, rid, False, str(e)))
                except Exception:
                    pass

        threading.Thread(target=bg_thread(_do_delete), daemon=True).start()

    def _on_delete_done(self: SharingHost, win, tree, status_var,
                        rid, success, error=""):
        """删除完成回调"""
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        if success:
            self._browse_data_cache.pop(rid, None)
            try:
                tree.delete(rid)
            except Exception:
                pass
            # 清理发布映射
            if self._ensure_collections_core():
                try:
                    engine = self._get_sync_engine()
                    engine.remove_published(rid)
                    self._collections_core.save_config()
                except Exception:
                    pass
            n = len(tree.get_children())
            status_var.set(f"共 {n} 条" if n else "暂无分享，快来成为第一个吧")
            self._set_browse_detail("已删除")
        else:
            messagebox.showerror("删除失败", f"网络错误：{error[:200]}",
                                 parent=win)

    def _edit_selected_share(self: SharingHost, win, tree, status_var):
        """编辑自己的分享条目（标题/描述）"""
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个条目。", parent=win)
            return
        rid = sel[0]
        row = self._browse_data_cache.get(rid, {})

        edit_win = tk.Toplevel(win)
        edit_win.title("编辑分享")
        edit_win.transient(win)
        edit_win.grab_set()

        tk.Label(edit_win, text="标题：").grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        title_var = tk.StringVar(value=row.get("title", ""))
        tk.Entry(edit_win, textvariable=title_var, width=40).grid(
            row=0, column=1, sticky="ew", padx=8, pady=(8, 2))

        tk.Label(edit_win, text="描述：").grid(
            row=1, column=0, sticky="w", padx=8, pady=2)
        desc_var = tk.StringVar(value=row.get("description", ""))
        tk.Entry(edit_win, textvariable=desc_var, width=40).grid(
            row=1, column=1, sticky="ew", padx=8, pady=2)
        edit_win.grid_columnconfigure(1, weight=1)

        btn_frame = tk.Frame(edit_win)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
        status_label = tk.Label(btn_frame, text="", fg="gray")
        status_label.pack(side="left", padx=8)

        save_btn = tk.Button(
            btn_frame, text="保存",
            command=lambda: self._do_edit_share(
                edit_win, win, tree, status_var, rid,
                title_var.get().strip(), desc_var.get().strip(),
                save_btn, status_label))
        save_btn.pack(side="right", padx=4)

        def _close():
            edit_win.grab_release()
            edit_win.destroy()
        tk.Button(btn_frame, text="取消", command=_close).pack(
            side="right", padx=4)
        edit_win.protocol("WM_DELETE_WINDOW", _close)
        self._center_window(edit_win)

    def _do_edit_share(self: SharingHost, edit_win, browse_win, tree,
                       status_var, rid, title, desc, btn, status_label):
        """验证 → 后台 PATCH → 刷新"""
        if not title:
            messagebox.showwarning("提示", "标题不能为空。", parent=edit_win)
            return
        btn.config(state=tk.DISABLED)
        status_label.config(text="正在保存...")
        my_code = self.current_account.friend_code
        payload = {"title": title, "description": desc}

        def _do_patch():
            try:
                _supabase_patch("shared_collections",
                                f"id=eq.{rid}", payload, my_code)
                try:
                    edit_win.after(0, lambda: self._on_edit_done(
                        edit_win, browse_win, tree, status_var,
                        rid, title, desc, True))
                except Exception:
                    pass
            except Exception as e:
                try:
                    edit_win.after(0, lambda: self._on_edit_done(
                        edit_win, browse_win, tree, status_var,
                        rid, title, desc, False, str(e),
                        btn=btn, status_label=status_label))
                except Exception:
                    pass

        threading.Thread(target=bg_thread(_do_patch), daemon=True).start()

    def _on_edit_done(self: SharingHost, edit_win, browse_win, tree,
                      status_var, rid, title, desc, success, error="",
                      btn=None, status_label=None):
        """编辑完成回调"""
        try:
            if not edit_win.winfo_exists():
                return
        except Exception:
            return
        if success:
            # 更新本地缓存
            if rid in self._browse_data_cache:
                self._browse_data_cache[rid]["title"] = title
                self._browse_data_cache[rid]["description"] = desc
            # 更新 Treeview 中的标题列
            try:
                if tree.winfo_exists():
                    old_vals = list(tree.item(rid, "values"))
                    old_vals[1] = title  # title 是第2列
                    tree.item(rid, values=old_vals)
            except Exception:
                pass
            edit_win.grab_release()
            edit_win.destroy()
            messagebox.showinfo("完成", "修改已保存。", parent=browse_win)
        else:
            if status_label:
                status_label.config(text="")
            if btn:
                btn.config(state=tk.NORMAL)
            messagebox.showerror("保存失败", f"网络错误：{error[:200]}",
                                 parent=edit_win)

    # ── 订阅管理 ──

    def _update_unsub_btn(self: SharingHost, rid):
        """根据订阅状态启用/禁用取消订阅按钮"""
        btn = getattr(self, '_browse_unsub_btn', None)
        if not btn:
            return
        try:
            if not btn.winfo_exists():
                return
        except Exception:
            return
        subscribed = False
        if rid and self._ensure_collections_core():
            try:
                engine = self._get_sync_engine()
                subscribed = engine.is_subscribed(rid)
            except Exception:
                pass
        btn.config(state=tk.NORMAL if subscribed else tk.DISABLED)

    def _unsub_selected(self: SharingHost, win, tree, btn):
        """取消订阅选中条目"""
        sel = tree.selection()
        if not sel:
            return
        rid = sel[0]
        row = self._browse_data_cache.get(rid, {})
        title = row.get("title", "")
        if not messagebox.askyesno(
                "取消订阅",
                f"确定取消订阅「{title}」吗？\n本地分类将保留，但不再自动同步。",
                parent=win):
            return
        if self._ensure_collections_core():
            try:
                engine = self._get_sync_engine()
                engine.unsubscribe(rid)
                self._collections_core.save_config()
                btn.config(state=tk.DISABLED)
                # 刷新详情
                self._show_share_detail(row)
                messagebox.showinfo("完成", "已取消订阅。", parent=win)
            except Exception as e:
                messagebox.showerror("错误", f"取消订阅失败：{e}",
                                     parent=win)

    # ── 后台同步（启动时调用） ──

    def _sync_published_shares_bg(self: SharingHost):
        """分享者：检测本地分类变动 → 自动 PATCH 到 Supabase"""
        if not self._ensure_collections_core():
            return
        cache = getattr(self, '_coll_data_cache', {})
        if not cache:
            return
        engine = self._get_sync_engine()
        published = engine.get_published()
        if not published:
            return

        def _sync():
            synced = 0
            for share_id in list(published):
                result = engine.build_updated_payload(share_id, cache)
                if result is None:
                    continue
                payload, new_hash = result
                if engine.upload_share_update(share_id, payload, new_hash):
                    synced += 1
            if synced:
                try:
                    self.root.after(0, lambda: self._collections_core.save_config())
                except Exception:
                    pass
                print(f"[sharing-sync] uploaded {synced} share(s)")

        threading.Thread(target=bg_thread(_sync), daemon=True).start()

    def _sync_subscriptions_bg(self: SharingHost):
        """订阅者：拉取远端更新 → 自动更新本地分类"""
        if not self._ensure_collections_core():
            return
        engine = self._get_sync_engine()
        subs = engine.get_subscriptions()
        if not subs:
            return

        def _sync():
            changed = engine.check_subscription_updates()
            if not changed:
                return
            data = self._collections_core.load_json()
            if data is None:
                return
            total_updated = 0
            total_added = 0
            removed_subs = []
            for share_id, remote_hash in changed:
                if remote_hash is None:
                    # 分享已被删除 → 移除订阅
                    engine.unsubscribe(share_id)
                    removed_subs.append(share_id)
                    continue
                row = engine.fetch_share_data(share_id)
                if not row:
                    continue
                remote_colls = row.get("collections", [])
                if not remote_colls:
                    continue
                updated, added, new_mapping = engine.apply_update(
                    share_id, remote_colls, self._collections_core, data)
                total_updated += updated
                total_added += added
                # 更新订阅记录
                sub_info = engine.get_subscriptions().get(share_id, {})
                engine.subscribe(
                    share_id, sub_info.get("title", ""),
                    sub_info.get("author", ""), new_mapping,
                    row.get("content_hash", ""))
            if total_updated or total_added or removed_subs:
                if total_updated or total_added:
                    self._save_and_sync(data, backup_description="订阅同步更新")
                self._collections_core.save_config()
                def _refresh():
                    self._ui_refresh()
                try:
                    self.root.after(0, _refresh)
                except Exception:
                    pass
                print(f"[sharing-sync] subscriptions: "
                      f"{total_updated} updated, {total_added} added, "
                      f"{len(removed_subs)} removed")

        threading.Thread(target=bg_thread(_sync), daemon=True).start()
