"""
ui_curator.py — Steam 列表页面获取界面（CuratorMixin）

从 _legacy_A/ui_curator.py 移植。
引用映射：self.core → self._collections_core

宿主协议：CuratorHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import CuratorHost  # noqa: F401

import os
import threading
import tkinter as tk

from utils import sanitize_filename
from ui_utils import bg_thread
import webbrowser
from tkinter import filedialog, messagebox, ttk, simpledialog


class CuratorMixin:
    """Steam 列表页面获取界面（Mixin，self 指向 SteamToolboxMain 实例）"""

    def curator_sync_ui(self, target_col=None):
        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return
        cur_win = tk.Toplevel(self.root)
        if target_col:
            cur_win.title(f"从 Steam 列表更新「{target_col[1]}」")
        else:
            cur_win.title("同步 Steam 列表页面")

        fetched_ids = []
        fetched_name = tk.StringVar(value="")

        url_frame = tk.Frame(cur_win)
        url_frame.pack(fill="x", padx=20, pady=(5, 0))
        tk.Label(url_frame, text="Steam 列表 URL：",
                 font=("微软雅黑", 9)).pack(side="left")
        url_entry = tk.Entry(url_frame, width=40, font=("微软雅黑", 9))
        url_entry.pack(side="left", padx=5, fill="x", expand=True)
        url_entry.insert(0, "https://store.steampowered.com/curator/44791597/")

        ex_frame = tk.Frame(cur_win)
        ex_frame.pack(fill="x", padx=20, pady=(3, 0))
        tk.Label(ex_frame, text="示例：", font=("微软雅黑", 8),
                 fg="gray").pack(side="left")

        def set_url(url):
            url_entry.delete(0, "end")
            url_entry.insert(0, url)

        for _lbl, _url in [
            ("鉴赏家", "https://store.steampowered.com/curator/44791597/"),
            ("发行商", "https://store.steampowered.com/publisher/DevolverDigital"),
            ("开发商", "https://store.steampowered.com/developer/Valve"),
        ]:
            lnk = tk.Label(ex_frame, text=_lbl, fg="#1a73e8",
                            font=("微软雅黑", 8, "underline"), cursor="hand2")
            lnk.pack(side="left", padx=3)
            lnk.bind("<Button-1>", lambda e, u=_url: set_url(u))

        open_lnk = tk.Label(ex_frame, text="🌐 浏览器打开", fg="gray",
                             font=("微软雅黑", 8), cursor="hand2")
        open_lnk.pack(side="right")
        open_lnk.bind("<Button-1>",
                       lambda e: webbrowser.open(url_entry.get().strip()))

        # ── 代理 + Cookie 状态提示（动态刷新） ──
        cap_frame = tk.Frame(cur_win)
        cap_frame.pack(fill="x", padx=20, pady=(6, 0))
        cap_status = tk.Label(cap_frame, font=("微软雅黑", 8), fg="#666")
        cap_status.pack(side="left")
        cap_hint = tk.Label(cap_frame, font=("微软雅黑", 8))
        cap_hint.pack(side="left")

        def _refresh_cap():
            p = getattr(self, '_has_proxy', False)
            c = bool(self._collections_core.get_saved_cookie())
            proxy_tag = getattr(self, '_proxy_country', '✅') if p else '⚫'
            cap_status.config(
                text=f"🌐 代理: {proxy_tag}　🍪 Cookie: {'✅' if c else '⚫'}")
            if p and c:
                cap_hint.config(text="— 可获取成人游戏", fg="green")
            elif p:
                cap_hint.config(text="— 缺少Cookie，成人游戏可能不完整", fg="#999")
            else:
                m = []
                if not p: m.append("代理")
                if not c: m.append("Cookie")
                cap_hint.config(
                    text=f"— 缺少{'+'.join(m)}，成人游戏可能不完整", fg="#999")

        _refresh_cap()
        self._curator_refresh_cap = _refresh_cap

        status_var = tk.StringVar(value="尚未获取数据。")
        status_label = tk.Label(cur_win, textvariable=status_var,
                                font=("微软雅黑", 9), fg="gray")
        status_label.pack(padx=20, pady=(8, 0), anchor="w")

        progress_bar = ttk.Progressbar(cur_win, length=400,
                                        mode='indeterminate')
        progress_bar.pack(padx=20, pady=(4, 0), fill="x")
        progress_bar.pack_forget()

        detail_var = tk.StringVar(value="")
        detail_label = tk.Label(cur_win, textvariable=detail_var,
                                font=("微软雅黑", 8), fg="#888")
        detail_label.pack(padx=20, anchor="w")
        detail_label.pack_forget()

        is_fetching = [False]
        btn_widgets = []

        def fetch_and_execute(action_callback):
            """获取数据后执行指定操作"""
            if is_fetching[0]:
                return
            url_text = url_entry.get().strip()
            page_type, identifier = \
                self._collections_core.extract_steam_list_info(url_text)
            if not page_type or not identifier:
                messagebox.showwarning("错误",
                    "无法识别 Steam 列表页面。\n"
                    "请输入有效的 URL。", parent=cur_win)
                return

            is_fetching[0] = True
            for btn in btn_widgets:
                btn.config(state="disabled")
            status_var.set("正在连接 Steam...")
            status_label.config(fg="gray")

            login_cookies = None
            cookie_val = self._collections_core.get_saved_cookie()
            if cookie_val:
                login_cookies = f"steamLoginSecure={cookie_val}"

            def update_progress(fetched, total, phase_info="",
                                detail_info=""):
                def _up():
                    phase_str = f" ({phase_info})" if phase_info else ""
                    status_var.set(
                        f"正在获取: 已发现 {fetched} 个游戏{phase_str}...")
                    if detail_info:
                        detail_var.set(detail_info)
                cur_win.after(0, _up)

            def fetch_thread():
                nonlocal fetched_ids
                def show_progress():
                    progress_bar.pack(padx=20, pady=(4, 0), fill="x")
                    detail_label.pack(padx=20, anchor="w")
                    progress_bar.start(15)
                cur_win.after(0, show_progress)

                ids, name, error, has_login = \
                    self._collections_core.fetch_steam_list(
                        page_type, identifier, update_progress,
                        login_cookies)

                def finish():
                    is_fetching[0] = False
                    for btn in btn_widgets:
                        btn.config(state="normal")
                    progress_bar.stop()
                    progress_bar.pack_forget()
                    detail_label.pack_forget()
                    detail_var.set("")
                    if error:
                        status_var.set(f"❌ {error}")
                        status_label.config(fg="red")
                        return
                    if not ids:
                        status_var.set("❌ 未获取到任何游戏。")
                        status_label.config(fg="red")
                        return
                    fetched_ids.clear()
                    fetched_ids.extend(ids)
                    fetched_name.set(name if name else "Steam 列表")
                    login_str = ("🔐 已配置 Cookie" if has_login
                                 else "💡 未配置 Cookie")
                    status_var.set(
                        f"✅ 成功获取 {len(ids)} 个游戏！({login_str})")
                    status_label.config(fg="green")
                    action_callback()

                cur_win.after(0, finish)

            threading.Thread(target=bg_thread(fetch_thread), daemon=True).start()

        disclaimer = self._collections_core.disclaimer

        btn_frame = tk.Frame(cur_win)
        btn_frame.pack(pady=15)

        def do_create():
            def create_action():
                url_text = url_entry.get().strip()
                name = simpledialog.askstring("新建收藏夹",
                    "请输入收藏夹名称：",
                    initialvalue=fetched_name.get(), parent=cur_win)
                if name:
                    filtered = self._ask_filter_owned(
                        list(fetched_ids), parent=cur_win)
                    if filtered is None:
                        return
                    col_id = self._collections_core.add_static_collection(
                        data, name, filtered)
                    if col_id and mode_combo.get() != "无":
                        mode_map = {"增量": "incremental",
                                    "增量+辅助": "incremental_aux",
                                    "替换": "replace"}
                        self._collections_core.save_collection_source(
                            col_id, 'curator', {'url': url_text},
                            fetched_name.get() or name,
                            mode_map.get(mode_combo.get(), 'incremental'))
                    self._save_and_sync(
                        data,
                        backup_description=f"从 Steam 列表创建收藏夹: {name}")
                    messagebox.showinfo("录入成功",
                        f"已建立新收藏夹。共录入 {len(filtered)} 个 AppID。"
                        + disclaimer, parent=cur_win)
                    cur_win.destroy()
            fetch_and_execute(create_action)

        def do_export():
            def export_action():
                name = simpledialog.askstring("导出设置",
                    "请输入生成的 TXT 文件名：",
                    initialvalue=sanitize_filename(fetched_name.get()),
                    parent=cur_win)
                if not name:
                    return
                save_path = filedialog.asksaveasfilename(
                    initialdir=self._last_dir('curator_export'),
                    title="保存 AppID 列表",
                    defaultextension=".txt",
                    initialfile=f"{sanitize_filename(name)}.txt",
                    filetypes=[("Text files", "*.txt")])
                if save_path:
                    self._save_dir('curator_export', save_path)
                    with open(save_path, 'w', encoding='utf-8') as f:
                        for aid in fetched_ids:
                            f.write(f"{aid}\n")
                    messagebox.showinfo("成功",
                        f"已成功导出 {len(fetched_ids)} 个 AppID。"
                        + disclaimer, parent=cur_win)
            fetch_and_execute(export_action)

        def do_update():
            def update_action():
                all_cols = \
                    self._collections_core.get_all_collections_with_refs(data)
                if not all_cols:
                    messagebox.showwarning("提示", "未找到任何收藏夹。",
                                           parent=cur_win)
                    return
                url_text = url_entry.get().strip()
                sources = {
                    fetched_name.get() or "Steam 列表": {
                        "name": fetched_name.get() or "Steam 列表",
                        "ids": list(fetched_ids),
                        "source_type": "curator",
                        "source_params": {"url": url_text},
                    }
                }
                def on_done():
                    self._save_and_sync(
                        data, backup_description="从 Steam 列表更新收藏夹")
                    cur_win.destroy()
                self.show_batch_update_mapping(
                    data, all_cols, sources, on_done,
                    parent_to_close=cur_win)
            fetch_and_execute(update_action)

        def do_target_update():
            """自动获取数据后更新目标收藏夹"""
            if is_fetching[0]:
                return

            url_text = url_entry.get().strip()
            page_type, identifier = \
                self._collections_core.extract_steam_list_info(url_text)
            if not page_type or not identifier:
                messagebox.showwarning("错误",
                    "无法识别 Steam 列表页面。\n"
                    "请输入有效的 URL。", parent=cur_win)
                return

            is_fetching[0] = True
            update_btn.config(state="disabled")
            status_var.set("正在连接 Steam...")
            status_label.config(fg="gray")

            login_cookies = None
            cookie_val = self._collections_core.get_saved_cookie()
            if cookie_val:
                login_cookies = f"steamLoginSecure={cookie_val}"

            def update_progress(fetched, total, phase_info="",
                                detail_info=""):
                def _up():
                    phase_str = f" ({phase_info})" if phase_info else ""
                    status_var.set(
                        f"正在获取: 已发现 {fetched} 个游戏{phase_str}...")
                    if detail_info:
                        detail_var.set(detail_info)
                cur_win.after(0, _up)

            def fetch_and_update_thread():
                def show_progress():
                    progress_bar.pack(padx=20, pady=(4, 0), fill="x")
                    detail_label.pack(padx=20, anchor="w")
                    progress_bar.start(15)
                cur_win.after(0, show_progress)

                ids, name, error, has_login = \
                    self._collections_core.fetch_steam_list(
                        page_type, identifier, update_progress,
                        login_cookies)

                def finish():
                    is_fetching[0] = False
                    update_btn.config(state="normal")
                    progress_bar.stop()
                    progress_bar.pack_forget()
                    detail_label.pack_forget()
                    detail_var.set("")

                    if error:
                        status_var.set(f"❌ {error}")
                        status_label.config(fg="red")
                        return
                    if not ids:
                        status_var.set("❌ 未获取到任何游戏。")
                        status_label.config(fg="red")
                        return

                    fetched_name.set(name if name else "Steam 列表")

                    col_id, col_name = target_col
                    all_cols = \
                        self._collections_core.get_all_collections_with_refs(
                            data)
                    entry = None
                    for c in all_cols:
                        if c.get('id') == col_id:
                            entry = c['entry_ref']
                            break
                    if not entry:
                        messagebox.showerror("错误", "未找到目标收藏夹。",
                                             parent=cur_win)
                        return

                    mode = mode_combo.get()
                    mode_map = {"增量": "incremental",
                                "增量+辅助": "incremental_aux",
                                "替换": "replace"}
                    mode_key = mode_map.get(mode, "incremental")

                    if mode == "替换":
                        old_c, new_c = \
                            self._collections_core.perform_replace_update(
                                data, entry, ids)
                        result_msg = f"🔄 替换更新完成\n{old_c} → {new_c}"
                        updated = True
                    else:
                        create_aux = (mode == "增量+辅助")
                        a, r, t, updated = \
                            self._collections_core.perform_incremental_update(
                                data, entry, ids, col_name,
                                create_aux=create_aux)
                        result_msg = (
                            f"✅「{col_name}」已更新\n"
                            f"新增: {a}, 移除: {r}, 总计: {t}")

                    self._save_and_sync(
                        data,
                        backup_description=f"从 Steam 列表更新: {col_name}")
                    cur_win.destroy()
                    self._ui_refresh()
                    if updated:
                        messagebox.showinfo("更新完成",
                            result_msg + disclaimer, parent=self.root)
                    else:
                        messagebox.showinfo("已是最新",
                            f"「{col_name}」已是最新，无需更新。",
                            parent=self.root)
                    if mode != "无":
                        self._ask_bind_source(
                            col_id, 'curator', {'url': url_text},
                            fetched_name.get() or col_name,
                            update_mode=mode_key)

                cur_win.after(0, finish)

            threading.Thread(target=bg_thread(fetch_and_update_thread),
                             daemon=True).start()

        mode_descs = {
            "增量": "收藏夹将绑定此来源。\n后续可一键更新，仅追加新游戏，已有的不变。",
            "增量+辅助": "收藏夹将绑定此来源。\n追加新游戏，并额外生成「多的/少的」对比收藏夹。",
            "替换": "⚠️ 收藏夹将绑定此来源。\n后续更新时清空原内容，用最新列表完全替换。",
            "无": "不绑定来源，仅执行一次操作。\n后续无法一键更新。",
        }
        mode_desc_var = tk.StringVar(value=mode_descs["增量"])

        # 左列：模式选择 + 说明
        left_frame = tk.Frame(btn_frame)
        left_frame.pack(side="left", fill="y", padx=(0, 15))

        mode_row = tk.Frame(left_frame)
        mode_row.pack(anchor="w")
        tk.Label(mode_row, text="模式：", font=("微软雅黑", 9)).pack(side="left")
        mode_combo = ttk.Combobox(mode_row, values=["增量", "增量+辅助", "替换", "无"],
                                  width=8, state="readonly")
        mode_combo.set("增量")
        mode_combo.pack(side="left")
        mode_combo.bind("<<ComboboxSelected>>",
                        lambda e: mode_desc_var.set(mode_descs.get(mode_combo.get(), "")))

        desc_label = tk.Label(left_frame, textvariable=mode_desc_var,
                 font=("微软雅黑", 8), fg="#666",
                 width=28, anchor="nw", justify="left")
        desc_label.pack(anchor="w", pady=(4, 0))

        # 右列：按钮竖排
        right_frame = tk.Frame(btn_frame)
        right_frame.pack(side="left", fill="y")

        if target_col:
            update_btn = ttk.Button(right_frame, text="🔄 更新",
                                    command=do_target_update, width=16)
            update_btn.pack(fill="x", pady=2)
            ttk.Button(right_frame, text="取消",
                       command=cur_win.destroy, width=16).pack(fill="x", pady=2)
        else:
            btn1 = ttk.Button(right_frame, text="📁 建立为新收藏夹",
                              command=do_create, width=16)
            btn1.pack(fill="x", pady=2)
            btn_widgets.append(btn1)
            btn2 = ttk.Button(right_frame, text="📥 导出为 TXT 文件",
                              command=do_export, width=16)
            btn2.pack(fill="x", pady=2)
            btn_widgets.append(btn2)
            btn3 = ttk.Button(right_frame, text="🔄️ 更新现有收藏夹",
                              command=do_update, width=16)
            btn3.pack(fill="x", pady=2)
            btn_widgets.append(btn3)

        self._center_window(cur_win)
