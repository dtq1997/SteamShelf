"""ui_settings_steam.py — Steam 数据源配置（Steam API Key、Cookie、IGDB 凭证）

从 ui_settings.py 拆分。
"""

import os
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk


def build_steam_data_settings_ui(app):
    """Steam Web API Key 配置窗口"""
    win = tk.Toplevel(app.root)
    win.title("🔑 Steam Web API Key")
    win.resizable(False, False)

    tk.Label(win, text="🔑 Steam Web API Key",
             font=("", 13, "bold")).pack(pady=(15, 10))

    # ── 启用/禁用开关 ──
    has_key = bool(app._config.get("steam_web_api_key", ""))
    enabled_var = tk.BooleanVar(value=has_key)

    toggle_frame = tk.Frame(win, padx=20)
    toggle_frame.pack(fill=tk.X)
    tk.Checkbutton(toggle_frame, text="启用 Steam Web API Key",
                   variable=enabled_var, font=("", 10),
                   command=lambda: _on_toggle()).pack(anchor=tk.W)

    # ── 好处说明 ──
    info_frame = tk.Frame(win, padx=25)
    info_frame.pack(fill=tk.X, pady=(4, 8))
    tk.Label(info_frame, text=(
        "配置后的好处：\n"
        "• 游戏名称获取更快更完整（通过 GetAppList API 批量获取）\n"
        "• 未配置时仍可使用，但部分游戏可能只显示 AppID"),
        font=("", 9), fg="#666", justify=tk.LEFT).pack(anchor=tk.W)

    # ── Key 输入区 ──
    key_frame = tk.LabelFrame(win, text="API Key", font=("", 10),
                               padx=10, pady=8)
    key_frame.pack(fill=tk.X, padx=20, pady=(0, 8))

    key_row = tk.Frame(key_frame)
    key_row.pack(fill=tk.X)

    steam_var = tk.StringVar(value=app._config.get("steam_web_api_key", ""))
    steam_entry = tk.Entry(key_row, textvariable=steam_var, width=40,
                           font=("", 9), show="•")
    steam_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def toggle_show():
        if steam_entry.cget("show") == "•":
            steam_entry.config(show="")
            show_btn.config(text="🙈")
        else:
            steam_entry.config(show="•")
            show_btn.config(text="👁️")
    show_btn = tk.Button(key_row, text="👁️", font=("", 9),
                         relief=tk.FLAT, command=toggle_show)
    show_btn.pack(side=tk.LEFT, padx=(3, 0))

    steam_status = tk.Label(key_frame, text="", font=("", 8), fg="green")
    steam_status.pack(anchor=tk.W)

    save_btn = ttk.Button(key_frame, text="💾 保存", command=lambda: _save())
    save_btn.pack(anchor=tk.W, pady=(3, 0))

    # ── 获取方法 ──
    help_frame = tk.LabelFrame(win, text="📖 如何获取",
                                font=("", 10), padx=10, pady=8)
    help_frame.pack(fill=tk.X, padx=20, pady=(0, 8))

    help_text = tk.Label(help_frame, text=(
        "1. 访问 Steam Web API Key 注册页面\n"
        "2. 登录 Steam 账号\n"
        "3. 填写域名（随意填写，如 localhost）\n"
        "4. 点击「注册」，复制生成的 Key"),
        font=("", 9), fg="#555", justify=tk.LEFT)
    help_text.pack(anchor=tk.W)

    link = tk.Label(help_frame,
                    text="🔗 https://steamcommunity.com/dev/apikey",
                    font=("", 9, "underline"), fg="#1a73e8", cursor="hand2")
    link.pack(anchor=tk.W, pady=(4, 0))
    link.bind("<Button-1>", lambda e: webbrowser.open(
        "https://steamcommunity.com/dev/apikey"))

    def _on_toggle():
        on = enabled_var.get()
        for w in (steam_entry, show_btn, save_btn):
            w.config(state=tk.NORMAL if on else tk.DISABLED)
        if not on:
            # 禁用时清除已保存的 key
            app._config.pop("steam_web_api_key", None)
            app._save_config(app._config)
            steam_status.config(text="⚫ 已禁用", fg="#888")

    # 初始状态
    if not has_key:
        for w in (steam_entry, show_btn, save_btn):
            w.config(state=tk.DISABLED)

    def _save():
        sk = steam_var.get().strip()
        if sk:
            app._config["steam_web_api_key"] = sk
        elif "steam_web_api_key" in app._config:
            del app._config["steam_web_api_key"]
        app._save_config(app._config)
        steam_status.config(text="✅ 已保存" if sk else "✅ 已清除",
                            fg="green")

    ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(10, 15))
    app._center_window(win)


