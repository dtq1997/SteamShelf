"""AI 智能筛选（AISearchMixin）— 根据提示词从游戏库中筛选游戏

用户输入自然语言描述，AI 从游戏库中找出匹配的游戏，
可直接创建为 Steam 收藏夹或重新生成。

宿主协议：AISearchHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import AISearchHost  # noqa: F401

import json
import re
import threading
import tkinter as tk
import urllib.request
import urllib.error
from tkinter import messagebox, simpledialog, ttk

from ai_generator import SteamAIGenerator
from utils import urlopen as _urlopen
from ui_utils import bg_thread

_SYSTEM_PROMPT = (
    "你是一个 Steam 游戏库筛选助手。用户会给你一份游戏列表和一段筛选描述。\n"
    "你的任务是从列表中找出所有符合描述的游戏。\n\n"
    "用户可能还会提供补充信息（收藏夹分类、笔记摘要、IGDB分类等），\n"
    "请结合这些信息做出更准确的判断。\n\n"
    "输出格式（严格遵守）：\n"
    "1. 先输出你的筛选思路和理由，解释为什么选择这些游戏\n"
    "2. 然后输出一行分隔符：===RESULT===\n"
    "3. 最后每行输出一个匹配游戏的 AppID（纯数字）\n"
    "4. 如果没有匹配的游戏，分隔符后输出：NONE\n\n"
    "规则：\n"
    "- 只从用户提供的列表中选择，不要推荐列表外的游戏\n"
    "- 宁可多选一些相关的，也不要遗漏明显符合的\n"
    "- 筛选理由要简洁，按类别分组说明"
)


class AISearchMixin:
    """AI 智能筛选相关 UI 方法"""

    def ai_search_ui(self, target_col=None):
        """打开 AI 智能筛选窗口"""
        all_tokens = self._get_ai_tokens()
        if not all_tokens:
            messagebox.showwarning("⚠️ 未配置 AI 令牌",
                "请先在主界面点击「🔑 AI 配置」添加至少一个 AI 令牌。",
                parent=self.root)
            return

        win = tk.Toplevel(self.root)

        def _safe_after(fn):
            try:
                win.after(0, fn)
            except Exception:
                pass

        if target_col:
            win.title(f"🤖 AI 智能筛选更新「{target_col[1]}」")
        else:
            win.title("🤖 AI 智能筛选")
        win.transient(self.root)
        win.minsize(520, 400)

        # 顶部：提示词输入
        top = tk.Frame(win)
        top.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(top, text="描述你想找的游戏：",
                 font=("微软雅黑", 10, "bold")).pack(anchor=tk.W)

        prompt_text = tk.Text(top, height=3, font=("微软雅黑", 10),
                              wrap=tk.WORD)
        prompt_text.pack(fill=tk.X, pady=(4, 0))
        prompt_text.insert("1.0", "例：适合和朋友一起玩的合作游戏")
        prompt_text.bind("<FocusIn>", lambda e: self._clear_placeholder(prompt_text))

        # 信息源勾选
        src_frame = tk.Frame(win)
        src_frame.pack(fill=tk.X, padx=12, pady=(4, 0))
        tk.Label(src_frame, text="信息源：", font=("微软雅黑", 9)).pack(side=tk.LEFT)

        web_var = tk.BooleanVar(value=False)
        notes_var = tk.BooleanVar(value=False)
        colls_var = tk.BooleanVar(value=False)
        igdb_var = tk.BooleanVar(value=False)

        # 联网搜索：Anthropic provider 支持（含代理）
        active_idx = min(self._get_active_token_index(), len(all_tokens) - 1)
        _tok = all_tokens[active_idx]
        _prov = _tok.get("provider", "anthropic")
        web_cb = ttk.Checkbutton(src_frame, text="🌐 联网搜索", variable=web_var)
        web_cb.pack(side=tk.LEFT, padx=(4, 0))
        if _prov != "anthropic":
            web_cb.config(state=tk.DISABLED)

        ttk.Checkbutton(src_frame, text="📝 笔记内容",
                        variable=notes_var).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Checkbutton(src_frame, text="📁 Steam分类",
                        variable=colls_var).pack(side=tk.LEFT, padx=(4, 0))

        # IGDB：需要 CollectionsCore + 缓存
        igdb_cb = ttk.Checkbutton(src_frame, text="🎮 IGDB", variable=igdb_var)
        igdb_cb.pack(side=tk.LEFT, padx=(4, 0))
        _has_igdb = (self._collections_core is not None
                     and self._collections_core.get_igdb_cache_summary() is not None)
        if not _has_igdb:
            igdb_cb.config(state=tk.DISABLED)

        # 按钮行
        btn_row = tk.Frame(win)
        btn_row.pack(fill=tk.X, padx=12, pady=(6, 0))

        search_btn = ttk.Button(btn_row, text="🔍 开始筛选", width=14)
        search_btn.pack(side=tk.LEFT)

        status_var = tk.StringVar(value="")
        tk.Label(btn_row, textvariable=status_var,
                 font=("微软雅黑", 9), fg="#666").pack(side=tk.LEFT, padx=(8, 0))

        progress = ttk.Progressbar(btn_row, mode='indeterminate', length=120)

        # 主体：上下分栏（推理过程 + 结果列表）
        paned = ttk.PanedWindow(win, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 0))

        # 上：AI 推理过程
        reason_frame = tk.LabelFrame(paned, text="💭 AI 筛选思路",
                                      font=("微软雅黑", 9))
        reason_text = tk.Text(reason_frame, height=6, font=("微软雅黑", 9),
                              wrap=tk.WORD, state=tk.DISABLED, bg="#f8f8f8")
        reason_scroll = ttk.Scrollbar(reason_frame, orient=tk.VERTICAL,
                                       command=reason_text.yview)
        reason_text.config(yscrollcommand=reason_scroll.set)
        reason_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        reason_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        paned.add(reason_frame, weight=1)

        # 下：结果列表
        list_frame = tk.Frame(paned)
        result_tree = ttk.Treeview(list_frame,
            columns=("appid", "name"), show="headings", height=8)
        result_tree.heading("appid", text="AppID")
        result_tree.heading("name", text="游戏名称")
        result_tree.column("appid", width=70, stretch=False)
        result_tree.column("name", width=400, stretch=True)
        result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                                command=result_tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        result_tree.config(yscrollcommand=scroll.set)
        paned.add(list_frame, weight=1)

        # 底部按钮
        bottom = tk.Frame(win)
        bottom.pack(fill=tk.X, padx=12, pady=(6, 10))

        create_btn = ttk.Button(bottom, text="📁 创建为收藏夹", width=16,
                                state=tk.DISABLED)
        create_btn.pack(side=tk.LEFT, padx=(0, 6))
        regen_btn = ttk.Button(bottom, text="🔄 重新生成", width=12,
                               state=tk.DISABLED)
        regen_btn.pack(side=tk.LEFT)

        result_count_var = tk.StringVar(value="")
        tk.Label(bottom, textvariable=result_count_var,
                 font=("微软雅黑", 9), fg="#333").pack(side=tk.RIGHT)

        # 存储结果 app_ids
        matched_ids = []

        def do_search():
            prompt = prompt_text.get("1.0", tk.END).strip()
            if not prompt or prompt == "例：适合和朋友一起玩的合作游戏":
                messagebox.showwarning("提示", "请输入筛选描述。", parent=win)
                return

            games = self._lib_all_games_backup or self._lib_all_games
            if not games:
                messagebox.showwarning("提示", "游戏库为空，请先加载游戏列表。",
                                       parent=win)
                return

            search_btn.config(state=tk.DISABLED)
            regen_btn.config(state=tk.DISABLED)
            create_btn.config(state=tk.DISABLED)
            result_tree.delete(*result_tree.get_children())
            matched_ids.clear()
            status_var.set("正在调用 AI...")
            progress.pack(side=tk.LEFT, padx=(8, 0))
            progress.start(15)

            # 准备实时更新文本框
            reason_text.config(state=tk.NORMAL)
            reason_text.delete("1.0", tk.END)
            result_count_var.set("")

            def on_token(delta):
                """流式回调：每收到一段文字就追加到文本框"""
                def _append():
                    reason_text.config(state=tk.NORMAL)
                    reason_text.insert(tk.END, delta)
                    reason_text.see(tk.END)
                    reason_text.config(state=tk.DISABLED)
                _safe_after(_append)

            def worker():
                try:
                    sources = {
                        'web_search': web_var.get(),
                        'notes': notes_var.get(),
                        'collections': colls_var.get(),
                        'igdb': igdb_var.get(),
                    }
                    ids, full_text, error, usage = self._ai_search_call(
                        prompt, games, on_token=on_token, sources=sources)
                except Exception as e:
                    ids, full_text, error, usage = [], "", str(e), {}

                def on_done():
                    if not win.winfo_exists():
                        return
                    progress.stop()
                    progress.pack_forget()
                    search_btn.config(state=tk.NORMAL)
                    regen_btn.config(state=tk.NORMAL)

                    # Token 用量文本
                    token_text = ""
                    inp = usage.get("input_tokens", 0)
                    out = usage.get("output_tokens", 0)
                    if inp or out:
                        token_text = f" | Token: {inp}+{out}={inp+out}"

                    # 流式已实时显示，最终清理：只保留推理部分
                    if full_text and "===RESULT===" in full_text:
                        reasoning = full_text.split("===RESULT===", 1)[0].strip()
                        reason_text.config(state=tk.NORMAL)
                        reason_text.delete("1.0", tk.END)
                        reason_text.insert("1.0", reasoning)
                        reason_text.config(state=tk.DISABLED)

                    if error:
                        status_var.set(f"❌ {error[:60]}")
                        if token_text:
                            result_count_var.set(token_text.lstrip(" | "))
                        return
                    if not ids:
                        status_var.set("AI 未找到匹配的游戏")
                        if token_text:
                            result_count_var.set(token_text.lstrip(" | "))
                        return

                    name_map = {str(g['app_id']): g.get('name', f"AppID {g['app_id']}")
                                for g in games}
                    for aid in ids:
                        name = (name_map.get(aid)
                                or self._game_name_cache.get(aid)
                                or f"AppID {aid}")
                        result_tree.insert("", tk.END, values=(aid, name))
                    matched_ids.extend(ids)
                    status_var.set("✅ 筛选完成")
                    result_count_var.set(f"共 {len(ids)} 款游戏{token_text}")
                    create_btn.config(state=tk.NORMAL)

                _safe_after(on_done)

            threading.Thread(target=bg_thread(worker), daemon=True).start()

        def do_create():
            if not matched_ids:
                return
            if not self._ensure_collections_core():
                return
            data = self._collections_core.load_json()
            if data is None:
                return
            prompt = prompt_text.get("1.0", tk.END).strip()[:30]
            name = simpledialog.askstring("创建收藏夹", "请输入收藏夹名称：",
                                          initialvalue=f"AI: {prompt}",
                                          parent=win)
            if not name:
                return
            int_ids = [int(a) for a in matched_ids if a.isdigit()]
            filtered = self._ask_filter_owned(int_ids, parent=win)
            if filtered is None:
                return
            self._collections_core.add_static_collection(
                data, name, filtered)
            self._save_and_sync(data,
                backup_description=f"AI 智能筛选创建: {name}")
            self._ui_refresh()
            messagebox.showinfo("✅ 成功",
                f"已创建收藏夹「{name}」，包含 {len(filtered)} 款游戏。",
                parent=win)

        def do_target_update():
            if not matched_ids:
                return
            if not self._ensure_collections_core():
                return
            data = self._collections_core.load_json()
            if data is None:
                return
            col_id, col_name = target_col
            all_cols = self._collections_core.get_all_collections_with_refs(data)
            entry = None
            for c in all_cols:
                if c.get('id') == col_id:
                    entry = c['entry_ref']
                    break
            if not entry:
                messagebox.showerror("错误", "未找到目标收藏夹。", parent=win)
                return
            int_ids = [int(a) for a in matched_ids if a.isdigit()]
            a, r, t, updated = self._collections_core.perform_incremental_update(
                data, entry, int_ids, col_name, create_aux=True)
            self._save_and_sync(data, backup_description=f"AI 筛选更新: {col_name}")
            win.destroy()
            self._ui_refresh()
            disclaimer = self._collections_core.disclaimer
            if updated:
                messagebox.showinfo("更新完成",
                    f"✅「{col_name}」已更新\n新增: {a}, 移除: {r}, 总计: {t}"
                    + disclaimer, parent=self.root)
            else:
                messagebox.showinfo("已是最新",
                    f"「{col_name}」已是最新，无需更新。", parent=self.root)

        search_btn.config(command=do_search)
        regen_btn.config(command=do_search)
        if target_col:
            create_btn.config(text=f"🔄 更新「{target_col[1]}」",
                              command=do_target_update)
        else:
            create_btn.config(command=do_create)

        self._center_window(win)

    @staticmethod
    def _clear_placeholder(text_widget):
        content = text_widget.get("1.0", tk.END).strip()
        if content == "例：适合和朋友一起玩的合作游戏":
            text_widget.delete("1.0", tk.END)

    def _ai_search_call(self, prompt, games, on_token=None, sources=None):
        """调用 AI API 进行游戏筛选（流式），返回 (app_id_list, full_text, error_str)"""
        sources = sources or {}
        all_tokens = self._get_ai_tokens()
        active_idx = min(self._get_active_token_index(), len(all_tokens) - 1)
        token = all_tokens[active_idx]

        api_key = token.get("key", "")
        provider = token.get("provider", "anthropic")
        model = token.get("model", "")
        api_url = token.get("api_url", "") or None

        # 构建游戏列表文本
        lines = []
        game_aids = set()
        for g in games[:5000]:
            aid = str(g.get('app_id', ''))
            name = (self._game_name_cache.get(aid)
                    or g.get('name', '')
                    or f"AppID {aid}")
            lines.append(f"{aid}|{name}")
            game_aids.add(aid)
        game_list_text = "\n".join(lines)

        # 构建补充信息
        extra_sections = []
        if sources.get('collections'):
            coll_text = self._build_collection_context(game_aids)
            if coll_text:
                extra_sections.append(coll_text)
        if sources.get('notes'):
            notes_text = self._build_notes_context(game_aids)
            if notes_text:
                extra_sections.append(notes_text)
        if sources.get('igdb'):
            igdb_text = self._build_igdb_context(game_aids)
            if igdb_text:
                extra_sections.append(igdb_text)

        extra_block = ""
        if extra_sections:
            extra_block = (
                "\n\n===补充信息（帮助你更准确地筛选）===\n"
                + "\n".join(extra_sections)
            )

        user_msg = (
            f"以下是我的 Steam 游戏库（共 {len(lines)} 款），格式为 AppID|游戏名：\n\n"
            f"{game_list_text}"
            f"{extra_block}\n\n"
            f"请从中筛选出符合以下描述的游戏：\n{prompt}"
        )

        gen = SteamAIGenerator(api_key, model, provider=provider, api_url=api_url,
                               advanced_params=self._config.get("ai_advanced_params", {}))
        is_anthropic = (gen.provider == 'anthropic')

        req = self._ai_search_build_request(
            gen, api_key, user_msg, is_anthropic, sources)

        # 流式读取
        full_text = ""
        try:
            _timeout = 180 if sources.get('web_search') else 120
            resp = _urlopen(req, timeout=_timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            return [], "", f"HTTP {e.code}: {body}", {}
        except urllib.error.URLError as e:
            return [], "", f"连接失败: {e.reason}", {}

        usage = {}
        try:
            full_text, usage = self._read_sse_stream(resp, is_anthropic, on_token)
        except Exception as e:
            if full_text:
                pass
            else:
                return [], "", f"流式读取失败: {e}", {}
        finally:
            resp.close()

        if not full_text:
            return [], "", "AI 未返回内容", usage

        found, reasoning = self._ai_search_parse_ids(full_text, games)
        return found, reasoning or full_text, None, usage

    @staticmethod
    def _ai_search_build_request(gen, api_key, user_msg, is_anthropic, sources):
        """构建 AI 搜索的 HTTP 请求（Anthropic / OpenAI 兼容）"""
        if is_anthropic:
            _default_url = gen.PROVIDERS['anthropic']['api_url']
            _is_proxy = (gen.api_url != _default_url)
            _actual_user_msg = user_msg
            if _is_proxy:
                _actual_user_msg = (
                    "【系统指令 — 请严格遵守以下全部要求】\n"
                    f"{_SYSTEM_PROMPT}\n"
                    "【系统指令结束】\n\n"
                    f"{user_msg}")
            payload = {
                "model": gen.model,
                "max_tokens": 8192 if sources.get('web_search') else 4096,
                "stream": True,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": _actual_user_msg}],
            }
            if sources.get('web_search'):
                payload["tools"] = [{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }]
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "SteamNotesGen/5.9",
                "Accept": "text/event-stream",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            if _is_proxy:
                headers["Authorization"] = f"Bearer {api_key}"
        else:
            payload = {
                "model": gen.model,
                "max_tokens": 4096,
                "stream": True,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            }
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "SteamNotesGen/5.9",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {api_key}",
            }
        return urllib.request.Request(
            gen.api_url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")

    @staticmethod
    def _ai_search_parse_ids(full_text, games):
        """从 AI 响应中解析 ===RESULT=== 分隔符并提取 AppID 列表
        返回 (found_ids, reasoning_text)"""
        reasoning = full_text
        id_section = full_text
        if "===RESULT===" in full_text:
            parts = full_text.split("===RESULT===", 1)
            reasoning = parts[0].strip()
            id_section = parts[1].strip()
        if "NONE" in id_section.upper():
            return [], reasoning
        valid_ids = {str(g['app_id']) for g in games}
        found = []
        for line in id_section.splitlines():
            for n in re.findall(r'\b(\d{3,7})\b', line.strip()):
                if n in valid_ids and n not in found:
                    found.append(n)
        return found, full_text

    @staticmethod
    def _read_sse_stream(resp, is_anthropic, on_token):
        """逐行读取 SSE 流，提取文本 delta 并通过 on_token 回调实时输出
        返回 (full_text, usage_dict)"""
        full_text = ""
        usage = {"input_tokens": 0, "output_tokens": 0}
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                evt = json.loads(data_str)
            except (json.JSONDecodeError, ValueError):
                continue

            delta = ""
            if is_anthropic:
                evt_type = evt.get("type", "")
                if evt_type == "content_block_delta":
                    delta = evt.get("delta", {}).get("text", "")
                elif evt_type == "message_start":
                    u = evt.get("message", {}).get("usage", {})
                    usage["input_tokens"] = u.get("input_tokens", 0)
                elif evt_type == "message_delta":
                    u = evt.get("usage", {})
                    usage["output_tokens"] = u.get("output_tokens", 0)
            else:
                choices = evt.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {}).get("content", "")
                u = evt.get("usage")
                if u:
                    usage["input_tokens"] = u.get("prompt_tokens", 0)
                    usage["output_tokens"] = u.get("completion_tokens", 0)

            if delta:
                full_text += delta
                if on_token:
                    on_token(delta)

        return full_text, usage

    def _build_collection_context(self, game_aids):
        """构建收藏夹信息：每个收藏夹名称 → 包含的 AppID 列表"""
        cache = getattr(self, '_coll_data_cache', {})
        if not cache:
            return ""
        parts = []
        for col_id, data in cache.items():
            name = data.get('name', '')
            if not name or data.get('is_dynamic'):
                continue
            owned = [a for a in data.get('owned_app_ids', []) if a in game_aids]
            if owned:
                parts.append(f"「{name}」: {','.join(owned[:200])}")
        if not parts:
            return ""
        return "\n【Steam 收藏夹分类】\n" + "\n".join(parts)

    def _build_notes_context(self, game_aids):
        """构建笔记摘要：每个有笔记的游戏 → 截取内容"""
        if not self.manager:
            return ""
        parts = []
        for aid in list(game_aids)[:2000]:
            try:
                data = self.manager.read_notes(aid)
                notes = data.get("notes", [])
                if not notes:
                    continue
                # 取第一条笔记的内容，去 BBCode，截断
                content = notes[0].get("content", "")
                content = re.sub(r'\[/?[a-z0-9*]+(?:=[^\]]*)?]', '', content)
                content = content.strip()[:120]
                if content:
                    parts.append(f"{aid}: {content}")
            except Exception:
                continue
        if not parts:
            return ""
        return "\n【用户笔记摘要】\n" + "\n".join(parts)

    def _ensure_igdb_dim_names(self):
        """确保 IGDB 维度名称已缓存（首次需要 API 调用）"""
        if not self._collections_core:
            return
        cache = self._collections_core.load_igdb_cache()
        if not cache or cache.get("_dim_names"):
            return  # 已有或无缓存
        dim_names = {}
        for dim in ("genres", "themes", "game_modes", "player_perspectives"):
            if dim not in cache:
                continue
            try:
                items, err = self._collections_core.fetch_igdb_dimension_list(dim)
                if items:
                    dim_names[dim] = {str(it["id"]): it["name"] for it in items}
            except Exception:
                continue
        if dim_names:
            cache["_dim_names"] = dim_names
            self._collections_core.save_igdb_cache(cache)

    def _build_igdb_context(self, game_aids):
        """构建 IGDB 分类信息：从本地缓存反查每个游戏的类型/主题等"""
        if not self._collections_core:
            return ""
        self._ensure_igdb_dim_names()
        cache = self._collections_core.load_igdb_cache()
        if not cache:
            return ""

        dim_names = cache.get("_dim_names", {})

        # 构建反向索引：app_id → {dimension: [item_names]}
        aid_ints = {int(a) for a in game_aids if a.isdigit()}
        reverse = {}  # aid_str → list of label strings

        for dim in ("genres", "themes", "game_modes", "player_perspectives"):
            dim_data = cache.get(dim, {})
            dim_label = {"genres": "类型", "themes": "主题",
                         "game_modes": "模式",
                         "player_perspectives": "视角"}.get(dim, dim)
            names_map = dim_names.get(dim, {})

            for item_id, entry in dim_data.items():
                if not isinstance(entry, dict):
                    continue
                steam_ids = entry.get("steam_ids", [])
                item_name = names_map.get(str(item_id), "")
                if not item_name:
                    continue  # 没有名称的跳过
                for sid in steam_ids:
                    if sid in aid_ints:
                        aid_str = str(sid)
                        reverse.setdefault(aid_str, []).append(
                            f"{dim_label}:{item_name}")

        if not reverse:
            return ""

        # 紧凑格式：按分类聚合（类型:RPG → AppID列表）
        cat_to_aids = {}
        for aid_str, labels in reverse.items():
            for label in labels:
                cat_to_aids.setdefault(label, []).append(aid_str)

        parts = []
        for cat, aids in sorted(cat_to_aids.items(),
                                key=lambda x: -len(x[1])):
            parts.append(f"{cat}: {','.join(aids[:200])}")

        return "\n【IGDB 分类数据】\n" + "\n".join(parts)
