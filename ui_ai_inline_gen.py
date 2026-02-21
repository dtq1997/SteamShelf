"""ui_ai_inline_gen.py — 内联 AI 生成（InlineAIGenMixin）

在库管理标签页的游戏列表下方提供 AI 生成游戏说明功能，
无需打开独立窗口。

宿主协议：InlineAIGenHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import InlineAIGenHost  # noqa: F401

import re
import threading
import time
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

try:
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

from ai_generator import SteamAIGenerator, AI_SYSTEM_PROMPT
from core_notes import (
    CONFIDENCE_EMOJI, INFO_VOLUME_EMOJI, QUALITY_EMOJI,
    INFO_SOURCE_WEB, INFO_SOURCE_LOCAL, INSUFFICIENT_INFO_MARKER,
    WARN_STEAM_UNAVAIL, WARN_STEAM_REVIEW_UNAVAIL,
    is_ai_note,
)
from steam_data import (
    get_game_name_from_steam, get_game_details_from_steam,
    format_game_context, get_game_reviews_from_steam, format_review_context,
)


class InlineAIGenMixin:
    """内联 AI 生成控件，混入 SteamToolboxMain 使用。"""

    # ────────────────────── UI 构建 ──────────────────────

    def _build_inline_ai_controls(self, parent):
        """在游戏列表下方构建 AI 生成控件

        容器 frame（side=BOTTOM）包住所有 AI 控件：
          1. 完整进度区（进度条 + 日志）— 展开时可见
          2. 精简进度行 — 收起时可见
          3. 操作行（🤖按钮 + 勾选项 + 暂停/停止/收起）— 始终可见

        容器大小随内容变化，Treeview（expand=True）自动伸缩。
        """
        # 状态
        self._inline_ai_running = False
        self._inline_ai_paused = False
        self._inline_ai_stopped = False
        self._inline_ai_queue = []
        self._inline_collapsed = False

        # ── 容器（pack 到 parent 底部，Treeview 填充剩余空间） ──
        container = tk.Frame(parent)
        container.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))

        # ── 完整进度区域（容器内，初始隐藏） ──
        self._inline_ai_progress_frame = tk.Frame(container)

        self._inline_progress_var = tk.StringVar(value="")
        tk.Label(self._inline_ai_progress_frame,
                 textvariable=self._inline_progress_var,
                 font=("微软雅黑", 9), fg="#333", anchor=tk.W
                 ).pack(fill=tk.X)

        self._inline_progress_bar = ttk.Progressbar(
            self._inline_ai_progress_frame, length=300)
        self._inline_progress_bar.pack(fill=tk.X, pady=(2, 0))

        log_frame = tk.Frame(self._inline_ai_progress_frame)
        log_frame.pack(fill=tk.X, pady=(4, 0))
        self._inline_log_text = tk.Text(
            log_frame, height=6, font=("微软雅黑", 8), wrap=tk.WORD,
            state=tk.DISABLED, bg="#fafafa", relief=tk.SUNKEN, bd=1)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL,
                                   command=self._inline_log_text.yview)
        self._inline_log_text.config(yscrollcommand=log_scroll.set)
        self._inline_log_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ── 精简进度行（收起时显示，初始隐藏） ──
        self._inline_compact_frame = tk.Frame(container)
        self._inline_compact_var = tk.StringVar(value="")
        tk.Label(self._inline_compact_frame,
                 textvariable=self._inline_compact_var,
                 font=("微软雅黑", 9), fg="#555", anchor=tk.W
                 ).pack(fill=tk.X)

        # ── 操作行（始终可见） ──
        self._inline_action_frame = tk.Frame(container)
        self._inline_action_frame.pack(fill=tk.X)

        self._web_search_mode = "local"  # "local" / "ai_web"
        self._inline_gen_btn = ttk.Button(
            self._inline_action_frame, text="🤖 AI 生成游戏说明",
            command=self._show_ai_gen_menu)
        self._inline_gen_btn.pack(side=tk.LEFT)

        self._inline_skip_existing_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self._inline_action_frame, text="跳过已有AI笔记",
            variable=self._inline_skip_existing_var,
            style="Filter.TCheckbutton"
        ).pack(side=tk.LEFT, padx=(10, 0))

        # ── 控制按钮（操作行右侧，初始不 pack） ──
        # pack 顺序：collapse 先 pack(RIGHT) 到最右，stop 次之，pause 最左
        self._inline_collapse_btn = ttk.Button(
            self._inline_action_frame, text="收起", width=4,
            command=self._inline_toggle_collapse)
        self._inline_stop_btn = ttk.Button(
            self._inline_action_frame, text="⏹ 停止", width=6,
            command=self._inline_ai_stop)
        self._inline_pause_btn = ttk.Button(
            self._inline_action_frame, text="⏸ 暂停", width=6,
            command=self._inline_ai_pause)

    # ────────────────────── AI 生成弹出菜单 ──────────────────────

    def _show_ai_gen_menu(self):
        """弹出 AI 生成菜单（提示词 + 搜索模式选择即生成）"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="📝 提示词设置",
                         command=self._open_prompt_editor)
        menu.add_separator()
        modes = [
            ("local", "📚 本地搜索 + 生成"),
            ("ai_web", "🌐 AI联网搜索 + 生成"),
        ]
        for mode, label in modes:
            prefix = "✓ " if mode == self._web_search_mode else "   "
            menu.add_command(
                label=prefix + label,
                command=lambda m=mode: self._gen_with_mode(m))
        btn = self._inline_gen_btn
        menu_h = menu.yposition("end") + 30
        menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() - menu_h)

    def _gen_with_mode(self, mode):
        """设置搜索模式并立即触发生成"""
        self._web_search_mode = mode
        self._inline_ai_generate()

    # ────────────────────── 日志 ──────────────────────

    def _inline_log(self, msg):
        self._inline_log_text.config(state=tk.NORMAL)
        self._inline_log_text.insert(tk.END, msg + "\n")
        self._inline_log_text.see(tk.END)
        self._inline_log_text.config(state=tk.DISABLED)

    # ────────────────────── 按钮状态 ──────────────────────

    def _inline_update_buttons(self):
        if self._inline_ai_running and not self._inline_ai_paused:
            self._inline_gen_btn.config(state=tk.DISABLED)
            self._inline_pause_btn.config(state=tk.NORMAL, text="⏸ 暂停")
            self._inline_stop_btn.config(state=tk.NORMAL)
        elif self._inline_ai_running and self._inline_ai_paused:
            self._inline_gen_btn.config(state=tk.DISABLED)
            self._inline_pause_btn.config(state=tk.NORMAL, text="▶ 继续")
            self._inline_stop_btn.config(state=tk.NORMAL)
        else:
            self._inline_gen_btn.config(state=tk.NORMAL)

    def _inline_show_ctrl_buttons(self):
        """显示操作行右侧的控制按钮（暂停/停止/收起）"""
        # 逐个检查并 pack，避免某个按钮已 mapped 导致其余按钮被跳过
        if not self._inline_collapse_btn.winfo_ismapped():
            self._inline_collapse_btn.pack(side=tk.RIGHT)
        if not self._inline_stop_btn.winfo_ismapped():
            self._inline_stop_btn.pack(side=tk.RIGHT, padx=(0, 4))
        if not self._inline_pause_btn.winfo_ismapped():
            self._inline_pause_btn.pack(side=tk.RIGHT, padx=(0, 4))

    def _inline_hide_ctrl_buttons(self):
        """隐藏操作行右侧的控制按钮"""
        self._inline_pause_btn.pack_forget()
        self._inline_stop_btn.pack_forget()
        self._inline_collapse_btn.pack_forget()

    # ────────────────────── 显示/隐藏/收起 ──────────────────────

    def _inline_ai_show_progress(self):
        """显示完整进度区（action 上方）+ 控制按钮"""
        self._inline_collapsed = False
        self._inline_compact_frame.pack_forget()
        if not self._inline_ai_progress_frame.winfo_ismapped():
            self._inline_ai_progress_frame.pack(
                fill=tk.X, before=self._inline_action_frame, pady=(0, 4))
        self._inline_show_ctrl_buttons()
        self._inline_collapse_btn.config(text="收起")

    def _inline_toggle_collapse(self):
        """收起/展开进度区"""
        if not self._inline_ai_running:
            # 生成结束后，收起 = 隐藏一切
            self._inline_ai_progress_frame.pack_forget()
            self._inline_compact_frame.pack_forget()
            self._inline_hide_ctrl_buttons()
            return
        if self._inline_collapsed:
            # 展开：隐藏精简行，显示完整进度
            self._inline_collapsed = False
            self._inline_compact_frame.pack_forget()
            self._inline_ai_progress_frame.pack(
                fill=tk.X, before=self._inline_action_frame, pady=(0, 4))
            self._inline_collapse_btn.config(text="收起")
        else:
            # 收起：隐藏完整进度，显示精简行
            self._inline_collapsed = True
            self._inline_ai_progress_frame.pack_forget()
            self._inline_compact_frame.pack(
                fill=tk.X, before=self._inline_action_frame, pady=(0, 2))
            self._inline_collapse_btn.config(text="展开")

    # ────────────────────── 暂停 / 停止 ──────────────────────

    def _inline_ai_pause(self):
        if not self._inline_ai_running:
            return
        if self._inline_ai_paused:
            # 继续
            self._inline_ai_paused = False
            resume_list = list(self._inline_ai_queue)
            if resume_list:
                self._inline_ai_running = False
                self._inline_update_buttons()
                self._inline_log("▶️ 继续生成...")
                self._inline_start_worker(resume_list)
            else:
                self._inline_ai_running = False
                self._inline_update_buttons()
                self._inline_progress_var.set("队列为空")
        else:
            # 暂停
            self._inline_ai_paused = True
            self._inline_update_buttons()
            self._inline_progress_var.set("⏸️ 正在暂停（等待当前游戏完成）...")
            self._inline_log("⏸️ 正在暂停...")

    def _inline_ai_stop(self):
        if not self._inline_ai_running:
            return
        self._inline_ai_stopped = True
        self._inline_ai_paused = False
        self._inline_progress_var.set("⏹️ 正在停止...")
        self._inline_log("⏹️ 正在停止...（等待当前游戏完成）")

    # ────────────────────── 提示词编辑 ──────────────────────

    def _open_prompt_editor(self):
        """独立窗口编辑 AI 系统提示词"""
        from ai_generator import AI_SYSTEM_PROMPT
        pw = tk.Toplevel(self.root)
        pw.title("📝 AI 系统提示词")
        pw.transient(self.root)
        pw.grab_set()
        tk.Label(pw, text="自定义 AI 生成游戏说明时使用的系统提示词（留空则使用默认）",
                 font=("", 9), fg="#666").pack(padx=15, pady=(10, 5), anchor=tk.W)
        pt = tk.Text(pw, width=70, height=18, wrap=tk.WORD, font=("微软雅黑", 9))
        pt.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 5))
        saved_p = self._config.get("ai_system_prompt", "")
        pt.insert("1.0", saved_p if saved_p else AI_SYSTEM_PROMPT)
        pb = tk.Frame(pw)
        pb.pack(fill=tk.X, padx=15, pady=(0, 10))

        def _reset():
            pt.delete("1.0", tk.END)
            pt.insert("1.0", AI_SYSTEM_PROMPT)

        def _save():
            p = pt.get("1.0", tk.END).strip()
            if p and p != AI_SYSTEM_PROMPT.strip():
                self._config["ai_system_prompt"] = p
            else:
                self._config.pop("ai_system_prompt", None)
            self._save_config(self._config)
            messagebox.showinfo("✅", "系统提示词已保存。", parent=pw)
            pw.destroy()

        ttk.Button(pb, text="🔄 恢复默认", command=_reset).pack(side=tk.LEFT)
        ttk.Button(pb, text="💾 保存", command=_save).pack(side=tk.RIGHT)
        ttk.Button(pb, text="取消", command=pw.destroy).pack(side=tk.RIGHT, padx=5)
        self._center_window(pw)

    # ────────────────────── 生成入口 ──────────────────────

    def _inline_ai_generate(self):
        if self._inline_ai_running:
            return

        try:
            self._inline_ai_generate_impl()
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("❌ 错误",
                f"启动 AI 生成时出错：\n{e}", parent=self.root)

    def _inline_ai_generate_impl(self):
        # 获取活跃令牌
        tokens = self._get_ai_tokens()
        idx = self._get_active_token_index()
        if not tokens or idx >= len(tokens):
            messagebox.showwarning("提示",
                "未配置 AI 令牌，请在「⚙️ 设置 → 🔑 AI 配置」中添加。",
                parent=self.root)
            return
        token = tokens[idx]
        api_key = token.get("key", "")
        if not api_key:
            messagebox.showwarning("提示",
                "当前令牌未配置 API Key，请在「⚙️ 设置 → 🔑 AI 配置」中设置。",
                parent=self.root)
            return

        # 获取选中的游戏
        aids = self._get_selected_app_ids()
        if not aids:
            messagebox.showinfo("提示", "请先在列表中选择游戏。",
                                parent=self.root)
            return

        games_list = []
        for aid in aids:
            name = self._game_name_cache.get(aid, "")
            games_list.append((aid, name))

        # 先显示进度区域、清空日志（这样跳过消息也能看到）
        self._inline_log_text.config(state=tk.NORMAL)
        self._inline_log_text.delete("1.0", tk.END)
        self._inline_log_text.config(state=tk.DISABLED)
        self._inline_ai_show_progress()

        # 跳过已有 AI 笔记
        if self._inline_skip_existing_var.get():
            filtered = []
            for aid, name in games_list:
                existing = self.manager.read_notes(aid).get("notes", [])
                if any(is_ai_note(n) for n in existing):
                    self._inline_log(f"⏭️ 跳过 {name or aid} (已有 AI 笔记)")
                else:
                    filtered.append((aid, name))
            games_list = filtered

        if not games_list:
            self._inline_log("所有选中的游戏都已有 AI 笔记。")
            self._inline_progress_var.set("无需生成")
            return

        self._inline_start_worker(games_list)

    # ────────────────────── Worker 启动 ──────────────────────

    def _inline_start_worker(self, games_list):
        self._inline_ai_running = True
        self._inline_ai_paused = False
        self._inline_ai_stopped = False
        self._inline_ai_queue = list(games_list)
        self._inline_update_buttons()
        self._inline_progress_bar["maximum"] = len(games_list)
        self._inline_progress_bar["value"] = 0

        # 读取令牌配置
        tokens = self._get_ai_tokens()
        idx = self._get_active_token_index()
        token = tokens[idx]
        api_key = token.get("key", "")
        provider = token.get("provider", "anthropic")
        model = token.get("model", "")
        custom_url = token.get("api_url", "") or None
        if not model:
            pinfo = SteamAIGenerator.PROVIDERS.get(provider, {})
            model = pinfo.get("default_model", "claude-sonnet-4-5-20250929")

        # 系统提示词
        custom_prompt = self._config.get("ai_system_prompt", "").strip()
        if not custom_prompt:
            custom_prompt = AI_SYSTEM_PROMPT

        generator = SteamAIGenerator(
            api_key, model, provider=provider, api_url=custom_url,
            advanced_params=self._config.get("ai_advanced_params", {}))

        self._inline_log(
            f"🚀 开始生成 {len(games_list)} 个游戏的说明"
            f"（{provider} / {model}）")

        thread = threading.Thread(
            target=self._inline_ai_worker,
            args=(list(games_list), generator, custom_prompt),
            daemon=True)
        thread.start()

    # ────────────────────── Worker 线程 ──────────────────────

    def _inline_ai_worker(self, games_list, generator, custom_prompt):
        total = len(games_list)
        success_count = 0
        fail_count = 0
        processed = 0
        ws_mode = self._web_search_mode

        while self._inline_ai_queue:
            if self._inline_ai_stopped:
                self.root.after(0, lambda s=success_count, f=fail_count:
                    self._inline_log(
                        f"⏹️ 已停止。成功 {s} / 失败 {f}"))
                break

            if self._inline_ai_paused:
                self._inline_on_paused(success_count, fail_count)
                return

            aid, name = self._inline_ai_queue[0]

            # 跳过上传中的游戏
            if self.is_app_uploading(aid):
                self.root.after(0, lambda a=aid, n=name:
                    self._inline_log(
                        f"☁️⬆ 跳过 {n or a} (AppID {a})：正在上传中"))
                self._inline_ai_queue.pop(0)
                processed += 1
                continue

            # 获取游戏名称
            if not name:
                self.root.after(0, lambda a=aid:
                    self._inline_log(f"🔍 查询 AppID {a} 的游戏名..."))
                try:
                    name = get_game_name_from_steam(aid)
                except Exception:
                    name = f"AppID {aid}"

            # 更新进度
            idx = processed
            display_name = name or f"AppID {aid}"
            self.root.after(0, lambda i=idx, dn=display_name, t=total: (
                self._inline_progress_var.set(
                    f"正在处理 {i+1}/{t}: {dn}..."),
                self._inline_compact_var.set(
                    f"正在处理 {i+1}/{t}: {dn}"),
                self._inline_progress_bar.configure(value=i)))

            # 获取上下文 + 生成 + 保存
            try:
                game_context, name, steam_warns = \
                    self._inline_fetch_context(aid, name)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.root.after(0, lambda n=name:
                        self._inline_log(
                            f"⛔ {n}: Steam API 限速 (429)，已停止生成"))
                    self._inline_ai_stopped = True
                    break
                raise
            self.root.after(0, lambda a=aid, n=name, ws=ws_mode:
                self._inline_log(
                    f"🤖 生成中: {n} (AppID {a})"
                    f" [{ws}]..."))

            ok = self._inline_generate_and_save(
                aid, name, generator, custom_prompt,
                ws_mode, game_context, steam_warns)
            if ok is True:
                success_count += 1
            elif ok is None:
                continue  # 429 限速重试，不 pop
            else:
                fail_count += 1

            self._inline_ai_queue.pop(0)
            processed += 1
            if self._inline_ai_queue and not self._inline_ai_stopped:
                time.sleep(2)

        # 完成
        if not self._inline_ai_paused:
            self.root.after(0, lambda s=success_count, f=fail_count:
                self._inline_finish(s, f))

    def _inline_on_paused(self, success, fail):
        """暂停时更新 UI 状态"""
        r = len(self._inline_ai_queue)
        def _update(s=success, f=fail, r=r):
            self._inline_progress_var.set(
                f"⏸️ 已暂停 — 完成 {s}，失败 {f}，剩余 {r}")
            self._inline_compact_var.set(f"⏸️ 已暂停，剩余 {r} 款待处理")
            self._inline_log(f"⏸️ 已暂停，剩余 {r} 款待处理")
        self.root.after(0, _update)
        self._inline_ai_running = True
        self.root.after(0, self._inline_update_buttons)

    def _inline_fetch_context(self, aid, name):
        """获取游戏详情+评测，返回 (context_str, updated_name, steam_warns)"""
        self.root.after(0, lambda a=aid, n=name:
            self._inline_log(f"📋 获取 {n} 的详细信息..."))
        game_context = ""
        _details_ok = False
        _reviews_ok = False
        try:
            details = get_game_details_from_steam(aid)
            _details_ok = True  # API 调用成功即可，游戏无商店页不算故障
            if details:
                game_context = format_game_context(details)
                if details.get("name") and name.startswith("AppID"):
                    name = details["name"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.root.after(0, lambda n=name:
                    self._inline_log(
                        f"⚠️ {n}: Steam 商店 API 限速，跳过详情"))
        except Exception:
            pass
        self.root.after(0, lambda a=aid, n=name:
            self._inline_log(f"💬 获取 {n} 的玩家评测..."))
        try:
            reviews_data = get_game_reviews_from_steam(aid)
            _reviews_ok = True  # API 调用成功即可，游戏没评测不算故障
            if reviews_data:
                review_ctx = format_review_context(reviews_data)
                if review_ctx:
                    game_context = ((game_context + "\n\n" + review_ctx)
                                    if game_context else review_ctx)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.root.after(0, lambda n=name:
                    self._inline_log(
                        f"⚠️ {n}: Steam 评测 API 限速，跳过评测"))
        except Exception:
            pass
        # 分别标注商店详情和评测的故障状态
        steam_warns = []
        if not _details_ok:
            steam_warns.append(WARN_STEAM_UNAVAIL)
        if not _reviews_ok:
            steam_warns.append(WARN_STEAM_REVIEW_UNAVAIL)
        return game_context, name, steam_warns

    # ────────────────────── 单游戏生成+保存 ──────────────────────

    def _inline_generate_and_save(self, aid, name, generator,
                                   custom_prompt, ws_mode, game_context,
                                   steam_warns=None):
        """生成单个游戏的 AI 笔记并保存，返回 True/False"""
        try:
            (content, actual_model, confidence,
             info_volume, is_insufficient, quality) = \
                generator.generate_note(
                    name, aid, extra_context=game_context,
                    system_prompt=custom_prompt,
                    web_search_mode=ws_mode)
        except urllib.error.HTTPError as e:
            return self._inline_handle_http_error(aid, e)
        except Exception as e:
            self.root.after(0, lambda a=aid, err=e:
                self._inline_log(f"❌ AppID {a}: {err}"))
            return False

        # 搜索故障检测
        search_warn = getattr(generator, '_last_search_warn', '')
        if search_warn:
            self.root.after(0, lambda n=name, a=aid, w=search_warn:
                self._inline_log(
                    f"⚠️ {n} (AppID {a}): {w}"))

        # 构建信息源故障标签（只显示故障源，用 | 分隔）
        all_warns = list(steam_warns or [])
        if search_warn:
            all_warns.append(search_warn)
        source_status = "|".join(all_warns)

        if is_insufficient:
            return self._inline_save_insufficient(
                aid, name, actual_model, info_volume,
                ws_mode, source_status)
        if not content.strip():
            self.root.after(0, lambda a=aid:
                self._inline_log(f"⚠️ AppID {a}: API 返回空内容"))
            return False
        return self._inline_save_normal(
            aid, name, content, actual_model, confidence,
            info_volume, quality, ws_mode, source_status)

    def _inline_handle_http_error(self, aid, e):
        """处理 HTTP 错误。返回 False=失败，None=429重试"""
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        self.root.after(0, lambda a=aid, err=e, body=error_body:
            self._inline_log(
                f"❌ AppID {a}: HTTP {err.code} — {body[:200]}"))
        if e.code == 401:
            self.root.after(0, lambda:
                self._inline_log(
                    "💡 401 认证失败，请检查 API Key 是否有效。"))
            self._inline_ai_stopped = True
        elif e.code == 429:
            self.root.after(0, lambda:
                self._inline_log("⏳ 触发限速，等待 60 秒..."))
            time.sleep(60)
            return None  # 重试同一游戏
        return False

    def _inline_save_insufficient(self, aid, name, model, info_volume,
                                   ws_mode, source_status=""):
        """保存信息过少标注笔记"""
        vol_emoji = INFO_VOLUME_EMOJI.get(info_volume, "")
        info_source_tag = INFO_SOURCE_WEB if ws_mode == "ai_web" else INFO_SOURCE_LOCAL
        date_str = datetime.now().strftime("%Y-%m-%d")
        source_suffix = f" |{source_status}" if source_status else ""
        flat = (f"🤖AI: {INSUFFICIENT_INFO_MARKER} "
                f"{info_source_tag} | "
                f"相关信息量：{info_volume}{vol_emoji} "
                f"该游戏相关信息过少，无法生成有效的游戏说明。"
                f"（由 {model} 判定）"
                f" 📅生成于 {date_str}{source_suffix}")
        self.manager.create_note(aid, flat, flat)
        self.root.after(0, lambda a=aid, n=name, v=info_volume:
            self._inline_log(
                f"⛔ 信息过少: {n} (AppID {a}) "
                f"[信息量: {v}] — 已生成标注性笔记"))
        return True

    def _inline_save_normal(self, aid, name, content, model,
                             confidence, info_volume, quality, ws_mode,
                             source_status=""):
        """格式化并保存正常 AI 笔记"""
        conf_emoji = CONFIDENCE_EMOJI.get(confidence, "")
        vol_emoji = INFO_VOLUME_EMOJI.get(info_volume, "")
        qual_emoji = QUALITY_EMOJI.get(quality, "")
        info_source_tag = INFO_SOURCE_WEB if ws_mode == "ai_web" else INFO_SOURCE_LOCAL
        source_suffix = f" |{source_status}" if source_status else ""
        date_str = datetime.now().strftime("%Y-%m-%d")

        flat_content = ' '.join(content.strip().splitlines())
        flat_content = re.sub(
            r'\[/?[a-z0-9*]+(?:=[^\]]*)?\]', '', flat_content).strip()
        ai_prefix = (
            f"🤖AI: {info_source_tag} | "
            f"相关信息量：{info_volume}{vol_emoji} | "
            f"游戏总体质量：{quality}{qual_emoji} "
            f"⚠️ 以下内容由 {model} 生成，"
            f"该模型对以下内容的确信程度："
            f"{confidence}{conf_emoji}。")
        flat_content = (f"{ai_prefix} {flat_content}"
                        f" 📅生成于 {date_str}{source_suffix}")

        # 覆盖模式：删除旧 AI 笔记
        if not self._inline_skip_existing_var.get():
            data = self.manager.read_notes(aid)
            notes_list = data.get("notes", [])
            had_old = any(is_ai_note(n) for n in notes_list)
            if had_old:
                data["notes"] = [n for n in notes_list
                                 if not is_ai_note(n)]
                self.manager.write_notes(aid, data)

        self.manager.create_note(aid, flat_content, flat_content)
        self.root.after(0, lambda a=aid, n=name, c=confidence,
                        v=info_volume, q=quality:
            self._inline_log(
                f"✅ 完成: {n} (AppID {a}) "
                f"[确信: {c}] [信息量: {v}] [质量: {q}]"))
        return True

    # ────────────────────── 生成完成 ──────────────────────

    def _inline_finish(self, success, fail):
        """生成全部完成后更新 UI 状态"""
        self._inline_progress_bar["value"] = \
            self._inline_progress_bar["maximum"]
        if self._inline_ai_stopped:
            final = f"⏹️ 已停止 — 成功 {success} / 失败 {fail}"
        else:
            final = f"✅ 完成！成功 {success} / 失败 {fail}"
        self._inline_progress_var.set(final)
        self._inline_compact_var.set(final)
        self._inline_log(f"\n{'='*40}")
        self._inline_log(f"✅ 成功: {success}  ❌ 失败: {fail}")
        self._inline_ai_running = False
        self._inline_ai_stopped = False
        self._inline_update_buttons()
        self._inline_pause_btn.pack_forget()
        self._inline_stop_btn.pack_forget()
        self._refresh_games_list()