def build_cookie_manager_ui(app):
    """打开全局 Cookie 管理界面"""
    if not app._ensure_collections_core():
        return
    cookie_win = tk.Toplevel(app.root)
    cookie_win.title("管理登录态 Cookie")
    cookie_win.resizable(False, False)

    # 说明区域
    guide_frame = tk.Frame(cookie_win)
    guide_frame.pack(fill="x", padx=20, pady=(15, 10))

    guide_text = tk.Text(guide_frame, font=("微软雅黑", 9), height=6,
                         bg=cookie_win.cget("bg"), relief="flat",
                         wrap="word")
    guide_text.tag_config("bold", font=("微软雅黑", 9, "bold"))
    guide_text.tag_config("orange", foreground="orange")
    guide_text.insert("end", "Cookie 的用途：\n", "bold")
    guide_text.insert("end",
        "配置 Steam 登录态 Cookie + 代理后，从鉴赏家列表获取游戏时可以抓取")
    guide_text.insert("end", "成人游戏", "orange")
    guide_text.insert("end",
        "。\n\nCookie 值内含短期令牌（约 1-2 天过期），浏览器会自动刷新，"
        "但复制出来的值不会。过期后需重新从浏览器复制。"
        "修改密码或退出登录会使 Cookie 立即失效。")
    guide_text.config(state="disabled")
    guide_text.pack(fill="x")

    # 当前状态
    status_frame = tk.Frame(cookie_win)
    status_frame.pack(fill="x", padx=20, pady=(0, 10))

    def _parse_cookie_expiry(cookie_val):
        """从 steamLoginSecure 的 JWT 中解析过期时间"""
        import base64, json as _json
        from urllib.parse import unquote
        from datetime import datetime
        try:
            decoded = unquote(cookie_val)
            parts = decoded.split("||")
            if len(parts) < 2:
                return None
            jwt_parts = parts[1].split(".")
            if len(jwt_parts) < 2:
                return None
            payload_b64 = jwt_parts[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = _json.loads(
                base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            if exp:
                return datetime.fromtimestamp(exp)
        except Exception:
            pass
        return None

    def _cookie_status_text(cookie_val):
        """生成 Cookie 状态文本（含过期时间）"""
        import time
        from datetime import datetime
        if not cookie_val:
            return "⚠️ 当前状态：未配置 Cookie", "orange"
        expiry = _parse_cookie_expiry(cookie_val)
        if expiry:
            now = datetime.now()
            if expiry < now:
                return (f"❌ 当前状态：Cookie 已过期"
                        f"（{expiry:%Y-%m-%d %H:%M}）"), "red"
            days_left = (expiry - now).days
            return (f"🔐 当前状态：已配置 Cookie"
                    f"（{expiry:%Y-%m-%d %H:%M} 过期，"
                    f"剩余 {days_left} 天）"), "green"
        return "🔐 当前状态：已配置 Cookie", "green"

    saved_cookie = app._collections_core.get_saved_cookie()
    _status_text, _status_fg = _cookie_status_text(saved_cookie)
    status_label = tk.Label(status_frame, text=_status_text,
        font=("微软雅黑", 10, "bold"), fg=_status_fg)
    status_label.pack(anchor="w")

    # 获取方法说明
    help_frame = tk.LabelFrame(cookie_win,
        text="📖 获取 Cookie 的方法",
        font=("微软雅黑", 10, "bold"), padx=15, pady=10)
    help_frame.pack(fill="x", padx=20, pady=(0, 10))

    help_tw = tk.Text(help_frame, font=("微软雅黑", 9), height=6,
                      bg=help_frame.cget("bg"), relief="flat",
                      wrap="word", cursor="arrow")
    help_tw.tag_config("link", foreground="blue", underline=True)
    help_tw.tag_config("copy", foreground="#c04000",
                       font=("Consolas", 9, "bold"))

    def _hand(e):
        help_tw.config(cursor="hand2")

    def _arrow(e):
        help_tw.config(cursor="arrow")

    help_tw.tag_bind("link", "<Enter>", _hand)
    help_tw.tag_bind("link", "<Leave>", _arrow)
    help_tw.tag_bind("link", "<Button-1>",
                     lambda e: webbrowser.open(
                         "https://store.steampowered.com"))
    help_tw.tag_bind("copy", "<Enter>", _hand)
    help_tw.tag_bind("copy", "<Leave>", _arrow)

    def _copy_cookie_name(e):
        cookie_win.clipboard_clear()
        cookie_win.clipboard_append("steamLoginSecure")
        messagebox.showinfo("已复制",
            "「steamLoginSecure」已复制到剪贴板，"
            "可粘贴到筛选栏中。", parent=cookie_win)

    help_tw.tag_bind("copy", "<Button-1>", _copy_cookie_name)
    help_tw.insert("end", "1. 用浏览器登录 ")
    help_tw.insert("end", "store.steampowered.com", "link")
    help_tw.insert("end", "\n2. 按 F12 打开开发者工具\n"
                   "3. 切换到 Application（应用）标签页\n"
                   "4. 左侧找到 Cookies → store.steampowered.com\n"
                   "5. 点击复制 ")
    help_tw.insert("end", "steamLoginSecure", "copy")
    help_tw.insert("end", " 到筛选栏中筛选，然后复制其 Value 值")
    help_tw.config(state="disabled")
    help_tw.pack(anchor="w", fill="x")

    # Cookie 输入区域
    input_frame = tk.LabelFrame(cookie_win, text="🔑 输入 Cookie",
        font=("微软雅黑", 10, "bold"), padx=15, pady=10)
    input_frame.pack(fill="x", padx=20, pady=(0, 10))

    cookie_var = tk.StringVar(value=saved_cookie)
    cookie_entry = tk.Entry(input_frame, textvariable=cookie_var,
                            width=45, font=("微软雅黑", 9), show="•")
    cookie_entry.pack(fill="x", pady=(0, 8))

    btn_frame = tk.Frame(input_frame)
    btn_frame.pack(fill="x")

    def toggle_show():
        if cookie_entry.cget('show') == '•':
            cookie_entry.config(show='')
            show_btn.config(text="🙈 隐藏")
        else:
            cookie_entry.config(show='•')
            show_btn.config(text="👁 显示")

    def save_cookie():
        val = cookie_var.get().strip()
        if val:
            app._collections_core.save_cookie(val)
            txt, fg = _cookie_status_text(val)
            status_label.config(text=txt, fg=fg)
            messagebox.showinfo("保存成功",
                "✅ Cookie 已保存！\n\n"
                "此 Cookie 将用于所有鉴赏家列表的获取。",
                parent=cookie_win)
        else:
            messagebox.showwarning("提示",
                "请先输入 Cookie 值。", parent=cookie_win)

    def clear_cookie():
        if messagebox.askyesno("确认清除",
                "确定要清除已保存的 Cookie 吗？",
                parent=cookie_win):
            cookie_var.set("")
            app._collections_core.clear_saved_cookie()
            txt, fg = _cookie_status_text("")
            status_label.config(text=txt, fg=fg)
            messagebox.showinfo("已清除", "Cookie 已清除。",
                                parent=cookie_win)

    show_btn = tk.Button(btn_frame, text="👁 显示",
                         command=toggle_show,
                         font=("微软雅黑", 9), width=10)
    show_btn.pack(side="left", padx=(0, 8))
    tk.Button(btn_frame, text="💾 保存 Cookie",
              command=save_cookie, font=("微软雅黑", 9),
              width=15).pack(side="left", padx=8)
    tk.Button(btn_frame, text="🗑 清除 Cookie",
              command=clear_cookie, font=("微软雅黑", 9),
              width=15).pack(side="left", padx=8)

    tk.Label(cookie_win,
        text="⚠️ Cookie 包含敏感信息，请勿分享配置文件给他人",
        font=("微软雅黑", 8), fg="red").pack(pady=(0, 15))

    app._center_window(cookie_win)


def build_igdb_credentials_ui(app):
    """打开 IGDB API 凭证管理界面"""
    if not app._ensure_collections_core():
        return
    igdb_win = tk.Toplevel(app.root)
    igdb_win.title("管理 IGDB API 凭证")

    # 说明区域
    guide_frame = tk.Frame(igdb_win)
    guide_frame.pack(fill="x", padx=20, pady=(15, 10))

    guide_text = tk.Text(guide_frame, font=("微软雅黑", 9), height=4,
                         bg=igdb_win.cget("bg"), relief="flat",
                         wrap="word")
    guide_text.tag_config("bold", font=("微软雅黑", 9, "bold"))
    guide_text.tag_config("purple", foreground="#7c3aed")
    guide_text.insert("end", "IGDB API 的用途：\n", "bold")
    guide_text.insert("end", "配置 IGDB API 凭证后，可以按")
    guide_text.insert("end", "游戏类型分类", "purple")
    guide_text.insert("end",
        "获取游戏列表。\nIGDB（Internet Game Database）"
        "是一个综合性的游戏数据库，由 Twitch（Amazon）运营。")
    guide_text.config(state="disabled")
    guide_text.pack(fill="x")

    # 当前状态
    status_frame = tk.Frame(igdb_win)
    status_frame.pack(fill="x", padx=20, pady=(0, 10))

    saved_id, saved_secret = \
        app._collections_core.get_igdb_credentials()
    if saved_id and saved_secret:
        status_label = tk.Label(status_frame,
            text="🔐 当前状态：已配置 IGDB API 凭证",
            font=("微软雅黑", 10, "bold"), fg="green")
    else:
        status_label = tk.Label(status_frame,
            text="⚠️ 当前状态：未配置 IGDB API 凭证",
            font=("微软雅黑", 10, "bold"), fg="orange")
    status_label.pack(anchor="w")

    # 获取方法说明
    help_frame = tk.LabelFrame(igdb_win,
        text="📖 获取 IGDB API 凭证的方法",
        font=("微软雅黑", 10, "bold"), padx=15, pady=10)
    help_frame.pack(fill="x", padx=20, pady=(0, 10))

    help_text = (
        "1. 访问 https://dev.twitch.tv/console/apps 并登录 Twitch 账号\n"
        "2. 点击「Register Your Application」注册一个应用\n"
        "3. 名称随意，OAuth Redirect URLs 填写 http://localhost\n"
        "4. 分类选择「Application Integration」\n"
        "5. 创建后点击应用，复制 Client ID\n"
        "6. 点击「New Secret」生成并复制 Client Secret")
    tk.Label(help_frame, text=help_text, font=("微软雅黑", 9),
             justify="left").pack(anchor="w")

    # 输入区域
    input_frame = tk.LabelFrame(igdb_win, text="🔑 输入 API 凭证",
        font=("微软雅黑", 10, "bold"), padx=15, pady=10)
    input_frame.pack(fill="x", padx=20, pady=(0, 10))

    id_row = tk.Frame(input_frame)
    id_row.pack(fill="x", pady=(0, 5))
    tk.Label(id_row, text="Client ID:", font=("微软雅黑", 9),
             width=12, anchor="e").pack(side="left")
    id_var = tk.StringVar(value=saved_id)
    tk.Entry(id_row, textvariable=id_var, width=45,
             font=("微软雅黑", 9)).pack(side="left", padx=(5, 0))

    secret_row = tk.Frame(input_frame)
    secret_row.pack(fill="x", pady=(0, 8))
    tk.Label(secret_row, text="Client Secret:", font=("微软雅黑", 9),
             width=12, anchor="e").pack(side="left")
    secret_var = tk.StringVar(value=saved_secret)
    secret_entry = tk.Entry(secret_row, textvariable=secret_var,
                            width=45, font=("微软雅黑", 9), show="•")
    secret_entry.pack(side="left", padx=(5, 0))

    btn_frame = tk.Frame(input_frame)
    btn_frame.pack(fill="x")

    def toggle_show():
        if secret_entry.cget('show') == '•':
            secret_entry.config(show='')
            show_btn.config(text="🙈 隐藏")
        else:
            secret_entry.config(show='•')
            show_btn.config(text="👁 显示")

    def save_credentials():
        cid = id_var.get().strip()
        csecret = secret_var.get().strip()
        if cid and csecret:
            app._collections_core.save_igdb_credentials(
                cid, csecret)
            status_label.config(
                text="🔐 当前状态：已配置 IGDB API 凭证",
                fg="green")
            messagebox.showinfo("保存成功",
                "✅ IGDB API 凭证已保存！\n\n"
                "现在可以使用「游戏类型分类」功能了。",
                parent=igdb_win)
        else:
            messagebox.showwarning("提示",
                "请填写 Client ID 和 Client Secret。",
                parent=igdb_win)

    def test_credentials():
        cid = id_var.get().strip()
        csecret = secret_var.get().strip()
        if not cid or not csecret:
            messagebox.showwarning("提示",
                "请先填写 Client ID 和 Client Secret。",
                parent=igdb_win)
            return
        app._collections_core.save_igdb_credentials(cid, csecret)
        token, error = \
            app._collections_core.get_igdb_access_token(
                force_refresh=True)
        if error:
            messagebox.showerror("测试失败",
                f"❌ 无法获取访问令牌：\n\n{error}",
                parent=igdb_win)
        else:
            messagebox.showinfo("测试成功",
                "✅ IGDB API 凭证有效！\n\n已成功获取访问令牌。",
                parent=igdb_win)
            status_label.config(
                text="🔐 当前状态：已配置 IGDB API 凭证",
                fg="green")

    def clear_credentials():
        if messagebox.askyesno("确认清除",
                "确定要清除已保存的 IGDB API 凭证吗？",
                parent=igdb_win):
            id_var.set("")
            secret_var.set("")
            app._collections_core.clear_igdb_credentials()
            status_label.config(
                text="⚠️ 当前状态：未配置 IGDB API 凭证",
                fg="orange")
            messagebox.showinfo("已清除",
                "IGDB API 凭证已清除。", parent=igdb_win)

    show_btn = tk.Button(btn_frame, text="👁 显示",
                         command=toggle_show,
                         font=("微软雅黑", 9), width=8)
    show_btn.pack(side="left", padx=(0, 5))
    tk.Button(btn_frame, text="🔍 测试凭证",
              command=test_credentials, font=("微软雅黑", 9),
              width=12).pack(side="left", padx=5)
    tk.Button(btn_frame, text="💾 保存凭证",
              command=save_credentials, font=("微软雅黑", 9),
              width=12).pack(side="left", padx=5)
    tk.Button(btn_frame, text="🗑 清除凭证",
              command=clear_credentials, font=("微软雅黑", 9),
              width=12).pack(side="left", padx=5)

    tk.Label(igdb_win,
        text="⚠️ API 凭证包含敏感信息，请勿分享配置文件给他人",
        font=("微软雅黑", 8), fg="red").pack(pady=(0, 15))
