"""自动更新 UI（UpdaterMixin）

宿主协议：UpdaterHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import UpdaterHost  # noqa: F401

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import updater
from ui_utils import bg_thread


class UpdaterMixin:
    """自动更新相关 UI 方法"""

    def _check_update_bg(self, manual=False, parent=None):
        """后台检查更新（manual=True 时失败也弹提示）"""
        _parent = parent or self.root
        def _bg():
            info = updater.check_update()
            if info and info.get("has_update"):
                self._update_check_result = "available"
                self.root.after(0, lambda: self._on_update_available(info))
            else:
                self._update_check_result = "latest" if info is None else "failed"
                if manual:
                    def _show():
                        try:
                            p = _parent if _parent.winfo_exists() else self.root
                        except Exception:
                            p = self.root
                        messagebox.showinfo(
                            "检查更新", f"当前已是最新版本 v{updater.__version__}",
                            parent=p)
                    self.root.after(0, _show)
        threading.Thread(target=bg_thread(_bg), daemon=True).start()

    def _on_update_available(self, info):
        """顶部栏显示更新提示标签"""
        if not hasattr(self, '_update_label'):
            return
        ver = info["version"]
        self._update_label.config(
            text=f"🔔 v{ver} 可用", fg="#ffeb3b", cursor="hand2")
        self._update_label.pack(side=tk.RIGHT, padx=(2, 6))
        self._update_label.bind(
            "<Button-1>", lambda e: self._show_update_dialog(info))
        self._pending_update_info = info

    def _show_update_dialog(self, info):
        """弹窗显示更新日志 + 下载按钮"""
        win = tk.Toplevel(self.root)
        win.title(f"🔔 SteamShelf v{info['version']} 可用")
        win.resizable(False, True)
        win.grab_set()
        win.transient(self.root)

        tk.Label(win, text=f"新版本 v{info['version']} 可用",
                 font=("", 13, "bold")).pack(pady=(15, 5))
        tk.Label(win, text=f"当前版本: v{updater.__version__}",
                 font=("", 9), fg="#888").pack()

        # 更新日志
        if info.get("changelog"):
            log_frame = tk.LabelFrame(win, text="更新内容", font=("", 10),
                                       padx=10, pady=5)
            log_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
            log_text = tk.Text(log_frame, height=8, width=50, font=("", 10),
                               wrap=tk.WORD, state=tk.NORMAL)
            log_text.insert("1.0", info["changelog"])
            log_text.config(state=tk.DISABLED)
            log_text.pack(fill=tk.BOTH, expand=True)

        # 进度条（初始隐藏）
        prog_frame = tk.Frame(win)
        prog_label = tk.Label(prog_frame, text="", font=("", 9), fg="#666")
        prog_label.pack(anchor=tk.W)
        prog_bar = ttk.Progressbar(prog_frame, length=300, mode='determinate')
        prog_bar.pack(fill=tk.X)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(5, 15))

        update_btn = ttk.Button(btn_frame, text="立即更新",
            command=lambda: self._do_download_and_apply(
                info, win, prog_frame, prog_label, prog_bar, update_btn))
        update_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="稍后再说",
                   command=lambda: (win.grab_release(), win.destroy())
                   ).pack(side=tk.LEFT, padx=5)

        win.protocol("WM_DELETE_WINDOW",
                     lambda: (win.grab_release(), win.destroy()))
        self._center_window(win)

    def _do_download_and_apply(self, info, win, prog_frame,
                                prog_label, prog_bar, update_btn):
        """后台下载 → 进度条 → 应用更新"""
        update_btn.config(state=tk.DISABLED)
        prog_frame.pack(fill=tk.X, padx=15, pady=(0, 10))
        prog_label.config(text="正在下载...")
        dest = updater.get_temp_zip_path()

        def _progress(downloaded, total):
            def _ui():
                if total > 0:
                    pct = downloaded * 100 // total
                    prog_bar['value'] = pct
                    mb = downloaded / 1048576
                    total_mb = total / 1048576
                    prog_label.config(text=f"下载中: {mb:.1f}/{total_mb:.1f} MB ({pct}%)")
                else:
                    mb = downloaded / 1048576
                    prog_label.config(text=f"下载中: {mb:.1f} MB")
            self.root.after(0, _ui)

        def _bg():
            ok = updater.download_update(info["download_urls"], dest, _progress)
            self.root.after(0, lambda: _on_done(ok))

        def _on_done(ok):
            if not ok:
                prog_label.config(text="下载失败，请检查网络后重试")
                update_btn.config(state=tk.NORMAL)
                return
            prog_label.config(text="下载完成，准备更新...")
            prog_bar['value'] = 100
            result = updater.apply_update_and_restart(dest)
            if result:
                # 非 Windows 或源码运行：提示手动替换
                messagebox.showinfo("更新已下载",
                    f"更新包已下载到:\n{result}\n\n"
                    "请手动解压覆盖当前目录后重启。",
                    parent=win)
                win.grab_release()
                win.destroy()

        threading.Thread(target=bg_thread(_bg), daemon=True).start()
