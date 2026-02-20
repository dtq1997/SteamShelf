"""
ui_recommend.py — 推荐来源获取界面（RecommendMixin）

从 _legacy_A/ui_recommend.py 移植。
引用映射：self.core → self._collections_core

宿主协议：RecommendHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import RecommendHost  # noqa: F401

import os
import threading
import time

from utils import sanitize_filename
from ui_utils import bg_thread
from ui_recommend_igdb import IGDBState, build_igdb_panel, refresh_igdb_cache_status
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk


class RecommendMixin:
    """推荐来源获取界面（Mixin，self 指向 SteamToolboxMain 实例）"""

    # --- 个人推荐分类界面（Steam250 + 鉴赏家精选） ---

    def personal_recommend_ui(self, target_col=None):
        """个人推荐分类界面：Steam250 排行榜 + 鉴赏家精选"""
        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return

        fetched_data = {}  # key: source_key, value: {'ids': [...], 'name': '...'}

        rec_win = tk.Toplevel(self.root)
        if target_col:
            rec_win.title(f"从推荐来源更新「{target_col[1]}」")
        else:
            rec_win.title("从推荐来源获取")

        # 使用指南
        guide_frame = tk.Frame(rec_win)
        if not target_col:
            guide_frame.pack(fill="x", padx=20, pady=(15, 5))
        guide_text = tk.Text(guide_frame, font=("微软雅黑", 9), height=3,
                             bg=rec_win.cget("bg"), relief="flat", wrap="word")
        guide_text.tag_config("red", foreground="red",
                              font=("微软雅黑", 9, "bold"))
        guide_text.insert("end", "使用指南：\n1. 勾选要获取的来源（可多选），")
        guide_text.insert("end", "勾选框后面的文字将成为收藏夹名称", "red")
        guide_text.insert("end",
            "。\n2. 直接点击下方的导入、导出或更新按钮，"
            "程序会自动获取数据并执行操作。")
        guide_text.config(state="disabled")
        guide_text.pack(fill="x")

        # ===== 数据源定义 =====
        steam250_fixed_sources = [
            ("steam250_top250", "steam250",
             "https://steam250.com/top250", "前 250 优秀游戏"),
            ("steam250_hidden_gems", "steam250",
             "https://steam250.com/hidden_gems", "前 250 优秀小众游戏"),
            ("steam250_most_played", "steam250",
             "https://steam250.com/most_played", "前 250 优秀热门游戏"),
        ]

        curator_sources = [
            ("curator_indie_fest", "curator",
             "https://store.steampowered.com/curator/44791597/",
             "🏆 独立游戏节"),
            ("curator_thinky", "curator",
             "https://store.steampowered.com/curator/45228984-Thinky-Awards/",
             "📖 Thinky Games 数据库"),
            ("curator_moe_award", "curator",
             "https://store.steampowered.com/curator/45502290/",
             "🏆 萌系遊戲大賞"),
            ("curator_bishojo_award", "curator",
             "https://store.steampowered.com/curator/45531216/",
             "🏆 美少女游戏大赏"),
        ]

        check_vars = {}
        year_check_vars = {}

        # ===== 主内容区：左右两栏布局 =====
        main_content = tk.Frame(rec_win)
        main_content.pack(fill="both", expand=True, padx=10, pady=(5, 0))

        left_col = tk.Frame(main_content)
        left_col.pack(side="left", fill="y", padx=(10, 5), anchor="n")

        right_col = tk.Frame(main_content)
        right_col.pack(side="left", fill="both", expand=True, padx=(5, 10))

        # ===== 左栏：Steam250 区域 =====
        s250_frame = tk.LabelFrame(left_col, text="📊 Steam250 排行榜",
                                    font=("微软雅黑", 10, "bold"),
                                    padx=10, pady=5)
        s250_frame.pack(fill="x", pady=(0, 5))

        for key, src_type, url, name in steam250_fixed_sources:
            var = tk.BooleanVar(value=False)
            check_vars[key] = (var, src_type, url, name)
            tk.Checkbutton(s250_frame, text=name, variable=var,
                           font=("微软雅黑", 9)).pack(anchor="w")

        # 年度榜单区域
        year_frame = tk.Frame(s250_frame)
        year_frame.pack(fill="x", pady=(5, 0))
        tk.Label(year_frame, text="📅 年度榜单：",
                 font=("微软雅黑", 9)).pack(side="left")

        from datetime import datetime
        current_year = datetime.now().year
        available_years = list(range(current_year, current_year - 6, -1))

        year_inner_frame = tk.Frame(year_frame)
        year_inner_frame.pack(side="left", padx=(5, 0))

        for year in available_years:
            var = tk.BooleanVar(value=False)
            key = f"steam250_{year}"
            url = f"https://steam250.com/{year}"
            name = f"前 250 优秀游戏（{year} 年度）"
            year_check_vars[key] = (var, "steam250", url, name, year)
            tk.Checkbutton(year_inner_frame, text=str(year), variable=var,
                           font=("微软雅黑", 9)).pack(side="left")

        # Steam250 全选按钮
        select_all_frame = tk.Frame(s250_frame)
        select_all_frame.pack(fill="x", pady=(5, 0))

        def select_all_s250():
            for k, v in check_vars.items():
                if k.startswith("steam250"):
                    v[0].set(True)
            for k, v in year_check_vars.items():
                v[0].set(True)

        def deselect_all_s250():
            for k, v in check_vars.items():
                if k.startswith("steam250"):
                    v[0].set(False)
            for k, v in year_check_vars.items():
                v[0].set(False)

        tk.Button(select_all_frame, text="☑️ 全选 Steam250",
                  command=select_all_s250,
                  font=("微软雅黑", 8)).pack(side="left", padx=(0, 5))
        tk.Button(select_all_frame, text="☐ 取消全选 Steam250",
                  command=deselect_all_s250,
                  font=("微软雅黑", 8)).pack(side="left")

        # ===== 左栏：鉴赏家精选区域 =====
        curator_frame = tk.LabelFrame(left_col, text="🎮 鉴赏家精选",
                                       font=("微软雅黑", 10, "bold"),
                                       padx=10, pady=5)
        curator_frame.pack(fill="x", pady=5)

        for key, src_type, url, name in curator_sources:
            var = tk.BooleanVar(value=False)
            check_vars[key] = (var, src_type, url, name)
            tk.Checkbutton(curator_frame, text=name, variable=var,
                           font=("微软雅黑", 9)).pack(anchor="w")

        curator_btn_frame = tk.Frame(curator_frame)
        curator_btn_frame.pack(fill="x", pady=(5, 0))

        def select_all_curator():
            for k, v in check_vars.items():
                if k.startswith("curator"):
                    v[0].set(True)

        def deselect_all_curator():
            for k, v in check_vars.items():
                if k.startswith("curator"):
                    v[0].set(False)

        tk.Button(curator_btn_frame, text="☑️ 全选鉴赏家",
                  command=select_all_curator,
                  font=("微软雅黑", 8)).pack(side="left", padx=(0, 5))
        tk.Button(curator_btn_frame, text="☐ 取消全选鉴赏家",
                  command=deselect_all_curator,
                  font=("微软雅黑", 8)).pack(side="left")

        tk.Label(curator_frame,
                 text="💡 鉴赏家列表会使用多语言扫描以获取完整数据",
                 font=("微软雅黑", 8), fg="#666").pack(anchor="w", pady=(5, 0))

        # Cookie 状态提示
        cookie_status_frame = tk.Frame(curator_frame)
        cookie_status_frame.pack(fill="x", pady=(3, 0))

        saved_cookie = self._collections_core.get_saved_cookie()
        if saved_cookie:
            tk.Label(cookie_status_frame,
                     text="🔐 已配置登录态 Cookie，可获取完整列表",
                     font=("微软雅黑", 8), fg="green").pack(anchor="w")
        else:
            tk.Label(cookie_status_frame,
                     text="⚠️ 未配置登录态 Cookie，可能无法获取完整列表",
                     font=("微软雅黑", 8), fg="orange").pack(anchor="w")
            tk.Label(cookie_status_frame,
                     text="     → 可在主界面「🔑 管理登录态 Cookie」中配置",
                     font=("微软雅黑", 8), fg="#888").pack(anchor="w")

        # ===== 右栏：IGDB 多维度分类区域（已拆分到 ui_recommend_igdb.py）=====
        igdb_state = IGDBState()
        igdb_state.rec_win = rec_win
        build_igdb_panel(self, igdb_state, right_col)

        # ===== 状态显示 =====
        status_var = tk.StringVar(
            value="请勾选要获取的来源，然后点击下方按钮。")
        status_label = tk.Label(rec_win, textvariable=status_var,
                                font=("微软雅黑", 9), fg="gray")
        status_label.pack(padx=20, pady=(10, 0), anchor="w")

        progress_bar = ttk.Progressbar(rec_win, length=400,
                                        mode='indeterminate')
        progress_bar.pack(padx=20, pady=(5, 0), fill="x")
        progress_bar.pack_forget()

        detail_var = tk.StringVar(value="")
        detail_label = tk.Label(rec_win, textvariable=detail_var,
                                font=("微软雅黑", 8), fg="#888")
        detail_label.pack(padx=20, anchor="w")
        detail_label.pack_forget()

        is_fetching = [False]

        # 将后期创建的 UI 组件绑定到 igdb_state，供 force_rescan_igdb 使用
        igdb_state.ui_ctx.update({
            'is_fetching': is_fetching,
            'status_var': status_var,
            'detail_var': detail_var,
            'progress_bar': progress_bar,
            'detail_label': detail_label,
        })

        # ===== 核心：获取数据并执行后续操作 =====
        def fetch_and_execute(action_type, action_callback):
            """获取数据后执行指定操作"""
            selected = [(k, v[1], v[2], v[3])
                        for k, v in check_vars.items() if v[0].get()]
            for k, v in year_check_vars.items():
                if v[0].get():
                    selected.append((k, v[1], v[2], v[3]))
            for k, v in igdb_state.check_vars.items():
                if v[0].get():
                    selected.append((k, v[1], v[2], v[3]))

            if not selected:
                messagebox.showwarning("提示",
                    "请至少勾选一个来源。", parent=rec_win)
                return

            if is_fetching[0]:
                return
            is_fetching[0] = True

            for btn in btn_widgets:
                btn.config(state="disabled")

            def fetch_thread():
                fetched_data.clear()
                total = len(selected)

                def show_progress():
                    progress_bar.pack(padx=20, pady=(5, 0), fill="x")
                    detail_label.pack(padx=20, anchor="w")
                    progress_bar.start(15)
                rec_win.after(0, show_progress)

                for i, (key, src_type, url_or_id, name) in \
                        enumerate(selected):
                    def update_status(msg, detail=""):
                        def _up():
                            status_var.set(msg)
                            if detail:
                                detail_var.set(detail)
                        rec_win.after(0, _up)

                    update_status(
                        f"正在获取 [{i + 1}/{total}]: {name}...")

                    if src_type == "steam250":
                        ids, error = \
                            self._collections_core.fetch_steam250_ids(
                                url_or_id)
                        if error:
                            update_status(f"❌ {name}: {error}")
                        else:
                            fetched_data[key] = {
                                'ids': ids, 'name': name,
                                'source_type': 'steam250',
                                'source_params': {'url': url_or_id}}
                            update_status(
                                f"✅ {name}: 获取 {len(ids)} 个游戏")

                    elif src_type == "curator":
                        page_type, identifier = \
                            self._collections_core.extract_steam_list_info(
                                url_or_id)
                        if page_type and identifier:
                            def progress_cb(fetched, total_count,
                                            phase, detail):
                                update_status(
                                    f"正在获取 [{i + 1}/{total}]: "
                                    f"{name} ({phase})", detail)

                            login_cookies = None
                            saved_cookie = \
                                self._collections_core.get_saved_cookie()
                            if saved_cookie:
                                login_cookies = \
                                    f"steamLoginSecure={saved_cookie}"

                            ids, display_name, error, has_login = \
                                self._collections_core.fetch_steam_list(
                                    page_type, identifier, progress_cb,
                                    login_cookies)

                            if error:
                                update_status(f"❌ {name}: {error}")
                            else:
                                fetched_data[key] = {
                                    'ids': ids, 'name': name,
                                    'source_type': 'curator',
                                    'source_params': {'url': url_or_id}}
                                login_str = ("🔐" if has_login
                                             else "⚠️")
                                update_status(
                                    f"✅ {name}: 获取 {len(ids)}"
                                    f" 个游戏 {login_str}")
                        else:
                            update_status(
                                f"❌ {name}: 无法解析 URL")

                    elif src_type == "igdb_category":
                        dimension, item_id = url_or_id
                        display_name = name
                        for dim_info in \
                                self._collections_core.IGDB_DIMENSIONS.values():
                            display_name = display_name.replace(
                                dim_info["label"] + " ", "")

                        def igdb_progress_cb(fetched, total_count,
                                             phase, detail):
                            update_status(
                                f"正在获取 [{i + 1}/{total}]: "
                                f"{name} ({phase})", detail)

                        ids, error = \
                            self._collections_core.fetch_igdb_games_by_dimension(
                                dimension, item_id, display_name,
                                igdb_progress_cb,
                                force_refresh=igdb_state.force_refresh[0])

                        if error:
                            update_status(f"❌ {name}: {error}")
                        else:
                            fetched_data[key] = {
                                'ids': ids, 'name': name,
                                'source_type': 'igdb_category',
                                'source_params': {
                                    'dimension': dimension,
                                    'item_id': item_id,
                                    'item_name': display_name}}
                            cached_ids, cached_at = \
                                self._collections_core.get_igdb_dimension_cache(
                                    dimension, item_id)
                            if (not igdb_state.force_refresh[0]
                                    and cached_ids is not None
                                    and self._collections_core.is_igdb_cache_valid(
                                        cached_at)):
                                update_status(
                                    f"✅ {name}: {len(ids)}"
                                    " 个游戏（本地缓存）")
                            else:
                                update_status(
                                    f"✅ {name}: 获取 {len(ids)}"
                                    " 个游戏（已缓存）")

                    elif src_type == "igdb_company":
                        company_id = url_or_id
                        company_name = name.replace("🏢 ", "")

                        def company_progress_cb(fetched, total_count,
                                                phase, detail):
                            update_status(
                                f"正在获取 [{i + 1}/{total}]: "
                                f"{name} ({phase})", detail)

                        ids, error = \
                            self._collections_core.fetch_igdb_games_by_company(
                                company_id, company_name,
                                company_progress_cb)

                        if error:
                            update_status(f"❌ {name}: {error}")
                        else:
                            fetched_data[key] = {
                                'ids': ids, 'name': name,
                                'source_type': 'igdb_company',
                                'source_params': {
                                    'company_id': company_id,
                                    'company_name': company_name}}
                            update_status(
                                f"✅ {name}: 获取 {len(ids)} 个游戏")

                    time.sleep(0.3)

                def final_update():
                    is_fetching[0] = False
                    igdb_state.force_refresh[0] = False
                    progress_bar.stop()
                    progress_bar.pack_forget()
                    detail_label.pack_forget()
                    detail_var.set("")

                    for btn in btn_widgets:
                        btn.config(state="normal")

                    try:
                        refresh_igdb_cache_status(self, igdb_state)
                    except Exception:
                        pass

                    if fetched_data:
                        if merge_var.get() and len(fetched_data) > 1:
                            all_ids = set()
                            all_names = []
                            first_src = None
                            for d in fetched_data.values():
                                all_ids.update(d['ids'])
                                all_names.append(d['name'])
                                if first_src is None and d.get('source_type'):
                                    first_src = d
                            merged_name = " + ".join(all_names)
                            if len(merged_name) > 60:
                                merged_name = (
                                    merged_name[:57]
                                    + f"…（共 {len(all_names)} 个来源）")
                            merged = {'ids': sorted(all_ids),
                                      'name': merged_name}
                            if first_src:
                                merged['source_type'] = first_src['source_type']
                                merged['source_params'] = first_src.get(
                                    'source_params', {})
                            fetched_data.clear()
                            fetched_data["_merged"] = merged

                        total_ids = sum(
                            len(d['ids'])
                            for d in fetched_data.values())
                        status_var.set(
                            f"✅ 获取完成！共 {len(fetched_data)}"
                            f" 个来源，{total_ids} 个游戏。")
                        status_label.config(fg="green")
                        action_callback()
                    else:
                        status_var.set("❌ 所有来源获取失败。")
                        status_label.config(fg="red")

                rec_win.after(0, final_update)

            threading.Thread(target=bg_thread(fetch_thread),
                             daemon=True).start()

        # ===== 合并模式选项 =====
        merge_var = tk.BooleanVar(value=bool(target_col))
        if not target_col:
            merge_frame = tk.Frame(rec_win)
            merge_frame.pack(pady=(5, 0))
            tk.Checkbutton(merge_frame,
                text="🔗 合并所有勾选来源"
                     "（取并集后作为一个来源导入/导出/更新）",
                variable=merge_var, font=("微软雅黑", 9)).pack()

        # ===== 操作按钮 =====
        btn_frame = tk.Frame(rec_win)
        btn_frame.pack(pady=15)

        btn_widgets = []
        igdb_state.ui_ctx['btn_widgets'] = btn_widgets

        disclaimer = self._collections_core.disclaimer

        def do_create():
            def create_action():
                name_win = tk.Toplevel(self.root)
                name_win.title("确认收藏夹名称")

                tk.Label(name_win, text="请确认或修改收藏夹名称：",
                         font=("微软雅黑", 10, "bold")).pack(
                             pady=(15, 10), padx=20)

                if (self._cef_bridge
                        and self._cef_bridge.is_connected()):
                    hint_msg = (
                        "💡 修改下方文本框中的名称即可自定义收藏夹名称。\n"
                        "☁️ 云同步模式已启用，保存后自动同步到云端。")
                else:
                    hint_msg = (
                        "💡 修改下方文本框中的名称即可自定义收藏夹名称。\n"
                        "程序会自动添加后缀"
                        "「(删除这段字以触发云同步)」。")
                hint_text = tk.Text(name_win, font=("微软雅黑", 8),
                    height=2, bg=name_win.cget("bg"), relief="flat",
                    fg="#666")
                hint_text.insert("end", hint_msg)
                hint_text.config(state="disabled")
                hint_text.pack(padx=20, fill="x")

                edit_frame = tk.Frame(name_win)
                edit_frame.pack(fill="both", expand=True,
                                padx=20, pady=10)

                canvas = tk.Canvas(edit_frame, height=200)
                scrollbar = ttk.Scrollbar(edit_frame,
                    orient="vertical", command=canvas.yview)
                scrollable_frame = tk.Frame(canvas)

                scrollable_frame.bind("<Configure>",
                    lambda e: canvas.configure(
                        scrollregion=canvas.bbox("all")))

                canvas.create_window((0, 0), window=scrollable_frame,
                                     anchor="nw")
                canvas.configure(yscrollcommand=scrollbar.set)

                canvas.pack(side="left", fill="both", expand=True)
                scrollbar.pack(side="right", fill="y")

                name_entries = {}
                for key, d in fetched_data.items():
                    row_frame = tk.Frame(scrollable_frame)
                    row_frame.pack(fill="x", pady=3)

                    tk.Label(row_frame,
                        text=f"📦 {len(d['ids'])} 个游戏 →",
                        font=("微软雅黑", 9), width=15,
                        anchor="e").pack(side="left")

                    name_var = tk.StringVar(value=d['name'])
                    entry = tk.Entry(row_frame,
                        textvariable=name_var, width=35,
                        font=("微软雅黑", 9))
                    entry.pack(side="left", padx=5)
                    name_entries[key] = name_var

                def confirm_create():
                    for key, d in fetched_data.items():
                        new_name = name_entries[key].get().strip()
                        if new_name:
                            col_id = self._collections_core.add_static_collection(
                                data, new_name, d['ids'])
                            if col_id and d.get('source_type') and key != '_merged':
                                mode_map = {"增量": "incremental",
                                            "增量+辅助": "incremental_aux",
                                            "替换": "replace"}
                                self._collections_core.save_collection_source(
                                    col_id, d['source_type'],
                                    d.get('source_params', {}),
                                    d['name'],
                                    mode_map.get(mode_combo.get(),
                                                 'incremental'))
                    self._save_and_sync(
                        data,
                        backup_description="从个人推荐分类创建收藏夹")
                    messagebox.showinfo("成功",
                        f"已创建 {len(fetched_data)} 个收藏夹。"
                        + disclaimer, parent=name_win)
                    name_win.destroy()
                    self._ui_refresh()

                btn_row = tk.Frame(name_win)
                btn_row.pack(pady=15)
                tk.Button(btn_row, text="✅ 确认创建",
                          command=confirm_create,
                          width=15).pack(side="left", padx=10)
                tk.Button(btn_row, text="取消",
                          command=name_win.destroy,
                          width=10).pack(side="left", padx=10)

            fetch_and_execute('create', create_action)

        def do_export():
            dest_dir = filedialog.askdirectory(
                initialdir=self._last_dir('recommend_export'),
                title="选择保存文件夹")
            if not dest_dir:
                return
            self._save_dir('recommend_export', dest_dir)

            def export_action():
                for key, d in fetched_data.items():
                    safe_name = sanitize_filename(d['name'])
                    with open(os.path.join(dest_dir,
                              f"{safe_name}.txt"),
                              'w', encoding='utf-8') as f:
                        for aid in d['ids']:
                            f.write(f"{aid}\n")
                messagebox.showinfo("成功",
                    f"已导出 {len(fetched_data)} 个文件。",
                    parent=rec_win)

            fetch_and_execute('export', export_action)

        def do_update():
            all_cols = \
                self._collections_core.get_all_collections_with_refs(
                    data)
            if not all_cols:
                messagebox.showwarning("提示",
                    "未找到任何收藏夹。", parent=rec_win)
                return

            def update_action():
                sources = {}
                for key, d in fetched_data.items():
                    src = {"name": d['name'], "ids": d['ids']}
                    if d.get('source_type') and key != '_merged':
                        src['source_type'] = d['source_type']
                        src['source_params'] = d.get('source_params', {})
                    sources[key] = src

                def on_done():
                    self._save_and_sync(
                        data,
                        backup_description="从个人推荐分类更新收藏夹")
                    self._ui_refresh()

                self.show_batch_update_mapping(
                    data, all_cols, sources, on_done,
                    saved_mappings_key="recommend_update_mappings")

            fetch_and_execute('update', update_action)

        if target_col:
            # 更新模式选择
            mode_frame = tk.Frame(btn_frame)
            mode_frame.pack(side="left", padx=(0, 8))
            tk.Label(mode_frame, text="模式：",
                     font=("微软雅黑", 9)).pack(side="left")
            mode_combo = ttk.Combobox(mode_frame,
                values=["增量", "增量+辅助", "替换"],
                width=8, state="readonly")
            mode_combo.set("增量")
            mode_combo.pack(side="left")

            def do_target_update():
                def target_update_action():
                    all_ids = set()
                    first_source = None
                    for d in fetched_data.values():
                        all_ids.update(d['ids'])
                        if first_source is None and d.get('source_type'):
                            first_source = d
                    if not all_ids:
                        return
                    col_id, col_name = target_col
                    all_cols = self._collections_core.get_all_collections_with_refs(data)
                    entry = None
                    for c in all_cols:
                        if c.get('id') == col_id:
                            entry = c['entry_ref']
                            break
                    if not entry:
                        messagebox.showerror("错误", "未找到目标收藏夹。",
                                             parent=rec_win)
                        return
                    mode = mode_combo.get()
                    mode_map = {"增量+辅助": "incremental_aux",
                                "增量": "incremental", "替换": "replace"}
                    mode_key = mode_map.get(mode, "incremental")
                    if mode == "替换":
                        old_c, new_c = \
                            self._collections_core.perform_replace_update(
                                data, entry, sorted(all_ids))
                        result_msg = f"🔄 替换更新完成\n{old_c} → {new_c}"
                        updated = True
                    else:
                        create_aux = (mode == "增量+辅助")
                        a, r, t, updated = \
                            self._collections_core.perform_incremental_update(
                                data, entry, sorted(all_ids), col_name,
                                create_aux=create_aux)
                        result_msg = (
                            f"✅「{col_name}」已更新\n"
                            f"新增: {a}, 移除: {r}, 总计: {t}")
                    self._save_and_sync(data,
                        backup_description=f"从推荐来源更新: {col_name}")
                    rec_win.destroy()
                    self._ui_refresh()
                    if updated:
                        messagebox.showinfo("更新完成",
                            result_msg + disclaimer, parent=self.root)
                    else:
                        messagebox.showinfo("已是最新",
                            f"「{col_name}」已是最新，无需更新。",
                            parent=self.root)
                    # 询问绑定来源
                    if first_source and first_source.get('source_type'):
                        self._ask_bind_source(
                            col_id, first_source['source_type'],
                            first_source.get('source_params', {}),
                            first_source.get('name', col_name),
                            update_mode=mode_key)

                fetch_and_execute('update', target_update_action)

            btn_t = tk.Button(btn_frame, text="🔄 更新",
                              command=do_target_update, width=10)
            btn_t.pack(side="left", padx=5)
            btn_widgets.append(btn_t)
            btn_c = tk.Button(btn_frame, text="取消",
                              command=rec_win.destroy, width=8)
            btn_c.pack(side="left", padx=5)
            btn_widgets.append(btn_c)
        else:
            mode_frame = tk.Frame(btn_frame)
            mode_frame.pack(side="left", padx=(0, 8))
            tk.Label(mode_frame, text="模式：",
                     font=("微软雅黑", 9)).pack(side="left")
            mode_combo = ttk.Combobox(mode_frame,
                values=["增量", "增量+辅助", "替换"],
                width=8, state="readonly")
            mode_combo.set("增量")
            mode_combo.pack(side="left")

            btn1 = tk.Button(btn_frame, text="📁 建立为新收藏夹",
                             command=do_create, width=15)
            btn1.pack(side="left", padx=5)
            btn_widgets.append(btn1)

            btn2 = tk.Button(btn_frame, text="📥 导出为 TXT 文件",
                             command=do_export, width=18)
            btn2.pack(side="left", padx=5)
            btn_widgets.append(btn2)

            btn3 = tk.Button(btn_frame, text="🔄️ 更新现有收藏夹",
                             command=do_update, width=15)
            btn3.pack(side="left", padx=5)
            btn_widgets.append(btn3)

        self._center_window(rec_win)
