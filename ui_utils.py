"""
ui_utils.py — 全局 tkinter 补丁工具

包含弹窗置顶补丁 _patch_dialogs_topmost()，确保所有弹窗以正确的 parent 弹出。
ProgressWindow — 可复用的进度窗口组件（线程安全）。
bg_thread — 后台线程装饰器（捕获异常防止静默失败）。
"""

import functools
import logging
import os
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


# ── 性能追踪器（STEAMSHELF_DEBUG=1 启用）──

_PERF_ENABLED = os.environ.get('STEAMSHELF_DEBUG') == '1'
_PERF_LOG_PATH = os.path.join(os.path.expanduser('~'), '.steam_toolkit', 'perf.log')
_perf_t0 = time.perf_counter()  # app 启动基准时间


def perf_log(label, elapsed_ms=None, extra=''):
    """写一行性能日志。elapsed_ms=None 时仅记录时间戳。"""
    if not _PERF_ENABLED:
        return
    now = time.perf_counter() - _perf_t0
    tid = threading.current_thread().name
    ms_part = f' {elapsed_ms:.0f}ms' if elapsed_ms is not None else ''
    line = f'[{now:8.3f}s] [{tid:12s}]{ms_part} {label}'
    if extra:
        line += f'  ({extra})'
    line += '\n'
    try:
        with open(_PERF_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line)
    except OSError as e:
        print(f"[perf_log FAIL] {e}", flush=True)


class perf_trace:
    """计时上下文管理器。用法: with perf_trace('步骤名'): ..."""
    __slots__ = ('label', 'extra', 't0')

    def __init__(self, label, extra=''):
        self.label = label
        self.extra = extra

    def __enter__(self):
        if _PERF_ENABLED:
            self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if _PERF_ENABLED:
            ms = (time.perf_counter() - self.t0) * 1000
            perf_log(self.label, ms, self.extra)
        return False


def perf_install_hooks(obj):
    """monkey-patch obj 的关键方法，自动记录每次调用耗时。
    只在 STEAMSHELF_DEBUG=1 时生效，否则什么都不做。"""
    if not _PERF_ENABLED:
        return
    # 要追踪的方法名列表（覆盖启动、加载、筛选、渲染全链路）
    _METHODS = [
        # ui_main.py
        'show_main_window',
        '_ensure_game_name_cache_fast',
        '_bg_init_game_names',
        '_save_and_sync',
        '_refresh_games_list',
        '_parse_remotecache_syncstates',
        # ui_library.py
        '_lib_populate_tree',
        '_lib_populate_tree_core',
        '_lib_load_notes_data',
        '_bg_load_cef_data',
        '_apply_sort_order',
        '_auto_connect_cef',
        '_lib_load_owned_from_cef',
        # ui_library_collections.py
        '_lib_load_collections',
        '_lib_render_collections_cef',
        '_lib_render_collections_local',
        '_apply_coll_filters',
        '_eval_coll_expr',
        '_coll_filter_build_games',
        '_eval_filter_expression',
        '_auto_update_expression_collections',
        '_schedule_expression_update',
        '_cef_fetch_unowned_overviews',
        # ui_collection_ops.py
        '_update_all_cached_sources',
        # core
        '_ui_refresh',
    ]
    for name in _METHODS:
        orig = getattr(obj, name, None)
        if orig is None:
            continue
        # 用闭包捕获 orig 和 name
        def _make_wrapper(_orig, _name):
            @functools.wraps(_orig)
            def wrapper(*a, **kw):
                t = time.perf_counter()
                try:
                    return _orig(*a, **kw)
                finally:
                    ms = (time.perf_counter() - t) * 1000
                    tid = threading.current_thread().name
                    tag = '⚠️SLOW ' if ms > 100 else ''
                    perf_log(f'{tag}{_name}', ms)
            return wrapper
        setattr(obj, name, _make_wrapper(orig, name))

_OriginalToplevel = tk.Toplevel  # 保存原始 Toplevel，供内部使用


