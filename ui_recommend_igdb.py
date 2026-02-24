"""ui_recommend_igdb.py — IGDB 多维度分类面板

从 ui_recommend.py 拆分。包含 IGDB 标签页创建、加载、搜索、缓存管理。
"""

import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from ui_utils import bg_thread


class IGDBState:
    """IGDB 面板的共享状态"""
    __slots__ = (
        'check_vars', 'loaded_dims', 'tab_widgets', 'notebook',
        'configured', 'force_refresh', 'rec_win',
        'company_tree', 'company_tree_iids', 'company_slugs',
        'ui_ctx',
    )

    def __init__(self):
        self.check_vars = {}       # {key: (BooleanVar, src_type, url_or_id, name)}
        self.loaded_dims = {}      # {dim_key: True}
        self.tab_widgets = {}      # {dim_key: {tree, search_var, ...}}
        self.notebook = None       # ttk.Notebook
        self.configured = False    # IGDB API 凭证是否已配置
        self.force_refresh = [False]
        self.rec_win = None        # Toplevel 窗口
        self.company_tree = None
        self.company_tree_iids = []
        self.company_slugs = {}
        self.ui_ctx = {}           # late-bound: is_fetching, btn_widgets, status_var, etc.


def build_igdb_panel(app, state, parent_frame):
    """构建整个 IGDB 右栏面板，返回 IGDBState。"""
    igdb_frame = tk.LabelFrame(parent_frame,
        text="\U0001f5c2\ufe0f IGDB 游戏数据库分类",
        font=("微软雅黑", 10, "bold"), padx=10, pady=5)
    igdb_frame.pack(fill="both", expand=True)

    # IGDB 凭证状态
    igdb_status_frame = tk.Frame(igdb_frame)
    igdb_status_frame.pack(fill="x", pady=(0, 5))

    igdb_client_id, igdb_client_secret = \
        app._collections_core.get_igdb_credentials()
    state.configured = bool(igdb_client_id and igdb_client_secret)

    if state.configured:
        igdb_status_label = tk.Label(igdb_status_frame,
            text="\U0001f510 已配置 IGDB API 凭证",
            font=("微软雅黑", 8), fg="green")
    else:
        igdb_status_label = tk.Label(igdb_status_frame,
            text="⚠\ufe0f 未配置 IGDB API 凭证，无法使用此功能",
            font=("微软雅黑", 8), fg="orange")
    igdb_status_label.pack(side="left")

    if not state.configured:
        tk.Label(igdb_status_frame,
            text=" → 可在主界面「🎮 管理 IGDB API 凭证」中配置",
            font=("微软雅黑", 8), fg="#888").pack(side="left")

    # ---- 标签页容器 ----
    state.notebook = ttk.Notebook(igdb_frame)
    state.notebook.pack(fill="both", expand=True, pady=(0, 5))

    # 创建所有维度标签页
    for dim_key, dim_info in app._collections_core.IGDB_DIMENSIONS.items():
        _create_igdb_tab(app, state, dim_key, dim_info)

    # 开发商/发行商搜索标签页
    _build_company_tab(app, state)

    # IGDB 按钮区域
    igdb_btn_frame = tk.Frame(igdb_frame)
    igdb_btn_frame.pack(fill="x", pady=(5, 0))

    ttk.Button(igdb_btn_frame, text="☑\ufe0f 全选当前页",
               command=lambda: select_all_igdb(app, state),
               ).pack(side="left", padx=(0, 5))
    ttk.Button(igdb_btn_frame, text="☐ 取消全选当前页",
               command=lambda: deselect_all_igdb(app, state),
               ).pack(side="left", padx=(0, 5))
    ttk.Button(igdb_btn_frame, text="🔄 重新下载 IGDB 数据",
               command=lambda: force_rescan_igdb(app, state),
               state="normal" if state.configured else "disabled"
               ).pack(side="left")

    # 缓存状态信息
    igdb_cache_var = tk.StringVar()
    igdb_cache_label = tk.Label(igdb_frame,
        textvariable=igdb_cache_var,
        font=("微软雅黑", 8), fg="#666")
    igdb_cache_label.pack(anchor="w", pady=(3, 0))
    state.ui_ctx['igdb_cache_var'] = igdb_cache_var
    state.ui_ctx['igdb_cache_label'] = igdb_cache_label

    refresh_igdb_cache_status(app, state)

    tk.Label(igdb_frame,
        text="💡 首次使用时会自动从 IGDB 下载所有 Steam 游戏的"
             "分类数据（约 5-8 分钟），之后筛选均为本地秒查",
        font=("微软雅黑", 8), fg="#666", wraplength=400,
        justify="left").pack(anchor="w", pady=(3, 0))

    if state.configured:
        state.rec_win.after(200, lambda: load_all_igdb_tabs(app, state))

    return state


