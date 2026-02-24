"""SteamShelf — 库管理标签页（LibraryMixin）

从 ui_main.py 拆分。包含库管理标签页的 UI 构建、树渲染、筛选和 CEF 连接逻辑。
收藏夹相关逻辑（加载/渲染/操作/事件）已拆分到 ui_library_collections.py。

宿主协议：LibraryHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import LibraryHost  # noqa: F401

import platform
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from ui_utils import AutoScrollbar, bg_thread
from utils import (ETYPE_GAME, ETYPE_APP, ETYPE_TOOL, ETYPE_DEMO,
                   ETYPE_DLC, ETYPE_VIDEO, ETYPE_MUSIC, ETYPE_BETA,
                   ETYPE_SHORTCUT)

from account_manager import SteamAccountScanner
from steam_data import parse_release_date
from ui_library_collections import LibraryCollectionsMixin
from ui_library_source_update import LibrarySourceUpdateMixin

try:
    from cef_bridge import CEFBridge
except ImportError:
    CEFBridge = None

from core_notes import (
    CONFIDENCE_EMOJI,
    INFO_VOLUME_EMOJI,
    QUALITY_EMOJI,
    is_ai_note,
)


class LibraryMixin(LibraryCollectionsMixin, LibrarySourceUpdateMixin):
    """库管理标签页相关方法（Mixin，self 指向 SteamToolboxMain 实例）"""

    def _build_library_tab(self, parent):
        """构建库管理标签页"""
        frame = tk.Frame(parent, padx=12, pady=8)
        frame.pack(fill=tk.BOTH, expand=True)

        # ── 主体：左 + 右（grid 布局） ──
        body = tk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1, minsize=220)
        body.columnconfigure(1, weight=0)
        body.columnconfigure(2, weight=3, minsize=300)
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=0)

        # 左侧：收藏夹 / 详情
        left = tk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew")

        tk.Label(left, text="⭐ Steam 分类",
                 font=("微软雅黑", 11, "bold")).pack(anchor=tk.W)

        # 收藏夹筛选控件
        coll_filter_frame = tk.Frame(left)
        coll_filter_frame.pack(fill=tk.X, pady=(4, 2))
        tk.Label(coll_filter_frame, text="筛选:", font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self._coll_filter_var = tk.StringVar(value="已入库")
        coll_filter_combo = ttk.Combobox(
            coll_filter_frame, textvariable=self._coll_filter_var, width=12,
            values=["已入库", "全部", "未入库"], state='readonly')
        coll_filter_combo.pack(side=tk.LEFT, padx=(4, 0))
        coll_filter_combo.bind("<<ComboboxSelected>>",
                                lambda e: (self._lib_load_collections(
                                               skip_expression_update=True),
                                           self._apply_coll_filters()))

        # 收藏夹列表
        coll_frame = tk.Frame(left)
        coll_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        coll_frame.columnconfigure(0, weight=1)
        coll_frame.rowconfigure(0, weight=1)
        self._coll_tree = ttk.Treeview(coll_frame, show="tree", height=12, selectmode="extended")
        self._coll_tree.grid(row=0, column=0, sticky="nsew")
        coll_scroll = AutoScrollbar(coll_frame, orient=tk.VERTICAL,
                                     command=self._coll_tree.yview)
        coll_scroll.grid(row=0, column=1, sticky="ns")
        self._coll_tree.config(yscrollcommand=coll_scroll.set)

        # 三态筛选图标（○ 默认 / ＋ 包含 / － 排除）
        self._create_coll_filter_icons()
        self._coll_filter_states = {}  # col_id → 'default' | 'plus' | 'minus'
        self._viewed_coll_ids = set()  # 触发当前查看的分类 ID
        self._coll_tree.tag_configure("coll_plus", foreground="#2e7d32")
        self._coll_tree.tag_configure("coll_minus", foreground="#c62828")
        self._coll_tree.tag_configure("coll_empty", foreground="#bbb")

        # 绑定选择变化事件（含互斥逻辑）
        self._coll_tree.bind("<<TreeviewSelect>>", self._on_collection_selection_changed)

        # 拖动多选支持（鼠标拖动）
        self._coll_drag_start = None
        self._coll_tree.bind("<ButtonPress-1>", self._on_coll_drag_start)
        self._coll_tree.bind("<B1-Motion>", self._on_coll_drag_motion)
        self._coll_tree.bind("<ButtonRelease-1>", self._drag_scroll_cancel)
        self._coll_tree.bind("<Double-1>", self._on_coll_double_click)
        self._coll_tree.bind("<Return>", lambda e: self._batch_set_coll_filter("plus"))
        self._coll_tree.bind("<BackSpace>", lambda e: self._batch_set_coll_filter("minus"))
        self._coll_tree.bind("<space>", lambda e: self._batch_set_coll_filter("default"))
        self._coll_tree.bind("<Button-2>" if platform.system() == "Darwin" else "<Button-3>",
                              self._on_coll_right_click)

        # 创建分类按钮（弹出菜单统一所有收藏夹创建入口）
        coll_btn_frame = tk.Frame(left)
        coll_btn_frame.pack(fill=tk.X, pady=(6, 0))
        self._create_coll_btn = ttk.Button(coll_btn_frame, text="➕ 创建分类", width=12,
                   command=self._show_create_collection_menu)
        self._create_coll_btn.pack(side=tk.LEFT)

        # 上下文跟踪（选择事件仍需要）
        self._toolbar_context = 'game'

        style = ttk.Style()
        # macOS aqua 主题失焦时选中色变浅灰 → 用固定色覆盖
        style.map("Treeview",
                  background=[("selected", "#0078d7")],
                  foreground=[("selected", "white")])
        style.configure("Filter.TCheckbutton", font=("微软雅黑", 8))
        style.configure("Filter.TRadiobutton", font=("微软雅黑", 8))

        # 右侧：游戏列表
        right = tk.Frame(body)
        right.grid(row=0, column=2, sticky="nsew")

        # 标题行（精简：标题 + 搜索框）
        title_frame = tk.Frame(right)
        title_frame.pack(fill=tk.X)
        tk.Label(title_frame, text="📚 Steam 库",
                 font=("微软雅黑", 11, "bold")).pack(side=tk.LEFT)

        # 筛选变量（不显示在标题行，通过右键菜单访问）
        self._dirty_filter_var = tk.BooleanVar(value=False)
        self._uploading_filter_var = tk.BooleanVar(value=False)
        self._upload_all_btn = None  # 兼容引用

        # 搜索框（直接嵌入标题行右侧）
        self._lib_search_var = tk.StringVar()
        self._main_search_var = self._lib_search_var
        self._main_search_mode = tk.StringVar(value="name")
        self._search_mode_label = tk.Label(
            title_frame, text="🔍", font=("", 9), cursor="hand2")
        self._search_mode_label.pack(side=tk.RIGHT)
        self._search_mode_label.bind("<Button-1>",
            lambda e: self._set_search_mode(
                "content" if self._main_search_mode.get() == "name" else "name"))
        self._lib_search_entry = ttk.Entry(
            title_frame, textvariable=self._lib_search_var, width=20)
        self._lib_search_entry.pack(
            side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        self._lib_search_var.trace_add(
            "write", lambda *_: self._on_main_search_changed())
        self._lib_search_entry.bind("<Escape>", lambda e: (
            self._lib_search_var.set(""), self._games_tree.focus_set()))
        # 右键切换搜索模式（名称/内容）
        self._lib_search_entry.bind(
            "<Button-2>" if platform.system() == "Darwin" else "<Button-3>",
            self._show_search_mode_menu)
        self._update_search_placeholder()
        import platform as _plat
        _mod = "Command" if _plat.system() == "Darwin" else "Control"
        self.root.bind(
            f"<{_mod}-f>", lambda e: self._lib_search_entry.focus_set())
        self.root.bind(
            f"<{_mod}-a>", lambda e: (self._select_all_games(), "break")[-1])
        self.root.bind(
            f"<{_mod}-e>", lambda e: self._ui_export_dialog())

        # ── 下拉筛选（默认隐藏，通过右键菜单 "筛选" 显示） ──
        filter_frame = tk.Frame(right)
        self._filter_frame = filter_frame  # 保留引用，供右键菜单切换

        # ── 收藏夹筛选状态行（有 +/- 时显示，初始隐藏） ──
        self._coll_filter_status = tk.Text(
            right, height=1, wrap=tk.WORD, font=("微软雅黑", 12),
            bg=right.cget("bg"), bd=0, highlightthickness=0,
            cursor="arrow", state=tk.DISABLED)
        self._coll_ops_plus = []
        self._coll_ops_minus = []

        # AI 筛选（合并了模型筛选：全部/🤖AI/📝未AI/具体模型名）
        self._ai_filter_var = tk.StringVar(value="全部")
        self._ai_filter_base_values = ["全部", "🤖AI", "📝未AI"]
        self._ai_filter_combo = ttk.Combobox(
            filter_frame, textvariable=self._ai_filter_var, width=14,
            values=self._ai_filter_base_values, state='readonly')
        self._ai_filter_combo.pack(side=tk.LEFT)
        self._ai_filter_combo.bind("<<ComboboxSelected>>",
                                    lambda e: self._on_filter_changed())

        # 以下子筛选器创建但不 pack，选中 AI 后才显示
        self._source_filter_var = tk.StringVar(value="来源")
        self._source_filter_combo = ttk.Combobox(
            filter_frame, textvariable=self._source_filter_var, width=7,
            values=["来源", "📡 联网", "📚 本地"], state='readonly')
        self._source_filter_combo.bind("<<ComboboxSelected>>",
                                        lambda e: self._lib_schedule_tree_rebuild())

        self._vol_filter_var = tk.StringVar(value="信息量")
        self._vol_filter_combo = ttk.Combobox(
            filter_frame, textvariable=self._vol_filter_var, width=8,
            values=["信息量", "🟢 相当多", "🔵 较多", "🟡 中等", "🟠 较少", "🔴 相当少",
                    "⛔ 信息过少"],
            state='readonly')
        self._vol_filter_combo.bind("<<ComboboxSelected>>",
                                     lambda e: self._on_vol_filter_changed())

        self._conf_filter_var = tk.StringVar(value="确信度")
        self._conf_filter_combo = ttk.Combobox(
            filter_frame, textvariable=self._conf_filter_var, width=9,
            values=["确信度", "🟢 很高", "🔵 较高", "🟡 中等", "🟠 较低", "🔴 很低"],
            state='readonly')
        self._conf_filter_combo.bind("<<ComboboxSelected>>",
                                      lambda e: self._lib_schedule_tree_rebuild())

        self._qual_filter_var = tk.StringVar(value="质量")
        self._qual_filter_combo = ttk.Combobox(
            filter_frame, textvariable=self._qual_filter_var, width=8,
            values=["质量", "❓ 未评估", "💎 相当好", "✨ 较好", "➖ 中等", "👎 较差",
                    "💀 相当差"], state='readonly')
        self._qual_filter_combo.bind("<<ComboboxSelected>>",
                                      lambda e: self._lib_schedule_tree_rebuild())

        self._sub_filters_visible = False
        self._qual_filter_visible = False

        # ── 内联 AI 生成控件（先 pack 到底部，再让 Treeview 填充剩余空间） ──
        self._build_inline_ai_controls(right)

        # ── 底部：状态标签 + 进度条（必须在 Treeview 之前 pack side=BOTTOM） ──
        self._name_progress_frame = tk.Frame(right)
        self._name_progress_label = tk.Label(
            self._name_progress_frame,
            text="📥 正在获取游戏名称...", font=("微软雅黑", 8), fg="#666", anchor=tk.W)
        self._name_progress_label.pack(fill=tk.X)
        self._name_progress_bar = ttk.Progressbar(
            self._name_progress_frame, mode='indeterminate', length=180)
        self._name_progress_bar.pack(fill=tk.X)
        self._name_progress_bar.start(15)
        self._name_progress_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0))

        self._lib_status = tk.Label(right, text="", font=("微软雅黑", 8), fg="#666",
                                     anchor=tk.W)
        self._lib_status.pack(fill=tk.X, pady=(2, 0))

        # ── Treeview（统一游戏列表） ──
        lib_list_frame = tk.Frame(right)
        lib_list_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        lib_list_frame.columnconfigure(0, weight=1)
        lib_list_frame.rowconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("GameList.Treeview", rowheight=24, font=("微软雅黑", 9))
        style.map("GameList.Treeview",
                  background=[("selected", "#0078d7")],
                  foreground=[("selected", "white")])

        self._lib_tree = ttk.Treeview(
            lib_list_frame,
            columns=("type", "appid", "name", "notes", "source", "date",
                     "review_label", "review", "release", "acquired", "metacritic"),
            show="tree headings", style="GameList.Treeview",
            selectmode="extended", height=20)

        # 排序状态：{列名: 'asc'/'desc'/None}
        self._sort_columns = {}
        self._sort_order = []  # 排序优先级列表

        # 类型筛选（双击 Type 列头弹出）
        self._type_filter = {"Game"}  # 默认只显示 Game
        self._ALL_TYPES = ["Game", "App", "DLC", "Demo", "Tool", "Music", "Video", "Beta", "Link"]

        # 设置表头，绑定排序函数（Type 列单击弹筛选，不排序）
        for col, text in [("type", "Type ▼"), ("appid", "AppID"), ("name", "游戏名称"),
                          ("notes", "📝"), ("source", "AI信息"), ("date", "最新笔记"),
                          ("review_label", "评测"), ("review", "好评%"),
                          ("release", "发行"),
                          ("acquired", "入库"), ("metacritic", "MC")]:
            if col == "type":
                self._lib_tree.heading(col, text=text,
                                       command=self._show_type_filter_popup)
            else:
                self._lib_tree.heading(col, text=text,
                                       command=lambda c=col: self._lib_sort_column(c))

        self._lib_tree.column("type", width=40, minwidth=35, stretch=False, anchor=tk.W)
        self._lib_tree.column("appid", width=60, minwidth=50, stretch=False, anchor=tk.W)
        self._lib_tree.column("name", width=300, minwidth=200, stretch=True, anchor=tk.W)
        self._lib_tree.column("notes", width=45, minwidth=35, stretch=False, anchor=tk.CENTER)
        self._lib_tree.column("source", width=70, minwidth=50, stretch=False, anchor=tk.W)
        self._lib_tree.column("date", width=82, minwidth=70, stretch=False, anchor=tk.W)
        # 新增信息列（默认隐藏，通过右键表头菜单切换）
        self._lib_tree.column("review_label", width=65, minwidth=50, stretch=False, anchor=tk.W)
        self._lib_tree.column("review", width=50, minwidth=40, stretch=False, anchor=tk.W)
        self._lib_tree.column("release", width=82, minwidth=70, stretch=False, anchor=tk.W)
        self._lib_tree.column("acquired", width=82, minwidth=70, stretch=False, anchor=tk.W)
        self._lib_tree.column("metacritic", width=35, minwidth=30, stretch=False, anchor=tk.CENTER)

        # 列可见性系统
        self._col_defaults = {
            "type": (40, 35), "appid": (60, 50), "name": (300, 200),
            "notes": (45, 35), "source": (70, 50), "date": (82, 70),
            "review_label": (65, 50), "review": (50, 40),
            "release": (82, 70), "acquired": (82, 70), "metacritic": (35, 30),
        }
        _default_visible = {"type", "name", "source", "review_label", "acquired"}
        saved = self._config.get("visible_columns")
        if saved:
            self._visible_columns = set(saved)
            # 一次性迁移 v2：补 release + review_label 列
            if not self._config.get("_migrated_cols_v2"):
                for col in ("release", "review_label"):
                    if col not in self._visible_columns:
                        self._visible_columns.add(col)
                self._config["_migrated_cols_v2"] = True
                self._config["visible_columns"] = list(self._visible_columns)
                self._config_mgr.save()
        else:
            self._visible_columns = _default_visible
        self._apply_displaycolumns()
        # 树列（展开箭头）— 窄且不可拖，与内容融为一体
        self._lib_tree.column("#0", width=20, minwidth=20, stretch=False)
        self._lib_tree.heading("#0", text="")

        # 创建tags（合并库管理+笔记管理的标签）
        self._lib_tree.tag_configure("not_owned", background="#e0e0e0")
        self._lib_tree.tag_configure("dirty", foreground="#b8860b", background="#fffff0")
        self._lib_tree.tag_configure("uploading", foreground="#2e7d32", background="#e8f5e9")
        self._lib_tree.tag_configure("ai", foreground="#1a73e8")
        self._lib_tree.tag_configure("insufficient", foreground="#cc3333", background="#fff5f5")
        self._lib_tree.tag_configure("normal", foreground="#333")
        self._lib_tree.tag_configure("note_child", foreground="#666")
        self._lib_tree.tag_configure("partial_select", background="#e8f0fe")

        self._lib_tree.grid(row=0, column=0, sticky="nsew")

        lib_scroll = AutoScrollbar(lib_list_frame, orient=tk.VERTICAL,
                                    command=self._lib_tree.yview)
        lib_scroll.grid(row=0, column=1, sticky="ns")
        self._lib_tree.config(yscrollcommand=lib_scroll.set)

        # 统一 Button-1 处理：分隔线拦截 + 表头排序 + 展开箭头
        def _on_tree_click(event):
            region = self._lib_tree.identify_region(event.x, event.y)

            # 1. 阻止所有分隔线拖动（列宽固定，name 列自动伸缩）
            if region == "separator":
                return "break"

            # 2. 展开箭头点击 → 只切换展开/收起，不改变选中状态
            elif region == "tree":
                item = self._lib_tree.identify_row(event.y)
                if item and self._lib_tree.get_children(item):
                    element = self._lib_tree.identify_element(event.x, event.y)
                    if "indicator" in str(element):
                        self._lib_tree.focus(item)
                        is_open = self._lib_tree.item(item, 'open')
                        self._lib_tree.item(item, open=not is_open)
                        if not is_open:
                            self._on_tree_open()
                        return "break"

            # 记录拖动起始项（用于 B1-Motion 拖动多选）
            self._game_drag_start = self._lib_tree.identify_row(event.y)
            self._game_drag_last = None
            self._game_drag_flat = None
            self._game_drag_idx = None
            # Ctrl/Cmd 拖动：保存已有选择作为基底
            if event.state & 0xC:  # Ctrl(0x4) or Command(0x8)
                self._game_drag_base = set(self._lib_tree.selection())
            else:
                self._game_drag_base = None

        self._lib_tree.bind("<Button-1>", _on_tree_click)
        self._lib_tree.bind("<B1-Motion>", self._on_game_drag_motion)
        self._lib_tree.bind("<ButtonRelease-1>", self._drag_scroll_cancel)

        # 双击：按列分发（📝列→笔记查看器，AI信息列→AI预览）
        self._lib_tree.bind("<Double-1>", self._on_tree_double_click_dispatch)
        # 右键菜单
        self._lib_tree.bind("<Button-2>" if platform.system() == "Darwin" else "<Button-3>",
                              self._on_tree_right_click)
        # 选择互斥：选中游戏时取消收藏夹选择，切换上下文
        self._lib_tree.bind("<<TreeviewSelect>>", self._on_game_selection_changed)
        # 懒加载：展开时替换占位子节点为真实笔记
        self._lib_tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        # 回车键：游戏行→收放笔记，笔记行→打开查看器
        self._lib_tree.bind("<Return>", self._on_tree_return)
        # 焦点指示器（模拟 Windows 虚线框）
        self._lib_tree.tag_configure("focused_row",
            background="#d0e4f7")
        # 有自身背景色的 tag，focused_row 不应覆盖
        self._bg_tags = {"dirty", "not_owned", "uploading", "insufficient",
                         "partial_select"}
        self._focused_iid = None
        self._lib_tree.bind("<<TreeviewSelect>>",
            self._update_focus_indicator, add=True)
        self._lib_tree.bind("<FocusOut>",
            lambda e: self._clear_focus_indicator())

        # 存储库数据
        self._lib_all_games = []  # 全部游戏列表
        self._lib_all_games_backup = None  # 筛选前的完整列表备份
        self._viewing_collections = False  # 是否正在查看收藏夹
        self._selected_game_idx = None  # 选中状态（兼容）
        self._selection_updating = False  # 防止选择事件递归
        self._prev_tree_selection = set()  # 上次选择状态（用于差量计算）
        self._game_drag_start = None  # 拖动多选起始项

        # 别名：让所有 Mixin 通过 self._games_tree 访问的方法都指向 self._lib_tree
        self._games_tree = self._lib_tree

        # ── 底部状态栏（row=1，与主体共享 grid column，分隔线结构性对齐） ──
        self._build_status_bar(body)

        # 更新 Cloud 状态 + 初始加载
        self._update_library_cloud_status()
        self._lib_load_initial()
        # 初始加载本地收藏夹数据（无需 CEF 连接）
        self._lib_load_collections()

        # 预加载名称缓存（不做 tree rebuild，等 _lib_load_initial bg 线程完成后统一 rebuild）
        self._ensure_game_name_cache_fast()
        # 如果已有持久化缓存且未过期，隐藏进度条
        bulk_cache_ts = self._config.get("game_name_bulk_cache_ts", 0)
        if self._config.get("game_name_cache", {}) and (time.time() - bulk_cache_ts < 86400):
            self._name_progress_frame.pack_forget()

        # 后台加载全量游戏名称缓存 + 解析未知名称
        threading.Thread(target=bg_thread(self._bg_init_game_names), daemon=True).start()

    def _build_status_bar(self, body):
        """在 body grid 的 row=1 构建底部状态栏（与主体共享列，保证对齐）"""
        import os

        def _short_path(p, parts=3):
            segs = p.replace("\\", "/").rstrip("/").split("/")
            return (".../" if len(segs) > parts else "") + "/".join(segs[-parts:])

        # 状态标签（row=1，与主体共享 column 0 和 column 2）
        storage_path = getattr(self.current_account, 'storage_path', None)
        if storage_path:
            coll_dir = os.path.dirname(storage_path)
            coll_link = tk.Label(body,
                text=f"📁 分类: {_short_path(coll_dir)}",
                font=("微软雅黑", 8), fg="#4a90d9", cursor="hand2")
            coll_link.grid(row=1, column=0, sticky="w", pady=(4, 0))
            coll_link.bind("<Button-1>",
                           lambda e, d=coll_dir: self._open_folder(d))

        # 短分隔线（仅状态栏行，column=1）
        ttk.Separator(body, orient=tk.VERTICAL).grid(
            row=1, column=1, sticky="ns", padx=4, pady=(4, 0))

        notes_dir = self.current_account['notes_dir']
        notes_link = tk.Label(body,
            text=f"📝 笔记: {_short_path(notes_dir)}",
            font=("微软雅黑", 8), fg="#4a90d9", cursor="hand2")
        notes_link.grid(row=1, column=2, sticky="w", pady=(4, 0))
        notes_link.bind("<Button-1>",
                        lambda e: self._open_folder(notes_dir))

    def _update_library_cloud_status(self):
        """更新全局连接状态栏的 CEF 状态"""
        if not hasattr(self, '_cef_status_label'):
            return
        if self._cef_bridge is not None and self._cef_bridge.is_connected():
            self._cef_status_label.config(text="CEF: 🟢已连接", fg="white")
        else:
            self._cef_status_label.config(text="CEF: 未连接", fg="#aac8ee")

    def _auto_connect_cef(self):
        """启动时自动尝试连接 CEF（后台检测，不阻塞 UI）"""
        if self._cef_bridge is not None:
            return
        if CEFBridge is None:
            return

        def _bg_check():
            if not CEFBridge.is_available():
                return
            bridge = CEFBridge()
            ok, err = bridge.connect()
            if not ok:
                return
            def _set_bridge():
                self._cef_bridge = bridge
                self._update_library_cloud_status()
                if self._collections_core:
                    self._collections_core.cef = bridge
                self._lib_status.config(text="🔄 正在从 CEF 获取数据...")
                self._bg_load_cef_data()
            self.root.after(0, _set_bridge)

        threading.Thread(target=bg_thread(_bg_check), daemon=True).start()

    def _apply_cef_bridge(self):
        """bridge 已从 intro 传入时，立即应用（跳过连接步骤）"""
        self._update_library_cloud_status()
        if self._collections_core:
            self._collections_core.cef = self._cef_bridge
        self._lib_status.config(text="🔄 正在从 CEF 获取数据...")
        self._bg_load_cef_data()

    def _bg_load_cef_data(self, rebuild=False):
        """后台加载 CEF 数据（名称缓存 + 收藏夹 + 游戏列表），不阻塞 UI

        Args:
            rebuild: True 时完成后触发 tree rebuild（手动刷新用）
        """
        def _work():
            self._lib_enhance_name_cache_from_cef()
            self._lib_load_owned_from_cef()
            cef_colls = None
            if self._cef_bridge and self._cef_bridge.is_connected():
                try:
                    cef_colls = self._cef_bridge.get_all_collections_with_apps(
                        timeout=20)
                    if "error" in cef_colls or "collections" not in cef_colls:
                        cef_colls = None
                except Exception:
                    cef_colls = None

            def _on_main():
                self._lib_load_collections(
                    prefetched_cef=cef_colls, skip_expression_update=True)
                if rebuild:
                    self._lib_schedule_tree_rebuild()

            self.root.after(0, _on_main)
        threading.Thread(target=bg_thread(_work), daemon=True).start()

    def _lib_load_initial(self):
        """库管理标签页的初始数据加载"""
        # 后台加载本地库数据
        def _bg():
            try:
                steam_path = self.current_account.get('steam_path', '')
                games = SteamAccountScanner.scan_library(steam_path)
                # 为本地扫描的游戏添加owned标记（本地扫描的都是已安装的，肯定是已入库）
                for g in games:
                    if 'owned' not in g:
                        g['owned'] = True
                def _apply(g=games):
                    self._lib_all_games = g
                    self._lib_schedule_tree_rebuild()
                self.root.after(0, _apply)
            except Exception as e:
                msg = str(e)
                print(f"[库管理] 加载失败: {msg}")
                self.root.after(0, lambda: self._lib_status.config(
                    text=f"⚠️ 加载失败: {msg}"))
        threading.Thread(target=bg_thread(_bg), daemon=True).start()

    # ── 工具方法（纯函数） ──

    _ALL_COLUMNS = ("type", "appid", "name", "notes", "source", "date",
                    "review_label", "review", "release", "acquired", "metacritic")

    def _apply_displaycolumns(self):
        """根据 _visible_columns 设置 displaycolumns，彻底隐藏不可见列"""
        shown = [c for c in self._ALL_COLUMNS if c in self._visible_columns]
        self._lib_tree["displaycolumns"] = shown
        # displaycolumns 会重置 stretch，必须重新应用
        for col in shown:
            self._lib_tree.column(col, stretch=(col == "name"))
        # Tkinter bug: displaycolumns 后 heading command 按原始列序号分发
        # 必须用 #N 位置语法重绑，才能对应实际显示列
        for i, col in enumerate(shown):
            if col == "type":
                self._lib_tree.heading(f"#{i+1}",
                    command=self._show_type_filter_popup)
            else:
                self._lib_tree.heading(f"#{i+1}",
                    command=lambda c=col: self._lib_sort_column(c))

    @staticmethod
    def _get_app_type(g):
        """从游戏 dict 提取 app_type 值（SSOT：唯一的类型字段解析入口）"""
        return g.get('type') or g.get('app_type') or g.get('nAppType') or 1

    @staticmethod
    def _get_type_name(app_type):
        """将 Steam EAppType 枚举值转换为显示字符串（常量定义在 utils.py）"""
        if app_type == 0 or app_type & ETYPE_GAME:
            return "Game"
        elif app_type & ETYPE_MUSIC:
            return "Music"
        elif app_type & ETYPE_DLC:
            return "DLC"
        elif app_type & ETYPE_DEMO:
            return "Demo"
        elif app_type & ETYPE_TOOL:
            return "Tool"
        elif app_type & ETYPE_APP:
            return "App"
        elif app_type & ETYPE_VIDEO:
            return "Video"
        elif app_type & ETYPE_BETA:
            return "Beta"
        elif app_type & ETYPE_SHORTCUT:
            return "Link"
        else:
            return "Game"

    @staticmethod
    def _strip_filter_prefix(val):
        """去掉筛选值的 emoji 前缀，如 '🟢 很高' → '很高'"""
        return val.split(' ', 1)[1] if ' ' in val else val

    def _on_tree_open(self, event=None):
        """展开游戏行时，将占位子节点替换为真实笔记子节点"""
        tree = self._lib_tree
        sel = tree.focus()
        if not sel or "::n::" in sel:
            return
        children = tree.get_children(sel)
        if not children or not children[0].endswith("::lazy"):
            return  # 已加载过真实子节点
        # 读取筛选模式
        filters = self._lib_read_filter_state()
        filter_mode = filters['filter_mode']
        # 删除占位节点
        tree.delete(children[0])
        # 加载真实子节点
        try:
            note_data = self.manager.read_notes_cached(sel)
            for note in note_data.get("notes", []):
                note_is_ai = is_ai_note(note)
                if filter_mode == "🤖AI" and not note_is_ai:
                    continue
                if filter_mode == "📝未AI" and note_is_ai:
                    continue
                nid = note.get("id", "")
                title = note.get("title", "无标题")
                if len(title) > 80:
                    title = title[:77] + "..."
                nts = note.get("time_modified", note.get("time_created", 0))
                note_date = datetime.fromtimestamp(nts).strftime("%Y-%m-%d") if nts else ""
                tree.insert(sel, tk.END, iid=f"{sel}::n::{nid}",
                            values=("", "", f"📄 {title}", "", "", note_date,
                                    "", "", "", "", ""),
                            tags=("note_child",))
        except Exception:
            pass

    def _on_tree_return(self, event=None):
        """回车键：游戏行→收放笔记，笔记行→打开查看器"""
        tree = self._lib_tree
        iid = tree.focus()
        if not iid:
            return
        if "::n::" in iid:
            # 笔记子节点 → 打开查看器
            aid = iid.split("::n::")[0]
            self._open_notes_viewer(aid)
        else:
            # 游戏行 → 切换展开/收起
            if tree.get_children(iid):
                is_open = tree.item(iid, 'open')
                tree.item(iid, open=not is_open)
                if not is_open:
                    self._on_tree_open()

    def _update_focus_indicator(self, event=None):
        """更新焦点指示器：给当前 focus 项加浅色背景"""
        tree = self._lib_tree
        new_iid = tree.focus()
        old_iid = self._focused_iid
        if old_iid == new_iid:
            return
        # 移除旧标签
        if old_iid:
            try:
                tags = [t for t in tree.item(old_iid, "tags")
                        if t != "focused_row"]
                tree.item(old_iid, tags=tuple(tags))
            except Exception:
                pass
        # 添加新标签（跳过已有背景色 tag 的项，避免覆盖 dirty 等视觉标记）
        if new_iid:
            try:
                tags = list(tree.item(new_iid, "tags"))
                if not (self._bg_tags & set(tags)):
                    if "focused_row" not in tags:
                        tags.append("focused_row")
                    tree.item(new_iid, tags=tuple(tags))
            except Exception:
                pass
        self._focused_iid = new_iid

    def _clear_focus_indicator(self):
        """失焦时移除焦点指示器"""
        if self._focused_iid:
            try:
                tags = [t for t in self._lib_tree.item(
                    self._focused_iid, "tags") if t != "focused_row"]
                self._lib_tree.item(self._focused_iid, tags=tuple(tags))
            except Exception:
                pass
            self._focused_iid = None

    def _get_visible_note_ids(self, app_id):
        """返回当前筛选条件下可见的笔记 ID 集合（None = 无筛选，全部可见）"""
        filters = self._lib_read_filter_state()
        fm = filters['filter_mode']
        if fm == "全部":
            return None
        notes = self.manager.read_notes_cached(app_id).get("notes", [])
        result = set()
        for n in notes:
            ai = is_ai_note(n)
            if fm == "🤖AI" and not ai:
                continue
            if fm == "📝未AI" and ai:
                continue
            nid = n.get("id")
            if nid:
                result.add(nid)
        return result

    def _lib_load_notes_data(self):
        """加载笔记相关数据（2秒 TTL 缓存，避免重复扫描 4600+ 文件）"""
        import time as _t
        cache = getattr(self, '_notes_data_cache', None)
        if cache and (_t.monotonic() - cache[0]) < 2.0:
            return cache[1]
        notes_games = {}
        ai_notes_map = {}
        syncstate_map = {}
        try:
            if self.manager:
                notes_games, ai_notes_map = self.manager.scan_all()
                syncstate_map = self._parse_remotecache_syncstates()
        except Exception:
            pass
        result = (notes_games, ai_notes_map, syncstate_map)
        self._notes_data_cache = (_t.monotonic(), result)
        return result

    def _invalidate_notes_cache(self):
        """笔记变更时调用，强制下次 _lib_load_notes_data 重新扫描"""
        self._notes_data_cache = None

    def _lib_read_filter_state(self):
        """读取所有筛选器的当前状态，返回统一的筛选参数字典"""
        filter_val = self._ai_filter_var.get() if hasattr(self, '_ai_filter_var') else "全部"
        dirty_only = self._dirty_filter_var.get() if hasattr(self, '_dirty_filter_var') else False
        uploading_only = self._uploading_filter_var.get() if hasattr(self, '_uploading_filter_var') else False
        source_filter = self._source_filter_var.get() if hasattr(self, '_source_filter_var') else "来源"
        conf_filter = self._conf_filter_var.get() if hasattr(self, '_conf_filter_var') else "确信度"
        vol_filter = self._vol_filter_var.get() if hasattr(self, '_vol_filter_var') else "信息量"
        qual_filter = self._qual_filter_var.get() if hasattr(self, '_qual_filter_var') else "质量"

        _base = getattr(self, '_ai_filter_base_values', ["全部", "🤖AI", "📝未AI"])
        if filter_val in _base:
            filter_mode = filter_val
            model_filter = None
        else:
            filter_mode = "🤖AI"
            model_filter = filter_val

        return {
            'filter_mode': filter_mode,
            'model_filter': model_filter,
            'dirty_only': dirty_only,
            'uploading_only': uploading_only,
            'source_filter': source_filter,
            'conf_filter': conf_filter,
            'vol_filter': vol_filter,
            'qual_filter': qual_filter,
        }

    def _lib_update_model_combo(self, ai_notes_map):
        """收集所有 AI 模型名称，更新筛选器下拉选项"""
        all_models = set()
        for info in ai_notes_map.values():
            for m in info.get('models', []):
                all_models.add(m)
        if hasattr(self, '_ai_filter_combo'):
            _base = getattr(self, '_ai_filter_base_values', ["全部", "🤖AI", "📝未AI"])
            self._ai_filter_combo['values'] = list(_base) + sorted(all_models)
        self._update_sub_filter_visibility()

    def _lib_should_include_game(self, aid, has_ai, is_dirty, is_uploading,
                                  ai_notes_map, filters, search_q, search_mode, name, g=None):
        """判断单个游戏是否通过所有筛选条件（返回 True 表示应显示）"""
        # 类型筛选
        if self._type_filter and len(self._type_filter) < len(self._ALL_TYPES) and g:
            if self._get_type_name(self._get_app_type(g)) not in self._type_filter:
                return False
        f = filters
        # 笔记状态筛选
        if f['dirty_only'] and not is_dirty:
            return False
        if f['uploading_only'] and not is_uploading:
            return False
        # AI/模型筛选
        if f['filter_mode'] == "🤖AI" and not has_ai:
            return False
        if f['filter_mode'] == "📝未AI" and has_ai:
            return False
        if f['model_filter'] is not None:
            models = ai_notes_map.get(aid, {}).get('models', [])
            if f['model_filter'] not in models:
                return False
        # AI 元数据筛选（来源/信息量/确信度/质量）
        if not self._lib_match_ai_meta(aid, has_ai, ai_notes_map, f):
            return False
        # 搜索过滤
        if search_q and not self._lib_match_search(aid, name, search_q, search_mode):
            return False
        return True

    def _lib_match_ai_meta(self, aid, has_ai, ai_notes_map, f):
        """AI 元数据筛选：来源/信息量/确信度/质量（返回 False 表示不匹配）"""
        ai_info = ai_notes_map.get(aid, {})
        # 来源筛选
        if f['source_filter'] != "来源":
            src_key = self._strip_filter_prefix(f['source_filter'])
            if "联网" in src_key:
                if not has_ai or 'web' not in ai_info.get('info_sources', []):
                    return False
            elif "本地" in src_key:
                if not has_ai or 'local' not in ai_info.get('info_sources', []):
                    return False
        # 信息量筛选
        if f['vol_filter'] != "信息量":
            if "信息过少" in f['vol_filter']:
                if not has_ai or not ai_info.get('has_insufficient', False):
                    return False
            else:
                vol_key = self._strip_filter_prefix(f['vol_filter'])
                if vol_key not in ai_info.get('info_volumes', []):
                    return False
        # 确信度筛选
        if f['conf_filter'] != "确信度":
            conf_key = self._strip_filter_prefix(f['conf_filter'])
            if conf_key not in ai_info.get('confidences', []):
                return False
        # 质量筛选
        if f['qual_filter'] != "质量":
            if "未评估" in f['qual_filter']:
                if has_ai and ai_info.get('qualities', []):
                    return False
            else:
                qual_key = self._strip_filter_prefix(f['qual_filter'])
                if qual_key not in ai_info.get('qualities', []):
                    return False
        return True

    def _lib_match_search(self, aid, name, search_q, search_mode):
        """搜索过滤（返回 True 表示匹配）"""
        if search_mode == "name":
            return search_q in name.lower() or search_q in aid.lower()
        try:
            note_data = self.manager.read_notes_cached(aid)
            all_text = " ".join(
                n.get("content", "") + " " + n.get("title", "")
                for n in note_data.get("notes", []))
            return search_q in all_text.lower()
        except Exception:
            return False

    # ── 搜索模式切换 ──

    def _show_search_mode_menu(self, event):
        """搜索框右键菜单：切换搜索模式"""
        menu = tk.Menu(self.root, tearoff=0)
        cur = self._main_search_mode.get()
        menu.add_command(
            label=("✓ " if cur == "name" else "   ") + "按名称搜索",
            command=lambda: self._set_search_mode("name"))
        menu.add_command(
            label=("✓ " if cur == "content" else "   ") + "按笔记内容搜索",
            command=lambda: self._set_search_mode("content"))
        menu.tk_popup(event.x_root, event.y_root)

    def _set_search_mode(self, mode):
        self._main_search_mode.set(mode)
        self._update_search_placeholder()
        self._on_main_search_changed()

    def _update_search_placeholder(self):
        """更新搜索模式指示标签"""
        lbl = getattr(self, '_search_mode_label', None)
        if not lbl:
            return
        mode = self._main_search_mode.get()
        lbl.config(text="📝" if mode == "content" else "🔍")

    # ── 评测等级标签（review_score 1-9） ──
    _REVIEW_LABELS = {
        9: "好评如潮", 8: "特别好评", 7: "好评", 6: "多半好评",
        5: "褒贬不一", 4: "多半差评", 3: "差评", 2: "特别差评",
        1: "差评如潮",
    }

    # ── AI 排序键常量 ──
    _SRC_RANK = {"web": 2, "local": 1}
    _VOL_RANK = {"相当多": 5, "较多": 4, "中等": 3, "较少": 2, "相当少": 1}
    _CONF_RANK = {"很高": 5, "较高": 4, "中等": 3, "较低": 2, "很低": 1}
    _QUAL_RANK = {"相当好": 5, "较好": 4, "中等": 3, "较差": 2, "相当差": 1}

    def _lib_build_display_columns(self, aid, has_ai, ai_notes_map, note_count):
        """构建笔记列和来源列的显示文本，同时计算 AI 排序键"""
        notes_col = f"📝{note_count}" if note_count > 0 else ""
        source_col = ""
        if has_ai:
            ai_info = ai_notes_map.get(aid, {})
            confs = ai_info.get('confidences', [])
            conf_emoji = CONFIDENCE_EMOJI.get(confs[0], "") if confs else ""
            quals = ai_info.get('qualities', [])
            qual_emoji = QUALITY_EMOJI.get(quals[0], "") if quals else ""
            vols = ai_info.get('info_volumes', [])
            vol_emoji = INFO_VOLUME_EMOJI.get(vols[0], "") if vols else ""
            has_insuf = ai_info.get('has_insufficient', False)
            sources = ai_info.get('info_sources', [])
            source_emoji = "📡" if 'web' in sources else ("📚" if 'local' in sources else "")
            if has_insuf:
                source_col = f"⛔{source_emoji}"
            else:
                source_col = f"{source_emoji}{vol_emoji}{conf_emoji}{qual_emoji}"
            # AI 排序键
            sr = max((self._SRC_RANK.get(s, 0) for s in sources), default=0)
            vr = self._VOL_RANK.get(vols[0], 0) if vols else 0
            cr = self._CONF_RANK.get(confs[0], 0) if confs else 0
            qr = self._QUAL_RANK.get(quals[0], 0) if quals else 0
            self._ai_sort_data[aid] = (sr, vr, cr, qr)
        return notes_col, source_col

    def _format_info_cols(self, g):
        """格式化信息列：评测等级、好评%、发行、入库、MC"""
        review_score = g.get('review_score', 0)
        review_pct = g.get('review_pct', 0)
        label_col = self._REVIEW_LABELS.get(review_score, "") if review_score else ""
        pct_col = f"{review_pct}%" if review_pct else ""
        rt_release = g.get('rt_release', 0)
        release_col = (datetime.fromtimestamp(rt_release).strftime("%Y-%m-%d")
                       if rt_release else g.get('release_date_str', ''))
        rt_purchased = g.get('rt_purchased', 0)
        acquired_col = (datetime.fromtimestamp(rt_purchased).strftime("%Y-%m-%d")
                        if rt_purchased else "")
        mc = g.get('metacritic', 0)
        mc_col = str(mc) if mc else ""
        return label_col, pct_col, release_col, acquired_col, mc_col

    def _cache_sort_keys(self, aid, type_str, name, note_count, latest_ts, g):
        """预缓存排序键"""
        self._sort_key_cache[aid] = {
            'type': type_str,
            'appid': int(aid) if aid.isdigit() else 0,
            'name': name.lower(),
            'notes': note_count,
            'source': self._ai_sort_data.get(aid, (0, 0, 0, 0)),
            'date': latest_ts,
            'review_label': g.get('review_score', 0),
            'review': g.get('review_pct', 0),
            'release': g.get('rt_release', 0),
            'acquired': g.get('rt_purchased', 0),
            'metacritic': g.get('metacritic', 0),
        }

    def _lib_insert_game_row(self, tree, aid, g, name, is_owned, has_ai,
                              is_dirty, is_uploading, ai_notes_map,
                              notes_col, source_col, note_count, filter_mode,
                              latest_ts=0):
        """插入一行游戏到树视图（含子笔记节点），返回 enriched 游戏字典"""
        app_type = self._get_app_type(g)
        type_str = self._get_type_name(app_type)

        # 改动/上传标记
        dirty_tag = " ☁️⬆" if is_uploading else (" ⬆" if is_dirty else "")
        display_name = f"{name}{dirty_tag}"

        # 行标签
        if not is_owned:
            tag = "not_owned"
        elif is_uploading:
            tag = "uploading"
        elif is_dirty:
            tag = "dirty"
        elif has_ai and ai_notes_map.get(aid, {}).get('has_insufficient', False):
            tag = "insufficient"
        elif has_ai:
            tag = "ai"
        else:
            tag = "normal"

        # 游戏行日期 = 最新笔记日期（latest_ts 由 scan_all 预计算）
        date_col = datetime.fromtimestamp(latest_ts).strftime("%Y-%m-%d") if latest_ts else ""

        # 回填发行日期：CEF 无数据时从 Store API 缓存解析
        if not g.get('rt_release'):
            detail = getattr(self, '_app_detail_cache', {}).get(aid)
            if isinstance(detail, dict) and detail.get('release_date'):
                g['rt_release'] = parse_release_date(detail['release_date'])

        # 新增信息列 + 排序键
        label_col, pct_col, release_col, acquired_col, mc_col = self._format_info_cols(g)
        self._cache_sort_keys(
            aid, type_str, name, note_count, latest_ts, g)

        # 安全插入：清理残留项（detach/reattach fallback 可能遗留）
        if tree.exists(aid):
            tree.delete(aid)
        tree.insert("", tk.END, iid=aid,
                    values=(type_str, aid, display_name, notes_col, source_col,
                            date_col, label_col, pct_col, release_col,
                            acquired_col, mc_col),
                    tags=(tag,))

        # 懒加载占位：有笔记时插入占位子节点（展开时才加载真实子节点）
        if note_count > 0:
            lazy_iid = f"{aid}::lazy"
            if tree.exists(lazy_iid):
                tree.delete(lazy_iid)
            tree.insert(aid, tk.END, iid=lazy_iid,
                        values=("", "", "⏳ 加载中...", "", "", "",
                                "", "", "", "", ""),
                        tags=("note_child",))

        # enriched 数据
        g_copy = dict(g)
        g_copy.update({
            'has_ai': has_ai,
            'ai_models': ai_notes_map.get(aid, {}).get('models', []),
            'game_name': name,
            'is_dirty': is_dirty,
            'is_uploading': is_uploading,
            'note_count': note_count,
        })
        return g_copy

    def _lib_update_status_bar(self, count, owned_count, not_owned_count, notes_total):
        """更新状态栏文本和上传按钮"""
        if owned_count > 0 and not_owned_count > 0:
            text = f"共 {count} 个游戏（{owned_count} 已入库，{not_owned_count} 未入库）| {notes_total} 有笔记"
        elif not_owned_count > 0:
            text = f"共 {not_owned_count} 个未入库游戏 | {notes_total} 有笔记"
        else:
            text = f"共 {owned_count} 个游戏 | {notes_total} 有笔记"
        self._lib_status.config(text=text)
        if self.manager:
            dirty_n = self.manager.dirty_count()
            if self._upload_all_btn:
                if dirty_n > 0:
                    self._upload_all_btn.config(text=f"☁️ 全部上传({dirty_n})")
                    self._upload_all_btn.pack(side=tk.RIGHT, padx=(2, 0))
                else:
                    self._upload_all_btn.pack_forget()

    def _lib_schedule_tree_rebuild(self):
        """防抖合并：异步回调用此方法代替直接调用 _lib_populate_tree。
        100ms 内的多次调用合并为一次 force_rebuild。"""
        pending = getattr(self, '_tree_rebuild_timer', None)
        if pending is not None:
            self.root.after_cancel(pending)
        self._tree_rebuild_timer = self.root.after(
            100, self._lib_populate_tree_deferred)

    def _lib_populate_tree_deferred(self):
        """防抖定时器到期，执行实际 rebuild"""
        self._tree_rebuild_timer = None
        self._tree_rebuild_cache = None
        if getattr(self, '_viewing_collections', False):
            self._apply_coll_filters()
        else:
            self._lib_populate_tree()

    def _lib_populate_tree(self, force_rebuild=False):
        """填充统一游戏列表（库数据 + 笔记数据合并）"""
        tree = self._lib_tree
        # 防止重建过程中 <<TreeviewSelect>> 事件触发副作用
        self._selection_updating = True
        try:
            # 快速路径：仅筛选变化时用 detach/reattach 替代全量重建
            cache = getattr(self, '_tree_rebuild_cache', None)
            if not force_rebuild and cache is not None:
                self._lib_filter_reattach(tree, cache)
            else:
                self._lib_populate_tree_inner(tree)
        finally:
            self._selection_updating = False

    def _lib_populate_tree_inner(self, tree):
        """_lib_populate_tree 的内部实现（在 _selection_updating 保护下运行）"""
        if not tree.winfo_exists():
            return
        # 隐藏树控件避免逐条 insert 触发重绘（30000+ 项时显著加速）
        tree.grid_remove()
        try:
            self._lib_populate_tree_core(tree)
        finally:
            tree.grid()

    def _lib_populate_tree_core(self, tree):
        """实际重建逻辑（在 grid_remove 保护下运行）"""
        # 保存当前选中状态，重建后恢复
        saved_selection = set(tree.selection())
        # 删除可见节点 + detach 过的隐藏节点（防止重建时 iid 冲突）
        detached = getattr(self, '_tree_detached_aids', set())
        for aid in detached:
            try:
                tree.delete(aid)
            except Exception:
                pass
        detached.clear()
        tree.delete(*tree.get_children())
        self._prev_tree_selection = set()
        search_q = self._lib_search_var.get().strip().lower() if hasattr(self, '_lib_search_var') else ""
        search_mode = self._main_search_mode.get() if hasattr(self, '_main_search_mode') else "name"

        # 获取笔记数据
        notes_games, ai_notes_map, syncstate_map = self._lib_load_notes_data()
        from ui_utils import perf_log
        perf_log('  tree_core: notes loaded', extra=f'{len(notes_games)} notes')

        # 获取筛选器状态
        filters = self._lib_read_filter_state()
        filter_mode = filters['filter_mode']

        # 更新模型下拉框
        self._lib_update_model_combo(ai_notes_map)

        # 合并数据源：库游戏 + 仅有笔记但不在库中的游戏（收藏夹筛选模式下跳过）
        all_aids_in_lib = set(g.get('app_id', '') for g in self._lib_all_games)
        merged_games = list(self._lib_all_games)
        if not self._viewing_collections:
            for aid, ng in notes_games.items():
                if aid not in all_aids_in_lib:
                    merged_games.append({
                        'app_id': aid,
                        'name': self._get_game_name(aid),
                        'owned': True,
                        'type': 1,
                    })

            # 已删除但仍在云同步中的游戏（syncstate=3，文件已删除）
            all_merged_aids = all_aids_in_lib | set(notes_games.keys())
            for aid, state in syncstate_map.items():
                if state == 3 and aid not in all_merged_aids:
                    merged_games.append({
                        'app_id': aid,
                        'name': self._get_game_name(aid),
                        'owned': True,
                        'type': 1,
                    })

        count = 0
        owned_count = 0
        not_owned_count = 0
        notes_count = 0
        filtered_games = []
        seen_aids = set()
        self._ai_sort_data = {}  # {aid: (source_rank, vol_rank, conf_rank, qual_rank)}
        self._sort_key_cache = {}  # {aid: {col: sort_value}} — 预缓存排序键
        import time as _t; _loop_t0 = _t.perf_counter()

        for g in merged_games:
            aid = str(g['app_id']).split("::")[0]  # 防御性清理
            name = g.get('name', f"AppID {aid}")
            if aid in seen_aids:
                continue
            seen_aids.add(aid)
            is_owned = g.get('owned', True)

            # 笔记相关数据
            has_notes = aid in notes_games
            note_count = notes_games[aid]['note_count'] if has_notes else 0
            has_ai = aid in ai_notes_map
            is_dirty = self.manager.is_dirty(aid) if self.manager and has_notes else False
            is_uploading = syncstate_map.get(aid) == 3

            # ── 筛选 + 搜索 ──
            if not self._lib_should_include_game(
                    aid, has_ai, is_dirty, is_uploading,
                    ai_notes_map, filters, search_q, search_mode, name, g):
                continue

            # ── 构建显示列 + 插入行 ──
            notes_col, source_col = self._lib_build_display_columns(
                aid, has_ai, ai_notes_map, note_count)

            latest_ts = notes_games[aid]['latest_ts'] if has_notes else 0
            g_copy = self._lib_insert_game_row(
                tree, aid, g, name, is_owned, has_ai, is_dirty, is_uploading,
                ai_notes_map, notes_col, source_col, note_count, filter_mode,
                latest_ts)

            filtered_games.append(g_copy)
            count += 1
            if note_count > 0:
                notes_count += 1
            if is_owned:
                owned_count += 1
            else:
                not_owned_count += 1

        self._games_data = filtered_games
        perf_log('  tree_core: insert loop', (_t.perf_counter() - _loop_t0) * 1000,
                 f'{count} rows from {len(merged_games)} merged')
        self._lib_update_status_bar(count, owned_count, not_owned_count, notes_count)

        # 缓存重建数据（供 L4 快速筛选路径使用）
        self._tree_rebuild_cache = {
            'merged': merged_games, 'notes': notes_games,
            'ai': ai_notes_map, 'sync': syncstate_map,
        }

        # 如果有活跃的排序状态，在插入后立即排序（单次 Tcl 调用）
        if self._sort_columns and self._sort_key_cache:
            self._apply_sort_order(tree)

        # 恢复之前的选中状态（仅恢复仍存在于树中的项）
        if saved_selection:
            existing = set(tree.get_children())
            for game_iid in list(existing):
                existing.update(tree.get_children(game_iid))
            restore = saved_selection & existing
            if restore:
                tree.selection_set(list(restore))
                self._prev_tree_selection = set(restore)

    def _lib_filter_reattach(self, tree, cache):
        """快速筛选路径：用 detach/reattach 替代全量 delete+insert。
        仅在筛选条件变化（搜索、AI筛选、类型筛选等）时使用，
        数据变化（笔记增删、CEF刷新）时仍走全量重建。
        """
        merged = cache['merged']
        notes_games = cache['notes']
        ai_notes_map = cache['ai']
        syncstate_map = cache['sync']

        filters = self._lib_read_filter_state()
        filter_mode = filters['filter_mode']
        search_q = self._lib_search_var.get().strip().lower() if hasattr(self, '_lib_search_var') else ""
        search_mode = self._main_search_mode.get() if hasattr(self, '_main_search_mode') else "name"

        self._lib_update_model_combo(ai_notes_map)

        # 当前树中可见的顶层项
        visible_now = set(tree.get_children())
        should_visible = set()
        count = owned_count = not_owned_count = notes_count = 0
        filtered_games = []

        for g in merged:
            aid = str(g['app_id']).split("::")[0]  # 防御性清理
            name = g.get('name', f"AppID {aid}")
            is_owned = g.get('owned', True)
            has_notes = aid in notes_games
            note_count = notes_games[aid]['note_count'] if has_notes else 0
            has_ai = aid in ai_notes_map
            is_dirty = self.manager.is_dirty(aid) if self.manager and has_notes else False
            is_uploading = syncstate_map.get(aid) == 3

            if not self._lib_should_include_game(
                    aid, has_ai, is_dirty, is_uploading,
                    ai_notes_map, filters, search_q, search_mode, name, g):
                continue

            should_visible.add(aid)
            count += 1
            if note_count > 0:
                notes_count += 1
            if is_owned:
                owned_count += 1
            else:
                not_owned_count += 1

        # detach 不再匹配的项
        to_detach = visible_now - should_visible
        for aid in to_detach:
            tree.detach(aid)

        # 立即更新 detached 记录（fallback 到 _lib_populate_tree_inner 时需要）
        if not hasattr(self, '_tree_detached_aids'):
            self._tree_detached_aids = set()
        self._tree_detached_aids |= to_detach

        # reattach 新匹配的项（之前被 detach 过的）
        to_show = should_visible - visible_now
        for aid in to_show:
            if aid in self._tree_detached_aids:
                tree.reattach(aid, "", tk.END)
                self._tree_detached_aids.discard(aid)
            # 从未插入过的项需要全量重建
            elif aid not in visible_now:
                self._tree_rebuild_cache = None
                self._lib_populate_tree_inner(tree)
                return

        # 移除已 reattach 的项
        self._tree_detached_aids -= to_show

        # 重建 _games_data（供右键菜单等使用）
        for g in merged:
            if str(g['app_id']).split("::")[0] in should_visible:
                filtered_games.append(g)
        self._games_data = filtered_games
        self._lib_update_status_bar(count, owned_count, not_owned_count, notes_count)

        if self._sort_columns and self._sort_key_cache:
            self._apply_sort_order(tree)

    def _update_sub_filter_visibility(self):
        """根据 AI 筛选器状态，渐进显示/隐藏子筛选器"""
        if not hasattr(self, '_ai_filter_var'):
            return
        # 合并筛选器：选中 🤖AI 或具体模型名时都算 AI 模式
        _base = getattr(self, '_ai_filter_base_values', ["全部", "🤖AI", "📝未AI"])
        filter_val = self._ai_filter_var.get()
        is_ai = (filter_val not in ("全部", "📝未AI"))
        is_insufficient = ("信息过少" in self._vol_filter_var.get())

        # 子筛选器（来源/信息量/确信度/质量）：仅 AI 模式时显示
        if is_ai:
            if not self._sub_filters_visible:
                self._source_filter_combo.pack(side=tk.LEFT, padx=(3, 0))
                self._vol_filter_combo.pack(side=tk.LEFT, padx=(3, 0))
                self._conf_filter_combo.pack(side=tk.LEFT, padx=(3, 0))
                self._sub_filters_visible = True
                # 质量单独控制
                if not is_insufficient:
                    self._qual_filter_combo.pack(side=tk.LEFT, padx=(3, 0))
                    self._qual_filter_visible = True
        else:
            if self._sub_filters_visible:
                self._source_filter_combo.pack_forget()
                self._vol_filter_combo.pack_forget()
                self._conf_filter_combo.pack_forget()
                self._sub_filters_visible = False
            if self._qual_filter_visible:
                self._qual_filter_combo.pack_forget()
                self._qual_filter_visible = False

        # 质量筛选：信息过少时隐藏
        if is_ai and self._sub_filters_visible:
            if is_insufficient:
                if self._qual_filter_visible:
                    self._qual_filter_combo.pack_forget()
                    self._qual_filter_visible = False
                    self._qual_filter_var.set("质量")
            else:
                if not self._qual_filter_visible:
                    self._qual_filter_combo.pack(side=tk.LEFT, padx=(3, 0))
                    self._qual_filter_visible = True

    def _create_coll_filter_icons(self):
        """程序化绘制三态筛选图标（14×14 PhotoImage）"""
        size = 14
        # ○ 默认：灰色圆圈
        img_def = tk.PhotoImage(width=size, height=size)
        cx, cy, r = 7, 7, 5
        for y in range(size):
            for x in range(size):
                dx, dy = x - cx, y - cy
                d2 = dx * dx + dy * dy
                if r * r - r * 2 < d2 <= r * r:
                    img_def.put("#999", to=(x, y, x + 1, y + 1))
        self._img_coll_default = img_def

        # ＋ 包含：绿色加号
        img_plus = tk.PhotoImage(width=size, height=size)
        c = "#2e7d32"
        for x in range(3, 12):
            img_plus.put(c, to=(x, 6, x + 1, 8))
        for y in range(3, 12):
            img_plus.put(c, to=(6, y, 8, y + 1))
        self._img_coll_plus = img_plus

        # － 排除：红色减号
        img_minus = tk.PhotoImage(width=size, height=size)
        c = "#c62828"
        for x in range(3, 12):
            img_minus.put(c, to=(x, 6, x + 1, 8))
        self._img_coll_minus = img_minus

    def _refresh_games_list_fast(self):
        """启动时快速刷新列表：仅使用持久化缓存，不做网络请求"""
        self._ensure_game_name_cache_fast()
        self._lib_populate_tree()

    def _lib_refresh(self):
        """刷新库列表：CEF 已连接时后台获取全量，否则扫描本地已安装"""
        self._lib_status.config(text="🔄 正在刷新...")

        if self._cef_bridge and self._cef_bridge.is_connected():
            self._bg_load_cef_data(rebuild=True)
        else:
            self._lib_all_games = []
            self._lib_load_initial()
            self._lib_load_collections()

        # 刷新后：如果有活跃 +/- 筛选，重新应用
        if any(v in ('plus', 'minus') for v in self._coll_filter_states.values()):
            self._lib_all_games_backup = None
            self._apply_coll_filters()

    def _lib_toggle_cef(self):
        """连接 / 断开 CEF（库管理用）"""
        if self._cef_bridge is not None:
            try:
                self._cef_bridge.disconnect()
            except Exception:
                pass
            self._cef_bridge = None
            # 清除 CollectionsCore 的 CEF 引用
            if self._collections_core:
                self._collections_core.cef = None
            self._update_library_cloud_status()
            # 断开 CEF 后刷新：回退到本地数据
            self._lib_refresh()
            return

        if CEFBridge is None:
            messagebox.showwarning("提示", "websocket-client 未安装，无法使用 CEF。",
                                   parent=self.root)
            return

        if not CEFBridge.is_available():
            messagebox.showinfo("提示",
                "CEF 调试端口未就绪。\n\n"
                "请确保 Steam 以 CEF 调试模式运行。",
                parent=self.root)
            return

        bridge = CEFBridge()
        ok, err = bridge.connect()
        if ok:
            self._cef_bridge = bridge
            self._update_library_cloud_status()
            # 将 CEF 桥接器传给 CollectionsCore（如果已初始化）
            if self._collections_core:
                self._collections_core.cef = bridge
            # CEF 连接成功：补充名称缓存 → 加载收藏夹 → 加载完整游戏列表
            self._lib_status.config(text="🔄 正在从 CEF 获取数据...")
            self.root.update_idletasks()
            self._lib_enhance_name_cache_from_cef()
            self._lib_load_collections()
            self._lib_load_owned_from_cef()
        else:
            messagebox.showerror("❌ 连接失败", f"CEF 连接失败: {err}",
                                 parent=self.root)
