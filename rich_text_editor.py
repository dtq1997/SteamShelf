"""BBCode 富文本编辑器组件 (Steam Notes WYSIWYG)"""

import re
import webbrowser
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

class SteamRichTextEditor(tk.Frame):
    """支持 Steam BBCode 的富文本编辑器

    在可视模式下以 tkinter Text widget 的标签渲染 BBCode 效果；
    可切换到源码模式直接编辑 BBCode 源码。
    """

    # 所有支持的 Steam BBCode 标签
    SUPPORTED_TAGS = ['p', 'h1', 'h2', 'h3', 'b', 'i', 'u', 'strike',
                      'list', 'olist', 'hr', 'code', 'url']

    def __init__(self, parent, height=15, **kwargs):
        super().__init__(parent, **kwargs)
        self._source_mode = False
        self._build_ui(height)

    def _build_ui(self, height):
        """构建工具栏和编辑区"""
        # ── 工具栏 ──
        toolbar = tk.Frame(self, bg="#e8e8e8", pady=2)
        toolbar.pack(fill=tk.X)

        # 格式按钮
        btn_defs = [
            ("B", "b", {"font": ("", 10, "bold")}),
            ("I", "i", {"font": ("", 10, "italic")}),
            ("U", "u", {"font": ("", 10, "underline")}),
            ("S", "strike", {"font": ("", 10, "overstrike")}),
            ("|", None, None),  # 分隔
            ("H1", "h1", {}),
            ("H2", "h2", {}),
            ("H3", "h3", {}),
            ("¶", "p", {}),
            ("|", None, None),
            ("• 列表", "list", {}),
            ("1. 列表", "olist", {}),
            ("── 分隔线", "hr", {}),
            ("{code}", "code", {}),
        ]

        for label, tag, _ in btn_defs:
            if tag is None:
                ttk.Separator(toolbar, orient=tk.VERTICAL).pack(
                    side=tk.LEFT, fill=tk.Y, padx=4, pady=2)
                continue
            btn = ttk.Button(toolbar, text=label, style="Toolbutton",
                             command=lambda t=tag: self._apply_tag(t))
            btn.pack(side=tk.LEFT, padx=1)

        # 源码模式切换
        self._mode_btn = ttk.Button(toolbar, text="📝 源码", style="Toolbutton",
                                     command=self._toggle_source_mode)
        self._mode_btn.pack(side=tk.RIGHT, padx=5)

        self._mode_label = tk.Label(toolbar, text="可视模式", font=("", 8),
                                     bg="#e8e8e8", fg="#666")
        self._mode_label.pack(side=tk.RIGHT)

        # ── 编辑区 ──
        editor_frame = tk.Frame(self)
        editor_frame.pack(fill=tk.BOTH, expand=True)

        self._text = tk.Text(editor_frame, font=("", 11), wrap=tk.WORD,
                             height=height, undo=True, padx=8, pady=5)
        scrollbar = ttk.Scrollbar(editor_frame, orient=tk.VERTICAL,
                                  command=self._text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.config(yscrollcommand=scrollbar.set)
        self._text.pack(fill=tk.BOTH, expand=True)

        # 绑定键盘事件用于"预设模式"
        self._text.bind("<Key>", self._on_key_press, add=True)

        # ── 配置富文本标签样式 ──
        # 解析 Text widget 实际使用的字体族名，确保 italic 等样式有效
        try:
            _base_font = tkfont.Font(font=self._text.cget("font"))
            _family = _base_font.actual()["family"]
        except Exception:
            _family = ""

        self._text.tag_configure("h1", font=(_family, 22, "bold"),
                                  spacing1=10, spacing3=5)
        self._text.tag_configure("h2", font=(_family, 17, "bold"),
                                  spacing1=8, spacing3=4)
        self._text.tag_configure("h3", font=(_family, 14, "bold"),
                                  spacing1=6, spacing3=3)
        self._text.tag_configure("bold", font=(_family, 11, "bold"))
        self._text.tag_configure("italic", font=(_family, 11, "italic"),
                                  foreground="#555555")
        self._text.tag_configure("underline", font=(_family, 11), underline=True)
        self._text.tag_configure("strike", font=(_family, 11), overstrike=True)
        self._text.tag_configure("code", font=("Courier", 10),
                                  background="#f0f0f0", relief=tk.SUNKEN,
                                  borderwidth=1, lmargin1=10, lmargin2=10,
                                  rmargin=10, spacing1=3, spacing3=3)
        self._text.tag_configure("bullet", lmargin1=20, lmargin2=35,
                                  font=("", 11))
        self._text.tag_configure("olist", lmargin1=20, lmargin2=35,
                                  font=("", 11))
        self._text.tag_configure("hr", font=("", 4), justify=tk.CENTER,
                                  foreground="#999", spacing1=5, spacing3=5)
        self._text.tag_configure("paragraph", font=("", 11),
                                  spacing1=2, spacing3=2)
        # URL 样式
        self._text.tag_configure("url", foreground="#1a73e8", underline=True,
                                  font=("", 11))
        self._text.tag_bind("url", "<Enter>",
                            lambda e: self._text.config(cursor="hand2"))
        self._text.tag_bind("url", "<Leave>",
                            lambda e: self._text.config(cursor=""))
        self._text.tag_bind("url", "<Button-1>", self._on_url_click)

        # ── 关键: 设置 tag 优先级 ──
        # 内联样式必须高于块级样式，否则 paragraph 的 font 会覆盖 bold 等
        # tag_raise(a, b) 表示 a 的优先级高于 b
        for inline_tag in ("bold", "italic", "underline", "strike", "url"):
            self._text.tag_raise(inline_tag, "paragraph")
            self._text.tag_raise(inline_tag, "bullet")
            self._text.tag_raise(inline_tag, "olist")

        # 用于"预设模式"——无选区时点格式按钮，后续输入自动带该格式
        self._pending_tags = set()
        # 用于存储 [url=...] 标签的 URL 目标映射: tag_name → url
        self._url_map = {}
        self._url_counter = 0

    # ────────── URL 点击 & 预设模式 ──────────

    _URL_RE = re.compile(r'https?://[^\s\[\]<>"\']+')

    def _on_url_click(self, event):
        """点击 URL 标签时在浏览器中打开"""
        idx = self._text.index(f"@{event.x},{event.y}")
        # 检查是否在带有特定 URL 映射的标签上（[url=...] 格式）
        tags_at_pos = self._text.tag_names(idx)
        for tag in tags_at_pos:
            if tag in self._url_map:
                webbrowser.open(self._url_map[tag])
                return
        # 回退：获取该位置 url tag 的完整范围，用显示文本作为 URL
        tag_range = self._text.tag_prevrange("url", f"{idx}+1c")
        if tag_range:
            url = self._text.get(tag_range[0], tag_range[1]).strip()
            if url:
                webbrowser.open(url)

    def _insert_url_link(self, display_text: str, target_url: str):
        """插入一个 URL 链接。如果 display_text != target_url，使用唯一标签存储映射"""
        if display_text.strip() == target_url.strip() or not target_url.strip():
            # 显示文本就是 URL，直接用通用 url tag
            self._text.insert(tk.END, display_text, "url")
        else:
            # 显示文本与 URL 不同，创建唯一标签
            self._url_counter += 1
            unique_tag = f"url_{self._url_counter}"
            self._url_map[unique_tag] = target_url
            self._text.tag_configure(unique_tag, foreground="#1a73e8",
                                      underline=True, font=("", 11))
            self._text.tag_bind(unique_tag, "<Enter>",
                                lambda e: self._text.config(cursor="hand2"))
            self._text.tag_bind(unique_tag, "<Leave>",
                                lambda e: self._text.config(cursor=""))
            self._text.tag_bind(unique_tag, "<Button-1>", self._on_url_click)
            self._text.insert(tk.END, display_text, unique_tag)

    def _on_key_press(self, event):
        """处理预设模式: 输入字符时自动附加 pending tags"""
        if self._source_mode or not self._pending_tags:
            return
        # 只处理普通可打印字符
        ch = event.char
        if not ch or len(ch) != 1 or ord(ch) < 32:
            return
        # 手动插入带 tag 的字符，阻止默认行为
        tags = tuple(self._pending_tags)
        self._text.insert(tk.INSERT, ch, tags)
        return "break"

    def _highlight_urls(self):
        """在 Text widget 中查找所有 URL 并添加 url tag"""
        self._text.tag_remove("url", "1.0", tk.END)
        content = self._text.get("1.0", tk.END)
        for m in self._URL_RE.finditer(content):
            # 计算 Text widget 中的位置
            start_offset = m.start()
            end_offset = m.end()
            start_idx = f"1.0+{start_offset}c"
            end_idx = f"1.0+{end_offset}c"
            self._text.tag_add("url", start_idx, end_idx)

    # ────────── 源码模式切换 ──────────

    def _toggle_source_mode(self):
        if self._source_mode:
            # 源码 → 可视: 获取源码然后渲染
            source = self._text.get("1.0", tk.END).rstrip()
            self._source_mode = False
            self._mode_btn.config(text="📝 源码")
            self._mode_label.config(text="可视模式")
            self._render_bbcode(source)
        else:
            # 可视 → 源码: 序列化为 BBCode 然后纯文本显示
            bbcode = self._serialize_to_bbcode()
            self._source_mode = True
            self._mode_btn.config(text="👁️ 可视")
            self._mode_label.config(text="源码模式")
            self._text.config(state=tk.NORMAL)
            self._text.delete("1.0", tk.END)
            self._text.insert("1.0", bbcode)

    # ────────── BBCode 解析 → 渲染 ──────────

    def _render_bbcode(self, bbcode: str):
        """将 BBCode 解析并渲染到 Text widget"""
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        # 重置 URL 映射
        self._url_map.clear()
        self._url_counter = 0

        if not bbcode.strip():
            return

        # 解析 token 流
        tokens = self._parse_bbcode(bbcode)
        for token in tokens:
            self._insert_token(token)

        # 渲染完成后高亮 URL
        self._highlight_urls()

    def _parse_bbcode(self, bbcode: str) -> list:
        """将 BBCode 解析为 token 列表
        每个 token: {'type': ..., 'content': ..., 'items': [...], 'url': ...}
        """
        tokens = []
        pos = 0
        text = bbcode

        while pos < len(text):
            # 查找下一个标签（包括 [url] 和 [url=...] 和 [/*]）
            match = re.search(
                r'\[(\/?)(h[123]|p|b|i|u|strike|list|olist|hr|code|url|\*)(?:=[^\]]*)?\]',
                text[pos:])
            if not match:
                # 剩余纯文本
                remaining = text[pos:]
                if remaining.strip():
                    tokens.append({'type': 'text', 'content': remaining})
                break

            # 标签前的纯文本
            before = text[pos:pos + match.start()]
            if before.strip():
                tokens.append({'type': 'text', 'content': before})

            tag_name = match.group(2)
            is_close = match.group(1) == '/'
            tag_pos = pos + match.start()
            tag_end = pos + match.end()

            if tag_name == 'hr' and not is_close:
                tokens.append({'type': 'hr', 'content': ''})
                pos = tag_end
                continue

            if tag_name == '*':
                # [*] 和 [/*] 只在列表内部有意义，在顶层跳过
                pos = tag_end
                continue

            if is_close:
                # 孤立闭合标签 → 跳过
                pos = tag_end
                continue

            # 寻找对应的闭合标签
            if tag_name in ('list', 'olist'):
                close_pattern = f'[/{tag_name}]'
                close_idx = text.find(close_pattern, tag_end)
                if close_idx == -1:
                    close_idx = len(text)
                inner = text[tag_end:close_idx]
                # 解析 [*] 项
                raw_items = [item.strip() for item in re.split(r'\[\*\]', inner) if item.strip()]
                # 去除列表项中可能的 [/*] 闭合标签和 [p]...[/p] 包裹
                items = []
                for it in raw_items:
                    # 先去除尾部的 [/*]
                    it = re.sub(r'\[/\*\]\s*$', '', it).strip()
                    # 再去除 [p]...[/p] 包裹
                    it = re.sub(r'^\[p\](.*)\[/p\]$', r'\1', it, flags=re.DOTALL).strip()
                    if it:
                        items.append(it)
                tokens.append({'type': tag_name, 'content': '', 'items': items})
                pos = close_idx + len(close_pattern) if close_idx < len(text) else len(text)
            elif tag_name == 'url':
                # 处理 [url=...]...[/url] 和 [url]...[/url]
                # 重新匹配完整开标签以提取可能的 url= 属性（含引号）
                url_attr = None
                full_open_match = re.match(
                    r'\[url(?:=([^\]]*))?\]', text[tag_pos:])
                if full_open_match:
                    tag_end = tag_pos + full_open_match.end()
                    raw_attr = full_open_match.group(1)
                    # 去除属性值两端的引号 "..." 或 '...'
                    if raw_attr:
                        url_attr = raw_attr.strip().strip('"').strip("'")
                close_pattern = '[/url]'
                close_idx = text.find(close_pattern, tag_end)
                if close_idx == -1:
                    close_idx = len(text)
                inner = text[tag_end:close_idx]
                # url_attr 存在时：显示文本=inner，链接=url_attr
                # url_attr 不存在时：显示文本=inner，链接=inner
                link_url = url_attr if url_attr else inner.strip()
                tokens.append({'type': 'url_link', 'content': inner, 'url': link_url})
                pos = close_idx + len(close_pattern) if close_idx < len(text) else len(text)
            else:
                close_pattern = f'[/{tag_name}]'
                close_idx = text.find(close_pattern, tag_end)
                if close_idx == -1:
                    close_idx = len(text)
                inner = text[tag_end:close_idx]
                tokens.append({'type': tag_name, 'content': inner})
                pos = close_idx + len(close_pattern) if close_idx < len(text) else len(text)

        return tokens

    def _insert_token(self, token):
        """将一个 token 插入到 Text widget"""
        t = token['type']
        content = token.get('content', '')

        if t == 'text':
            self._text.insert(tk.END, content, "paragraph")
        elif t in ('h1', 'h2', 'h3'):
            if self._text.get("end-2c", "end-1c") != "\n":
                self._text.insert(tk.END, "\n")
            self._insert_inline(content, t)
            self._text.insert(tk.END, "\n", t)
        elif t == 'p':
            # 段落内容可能含内联标签 [b] [i] [u] [strike]
            self._insert_inline(content, "paragraph")
            self._text.insert(tk.END, "\n", "paragraph")
        elif t == 'b':
            self._insert_inline(content, "bold")
        elif t == 'i':
            self._insert_inline(content, "italic")
        elif t == 'u':
            self._insert_inline(content, "underline")
        elif t == 'strike':
            self._insert_inline(content, "strike")
        elif t == 'code':
            if self._text.get("end-2c", "end-1c") != "\n":
                self._text.insert(tk.END, "\n")
            self._text.insert(tk.END, content + "\n", "code")
        elif t == 'hr':
            if self._text.get("end-2c", "end-1c") != "\n":
                self._text.insert(tk.END, "\n")
            self._text.insert(tk.END, "─" * 50 + "\n", "hr")
        elif t in ('list', 'olist'):
            if self._text.get("end-2c", "end-1c") != "\n":
                self._text.insert(tk.END, "\n")
            items = token.get('items', [])
            tag = "bullet" if t == 'list' else "olist"
            for idx, item in enumerate(items):
                prefix = "• " if t == 'list' else f"{idx + 1}. "
                self._text.insert(tk.END, prefix, tag)
                # 列表项内容可能含内联标签 [b][i][url] 等，需要解析
                self._insert_inline(item, tag)
                self._text.insert(tk.END, "\n", tag)
        elif t == 'url_link':
            # [url=...]显示文本[/url] 或 [url]链接[/url]
            display = content if content.strip() else token.get('url', '')
            self._insert_url_link(display, token.get('url', display))

    def _insert_inline(self, text: str, base_tag: str):
        """解析段落/列表项内的内联标签 [b] [i] [u] [strike] [url] 并渲染（递归支持嵌套）"""
        pos = 0
        while pos < len(text):
            # 匹配内联标签：[b]...[/b] 以及 [url=...]...[/url] 或 [url]...[/url]
            match = re.search(
                r'\[(b|i|u|strike)\](.*?)\[/\1\]|\[url(?:=([^\]]*))?\](.*?)\[/url\]',
                text[pos:], re.DOTALL)
            if not match:
                self._text.insert(tk.END, text[pos:], base_tag)
                break
            # 标签前文本
            before = text[pos:pos + match.start()]
            if before:
                self._text.insert(tk.END, before, base_tag)
            if match.group(1):
                # [b]/[i]/[u]/[strike] 匹配
                inline_tag = match.group(1)
                inline_content = match.group(2)
                tag_map = {'b': 'bold', 'i': 'italic', 'u': 'underline', 'strike': 'strike'}
                visual_tag = tag_map.get(inline_tag, base_tag)
                # 递归解析内部可能的嵌套内联标签
                self._insert_inline(inline_content, visual_tag)
            else:
                # [url] 匹配
                raw_attr = match.group(3)  # [url=VALUE] 的 VALUE，可能为 None
                url_content = match.group(4)
                # 去除引号
                url_attr = raw_attr.strip().strip('"').strip("'") if raw_attr else None
                display = url_content if url_content.strip() else (url_attr or '')
                target = url_attr if url_attr else url_content.strip()
                self._insert_url_link(display, target)
            pos = pos + match.end()

    # ────────── 可视模式 → BBCode 序列化 ──────────

    def _serialize_to_bbcode(self) -> str:
        """将 Text widget 的内容及标签序列化为 BBCode"""
        result = []
        index = "1.0"
        end = self._text.index(tk.END + "-1c")

        while self._text.compare(index, "<", end):
            tags = self._text.tag_names(index)
            next_idx = self._find_tag_boundary(index, tags)
            chunk = self._text.get(index, next_idx)
            bbcode = self._chunk_to_bbcode(chunk, tags)
            if bbcode:
                result.append(bbcode)
            index = next_idx

        return ''.join(result)

    def _chunk_to_bbcode(self, chunk: str, tags) -> str:
        """将单个文本块及其标签转换为 BBCode 字符串"""
        if 'hr' in tags and '─' in chunk:
            return '[hr]'

        # 块级标签：h1/h2/h3/code
        for block_tag in ('h1', 'h2', 'h3', 'code'):
            if block_tag in tags:
                line = chunk.rstrip('\n')
                return f'[{block_tag}]{line}[/{block_tag}]' if line else ''

        # 列表标签
        if 'bullet' in tags:
            return self._serialize_list_items(chunk, 'list', bullet_prefix='• ')
        if 'olist' in tags:
            return self._serialize_list_items(chunk, 'olist', numbered=True)

        # 内联标签
        _INLINE_MAP = {'bold': 'b', 'italic': 'i', 'underline': 'u', 'strike': 'strike'}
        for visual_tag, bbcode_tag in _INLINE_MAP.items():
            if visual_tag in tags:
                return f'[{bbcode_tag}]{chunk}[/{bbcode_tag}]'

        # URL 标签
        if 'url' in tags:
            return f'[url]{chunk}[/url]'
        url_tag = next((t for t in tags if t.startswith('url_') and t in self._url_map), None)
        if url_tag:
            return f'[url={self._url_map[url_tag]}]{chunk}[/url]'

        # 段落或纯文本
        if 'paragraph' in tags:
            return ''.join(f'[p]{p.strip()}[/p]' for p in chunk.split('\n') if p.strip())

        text_stripped = chunk.strip()
        return f'[p]{text_stripped}[/p]' if text_stripped else ''

    @staticmethod
    def _serialize_list_items(chunk: str, list_tag: str,
                               bullet_prefix: str = '', numbered: bool = False) -> str:
        """将列表文本块序列化为 BBCode [list] 或 [olist]"""
        lines = chunk.rstrip('\n').split('\n')
        items = []
        for ln in lines:
            ln = ln.strip()
            if bullet_prefix and ln.startswith(bullet_prefix):
                items.append(ln[len(bullet_prefix):])
            elif numbered:
                m = re.match(r'^\d+\.\s*(.+)', ln)
                items.append(m.group(1) if m else ln if ln else None)
            elif ln:
                items.append(ln)
        items = [it for it in items if it]
        if not items:
            return ''
        return f'[{list_tag}]' + ''.join(f'[*]{it}' for it in items) + f'[/{list_tag}]'

    def _find_tag_boundary(self, start_index, tags):
        """找到当前标签组合结束的位置"""
        tags_set = set(tags)
        index = start_index
        end = self._text.index(tk.END + "-1c")

        while self._text.compare(index, "<", end):
            next_char = self._text.index(f"{index}+1c")
            next_tags = set(self._text.tag_names(next_char))
            if next_tags != tags_set:
                return next_char
            index = next_char

        return end

    # ────────── 工具栏: 应用标签 ──────────

    def _apply_tag(self, tag_name: str):
        """工具栏按钮点击处理"""
        if self._source_mode:
            # 源码模式: 直接插入标签文本
            self._insert_bbcode_tag_source(tag_name)
            return

        # 可视模式
        if tag_name == 'hr':
            self._insert_hr()
            return

        if tag_name in ('list', 'olist'):
            self._insert_list(tag_name)
            return

        if tag_name in ('h1', 'h2', 'h3'):
            self._apply_block_tag(tag_name)
            return

        if tag_name == 'p':
            self._apply_block_tag('paragraph')
            return

        if tag_name == 'code':
            self._apply_code_block()
            return

        # 内联标签: b, i, u, strike
        tag_map = {'b': 'bold', 'i': 'italic', 'u': 'underline', 'strike': 'strike'}
        visual_tag = tag_map.get(tag_name, tag_name)
        try:
            sel_start = self._text.index(tk.SEL_FIRST)
            sel_end = self._text.index(tk.SEL_LAST)
            # 检查选区是否已有该标签 → 切换
            current_tags = self._text.tag_names(sel_start)
            if visual_tag in current_tags:
                self._text.tag_remove(visual_tag, sel_start, sel_end)
            else:
                self._text.tag_add(visual_tag, sel_start, sel_end)
        except tk.TclError:
            # 无选区: 进入"预设模式"——后续输入自动带该格式
            if visual_tag in self._pending_tags:
                self._pending_tags.discard(visual_tag)
            else:
                self._pending_tags.add(visual_tag)
            # 在工具栏按钮上给出视觉反馈（通过状态栏提示）
            if self._pending_tags:
                active = ", ".join(sorted(self._pending_tags))
                self._mode_label.config(text=f"预设: {active}")
            else:
                self._mode_label.config(text="可视模式")

    def _insert_bbcode_tag_source(self, tag_name: str):
        """源码模式下在光标处插入 BBCode 标签对"""
        if tag_name == 'hr':
            self._text.insert(tk.INSERT, "[hr]")
            return
        try:
            sel_text = self._text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self._text.delete(tk.SEL_FIRST, tk.SEL_LAST)
            if tag_name in ('list', 'olist'):
                items = sel_text.split('\n')
                inner = ''.join(f'[*]{item}' for item in items if item.strip())
                self._text.insert(tk.INSERT, f"[{tag_name}]{inner}[/{tag_name}]")
            else:
                self._text.insert(tk.INSERT, f"[{tag_name}]{sel_text}[/{tag_name}]")
        except tk.TclError:
            if tag_name in ('list', 'olist'):
                self._text.insert(tk.INSERT, f"[{tag_name}][*]项目一[*]项目二[/{tag_name}]")
            else:
                self._text.insert(tk.INSERT, f"[{tag_name}][/{tag_name}]")

    def _insert_hr(self):
        """可视模式下插入分隔线"""
        pos = self._text.index(tk.INSERT)
        if self._text.get(f"{pos}-1c", pos) != "\n":
            self._text.insert(tk.INSERT, "\n")
        self._text.insert(tk.INSERT, "─" * 50 + "\n", "hr")

    def _insert_list(self, list_type: str):
        """可视模式下插入列表"""
        tag = "bullet" if list_type == 'list' else "olist"
        try:
            sel_text = self._text.get(tk.SEL_FIRST, tk.SEL_LAST)
            sel_start = self._text.index(tk.SEL_FIRST)
            sel_end = self._text.index(tk.SEL_LAST)
            self._text.delete(sel_start, sel_end)
            lines = [ln.strip() for ln in sel_text.split('\n') if ln.strip()]
            pos = sel_start
        except tk.TclError:
            lines = ["项目一", "项目二"]
            pos = self._text.index(tk.INSERT)
            if self._text.get(f"{pos}-1c", pos) != "\n":
                self._text.insert(pos, "\n")
                pos = self._text.index(tk.INSERT)

        for idx, item in enumerate(lines):
            prefix = "• " if list_type == 'list' else f"{idx + 1}. "
            self._text.insert(pos, prefix + item + "\n", tag)
            pos = self._text.index(f"{pos}+{len(prefix) + len(item) + 1}c")

    def _apply_block_tag(self, tag_name: str):
        """为当前行或选区应用块级标签"""
        try:
            sel_start = self._text.index(tk.SEL_FIRST)
            sel_end = self._text.index(tk.SEL_LAST)
        except tk.TclError:
            # 没有选区: 选取当前行
            sel_start = self._text.index("insert linestart")
            sel_end = self._text.index("insert lineend")

        # 移除已有的块级标签
        for bt in ('h1', 'h2', 'h3', 'paragraph', 'code'):
            self._text.tag_remove(bt, sel_start, sel_end)
        # 应用新标签
        self._text.tag_add(tag_name, sel_start, sel_end)

    def _apply_code_block(self):
        """插入或应用代码块"""
        try:
            sel_start = self._text.index(tk.SEL_FIRST)
            sel_end = self._text.index(tk.SEL_LAST)
            current_tags = self._text.tag_names(sel_start)
            if 'code' in current_tags:
                self._text.tag_remove('code', sel_start, sel_end)
            else:
                for bt in ('h1', 'h2', 'h3', 'paragraph'):
                    self._text.tag_remove(bt, sel_start, sel_end)
                self._text.tag_add('code', sel_start, sel_end)
        except tk.TclError:
            pos = self._text.index(tk.INSERT)
            if self._text.get(f"{pos}-1c", pos) != "\n":
                self._text.insert(pos, "\n")
            self._text.insert(tk.INSERT, "代码内容\n", "code")

    # ────────── 公共接口 ──────────

    def set_content(self, bbcode: str):
        """设置内容（BBCode 字符串）"""
        if self._source_mode:
            self._text.config(state=tk.NORMAL)
            self._text.delete("1.0", tk.END)
            self._text.insert("1.0", bbcode)
        else:
            self._render_bbcode(bbcode)

    def get_content(self) -> str:
        """获取内容（BBCode 字符串）"""
        if self._source_mode:
            return self._text.get("1.0", tk.END).rstrip()
        else:
            return self._serialize_to_bbcode()

    def clear(self):
        """清空编辑器"""
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)

    def set_state(self, state):
        """设置 text widget 状态 (tk.NORMAL / tk.DISABLED)"""
        self._text.config(state=state)


# ═══════════════════════════════════════════════════════════════════════════════