def _create_igdb_tab(app, state, dim_key, dim_info):
    """创建单个维度的标签页"""
    tab_frame = tk.Frame(state.notebook)
    state.notebook.add(tab_frame, text=dim_info["label"])

    search_frame = tk.Frame(tab_frame)
    search_frame.pack(fill="x", padx=5, pady=(5, 3))
    tk.Label(search_frame, text="🔍",
             font=("微软雅黑", 9)).pack(side="left")
    search_var = tk.StringVar()
    search_entry = tk.Entry(search_frame, textvariable=search_var,
                            font=("微软雅黑", 9))
    search_entry.pack(side="left", fill="x", expand=True,
                      padx=(3, 0))

    tree_frame = tk.Frame(tab_frame)
    tree_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

    style = ttk.Style()
    style_name = f"IGDB_{dim_key}.Treeview"
    style.configure(style_name, rowheight=24,
                    font=("微软雅黑", 9))

    tree = ttk.Treeview(tree_frame, columns=("name", "link"),
                        show="headings", selectmode="none",
                        style=style_name)
    tree.heading("name", text="分类名称", anchor="w")
    tree.column("name", stretch=True, anchor="w")
    tree.heading("link", text="", anchor="center")
    tree.column("link", width=36, stretch=False, anchor="center")

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical",
                               command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    item_slugs = {}
    all_iids = []
    iid_name_map = {}
    detached_iids = set()

    def on_tree_click(event):
        region = tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        iid = tree.identify_row(event.y)
        if not iid or iid.startswith("_"):
            return
        col = tree.identify_column(event.x)

        if col == "#2":
            slug = item_slugs.get(iid, "")
            url_path = app._collections_core.IGDB_URL_PATHS.get(
                dim_key, dim_key)
            if slug:
                webbrowser.open(
                    f"https://www.igdb.com/{url_path}/{slug}")
            return

        key = iid
        if key in state.check_vars:
            var = state.check_vars[key][0]
            new_val = not var.get()
            var.set(new_val)
            current_text = tree.item(iid, "values")[0]
            idx = 0
            try:
                idx = tree.index(iid)
            except Exception:
                pass
            if new_val:
                new_text = current_text.replace("☐", "☑", 1)
                tag = ("even_checked" if idx % 2 == 0
                       else "checked")
            else:
                new_text = current_text.replace("☑", "☐", 1)
                tag = "even" if idx % 2 == 0 else "unchecked"
            tree.item(iid,
                values=(new_text, tree.item(iid, "values")[1]),
                tags=(tag,))

    tree.bind("<ButtonRelease-1>", on_tree_click)

    def block_separator(event, _tree=tree):
        if _tree.identify_region(event.x, event.y) == "separator":
            return "break"
    tree.bind("<Button-1>", block_separator)

    tree.tag_configure("checked", background="#d4edda")
    tree.tag_configure("unchecked", background="")
    tree.tag_configure("even", background="#f8f8f8")
    tree.tag_configure("even_checked", background="#c3e6cb")

    tree.insert("", "end", iid="_placeholder",
                values=("正在加载分类列表...", ""),
                tags=("unchecked",))

    state.tab_widgets[dim_key] = {
        "tree": tree,
        "search_var": search_var,
        "search_entry": search_entry,
        "tab_frame": tab_frame,
        "item_slugs": item_slugs,
        "all_iids": all_iids,
        "iid_name_map": iid_name_map,
        "detached_iids": detached_iids,
    }

    def on_search_changed(*args, _dim_key=dim_key):
        tw = state.tab_widgets[_dim_key]
        query = tw["search_var"].get().strip().lower()
        _tree = tw["tree"]
        _all_iids = tw["all_iids"]
        _iid_name_map = tw["iid_name_map"]
        _detached = tw["detached_iids"]

        if query == "":
            for iid in list(_detached):
                _tree.reattach(iid, "", "end")
            _detached.clear()
            for idx, iid in enumerate(_all_iids):
                _tree.move(iid, "", idx)
        else:
            for iid in _all_iids:
                name_lower = _iid_name_map.get(iid, "")
                if query in name_lower:
                    if iid in _detached:
                        _tree.reattach(iid, "", "end")
                        _detached.discard(iid)
                else:
                    if iid not in _detached:
                        _tree.detach(iid)
                        _detached.add(iid)

    search_var.trace_add("write", on_search_changed)


