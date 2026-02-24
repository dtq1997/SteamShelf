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
            elif info is None:
                # 所有源均失败（网络问题）
                self._update_check_result = "failed"
                if manual:
                    self.root.after(0, lambda: self._show_update_msg(
                        _parent, "检查更新",
                        "无法连接更新服务器，请检查网络后重试。"))
            else:
                # has_update=False，确认无更新
                self._update_check_result = "latest"
                if manual:
                    self.root.after(0, lambda: self._show_update_msg(
                        _parent, "检查更新",
                        f"当前已是最新版本 v{updater.__version__}"))
        threading.Thread(target=bg_thread(_bg), daemon=True).start()

    def _show_update_msg(self, parent, title, msg):
        """安全弹出更新提示（父窗口可能已销毁）"""
        try:
            p = parent if parent.winfo_exists() else self.root
        except Exception:
            p = self.root
        messagebox.showinfo(title, msg, parent=p)

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
        if getattr(self, '_update_dialog_open', False):
            return
        self._update_dialog_open = True
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
        def _close_update_dialog():
            self._update_dialog_open = False
            win.grab_release()
            win.destroy()
        ttk.Button(btn_frame, text="稍后再说",
                   command=_close_update_dialog).pack(side=tk.LEFT, padx=5)

        win.protocol("WM_DELETE_WINDOW", _close_update_dialog)
        self._center_window(win)

    def _do_download_and_apply(self, info, win, prog_frame,
                                prog_label, prog_bar, update_btn):
        """后台下载 → 进度条 → 应用更新"""
        import sys as _sys
        update_btn.config(state=tk.DISABLED)
        prog_frame.pack(fill=tk.X, padx=15, pady=(0, 10))
        prog_bar.config(mode='indeterminate')
        prog_bar.start(15)  # 连接阶段：不定进度动画
        prog_label.config(text="正在连接服务器...")
        # Windows frozen → 临时目录（bat 脚本自动处理）
        # 其他 → ~/Downloads（用户友好）
        if _sys.platform == "win32" and getattr(_sys, 'frozen', False):
            dest = updater.get_temp_zip_path()
        else:
            dest = updater.get_download_zip_path(info["version"])

        def _progress(downloaded, total):
            def _ui():
                try:
                    if not win.winfo_exists():
                        return
                except Exception:
                    return
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

        _switched_to_determinate = False

        def _status(msg):
            nonlocal _switched_to_determinate
            def _ui():
                nonlocal _switched_to_determinate
                try:
                    if not win.winfo_exists():
                        return
                except Exception:
                    return
                if msg is None:
                    # 连接成功，切换到确定进度条
                    if not _switched_to_determinate:
                        _switched_to_determinate = True
                        prog_bar.stop()
                        prog_bar.config(mode='determinate', value=0)
                else:
                    prog_label.config(text=msg)
            self.root.after(0, _ui)

        def _bg():
            ok = updater.download_update(
                info["download_urls"], dest, _progress, _status)
            self.root.after(0, lambda: _on_done(ok))

        def _on_done(ok):
            try:
                if not win.winfo_exists():
                    return  # 用户已关闭窗口
            except Exception:
                return
            if not ok:
                prog_label.config(text="下载失败，请检查网络后重试")
                update_btn.config(state=tk.NORMAL)
                return
            prog_label.config(text="下载完成，准备更新...")
            prog_bar['value'] = 100
            # apply 可能涉及文件 I/O（Defender 扫描会锁文件），放后台
            def _apply():
                result = updater.apply_update_and_restart(dest)
                # Windows frozen 走 os._exit(0)，不会到这里
                # 非 frozen 返回 zip_path，主线程显示提示
                if result:
                    self.root.after(0, lambda: _after_apply(result))
            def _after_apply(result):
                try:
                    if win.winfo_exists():
                        self._update_dialog_open = False
                        win.grab_release()
                        win.destroy()
                except Exception:
                    pass
                self._show_update_success_dialog(result, info["version"])
            threading.Thread(target=bg_thread(_apply), daemon=True).start()

        threading.Thread(target=bg_thread(_bg), daemon=True).start()

    def _show_update_success_dialog(self, zip_path, version):
        """下载完成后的友好提示对话框（非 Windows frozen 专用）"""
        import os
        win = tk.Toplevel(self.root)
        win.title("更新已下载")
        win.resizable(False, False)
        win.grab_set()
        win.transient(self.root)

        tk.Label(win, text=f"v{version} 已下载完成",
                 font=("", 13, "bold")).pack(pady=(15, 5))

        filename = os.path.basename(zip_path)
        folder = os.path.dirname(zip_path)
        tk.Label(win, text=f"文件：{filename}",
                 font=("", 10)).pack(padx=20, pady=(5, 2))
        tk.Label(win, text=f"位置：{folder}",
                 font=("", 9), fg="#666").pack(padx=20, pady=(0, 5))

        guide = tk.LabelFrame(win, text="更新步骤", font=("", 10),
                              padx=10, pady=5)
        guide.pack(fill=tk.X, padx=15, pady=8)
        steps = ("1. 关闭当前程序\n"
                 "2. 解压下载的 zip 文件\n"
                 "3. 用新文件覆盖当前目录\n"
                 "4. 重新启动")
        tk.Label(guide, text=steps, justify="left", anchor="w",
                 font=("", 10)).pack(fill=tk.X)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(5, 15))
        ttk.Button(btn_frame, text="在文件管理器中显示",
                   command=lambda: updater.reveal_in_file_manager(zip_path)
                   ).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="关闭",
                   command=lambda: (win.grab_release(), win.destroy())
                   ).pack(side=tk.LEFT, padx=5)

        win.protocol("WM_DELETE_WINDOW",
                     lambda: (win.grab_release(), win.destroy()))
        self._center_window(win)
