"""SteamShelf — 账号选择界面（来自软件 A，适配统一账号模型）"""

import platform
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from account_manager import SteamAccountScanner
from ui_utils import bg_thread

try:
    from cef_bridge import CEFBridge
except ImportError:
    CEFBridge = None


class SteamToolboxIntro:
    """
    SteamShelf 账号选择界面
    负责选中单个 Steam 账号并启动主界面
    """

    def intro_ui(self):
        """启动账号选择界面"""
        accounts = SteamAccountScanner.scan_accounts()

        if not accounts:
            self._show_no_account_ui()
            return

        self._show_launch_ui(accounts)

    def _launch_main(self, account, cef_bridge=None):
        """启动主界面（标签页版本）"""
        # lazy import 避免循环依赖（ui_main 导入了本模块）
        from ui_main import SteamToolboxMain
        main_ui = SteamToolboxMain(account, self.intro_ui)
        if cef_bridge is not None:
            main_ui._cef_bridge = cef_bridge
        main_ui.show_main_window()

    def _show_no_account_ui(self):
        """未找到账号时的界面"""
        root = tk.Tk()
        root.title("SteamShelf")
        root.resizable(False, False)

        tk.Label(root, text="❌ 自动发现 Steam 账号失败", font=("微软雅黑", 14, "bold"), fg="red").pack(pady=20)
        tk.Label(root,
                 text="请确保:\n1. Steam 已安装并登录\n2. 至少有一个账号的 userdata 目录存在",
                 font=("微软雅黑", 10), justify="left").pack(padx=30, pady=10)

        ttk.Button(root, text="🔄 重新扫描",
                   command=lambda: (root.destroy(), self.intro_ui())
                   ).pack(pady=20)

        root.update_idletasks()
        cw, ch = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{cw}x{ch}+{int((sw - cw) / 2)}+{int((sh - ch) / 2)}")
        root.mainloop()

    def _show_launch_ui(self, accounts):
        """账号选择 + 两个启动按钮"""
        root = tk.Tk()
        root.title("SteamShelf")
        root.resizable(False, False)

        has_cef = CEFBridge is not None

        # 标题
        tk.Label(root, text="🎮 SteamShelf",
                 font=("微软雅黑", 14, "bold")).pack(pady=(20, 10))

        # Steam 运行状态指示区
        steam_status_frame = tk.Frame(root)
        steam_status_frame.pack(fill="x", padx=25, pady=(0, 8))
        steam_status_label = tk.Label(steam_status_frame, text="🔍 检测 Steam 状态中...",
                                      font=("微软雅黑", 9), fg="#666", anchor="w")
        steam_status_label.pack(side="left", fill="x", expand=True)

        # 在后台检测 Steam 进程状态并更新显示
        def _refresh_steam_status():
            if not has_cef:
                steam_status_label.config(
                    text="⚠️ websocket-client 未安装，云同步不可用", fg="#cc6600")
                return

            def _detect():
                proc_info = CEFBridge.detect_steam_process()
                port_open = CEFBridge.is_port_open()
                cef_available = CEFBridge.is_available()

                # 如果 CEF 可用，尝试获取登录账号
                logged_in_id3 = None
                login_error = None
                if cef_available:
                    try:
                        bridge = CEFBridge()
                        ok, err = bridge.connect()
                        if ok:
                            if bridge.is_steam_fully_loaded():
                                logged_in_id3 = bridge.get_logged_in_steam_id3()
                                if logged_in_id3 is None:
                                    login_error = "API 无法获取"
                            else:
                                login_error = "Steam 仍在加载"
                            bridge.disconnect()
                        else:
                            login_error = f"连接失败: {err}"
                    except Exception as e:
                        login_error = str(e)

                def _update():
                    try:
                        if not proc_info['running']:
                            steam_status_label.config(
                                text="⬜ Steam 未运行", fg="#999")
                        elif cef_available and logged_in_id3:
                            acct_name = str(logged_in_id3)
                            matched_idx = None
                            for i, acc in enumerate(accounts):
                                if acc.friend_code == str(logged_in_id3):
                                    acct_name = f"{acc.persona_name} ({acc.friend_code})"
                                    matched_idx = i
                                    break
                            steam_status_label.config(
                                text=f"✅ Steam 运行中（CEF 就绪）— 登录: {acct_name}",
                                fg="#2e7d32")
                            if matched_idx is not None:
                                listbox.selection_clear(0, "end")
                                listbox.selection_set(matched_idx)
                                listbox.see(matched_idx)
                        elif cef_available:
                            steam_status_label.config(
                                text=f"✅ Steam 运行中（CEF 就绪）— 登录账号: 检测失败（{login_error}）",
                                fg="#2e7d32")
                        elif port_open:
                            steam_status_label.config(
                                text="🟡 Steam 运行中，CEF 端口已开放但尚未就绪（加载中）",
                                fg="#cc6600")
                        elif proc_info['cef_arg'] is False:
                            steam_status_label.config(
                                text="🟡 Steam 运行中，但未启用 CEF 调试端口",
                                fg="#cc6600")
                        elif proc_info['cef_arg'] is True:
                            steam_status_label.config(
                                text="🟡 Steam 运行中（带 CEF 参数），端口未就绪",
                                fg="#cc6600")
                        else:
                            steam_status_label.config(
                                text="🟡 Steam 运行中，CEF 状态未知",
                                fg="#cc6600")
                    except tk.TclError:
                        pass  # intro 窗口已销毁

                root.after(0, _update)

            threading.Thread(target=bg_thread(_detect), daemon=True).start()

        _refresh_steam_status()

        # 账号列表
        tk.Label(root, text="选择账号：", font=("微软雅黑", 10), anchor="w").pack(fill="x", padx=25)
        listbox = tk.Listbox(root, width=50, height=min(len(accounts), 6),
                             font=("微软雅黑", 10), selectmode="browse")
        listbox.pack(fill="x", padx=25, pady=(0, 15))
        for acc in accounts:
            listbox.insert("end", f"{acc.persona_name}  ({acc.friend_code})")
        listbox.selection_set(0)
        listbox.bind("<Double-1>", lambda e: launch_file())

        # 状态
        status_label = tk.Label(root, text="", font=("微软雅黑", 9), fg="#666", wraplength=400)
        status_label.pack(pady=(0, 5))

        # 按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=(0, 15))

        def get_account():
            sel = listbox.curselection()
            if not sel:
                messagebox.showwarning("提示", "请选择一个账号。")
                return None
            return accounts[sel[0]]

        def launch_cef():
            account = get_account()
            if not account:
                return

            # 将 friend_code 转为 int 以与 CEF 返回值比较
            account_id3 = int(account.friend_code)

            # 如果 CEF 已就绪，尝试直连
            if CEFBridge.is_available():
                bridge = CEFBridge()
                ok, _ = bridge.connect()
                if ok:
                    # 确保 Steam 已完全加载
                    if bridge.is_steam_fully_loaded():
                        cef_id3 = bridge.get_logged_in_steam_id3()
                        if cef_id3 is None or cef_id3 == account_id3:
                            # 匹配或无法判断 → 直接进入（保持 bridge 连接）
                            root.destroy()
                            self._launch_main(account, cef_bridge=bridge)
                            return
                        bridge.disconnect()
                        # 不匹配
                        status_label.config(
                            text=f"Steam 登录的账号({cef_id3})与所选不匹配，正在重启 Steam...",
                            fg="#cc6600")
                    else:
                        bridge.disconnect()
                        # CEF 端口在但 Steam 还没完全加载，等一下
                        status_label.config(
                            text="检测到 CEF 端口，等待 Steam 完全加载...",
                            fg="#1a6dcc")
                        cef_btn.config(state="disabled")
                        file_btn.config(state="disabled")
                        listbox.config(state="disabled")
                        _poll_cancelled[0] = False
                        cancel_btn.pack(side="left", padx=8)
                        _poll(account, 0)
                        return

            # 需要（重新）启动 Steam
            cef_btn.config(state="disabled")
            file_btn.config(state="disabled")
            listbox.config(state="disabled")
            _poll_cancelled[0] = False
            cancel_btn.pack(side="left", padx=8)
            status_label.config(text="正在关闭并重启 Steam...", fg="#1a6dcc")

            def do_start():
                ok, msg = CEFBridge.launch_steam_with_cef()
                if not ok:
                    root.after(0, lambda: (
                        status_label.config(text=f"❌ {msg}", fg="red"),
                        cef_btn.config(state="normal"),
                        file_btn.config(state="normal"),
                        listbox.config(state="normal"),
                    ))
                    return
                root.after(0, lambda: _poll(account, 0))

            threading.Thread(target=bg_thread(do_start), daemon=True).start()

        def _poll(account, n):
            if _poll_cancelled[0]:
                return
            max_wait = 120  # 最多等 240 秒（120 × 2）
            account_id3 = int(account.friend_code)
            if n > max_wait:
                cancel_btn.pack_forget()
                status_label.config(
                    text="⏰ 等待超时。点击「诊断 CEF 连接」查看详细原因。", fg="red")
                cef_btn.config(state="normal")
                file_btn.config(state="normal")
                listbox.config(state="normal")
                return

            elapsed = n * 2

            # 阶段1: 检测 CEF 端口是否已开放
            if not CEFBridge.is_port_open():
                dots = "." * (n % 4 + 1)
                # 每 20 秒额外检查一下 Steam 进程状态
                extra = ""
                if n > 0 and n % 10 == 0:
                    proc = CEFBridge.detect_steam_process()
                    if not proc['running']:
                        extra = "（⚠️ 未检测到 Steam 进程）"
                    elif proc['cef_arg'] is False:
                        extra = "（⚠️ Steam 未带 CEF 参数启动）"
                status_label.config(
                    text=f"正在等待 Steam 启动{dots}（{elapsed}秒）{extra}",
                    fg="#1a6dcc")
                root.after(2000, lambda: _poll(account, n + 1))
                return

            # 阶段2: 端口开放了，检查 SharedJSContext 是否就绪
            if not CEFBridge.is_available():
                dots = "." * (n % 4 + 1)
                status_label.config(
                    text=f"Steam 正在加载{dots}（{elapsed}秒）",
                    fg="#1a6dcc")
                root.after(2000, lambda: _poll(account, n + 1))
                return

            # 阶段3: SharedJSContext 可用，尝试连接并校验
            bridge = CEFBridge()
            ok, err = bridge.connect()
            if not ok:
                dots = "." * (n % 4 + 1)
                status_label.config(
                    text=f"正在连接 Steam{dots}（{elapsed}秒）",
                    fg="#1a6dcc")
                root.after(2000, lambda: _poll(account, n + 1))
                return

            # 阶段4: 已连接，检查 Steam 是否完全加载
            if not bridge.is_steam_fully_loaded():
                bridge.disconnect()
                dots = "." * (n % 4 + 1)
                status_label.config(
                    text=f"等待 Steam 登录完成{dots}（{elapsed}秒）",
                    fg="#1a6dcc")
                root.after(2000, lambda: _poll(account, n + 1))
                return

            # 阶段5: Steam 已就绪，校验账号
            cef_id3 = bridge.get_logged_in_steam_id3()

            if cef_id3 is not None and cef_id3 != account_id3:
                bridge.disconnect()
                cancel_btn.pack_forget()
                status_label.config(
                    text=f"❌ Steam 登录的账号({cef_id3})与所选({account.friend_code})不匹配，\n"
                         f"请在 Steam 里切换账号后重试。", fg="red")
                cef_btn.config(state="normal")
                file_btn.config(state="normal")
                listbox.config(state="normal")
                return

            if cef_id3 is None:
                # 无法确定账号，但 Steam 已完全加载，仍允许进入
                status_label.config(
                    text=f"⚠️ 无法确认 Steam 登录账号（API 未返回），但 Steam 已就绪。\n"
                         f"将以所选账号 {account.persona_name} ({account.friend_code}) 进入...",
                    fg="#cc6600")
            else:
                # 找到匹配的账号名
                acct_display = str(cef_id3)
                for acc in accounts:
                    if acc.friend_code == str(cef_id3):
                        acct_display = f"{acc.persona_name} ({cef_id3})"
                        break
                status_label.config(
                    text=f"✅ Steam 登录账号: {acct_display}，正在进入主界面...",
                    fg="#2e7d32")

            cancel_btn.pack_forget()
            root.after(400, lambda b=bridge: (
                root.destroy(), self._launch_main(account, cef_bridge=b)))

        def launch_file():
            account = get_account()
            if not account:
                return
            root.destroy()
            self._launch_main(account)

        cef_btn = ttk.Button(btn_frame, text="☁️ 云同步模式启动（推荐）",
                             command=launch_cef,
                             width=22, state="normal" if has_cef else "disabled")
        cef_btn.pack(side="left", padx=8)

        file_btn = ttk.Button(btn_frame, text="📁 本地模式启动",
                              command=launch_file, width=14)
        file_btn.pack(side="left", padx=8)

        _poll_cancelled = [False]

        def _cancel_poll():
            _poll_cancelled[0] = True
            cancel_btn.pack_forget()
            status_label.config(text="已取消。", fg="#666")
            cef_btn.config(state="normal" if has_cef else "disabled")
            file_btn.config(state="normal")
            listbox.config(state="normal")

        cancel_btn = ttk.Button(btn_frame, text="取消",
                                command=_cancel_poll)


        if not has_cef:
            tk.Label(root, text="⚠️ 云同步需安装: pip install websocket-client",
                     font=("微软雅黑", 8), fg="red").pack()

        # 调试按钮
        def _show_debug():
            if not has_cef:
                messagebox.showinfo("调试信息", "websocket-client 未安装，无法进行 CEF 诊断。")
                return

            status_label.config(text="🔍 正在运行诊断...", fg="#1a6dcc")
            root.update()

            def _run_diag():
                diag = CEFBridge.diagnose()
                lines = [
                    "═══ Steam CEF 诊断报告 ═══",
                    "",
                    f"平台: {diag['platform']}",
                    f"Steam 进程运行: {'是' if diag['steam_running'] else '否'}",
                ]
                if diag['steam_processes']:
                    lines.append("检测到的进程:")
                    for p in diag['steam_processes']:
                        lines.append(f"  {p}")
                lines.append(f"CEF 启动参数: {'已检测到' if diag['cef_arg_detected'] else '未检测到' if diag['cef_arg_detected'] is False else '未知'}")
                lines.append(f".cef-enable-remote-debugging 文件: {'已存在' if diag.get('cef_file_exists') else '不存在'}")
                if diag.get('cef_file_path'):
                    lines.append(f"  路径: {diag['cef_file_path']}")
                # .cef-enable-remote-debugging 文件
                if diag.get('cef_file_path'):
                    exists_str = '存在 ✅' if diag['cef_file_exists'] else '不存在 ❌'
                    lines.append(f".cef-enable-remote-debugging: {exists_str}")
                    lines.append(f"  路径: {diag['cef_file_path']}")
                lines.append("")
                lines.append(f"TCP 端口 {CEFBridge.CEF_PORT}: {'可连' if diag['tcp_port_open'] else '不可连'}")
                if diag['tcp_error']:
                    lines.append(f"  TCP 错误: {diag['tcp_error']}")
                # lsof 输出
                if diag.get('lsof_output'):
                    lines.append("lsof 端口检测:")
                    for ln in diag['lsof_output'].split('\n'):
                        lines.append(f"  {ln}")
                lines.append(f"HTTP /json: {'可达' if diag['http_reachable'] else '不可达'}")
                if diag['http_status']:
                    lines.append(f"  HTTP 状态码: {diag['http_status']}")
                if diag['http_error']:
                    lines.append(f"  HTTP 错误: {diag['http_error']}")
                if diag['targets'] is not None:
                    lines.append(f"CEF Targets ({len(diag['targets'])} 个):")
                    for t in diag['targets']:
                        lines.append(f"  • {t}")
                lines.append(f"SharedJSContext: {'已就绪' if diag['shared_js_ready'] else '未就绪'}")
                lines.append("")
                lines.append("── 诊断结论 ──")
                lines.append(diag['summary'])
                text = '\n'.join(lines)

                def _show():
                    status_label.config(text="", fg="#666")
                    _show_debug_window(root, text)
                root.after(0, _show)

            threading.Thread(target=bg_thread(_run_diag), daemon=True).start()

        def _show_debug_window(parent, text):
            """显示调试信息窗口"""
            win = tk.Toplevel(parent)
            win.title("CEF 诊断报告")
            win.resizable(True, True)

            text_widget = tk.Text(win, wrap="word", font=("Menlo" if platform.system() == "Darwin" else "Consolas", 10),
                                  width=70, height=25)
            text_widget.pack(fill="both", expand=True, padx=10, pady=10)
            text_widget.insert("1.0", text)
            text_widget.config(state="disabled")

            btn_frame2 = tk.Frame(win)
            btn_frame2.pack(pady=(0, 10))
            ttk.Button(btn_frame2, text="📋 复制到剪贴板",
                       command=lambda: (win.clipboard_clear(), win.clipboard_append(text))
                       ).pack(side="left", padx=5)
            ttk.Button(btn_frame2, text="🔄 重新检测",
                       command=lambda: (win.destroy(), _show_debug())
                       ).pack(side="left", padx=5)
            ttk.Button(btn_frame2, text="关闭", command=win.destroy
                       ).pack(side="left", padx=5)

        bottom_frame = tk.Frame(root)
        bottom_frame.pack(pady=(0, 10))
        debug_btn = ttk.Button(bottom_frame, text="🔧 诊断 CEF 连接",
                               command=_show_debug,
                               state="normal" if has_cef else "disabled")
        debug_btn.pack()
        refresh_btn = ttk.Button(bottom_frame, text="🔄 刷新状态",
                                 command=_refresh_steam_status)
        refresh_btn.pack(pady=(3, 0))

        root.update_idletasks()
        cw, ch = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{cw}x{ch}+{int((sw - cw) / 2)}+{int((sh - ch) / 2)}")
        root.mainloop()