def _build_company_tab(app, state):
    """构建开发商/发行商搜索标签页"""
    company_tab_frame = tk.Frame(state.notebook)
    state.notebook.add(company_tab_frame, text="🏢 开发商/发行商")

    company_search_frame = tk.Frame(company_tab_frame)
    company_search_frame.pack(fill="x", padx=5, pady=(5, 3))
    tk.Label(company_search_frame, text="🔍",
             font=("微软雅黑", 9)).pack(side="left")
    company_search_var = tk.StringVar()
    company_search_entry = tk.Entry(company_search_frame,
        textvariable=company_search_var, font=("微软雅黑", 9))
    company_search_entry.pack(side="left", fill="x", expand=True,
                              padx=(3, 5))

    company_tree_frame = tk.Frame(company_tab_frame)
    company_tree_frame.pack(fill="both", expand=True, padx=5,
                            pady=(0, 5))

    style = ttk.Style()
    style.configure("IGDB_company.Treeview", rowheight=24,
                    font=("微软雅黑", 9))

    company_tree = ttk.Treeview(company_tree_frame,
        columns=("name", "link"), show="headings",
        selectmode="none", style="IGDB_company.Treeview")
    company_tree.heading("name", text="公司名称", anchor="w")
    company_tree.column("name", stretch=True, anchor="w")
    company_tree.heading("link", text="", anchor="center")
    company_tree.column("link", width=36, stretch=False,
                        anchor="center")

    company_tree_scrollbar = ttk.Scrollbar(company_tree_frame,
        orient="vertical", command=company_tree.yview)
    company_tree.configure(
        yscrollcommand=company_tree_scrollbar.set)

    company_tree.pack(side="left", fill="both", expand=True)
    company_tree_scrollbar.pack(side="right", fill="y")

    company_tree.tag_configure("checked", background="#d4edda")
    company_tree.tag_configure("unchecked", background="")
    company_tree.tag_configure("even", background="#f8f8f8")
    company_tree.tag_configure("even_checked", background="#c3e6cb")

    state.company_tree = company_tree
    state.company_tree_iids = []
    state.company_slugs = {}

    def do_search_company():
        query = company_search_var.get().strip()
        if not query or len(query) < 2:
            messagebox.showwarning("提示",
                "请输入至少 2 个字符进行搜索。", parent=state.rec_win)
            return
        if not state.configured:
            messagebox.showwarning("提示",
                "请先在主界面配置 IGDB API 凭证。", parent=state.rec_win)
            return

        for iid in company_tree.get_children():
            company_tree.delete(iid)
        state.company_tree_iids.clear()
        for k in list(state.check_vars.keys()):
            if k.startswith("igdb_company_"):
                del state.check_vars[k]

        company_tree.insert("", "end", iid="_loading",
            values=(f"正在搜索 \"{query}\"...", ""),
            tags=("unchecked",))

        def search_thread():
            try:
                companies, error = \
                    app._collections_core.search_igdb_companies(query)
                company_counts = {}
                if companies and not error:
                    try:
                        cids = [c.get('id') for c in companies
                                if c.get('id')]
                        company_counts = \
                            app._collections_core.count_igdb_company_steam_games(cids)
                    except Exception as e:
                        print(f"[IGDB] 公司游戏计数失败: {e}")
            except Exception as ex:
                companies, error = [], \
                    f"线程异常：{type(ex).__name__}: {ex}"
                company_counts = {}

            def update_ui():
                for iid in company_tree.get_children():
                    company_tree.delete(iid)
                state.company_tree_iids.clear()
                state.company_slugs.clear()

                if error:
                    company_tree.insert("", "end", iid="_error",
                        values=(f"❌ 搜索失败：{error}", ""),
                        tags=("unchecked",))
                    return
                if not companies:
                    company_tree.insert("", "end", iid="_empty",
                        values=(f"未找到匹配 \"{query}\" 的公司", ""),
                        tags=("unchecked",))
                    return

                sorted_companies = sorted(companies,
                    key=lambda c: (
                        -company_counts.get(c.get('id', 0), 0),
                        c.get('name', '')))

                for i, company in enumerate(sorted_companies):
                    cid = company.get('id')
                    cname = company.get('name', '未知')
                    cslug = company.get('slug', '')
                    key = f"igdb_company_{cid}"
                    var = tk.BooleanVar(value=False)
                    state.check_vars[key] = (
                        var, "igdb_company", cid, f"🏢 {cname}")

                    count = company_counts.get(cid, 0)
                    display_text = (
                        f"☐  {cname}  ({count} 个游戏)"
                        if count > 0 else f"☐  {cname}")
                    link_text = "🔗" if cslug else ""
                    tags = (("even",) if i % 2 == 0
                            else ("unchecked",))
                    company_tree.insert("", "end", iid=key,
                        values=(display_text, link_text), tags=tags)
                    state.company_tree_iids.append(key)
                    if cslug:
                        state.company_slugs[key] = cslug

            try:
                state.rec_win.after(0, update_ui)
            except Exception:
                pass

        threading.Thread(target=bg_thread(search_thread), daemon=True).start()

    company_search_btn = ttk.Button(company_search_frame, text="搜索",
        command=do_search_company,
        state="normal" if state.configured else "disabled")
    company_search_btn.pack(side="left")

    company_search_entry.bind("<Return>",
        lambda e: do_search_company())

    def on_company_tree_click(event):
        region = company_tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        iid = company_tree.identify_row(event.y)
        if not iid or iid.startswith("_"):
            return
        col = company_tree.identify_column(event.x)

        if col == "#2":
            slug = state.company_slugs.get(iid, "")
            if slug:
                webbrowser.open(
                    f"https://www.igdb.com/companies/{slug}")
            return

        key = iid
        if key in state.check_vars:
            var = state.check_vars[key][0]
            new_val = not var.get()
            var.set(new_val)
            current_text = company_tree.item(iid, "values")[0]
            link_text = (company_tree.item(iid, "values")[1]
                         if len(company_tree.item(iid, "values")) > 1
                         else "")
            try:
                idx = company_tree.index(iid)
            except Exception:
                idx = 0
            if new_val:
                new_text = current_text.replace("☐", "☑", 1)
                tag = ("even_checked" if idx % 2 == 0
                       else "checked")
            else:
                new_text = current_text.replace("☑", "☐", 1)
                tag = "even" if idx % 2 == 0 else "unchecked"
            company_tree.item(iid,
                values=(new_text, link_text), tags=(tag,))

    company_tree.bind("<ButtonRelease-1>", on_company_tree_click)

    def block_company_separator(event):
        if company_tree.identify_region(event.x, event.y) == \
                "separator":
            return "break"
    company_tree.bind("<Button-1>", block_company_separator)

    company_tree.insert("", "end", iid="_placeholder",
        values=("输入开发商或发行商名称（如 Capcom、Valve），"
                "然后点击搜索", ""),
        tags=("unchecked",))


