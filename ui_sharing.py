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
import urllib.error
from tkinter import messagebox, ttk, simpledialog

from ui_utils import bg_thread
import utils

if TYPE_CHECKING:
    from _protocols import SharingHost

# ── Supabase 配置（创建项目后替换） ──
_SUPABASE_URL = ""
_SUPABASE_ANON_KEY = ""


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


class SharingMixin:
    """社区分类分享 Mixin（上传 + 浏览 + 导入）"""

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

        # 分类多选列表
        tk.Label(win, text="选择要分享的分类：").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))

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
        tk.Button(btn_frame, text="分享到社区",
                  command=lambda: self._do_share(
                      win, title_var.get().strip(), desc_var.get().strip(),
                      check_vars)).pack(side="right", padx=4)
        tk.Button(btn_frame, text="取消",
                  command=win.destroy).pack(side="right", padx=4)

        self._center_window(win)

    def _do_share(self: SharingHost, win, title, desc, check_vars):
        """验证输入 → 构建 payload → 后台上传"""
        selected = [cid for cid, var in check_vars.items() if var.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个分类。", parent=win)
            return
        if not title:
            messagebox.showwarning("提示", "请输入标题。", parent=win)
            return

        payload = self._build_share_payload(title, desc, selected)
        self._share_status.set("正在上传...")

        def _upload():
            try:
                _supabase_post("shared_collections", payload)
                win.after(0, lambda: self._on_share_done(win, True))
            except Exception as e:
                win.after(0, lambda: self._on_share_done(win, False, str(e)))

        threading.Thread(target=bg_thread(_upload), daemon=True).start()

    def _build_share_payload(self: SharingHost, title, desc, col_ids):
        """从缓存构建上传 payload"""
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
        }

    def _on_share_done(self: SharingHost, win, success, error=""):
        """上传完成回调"""
        if success:
            self._share_status.set("")
            messagebox.showinfo("完成", "分享成功！其他用户现在可以浏览你的分类。",
                                parent=win)
            win.destroy()
        else:
            self._share_status.set("")
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

        # 列表区
        cols = ("author", "title", "info", "date")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=10)
        tree.heading("author", text="作者")
        tree.heading("title", text="标题")
        tree.heading("info", text="分类/游戏数")
        tree.heading("date", text="日期")
        tree.column("author", width=100, stretch=False)
        tree.column("title", width=200)
        tree.column("info", width=100, stretch=False)
        tree.column("date", width=85, stretch=False)
        tree.pack(fill="both", expand=True, padx=8, pady=4)

        # 详情区
        detail_frame = tk.LabelFrame(win, text="详情")
        detail_frame.pack(fill="x", padx=8, pady=(0, 4))
        self._browse_detail_label = tk.Label(
            detail_frame, text="选择一个条目查看详情", anchor="w",
            justify="left", wraplength=560)
        self._browse_detail_label.pack(fill="x", padx=4, pady=4)

        # 底部按钮
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(btn_frame, text="导入选中分类",
                  command=lambda: self._import_selected_share(
                      win, tree)).pack(side="right", padx=4)
        tk.Button(btn_frame, text="关闭",
                  command=win.destroy).pack(side="right", padx=4)

        # 数据缓存
        self._browse_data_cache = {}
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
                win.after(0, lambda: self._populate_browse_tree(
                    tree, status_var, rows))
            except Exception as e:
                win.after(0, lambda: status_var.set(f"加载失败: {e}"))

        status_var.set("正在加载...")
        threading.Thread(target=bg_thread(_fetch), daemon=True).start()

    def _populate_browse_tree(self: SharingHost, tree, status_var, rows):
        """填充浏览列表"""
        tree.delete(*tree.get_children())
        self._browse_data_cache.clear()
        for row in rows:
            rid = row.get("id", "")
            date_str = row.get("created_at", "")[:10]
            cc = row.get("collection_count", 0)
            gc = row.get("game_count", 0)
            tree.insert("", "end", iid=rid, values=(
                row.get("persona_name", ""),
                row.get("title", ""),
                f"{cc} 分类 / {gc} 游戏",
                date_str))
            self._browse_data_cache[rid] = row
        status_var.set(f"共 {len(rows)} 条")

    def _on_browse_select(self: SharingHost, tree):
        """选中条目 → 显示详情（懒加载完整数据）"""
        sel = tree.selection()
        if not sel:
            return
        rid = sel[0]
        row = self._browse_data_cache.get(rid, {})

        # 已有完整数据（含 collections）则直接显示
        if "collections" in row:
            self._show_share_detail(row)
            return

        # 否则后台获取完整数据
        self._browse_detail_label.config(text="正在加载详情...")

        def _fetch_detail():
            try:
                full = _supabase_get(
                    "shared_collections", f"id=eq.{rid}&limit=1")
                if full:
                    self._browse_data_cache[rid] = full[0]
                    tree.after(0, lambda: self._show_share_detail(full[0]))
            except Exception as e:
                tree.after(0, lambda: self._browse_detail_label.config(
                    text=f"加载失败: {e}"))

        threading.Thread(target=bg_thread(_fetch_detail), daemon=True).start()

    def _show_share_detail(self: SharingHost, row):
        """显示分享条目的详情"""
        colls = row.get("collections", [])
        desc = row.get("description", "")
        lines = []
        if desc:
            lines.append(f"描述：{desc}")
        lines.append(f"作者：{row.get('persona_name', '')}  "
                     f"(ID: {row.get('friend_code', '')})")
        lines.append("")
        for c in colls:
            n = len(c.get("added", []))
            lines.append(f"  - {c.get('name', '?')} ({n} 游戏)")
        self._browse_detail_label.config(text="\n".join(lines))

    def _import_selected_share(self: SharingHost, win, tree):
        """导入选中条目的所有分类"""
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个条目。", parent=win)
            return
        row = self._browse_data_cache.get(sel[0], {})
        colls = row.get("collections", [])
        if not colls:
            messagebox.showwarning("提示", "该条目无分类数据，请先点击查看详情。",
                                   parent=win)
            return

        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return

        imported = 0
        for c in colls:
            name = c.get("name", "社区导入")
            aids = [int(a) for a in c.get("added", []) if str(a).isdigit()]
            if not aids:
                continue
            # 可选：过滤非入库游戏
            filtered = self._ask_filter_owned(aids, parent=win)
            if filtered is None:
                return  # 用户取消
            self._collections_core.add_static_collection(data, name, filtered)
            imported += 1

        if imported:
            author = row.get('persona_name', '社区')
            self._save_and_sync(
                data, backup_description=f"从社区导入 {author} 的分类")
            self._ui_refresh()
            messagebox.showinfo("完成",
                f"已导入 {imported} 个分类。", parent=win)
        else:
            messagebox.showinfo("提示", "没有可导入的分类。", parent=win)
