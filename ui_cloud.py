"""ui_cloud.py — Steam Cloud 事务性上传（CloudMixin）

从 ui_main.py 拆分。采用事务性模型：每次上传自动完成
连接→预检→上传→断开→轮询同步，无需持久连接。

宿主协议：CloudHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import CloudHost  # noqa: F401

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from cloud_uploader import SteamCloudUploader
from ui_utils import ProgressWindow


class CloudMixin:
    """Steam Cloud 事务性上传，混入 SteamToolboxMain 使用。"""

    # ────────────────────── 顶栏状态更新 ──────────────────────

    def _cloud_status_update(self, text, busy=False):
        """更新顶栏的 Cloud 上传状态标签（主线程调用）"""
        lbl = getattr(self, '_cloud_upload_label', None)
        if not lbl:
            return
        try:
            lbl.config(text=text, fg="#ffeb3b" if busy else "#aac8ee")
        except tk.TclError:
            pass

    def _cloud_status_update_async(self, text, busy=False):
        """线程安全版本"""
        try:
            self.root.after(0, lambda t=text, b=busy: self._cloud_status_update(t, b))
        except Exception:
            pass

    # ────────────────────── 事务性上传核心 ──────────────────────

    def _transactional_cloud_upload(self, upload_fn, on_done_fn=None,
                                     total_hint=0, modal=False):
        """事务性上传：连接→预检→上传→断开→轮询同步。

        Args:
            upload_fn: callable(manager, progress_cb) -> (ok, fail)
            on_done_fn: callable(ok, fail, pending) — 主线程回调
            total_hint: 进度条最大值提示
            modal: True=模态进度窗口（关闭前上传用），False=顶栏非阻塞
        """
        if getattr(self, '_upload_in_progress', False):
            messagebox.showwarning("提示", "已有上传任务正在进行。",
                                   parent=self.root)
            return
        self._upload_in_progress = True

        # ── 模态 vs 非阻塞 ──
        pw = None
        if modal:
            pw = ProgressWindow(self.root, "☁️ 正在上传...",
                "正在上传笔记到 Steam Cloud...",
                maximum=max(total_hint, 1), grab=True)
            pw.status_var.set("正在检查...")
            self._center_window(pw.win)

        def _set_status(text):
            if pw:
                pw.update(status=text)
            else:
                self._cloud_status_update_async(f"☁️ {text}", busy=True)

        def _set_progress(value):
            if pw:
                pw.update(value=value)

        def _bg():
            ok = fail = pending = 0
            error_msg = ""
            try:
                ok, fail, pending, error_msg = self._do_transactional_upload(
                    upload_fn, _set_status, _set_progress)
            except Exception as e:
                error_msg = str(e)
            finally:
                self._upload_in_progress = False

                def _finish():
                    if pw:
                        pw.close()
                    else:
                        self._cloud_status_update("", busy=False)
                    self._refresh_games_list()
                    if error_msg:
                        messagebox.showerror("❌ 上传失败", error_msg,
                                             parent=self.root)
                    elif on_done_fn:
                        on_done_fn(ok, fail, pending)

                try:
                    self.root.after(0, _finish)
                except Exception:
                    pass

        if not modal:
            self._cloud_status_update("☁️ 正在检查...", busy=True)
        threading.Thread(target=_bg, daemon=True).start()

    def _do_transactional_upload(self, upload_fn, set_status, set_progress):
        """事务上传的实际逻辑（在后台线程中运行）。

        Returns: (ok, fail, pending, error_msg)
        """
        # 1. 检测 Steam 是否运行
        set_status("正在检查 Steam...")
        if not SteamCloudUploader.is_steam_running():
            return 0, 0, 0, ("Steam 未运行。\n\n"
                              "请先启动 Steam 客户端。")

        # 2. 检测是否在游戏中（有 API Key 时）
        steam_api_key = self._config.get("steam_web_api_key", "")
        if steam_api_key:
            set_status("正在检查在线状态...")
            from account_manager import SteamAccountScanner
            result = SteamAccountScanner.check_player_in_game(
                self.current_account.get('friend_code', ''), steam_api_key)
            if result['in_game']:
                answer = [None]
                event = threading.Event()

                def _ask():
                    answer[0] = messagebox.askyesno(
                        "⚠️ 检测到游戏运行中",
                        f"您的账号正在运行「{result['game_name']}」。\n\n"
                        "上传笔记需要短暂启动 Steam Cloud 服务，\n"
                        "可能导致另一台设备的游戏被中断。\n\n"
                        "是否继续？",
                        parent=self.root)
                    event.set()

                self.root.after(0, _ask)
                event.wait()
                if not answer[0]:
                    return 0, 0, 0, ""  # 用户取消，无错误

        # 3. 连接
        set_status("正在连接 Steam Cloud...")
        uploader = SteamCloudUploader()
        steam_path = self.current_account.get('steam_path', '')
        conn_ok, msg = uploader.auto_init(steam_path)
        if not conn_ok:
            return 0, 0, 0, (f"无法连接 Steam Cloud:\n{msg}\n\n"
                              "请确保 Steam 客户端正在运行且库中有已安装的游戏。")

        # 验证账号匹配
        logged_in = uploader.logged_in_friend_code
        selected = self.current_account.get('friend_code', '')
        if logged_in and logged_in != selected:
            uploader.shutdown()
            selected_name = self.current_account.get('persona_name', selected)
            return 0, 0, 0, (
                f"Steam 登录账号 (ID: {logged_in}) 与程序选择的账号"
                f"「{selected_name}」(ID: {selected}) 不匹配。\n\n"
                "请先在 Steam 客户端切换到该账号。")

        # 4. 上传
        self.cloud_uploader = uploader
        self.manager.cloud_uploader = uploader
        try:
            def progress_cb(current, total, ok_n, fail_n):
                try:
                    self.root.after(0, lambda c=current, t=total,
                                    o=ok_n, f=fail_n: (
                        set_progress(c),
                        set_status(f"上传中 {c}/{t}（成功 {o}，失败 {f}）")))
                except Exception:
                    pass

            ok, fail = upload_fn(self.manager, progress_cb)
            self._save_uploaded_hashes()
        finally:
            # 5. 断开
            set_status("⏳ 等待 Steam 处理...")
            try:
                uploader.flush_callbacks(3.0)
            except Exception:
                pass
            try:
                uploader.shutdown()
            except Exception:
                pass
            self.cloud_uploader = None
            self.manager.cloud_uploader = None

        # 6. 轮询同步
        set_status("⏳ 等待 Steam 同步到云端...")
        pending = self._poll_sync_completion(timeout=30)

        return ok, fail, pending, ""

    def _poll_sync_completion(self, timeout=30, interval=1.0):
        """轮询 remotecache.vdf 直到所有 syncstate=3 变为 1。

        Returns: 超时后仍为 syncstate=3 的条目数。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            pending = self._verify_sync_status()
            if pending == 0:
                return 0
            time.sleep(interval)
        return self._verify_sync_status()

    # ────────────────────── 上传入口 ──────────────────────

    def _cloud_upload_all(self):
        """批量上传所有有改动的笔记（事务性）"""
        n = self.manager.dirty_count() if self.manager else 0
        if n == 0:
            messagebox.showinfo("提示", "没有需要上传的改动。",
                                parent=self.root)
            return

        def upload_fn(manager, progress_cb):
            return manager.cloud_upload_all_batch(
                progress_callback=progress_cb)

        def on_done(ok, fail, pending):
            if fail == 0 and pending == 0:
                messagebox.showinfo("✅ 成功",
                    f"已上传 {ok} 个游戏的笔记。", parent=self.root)
            elif fail == 0:
                messagebox.showinfo("☁️ 已写入",
                    f"已写入 {ok} 个游戏。\n"
                    f"{pending} 个仍在等待 Steam 同步。",
                    parent=self.root)
            else:
                messagebox.showwarning("⚠️ 部分失败",
                    f"成功 {ok}，失败 {fail}。", parent=self.root)

        self._transactional_cloud_upload(upload_fn, on_done,
                                          total_hint=n)

    def _cloud_upload_selected(self):
        """上传选中游戏的笔记（事务性）"""
        aids = self._get_selected_app_ids()
        if not aids:
            messagebox.showinfo("提示", "请先在列表中选择游戏。")
            return
        dirty_aids = [a for a in aids if self.manager.is_dirty(a)]
        if not dirty_aids:
            messagebox.showinfo("提示", "选中的游戏没有需要上传的改动。",
                                parent=self.root)
            return

        def upload_fn(manager, progress_cb):
            ok = fail = 0
            for aid in dirty_aids:
                if manager.cloud_upload(aid):
                    ok += 1
                else:
                    fail += 1
            return ok, fail

        def on_done(ok, fail, pending):
            if fail == 0 and pending == 0:
                messagebox.showinfo("✅ 成功",
                    f"已上传 {ok} 个游戏。", parent=self.root)
            elif fail == 0:
                messagebox.showinfo("☁️ 已写入",
                    f"已写入 {ok} 个游戏。\n"
                    f"{pending} 个仍在等待 Steam 同步。",
                    parent=self.root)
            else:
                messagebox.showwarning("⚠️",
                    f"成功 {ok}，失败 {fail}。", parent=self.root)

        self._transactional_cloud_upload(upload_fn, on_done,
                                          total_hint=len(dirty_aids))

    def _cloud_upload_single(self, app_id: str):
        """上传单个游戏的笔记（事务性）"""
        def upload_fn(manager, progress_cb):
            ok = 1 if manager.cloud_upload(app_id) else 0
            return ok, 1 - ok

        def on_done(ok, fail, pending):
            if fail > 0:
                messagebox.showerror("❌",
                    f"上传 AppID {app_id} 失败。", parent=self.root)

        self._transactional_cloud_upload(upload_fn, on_done, total_hint=1)

    # ────────────────────── 关闭前上传 ──────────────────────

    def _upload_and_close(self):
        """关闭程序前事务性上传所有脏笔记，完成后销毁窗口。"""
        def upload_fn(manager, progress_cb):
            return manager.cloud_upload_all_batch(
                progress_callback=progress_cb)

        def on_done(ok, fail, pending):
            if fail > 0:
                messagebox.showwarning("⚠️ 部分上传失败",
                    f"成功 {ok}，失败 {fail}。\n"
                    "失败的笔记仍保留在本地。",
                    parent=self.root)
            self._resolve_thread_running = False
            if getattr(self, '_app_detail_cache', None):
                self._persist_all_caches()
            self.root.destroy()

        n = self.manager.dirty_count() if self.manager else 0
        self._transactional_cloud_upload(upload_fn, on_done,
                                          total_hint=n, modal=True)

    # ────────────────────── 同步状态 ──────────────────────

    def _verify_sync_status(self) -> int:
        """检查 remotecache.vdf 中仍为 syncstate=3 的笔记数量"""
        try:
            syncstates = self._parse_remotecache_syncstates()
            return sum(1 for v in syncstates.values() if v == 3)
        except Exception:
            return 0

    # ────────────────────── 标记已同步 ──────────────────────

    def _mark_synced_selected(self):
        """将选中游戏标记为已同步"""
        aids = self._get_selected_app_ids()
        if not aids:
            messagebox.showinfo("提示", "请先在列表中选择游戏。")
            return
        dirty_aids = [a for a in aids if self.manager.is_dirty(a)]
        if not dirty_aids:
            messagebox.showinfo("提示", "选中的游戏没有需要同步的改动。",
                                parent=self.root)
            return
        if not messagebox.askyesno("确认标记为已同步",
                f"即将把 {len(dirty_aids)} 个游戏标记为已同步。\n\n"
                "这将消除改动标记，让程序认为本地版本即云版本。\n"
                "确认继续？", parent=self.root):
            return
        count = 0
        for aid in dirty_aids:
            if self.manager.mark_as_synced(aid):
                count += 1
        self._save_uploaded_hashes()
        self._refresh_games_list()
        messagebox.showinfo("✅ 完成",
            f"已将 {count} 个游戏标记为已同步。", parent=self.root)

    # ────────────────────── Steam 进程监控 ──────────────────────

    def _start_steam_monitor(self):
        """启动后台定时器，每 5 秒检测 Steam 是否在运行"""
        self._check_steam_alive()

    def _check_steam_alive(self):
        """定时检测 Steam 进程，若 CEF 已连接但 Steam 不在则自动断开。"""
        if self._cef_bridge and self._cef_bridge.is_connected():
            if not SteamCloudUploader.is_steam_running():
                try:
                    self._cef_bridge.disconnect()
                except Exception:
                    pass
                self._cef_bridge = None
                if self._collections_core:
                    self._collections_core.cef = None
                self._update_library_cloud_status()
        self._update_proxy_status()
        try:
            self._steam_monitor_id = self.root.after(
                5000, self._check_steam_alive)
        except Exception:
            pass  # root 已销毁