def _populate_igdb_tab(app, state, dim_key, items, game_counts):
    """用数据填充某个维度的标签页"""
    tw = state.tab_widgets[dim_key]
    tree = tw["tree"]
    dim_info = app._collections_core.IGDB_DIMENSIONS[dim_key]

    for iid in tree.get_children():
        tree.delete(iid)
    tw["all_iids"].clear()
    tw["iid_name_map"].clear()
    tw["item_slugs"].clear()
    tw["detached_iids"].clear()

    if not items:
        tree.insert("", "end", iid="_empty",
            values=("未找到分类项", ""), tags=("unchecked",))
        return

    if game_counts and len(items) > 100:
        items = [item for item in items
                 if game_counts.get(item.get('id', 0), 0) > 0]

    if game_counts:
        items.sort(key=lambda x: (
            -game_counts.get(x.get('id', 0), 0),
            x.get('name', '')))

    for i, item in enumerate(items):
        item_id = item.get('id')
        item_name = item.get('name', '未知')
        item_slug = item.get('slug', '')
        count = game_counts.get(item_id, 0)
        display_text = (
            f"☐  {item_name}  ({count} 个游戏)"
            if count > 0 else f"☐  {item_name}")
        link_text = "🔗" if item_slug else ""

        key = f"igdb_{dim_key}_{item_id}"
        var = tk.BooleanVar(value=False)
        state.check_vars[key] = (
            var, "igdb_category", (dim_key, item_id),
            f"{dim_info['label']} {item_name}")

        tags = ("even",) if i % 2 == 0 else ("unchecked",)
        tree.insert("", "end", iid=key,
            values=(display_text, link_text), tags=tags)

        tw["all_iids"].append(key)
        tw["iid_name_map"][key] = item_name.lower()
        if item_slug:
            tw["item_slugs"][key] = item_slug


