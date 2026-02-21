"""ui_settings_ai.py — AI 令牌管理

从 ui_settings.py 拆分。"""

import tkinter as tk
from tkinter import messagebox, ttk
from ai_generator import SteamAIGenerator


def build_ai_settings_ui(app):
    """API Key 与 AI 配置管理窗口 — 支持多令牌管理"""
    win = tk.Toplevel(app.root)
    win.title("🔑 API Key 与 AI 配置")
    win.resizable(False, False)
    win.grab_set()

    # ── 顶部标题 ──
    tk.Label(win, text="🔑 API Key 与 AI 配置", font=("", 13, "bold")).pack(pady=(15, 5))
    config_info_frame = tk.Frame(win)
    config_info_frame.pack(pady=(0, 5))
    tk.Label(config_info_frame, text="管理多个 AI 令牌，在 AI 生成页面可自由切换。",
             font=("", 9), fg="#666").pack()
    config_path_row = tk.Frame(config_info_frame)
    config_path_row.pack()
    tk.Label(config_path_row, text="配置存储于: ",
             font=("", 9), fg="#666").pack(side=tk.LEFT)
    config_link = tk.Label(config_path_row, text="~/.steam_notes_gen/",
                           font=("", 9, "underline"), fg="#4a90d9", cursor="hand2")
    config_link.pack(side=tk.LEFT)
    config_link.bind("<Button-1>", lambda e: app._open_config_dir())

    # ── 左右两栏容器 ──
    body = tk.Frame(win)
    body.pack(fill=tk.BOTH, padx=20, pady=(5, 0))

    left = tk.Frame(body)
    left.pack(side=tk.LEFT, fill=tk.BOTH)

    right = tk.Frame(body)
    right.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))

    # ══════════ 左栏：令牌列表 ══════════
    tokens_frame = tk.LabelFrame(left, text="🔑 已保存的 AI 令牌", font=("", 10),
                                  padx=10, pady=5)
    tokens_frame.pack(fill=tk.X, pady=(0, 5))

    tokens_data = list(app._get_ai_tokens())
    active_idx = [app._get_active_token_index()]

    tokens_listbox = tk.Listbox(tokens_frame, font=("", 9), height=4,
                                 exportselection=False)
    tokens_listbox.pack(fill=tk.X, pady=(0, 5))

    def _save_tokens():
        app._save_ai_tokens(tokens_data, active_idx[0])

    def _refresh_token_list():
        sel = tokens_listbox.curselection()
        sel_idx = sel[0] if sel else None
        tokens_listbox.delete(0, tk.END)
        for i, t in enumerate(tokens_data):
            prefix = "★ " if i == active_idx[0] else "   "
            key_preview = t.get("key", "")
            if len(key_preview) > 10:
                key_preview = key_preview[:6] + "..." + key_preview[-4:]
            prov_name = SteamAIGenerator.PROVIDERS.get(
                t.get("provider", ""), {}).get("name", t.get("provider", ""))
            tokens_listbox.insert(tk.END,
                f"{prefix}{t.get('name', '未命名')}  |  {prov_name}  |  "
                f"{t.get('model', '')}  |  Key: {key_preview}")
            if i == active_idx[0]:
                tokens_listbox.itemconfig(i, fg="#1a73e8")
        if sel_idx is not None and sel_idx < len(tokens_data):
            tokens_listbox.selection_set(sel_idx)

    _refresh_token_list()

    tokens_btn_row = tk.Frame(tokens_frame)
    tokens_btn_row.pack(fill=tk.X)

    def _delete_token():
        sel = tokens_listbox.curselection()
        if not sel:
            messagebox.showwarning("提示", "请先选择要删除的令牌。", parent=win)
            return
        idx = sel[0]
        name = tokens_data[idx].get("name", "")
        if not messagebox.askyesno("确认", f"确定删除令牌「{name}」？", parent=win):
            return
        tokens_data.pop(idx)
        if active_idx[0] >= len(tokens_data):
            active_idx[0] = max(0, len(tokens_data) - 1)
        elif active_idx[0] > idx:
            active_idx[0] -= 1
        _refresh_token_list()
        _save_tokens()

    def _set_default():
        sel = tokens_listbox.curselection()
        if not sel:
            return
        active_idx[0] = sel[0]
        _refresh_token_list()
        _save_tokens()

    def _move_token(delta):
        sel = tokens_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(tokens_data):
            return
        tokens_data[idx], tokens_data[new_idx] = \
            tokens_data[new_idx], tokens_data[idx]
        if active_idx[0] == idx:
            active_idx[0] = new_idx
        elif active_idx[0] == new_idx:
            active_idx[0] = idx
        tokens_listbox.selection_clear(0, tk.END)
        tokens_listbox.selection_set(new_idx)
        _refresh_token_list()
        _save_tokens()

    def _load_to_form():
        sel = tokens_listbox.curselection()
        if not sel:
            return
        t = tokens_data[sel[0]]
        name_var.set(t.get("name", ""))
        pk = t.get("provider", "anthropic")
        pn = provider_names.get(pk, provider_names.get("anthropic", ""))
        provider_var.set(pn)
        ai_key_var.set(t.get("key", ""))
        url_var.set(t.get("api_url", ""))
        _on_provider_changed()
        model_var.set(t.get("model", ""))

    ttk.Button(tokens_btn_row, text="🗑️ 删除", style="Toolbutton",
               command=_delete_token).pack(side=tk.LEFT, padx=(0, 5))
    ttk.Button(tokens_btn_row, text="★ 设为默认", style="Toolbutton",
               command=_set_default).pack(side=tk.LEFT, padx=5)
    ttk.Button(tokens_btn_row, text="📝 加载到表单", style="Toolbutton",
               command=_load_to_form).pack(side=tk.LEFT, padx=5)
    ttk.Button(tokens_btn_row, text="▲", style="Toolbutton",
               command=lambda: _move_token(-1)).pack(side=tk.RIGHT, padx=1)
    ttk.Button(tokens_btn_row, text="▼", style="Toolbutton",
               command=lambda: _move_token(1)).pack(side=tk.RIGHT, padx=1)

    # ══════════ 左栏：令牌编辑表单 ══════════
    form_frame = tk.LabelFrame(left, text="➕ 添加 / 修改令牌", font=("", 10),
                                padx=10, pady=5)
    form_frame.pack(fill=tk.X, pady=(5, 0))

    form = tk.Frame(form_frame)
    form.pack(fill=tk.X)
    row = 0

    tk.Label(form, text="令牌名称:", font=("", 10)).grid(
        row=row, column=0, sticky=tk.W, pady=3)
    name_var = tk.StringVar()
    tk.Entry(form, textvariable=name_var, width=30,
             font=("", 9)).grid(row=row, column=1, sticky=tk.W, pady=3, padx=(10, 0),
                                 columnspan=2)
    row += 1

    tk.Label(form, text="AI 提供商:", font=("", 10)).grid(
        row=row, column=0, sticky=tk.W, pady=3)
    provider_names = {k: v['name'] for k, v in SteamAIGenerator.PROVIDERS.items()}
    provider_var = tk.StringVar(value=provider_names.get("anthropic", ""))
    provider_combo = ttk.Combobox(form, textvariable=provider_var, width=30,
                                   values=list(provider_names.values()), state='readonly')
    provider_combo.grid(row=row, column=1, sticky=tk.W, pady=3, padx=(10, 0), columnspan=2)
    row += 1

    def _provider_key_from_name(display_name):
        for k, v in provider_names.items():
            if v == display_name:
                return k
        return 'anthropic'

    tk.Label(form, text="API Key:", font=("", 10)).grid(
        row=row, column=0, sticky=tk.W, pady=3)
    ai_key_var = tk.StringVar()
    ai_key_entry = tk.Entry(form, textvariable=ai_key_var, width=40,
                             font=("", 9), show="•")
    ai_key_entry.grid(row=row, column=1, sticky=tk.W, pady=3, padx=(10, 0))

    def toggle_show_ai():
        if ai_key_entry.cget("show") == "•":
            ai_key_entry.config(show="")
            show_ai_btn.config(text="🙈")
        else:
            ai_key_entry.config(show="•")
            show_ai_btn.config(text="👁️")
    show_ai_btn = ttk.Button(form, text="👁️", style="Toolbutton",
                              command=toggle_show_ai)
    show_ai_btn.grid(row=row, column=2, padx=3)
    row += 1

    tk.Label(form, text="模型:", font=("", 10)).grid(
        row=row, column=0, sticky=tk.W, pady=3)
    model_var = tk.StringVar()
    model_combo = ttk.Combobox(form, textvariable=model_var, width=35, values=[])
    model_combo.grid(row=row, column=1, sticky=tk.W, pady=3, padx=(10, 0), columnspan=2)
    row += 1

    tk.Label(form, text="API URL:", font=("", 10)).grid(
        row=row, column=0, sticky=tk.W, pady=3)
    url_var = tk.StringVar()
    tk.Entry(form, textvariable=url_var, width=40,
             font=("", 9)).grid(row=row, column=1, sticky=tk.W, pady=3, padx=(10, 0),
                                 columnspan=2)
    row += 1

    url_hint = tk.Label(form, text="", font=("", 8), fg="#888")
    url_hint.grid(row=row, column=0, sticky=tk.W, columnspan=3)
    row += 1

    def _on_provider_changed(*_):
        pk = _provider_key_from_name(provider_combo.get())
        pi = SteamAIGenerator.PROVIDERS.get(pk, {})
        model_combo['values'] = pi.get('models', [])
        if not model_var.get() or model_var.get() not in pi.get('models', []):
            dm = pi.get('default_model', '')
            if dm:
                model_var.set(dm)
        du = pi.get('api_url', '')
        url_hint.config(text=f"留空使用默认: {du}" if du else "⚠️ 请填写 API URL")
        if not name_var.get().strip():
            name_var.set(pi.get('name', pk))
    provider_combo.bind("<<ComboboxSelected>>", _on_provider_changed)
    _on_provider_changed()

    form_btn_row = tk.Frame(form_frame)
    form_btn_row.pack(fill=tk.X, pady=(5, 0))

    def _save_as_new():
        key = ai_key_var.get().strip()
        if not key:
            messagebox.showwarning("提示", "请输入 API Key。", parent=win)
            return
        token = {
            "name": name_var.get().strip() or "未命名",
            "key": key,
            "provider": _provider_key_from_name(provider_var.get()),
            "model": model_var.get().strip(),
            "api_url": url_var.get().strip(),
        }
        tokens_data.append(token)
        if len(tokens_data) == 1:
            active_idx[0] = 0
        _refresh_token_list()
        _save_tokens()

    def _update_selected():
        sel = tokens_listbox.curselection()
        if not sel:
            messagebox.showwarning("提示", "请先在上方列表中选择要更新的令牌。", parent=win)
            return
        key = ai_key_var.get().strip()
        if not key:
            messagebox.showwarning("提示", "请输入 API Key。", parent=win)
            return
        idx = sel[0]
        tokens_data[idx] = {
            "name": name_var.get().strip() or "未命名",
            "key": key,
            "provider": _provider_key_from_name(provider_var.get()),
            "model": model_var.get().strip(),
            "api_url": url_var.get().strip(),
        }
        _refresh_token_list()
        _save_tokens()

    ttk.Button(form_btn_row, text="➕ 添加为新令牌",
               command=_save_as_new).pack(side=tk.LEFT, padx=(0, 5))
    ttk.Button(form_btn_row, text="💾 更新选中令牌",
               command=_update_selected).pack(side=tk.LEFT, padx=5)

    # ══════════ 右栏：高级参数 ══════════
    adv_frame = tk.LabelFrame(right, text="⚙️ AI 高级参数",
                               font=("", 10), padx=8, pady=4)
    adv_frame.pack(fill=tk.BOTH, expand=True)

    _adv = app._config.get("ai_advanced_params", {})

    adv_grid = tk.Frame(adv_frame)
    adv_grid.pack(fill=tk.X)

    _adv_vars = {}

    _adv_fields = [
        ("web_search_max_uses", "搜索次数上限",
         SteamAIGenerator.DEFAULT_WEB_SEARCH_MAX_USES,
         "每次生成最多搜几次 (1-10)"),
        ("thinking_budget", "思维预算",
         SteamAIGenerator.DEFAULT_THINKING_BUDGET,
         "thinking 模型内部推理的 token 预算"),
        ("max_extra_context", "参考资料上限",
         SteamAIGenerator.DEFAULT_MAX_EXTRA_CONTEXT,
         "Steam 评测等素材的最大字符数"),
        ("max_tokens", "输出上限",
         SteamAIGenerator.DEFAULT_MAX_TOKENS,
         "非 thinking 模型的最大输出 tokens"),
        ("max_tokens_thinking", "思维输出上限",
         SteamAIGenerator.DEFAULT_MAX_TOKENS_THINKING,
         "thinking 模型的最大输出 tokens"),
        ("timeout", "请求超时(秒)",
         SteamAIGenerator.DEFAULT_TIMEOUT,
         "普通请求的超时时间"),
        ("timeout_web_search", "搜索超时(秒)",
         SteamAIGenerator.DEFAULT_TIMEOUT_WEB_SEARCH,
         "联网搜索的超时时间"),
    ]

    for ar, (key, label, default, tip) in enumerate(_adv_fields):
        _lbl = tk.Label(adv_grid, text=label, font=("", 9))
        _lbl.grid(row=ar, column=0, sticky=tk.W, pady=1)
        _lbl._tip_text = tip
        var = tk.IntVar(value=_adv.get(key, default))
        _adv_vars[key] = (var, default)
        sp = tk.Spinbox(adv_grid, textvariable=var, from_=1,
                        to=99999, width=6, font=("", 9))
        sp.grid(row=ar, column=1, sticky=tk.W, padx=(6, 0), pady=1)

    adv_bottom = tk.Frame(adv_frame)
    adv_bottom.pack(fill=tk.X, pady=(2, 0))

    _adv_tip_label = tk.Label(adv_bottom, text="悬停标签查看说明",
                               font=("", 8), fg="#999", anchor=tk.W)
    _adv_tip_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

    for child in adv_grid.winfo_children():
        if hasattr(child, '_tip_text'):
            _t = child._tip_text
            child.bind("<Enter>",
                lambda e, tip=_t: (
                    _adv_tip_label.config(text=tip, fg="#666"),
                    e.widget.config(fg="#4a90d9")))
            child.bind("<Leave>",
                lambda e: (
                    _adv_tip_label.config(text="悬停标签查看说明", fg="#999"),
                    e.widget.config(fg="#000")))

    def _reset_adv_defaults():
        for _k, (_v, _d) in _adv_vars.items():
            _v.set(_d)

    ttk.Button(adv_bottom, text="↩ 默认", style="Toolbutton",
               command=_reset_adv_defaults).pack(side=tk.RIGHT)

    # ── 关闭时保存高级参数 ──
    def _save_adv_and_close():
        adv_dict = {}
        for key, (var, default) in _adv_vars.items():
            try:
                val = var.get()
            except (tk.TclError, ValueError):
                val = default
            if val != default:
                adv_dict[key] = val
        if adv_dict:
            app._config["ai_advanced_params"] = adv_dict
        elif "ai_advanced_params" in app._config:
            del app._config["ai_advanced_params"]
        app._save_config(app._config)
        win.destroy()

    btn_frame = tk.Frame(win, padx=20)
    btn_frame.pack(fill=tk.X, pady=(8, 15))
    ttk.Button(btn_frame, text="关闭", command=_save_adv_and_close).pack(side=tk.RIGHT, padx=5)
    win.protocol("WM_DELETE_WINDOW", _save_adv_and_close)

    app._center_window(win)