def _patch_dialogs_topmost():
    """启动时调用一次，猴子补丁弹窗类，确保对话框以正确的 parent 弹出。

    v2.2 改进：不再强制所有窗口 -topmost，仅保留 parent 查找逻辑，
    避免功能窗口遮挡用户的其他工作。
    """

    # --- 1. Toplevel 子窗口：不再强制置顶，仅 lift + 抢焦点 ---
    class _TopmostToplevel(_OriginalToplevel):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.lift()
            self.focus_force()

    tk.Toplevel = _TopmostToplevel

    # --- 辅助：找到当前焦点所在的最上层窗口 ---
    def _find_active_toplevel():
        """查找当前焦点所在的 Toplevel，找不到则回退到 Tk 根窗口。"""
        try:
            focus_w = None
            root = tk._default_root
            if root:
                try:
                    focus_w = root.focus_get()
                except KeyError:
                    pass
            if focus_w is not None:
                w = focus_w
                while w is not None:
                    if isinstance(w, (_OriginalToplevel, tk.Tk)):
                        w.lift()
                        return w
                    w = getattr(w, 'master', None)
            if root:
                toplevels = [w for w in root.winfo_children()
                             if isinstance(w, _OriginalToplevel) and w.winfo_viewable()]
                if toplevels:
                    top = toplevels[-1]
                    top.lift()
                    return top
                return root
        except Exception:
            pass
        return None

    # --- 2. messagebox ---
    def _wrap_messagebox(func):
        def wrapper(*args, **kwargs):
            if 'parent' not in kwargs:
                parent = _find_active_toplevel()
                if parent:
                    kwargs['parent'] = parent
            p = kwargs.get('parent')
            if p and hasattr(p, 'lift'):
                try:
                    p.lift()
                except Exception:
                    pass
            return func(*args, **kwargs)
        return wrapper

    for name in ('showinfo', 'showwarning', 'showerror',
                 'askyesno', 'askyesnocancel', 'askquestion',
                 'askretrycancel', 'askokcancel'):
        original = getattr(messagebox, name, None)
        if original:
            setattr(messagebox, name, _wrap_messagebox(original))

    # --- 3. simpledialog ---
    def _wrap_simpledialog(func):
        def wrapper(*args, **kwargs):
            if 'parent' not in kwargs:
                parent = _find_active_toplevel()
                if parent:
                    kwargs['parent'] = parent
            p = kwargs.get('parent')
            if p and hasattr(p, 'lift'):
                try:
                    p.lift()
                except Exception:
                    pass
            return func(*args, **kwargs)
        return wrapper

    for name in ('askstring', 'askinteger', 'askfloat'):
        original = getattr(simpledialog, name, None)
        if original:
            setattr(simpledialog, name, _wrap_simpledialog(original))

    # --- 4. filedialog ---
    def _wrap_filedialog(func):
        def wrapper(*args, **kwargs):
            if 'parent' not in kwargs:
                parent = _find_active_toplevel()
                if parent:
                    kwargs['parent'] = parent
            p = kwargs.get('parent')
            if p and hasattr(p, 'lift'):
                try:
                    p.lift()
                except Exception:
                    pass
            return func(*args, **kwargs)
        return wrapper

    for name in ('askopenfilename', 'asksaveasfilename', 'askopenfilenames',
                 'askdirectory', 'askopenfile', 'asksaveasfile'):
        original = getattr(filedialog, name, None)
        if original:
            setattr(filedialog, name, _wrap_filedialog(original))


# 模块导入时自动执行补丁
_patch_dialogs_topmost()


def set_window_icon(root):
    """为 Tk 窗口设置 SteamShelf logo 图标"""
    import os
    logo = os.path.join(os.path.dirname(__file__), "logo.png")
    if os.path.exists(logo):
        try:
            img = tk.PhotoImage(file=logo)
            root.iconphoto(True, img)
            root._logo_img = img  # 防止 GC 回收
        except Exception:
            pass


def bg_thread(fn):
    """后台线程装饰器：捕获异常并打印+写日志，防止 daemon 线程静默失败"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            print(f"[bg_thread] {fn.__qualname__} 异常:")
            traceback.print_exc()
            logging.getLogger("steamshelf").exception(
                f"bg_thread {fn.__qualname__} 异常")
    return wrapper


class AutoScrollbar(tk.Scrollbar):
    """内容溢出时自动显示、不溢出时自动隐藏的滚动条。

    必须使用 grid 布局。macOS aqua 下自动强制经典渲染器以确保可见。

    用法::

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        sb = AutoScrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        tree.config(yscrollcommand=sb.set)
    """

    _AQUA_DEFAULTS = dict(
        width=14, bg="#c8c8c8", troughcolor="#f0f0f0",
        activebackground="#a0a0a0", highlightthickness=0,
    )

    def __init__(self, master=None, **kw):
        if master and master.tk.call("tk", "windowingsystem") == "aqua":
            for k, v in self._AQUA_DEFAULTS.items():
                kw.setdefault(k, v)
        super().__init__(master, **kw)

    def set(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(lo, hi)


class ProgressWindow:
    """可复用的进度窗口（线程安全）

    用法：
        pw = ProgressWindow(parent, "☁️ 同步中", "正在同步...", maximum=100)
        # 后台线程中：
        pw.update(value=50, status="50/100")
        # 完成后：
        pw.close()
    """

    def __init__(self, parent, title, message, maximum=100,
                 grab=False, detail=False):
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.resizable(False, False)
        if grab:
            self.win.grab_set()

        tk.Label(self.win, text=message,
                 font=("", 11, "bold")).pack(padx=30, pady=(15, 5))

        self.var = tk.DoubleVar(value=0)
        ttk.Progressbar(self.win, variable=self.var,
                        maximum=max(maximum, 1),
                        length=350).pack(padx=30, pady=5)

        self.status_var = tk.StringVar(value="准备中...")
        tk.Label(self.win, textvariable=self.status_var,
                 font=("", 9), fg="#666").pack(padx=30, pady=(0, 5))

        self.detail_var = None
        if detail:
            self.detail_var = tk.StringVar(value="")
            tk.Label(self.win, textvariable=self.detail_var,
                     font=("", 8), fg="#888").pack(padx=30, pady=(0, 10))

        self._parent = parent
        # 防止用户点 X 关闭时 grab 泄漏
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def update(self, value=None, status=None, detail=None):
        """线程安全更新进度"""
        def _up():
            try:
                if value is not None:
                    self.var.set(value)
                if status is not None:
                    self.status_var.set(status)
                if detail is not None and self.detail_var:
                    self.detail_var.set(detail)
            except tk.TclError:
                pass
        try:
            self._parent.after(0, _up)
        except Exception:
            pass

    def close(self):
        """线程安全关闭"""
        def _close():
            try:
                self.win.grab_release()
                self.win.destroy()
            except tk.TclError:
                pass
        try:
            self._parent.after(0, _close)
        except Exception:
            pass