def load_igdb_dimension_list(app, state, dim_key=None):
    """加载指定维度的分类项列表"""
    if not state.configured:
        return

    if dim_key is None:
        current_tab_idx = state.notebook.index("current")
        dim_keys = list(
            app._collections_core.IGDB_DIMENSIONS.keys())
        dim_key = dim_keys[current_tab_idx]

    if state.loaded_dims.get(dim_key):
        return

    tw = state.tab_widgets[dim_key]
    tree = tw["tree"]

    for iid in tree.get_children():
        tree.delete(iid)
    tree.insert("", "end", iid="_loading",
        values=("正在加载分类列表...", ""), tags=("unchecked",))

    def fetch_thread():
        items, error = \
            app._collections_core.fetch_igdb_dimension_list(
                dim_key)
        game_counts = \
            app._collections_core.get_igdb_dimension_game_counts(
                dim_key)

        def update_ui():
            try:
                for iid in tree.get_children():
                    tree.delete(iid)
                if error:
                    tree.insert("", "end", iid="_error",
                        values=(f"❌ 加载失败：{error}", ""),
                        tags=("unchecked",))
                    return
                _populate_igdb_tab(app, state, dim_key, items,
                                   game_counts)
                state.loaded_dims[dim_key] = True
            except tk.TclError:
                return

        try:
            state.rec_win.after(0, update_ui)
        except Exception:
            pass

    threading.Thread(target=bg_thread(fetch_thread), daemon=True).start()


def load_all_igdb_tabs(app, state):
    """加载所有维度的分类列表"""
    if not state.configured:
        return
    for dim_key in app._collections_core.IGDB_DIMENSIONS:
        if not state.loaded_dims.get(dim_key):
            load_igdb_dimension_list(app, state, dim_key)


def select_all_igdb(app, state):
    """全选当前标签页的所有可见项"""
    current_tab_idx = state.notebook.index("current")
    dim_keys = list(
        app._collections_core.IGDB_DIMENSIONS.keys())
    if current_tab_idx >= len(dim_keys):
        for k, v in state.check_vars.items():
            if k.startswith("igdb_company_"):
                v[0].set(True)
        for iid in state.company_tree_iids:
            vals = state.company_tree.item(iid, "values")
            if vals and vals[0].startswith("☐"):
                try:
                    idx = state.company_tree.index(iid)
                except Exception:
                    idx = 0
                link = vals[1] if len(vals) > 1 else ""
                state.company_tree.item(iid,
                    values=(vals[0].replace("☐", "☑", 1), link),
                    tags=(("even_checked",) if idx % 2 == 0
                          else ("checked",)))
        return
    dim_key = dim_keys[current_tab_idx]
    tw = state.tab_widgets[dim_key]
    tree = tw["tree"]
    query = tw["search_var"].get().strip().lower()
    for k, v in state.check_vars.items():
        if k.startswith(f"igdb_{dim_key}_"):
            if query:
                item_name = (v[3].split(" ", 1)[-1].lower()
                             if " " in v[3] else v[3].lower())
                if query in item_name:
                    v[0].set(True)
            else:
                v[0].set(True)
    for iid in tree.get_children():
        if iid.startswith("_"):
            continue
        vals = tree.item(iid, "values")
        if (vals and len(vals) >= 1
                and vals[0].startswith("☐")):
            key = iid
            if (key in state.check_vars
                    and state.check_vars[key][0].get()):
                tree.item(iid,
                    values=(vals[0].replace("☐", "☑", 1),
                            vals[1] if len(vals) > 1 else ""),
                    tags=(("checked",)
                          if tree.index(iid) % 2 != 0
                          else ("even_checked",)))


def deselect_all_igdb(app, state):
    """取消全选当前标签页"""
    current_tab_idx = state.notebook.index("current")
    dim_keys = list(
        app._collections_core.IGDB_DIMENSIONS.keys())
    if current_tab_idx >= len(dim_keys):
        for k, v in state.check_vars.items():
            if k.startswith("igdb_company_"):
                v[0].set(False)
        for iid in state.company_tree_iids:
            vals = state.company_tree.item(iid, "values")
            if vals and vals[0].startswith("☑"):
                try:
                    idx = state.company_tree.index(iid)
                except Exception:
                    idx = 0
                link = vals[1] if len(vals) > 1 else ""
                state.company_tree.item(iid,
                    values=(vals[0].replace("☑", "☐", 1), link),
                    tags=(("even",) if idx % 2 == 0
                          else ("unchecked",)))
        return
    dim_key = dim_keys[current_tab_idx]
    tw = state.tab_widgets[dim_key]
    tree = tw["tree"]
    for k, v in state.check_vars.items():
        if k.startswith(f"igdb_{dim_key}_"):
            v[0].set(False)
    for iid in tree.get_children():
        if iid.startswith("_"):
            continue
        vals = tree.item(iid, "values")
        if (vals and len(vals) >= 1
                and vals[0].startswith("☑")):
            tree.item(iid,
                values=(vals[0].replace("☑", "☐", 1),
                        vals[1] if len(vals) > 1 else ""),
                tags=(("even",) if tree.index(iid) % 2 == 0
                      else ("unchecked",)))


def force_rescan_igdb(app, state):
    """从 IGDB 重新下载所有 Steam 游戏及分类数据"""
    if not state.configured:
        messagebox.showwarning("提示",
            "请先在主界面配置 IGDB API 凭证。",
            parent=state.rec_win)
        return
    ctx = state.ui_ctx
    if ctx['is_fetching'][0]:
        messagebox.showwarning("提示",
            "正在执行其他操作，请稍候。", parent=state.rec_win)
        return
    if not messagebox.askyesno("重新下载 IGDB 数据",
            "将从 IGDB 重新下载所有 Steam 游戏及分类数据到本地。"
            "\n\n约需 5-8 分钟，期间请勿关闭窗口。\n\n确认开始？",
            parent=state.rec_win):
        return

    ctx['is_fetching'][0] = True
    for btn in ctx['btn_widgets']:
        btn.config(state="disabled")

    cancel_flag = [False]

    def rebuild_thread():
        def progress_cb(current, total, phase, detail):
            def _up():
                ctx['status_var'].set(phase)
                ctx['detail_var'].set(detail)
                if total > 0:
                    ctx['progress_bar'].config(
                        mode='determinate', maximum=total)
                    ctx['progress_bar']['value'] = current
                else:
                    if str(ctx['progress_bar'].cget('mode')) \
                            != 'indeterminate':
                        ctx['progress_bar'].config(
                            mode='indeterminate')
                        ctx['progress_bar'].start(15)
            try:
                state.rec_win.after(0, _up)
            except Exception:
                pass

        def show():
            ctx['progress_bar'].config(
                mode='determinate', maximum=100, value=0)
            ctx['progress_bar'].pack(
                padx=20, pady=(5, 0), fill="x")
            ctx['detail_label'].pack(padx=20, anchor="w")
        try:
            state.rec_win.after(0, show)
        except Exception:
            pass

        _, error = app._collections_core.build_igdb_full_cache(
            progress_cb, cancel_flag)

        def done():
            ctx['is_fetching'][0] = False
            ctx['progress_bar'].stop()
            ctx['progress_bar'].pack_forget()
            ctx['detail_label'].pack_forget()
            ctx['detail_var'].set("")
            for btn in ctx['btn_widgets']:
                btn.config(state="normal")
            refresh_igdb_cache_status(app, state)

            state.loaded_dims.clear()
            state.check_vars.clear()

            if error:
                ctx['status_var'].set(f"❌ 下载失败：{error}")
            else:
                ctx['status_var'].set("✅ IGDB 数据下载完成！")
                load_all_igdb_tabs(app, state)

        try:
            state.rec_win.after(0, done)
        except Exception:
            pass

    threading.Thread(target=bg_thread(rebuild_thread),
                     daemon=True).start()


def refresh_igdb_cache_status(app, state):
    """刷新 IGDB 缓存状态显示"""
    igdb_cache_var = state.ui_ctx['igdb_cache_var']
    igdb_cache_label = state.ui_ctx['igdb_cache_label']
    summary = app._collections_core.get_igdb_cache_summary()
    if summary:
        age_hours = (time.time() - summary['newest_at']) / 3600
        if age_hours < 24:
            age_str = f"{age_hours:.0f} 小时前"
        else:
            age_str = f"{age_hours / 24:.1f} 天前"
        if summary.get('is_full_dump'):
            dims = summary.get('dimensions', {})
            dim_parts = []
            for dk, dv in dims.items():
                label = \
                    app._collections_core.IGDB_DIMENSIONS.get(
                        dk, {}).get("label", dk)
                dim_parts.append(f"{label}{dv['count']}")
            dim_str = ("、".join(dim_parts) if dim_parts
                       else f"{summary.get('total_items', 0)} 个分类")
            igdb_cache_var.set(
                f"💾 已下载：{summary['total_steam_games']}"
                f" 个 Steam 游戏 | {dim_str}（{age_str}更新）")
        else:
            igdb_cache_var.set(
                f"💾 已缓存：{summary.get('total_items', 0)}"
                f" 个分类，共 {summary['total_games']}"
                f" 个游戏（{age_str}更新）")
        igdb_cache_label.config(fg="#2e7d32")
    else:
        igdb_cache_var.set(
            "💾 尚未下载（首次使用时自动下载，约 5-8 分钟）")
        igdb_cache_label.config(fg="#888")
