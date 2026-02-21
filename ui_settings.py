"""API 配置、缓存管理、关于 等设置对话框（SettingsMixin）

宿主协议：SettingsHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import SettingsHost  # noqa: F401

import os
import platform
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk

from ai_generator import SteamAIGenerator, AI_SYSTEM_PROMPT
from ui_settings_ai import build_ai_settings_ui
from ui_settings_steam import (build_steam_data_settings_ui,
    build_cookie_manager_ui, build_igdb_credentials_ui)
from ui_settings_cache import build_cache_manager_ui
from ui_utils import ProgressWindow, bg_thread


class SettingsMixin:
    """API Key 设置、缓存管理、关于 等 UI 方法"""

    def _open_unified_settings(self):
        """统一设置入口 — 顶部蓝色栏 ⚙️ 设置 按钮"""
        if hasattr(self, '_settings_win') and self._settings_win and \
                self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("⚙️ 设置")
        win.resizable(False, False)

        # 标题栏已有"⚙️ 设置"，不再重复

        frame = tk.Frame(win, padx=20)
        frame.pack(fill=tk.X)

        # ── 连接管理（动态状态） ──
        def _cef_desc():
            if self._cef_bridge and self._cef_bridge.is_connected():
                return "🟢 已连接 — 点击断开"
            return "⚪ 未连接 — 点击连接"

        cef_row = tk.Frame(frame)
        cef_row.pack(fill=tk.X, pady=3)
        cef_desc_label = tk.Label(cef_row, text=_cef_desc(),
                                  font=("", 9), fg="#666")

        def _do_cef_toggle():
            self._lib_toggle_cef()
            cef_desc_label.config(text=_cef_desc())

        ttk.Button(cef_row, text="🔌 CEF", width=12,
                   command=_do_cef_toggle).pack(side=tk.LEFT)
        cef_desc_label.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # ── 其他设置项 ──
        row = tk.Frame(frame)
        row.pack(fill=tk.X, pady=3)
        ttk.Button(row, text="🔑 AI 配置", width=12,
                   command=self._ui_api_key_settings).pack(side=tk.LEFT)
        tk.Label(row, text="管理 AI 令牌、模型、高级参数",
                 font=("", 9), fg="#666").pack(side=tk.LEFT, padx=(8, 0))

        # ── Steam 数据源（弹出菜单） ──
        steam_row = tk.Frame(frame)
        steam_row.pack(fill=tk.X, pady=3)
        self._steam_data_btn = ttk.Button(
            steam_row, text="🎮 Steam 数据源", width=12,
            command=self._show_steam_data_menu)
        self._steam_data_btn.pack(side=tk.LEFT)
        tk.Label(steam_row, text="Steam API Key、Cookie、IGDB 凭证",
                 font=("", 9), fg="#666").pack(side=tk.LEFT, padx=(8, 0))

        rest_items = [
            ("🗑️ 缓存管理", "本地缓存数据管理",
             self._ui_manage_cache),
            ("💾 管理备份", "收藏夹备份创建与恢复",
             self.open_backup_manager_ui),
        ]
        for text, desc, cmd in rest_items:
            row = tk.Frame(frame)
            row.pack(fill=tk.X, pady=3)
            ttk.Button(row, text=text, width=12, command=cmd).pack(side=tk.LEFT)
            tk.Label(row, text=desc, font=("", 9), fg="#666").pack(
                side=tk.LEFT, padx=(8, 0))

        # ── 维护工具（弹出菜单） ──
        maint_row = tk.Frame(frame)
        maint_row.pack(fill=tk.X, pady=3)
        self._maint_btn = ttk.Button(
            maint_row, text="🔧 维护工具", width=12,
            command=self._show_maintenance_menu)
        self._maint_btn.pack(side=tk.LEFT)
        tk.Label(maint_row, text="笔记去重、日期补充、DLC 清理等",
                 font=("", 9), fg="#666").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        upd_row = tk.Frame(frame)
        upd_row.pack(fill=tk.X, pady=3)
        ttk.Button(upd_row, text="🔔 检查更新", width=12,
                   command=lambda: self._check_update_bg(manual=True, parent=win)).pack(side=tk.LEFT)
        import updater
        tk.Label(upd_row, text=f"当前版本: v{updater.__version__}",
                 font=("", 9), fg="#666").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(10, 15))
        self._center_window(win)

    def _show_maintenance_menu(self):
        """弹出维护工具菜单（上拉式，与 Steam 数据源一致）"""
        menu = tk.Menu(self._settings_win, tearoff=0)
        menu.add_command(label="🔍 笔记去重",
                         command=self._ui_dedup_notes)
        menu.add_command(label="📅 补充 AI 生成日期",
                         command=self._ui_backfill_ai_dates)
        menu.add_command(label="🏷️ 清除分类名前缀",
                         command=self._ui_strip_collection_prefixes)
        menu.add_command(label="🧹 清理分类中的 DLC",
                         command=self._cleanup_dlc_from_collections)
        menu.add_separator()
        menu.add_command(label="✅ 标记选中笔记为已同步（慎用）",
                         command=self._mark_synced_selected)

        btn = self._maint_btn
        x = btn.winfo_rootx()
        y = btn.winfo_rooty()
        menu_h = menu.yposition("end") + 30
        self._settings_win.after(1, lambda: menu.tk_popup(x, y - menu_h))

    def _ui_strip_collection_prefixes(self):
        """清除所有收藏夹名称的前导空格/NBSP

        策略：先批量改名 + WriteLocalStorage 持久化（瞬间），
        再分批 SaveCollection 触发云同步（每批5个，间隔500ms）。
        """
        if not self._cef_bridge or not self._cef_bridge.is_connected():
            messagebox.showwarning("需要 CEF",
                "此功能需要连接 CEF（Steam 以调试模式运行）。",
                parent=self.root)
            return

        # 第一步：批量改名（内存中，瞬间完成）
        result = self._cef_bridge._eval_js(r'''
(function() {
    var uc = collectionStore.userCollections;
    if (!Array.isArray(uc)) return {error: "not array"};
    var changed = [];
    for (var i = 0; i < uc.length; i++) {
        var old = uc[i].m_strName;
        var stripped = old.replace(/^[\s\u00A0]+/, '');
        if (stripped !== old) {
            uc[i].m_strName = stripped;
            changed.push(uc[i].m_strId);
        }
    }
    return {fixed: changed.length, total: uc.length, ids: changed};
})()
''', timeout=15)

        if not isinstance(result, dict) or result.get('error'):
            messagebox.showerror("失败", str(result), parent=self.root)
            return

        fixed = result.get('fixed', 0)
        if fixed == 0:
            messagebox.showinfo("提示", "所有收藏夹名称均无前缀，无需清除。",
                                parent=self.root)
            return

        changed_ids = result.get('ids', [])

        # 第二步：分批 SaveCollection 触发云同步
        self._batch_save_collections(changed_ids,
            title=f"清除 {fixed} 个收藏夹前缀",
            on_done=lambda ok, fail: self._on_strip_done(ok, fail, fixed)
        )

    def _on_strip_done(self, ok, fail, fixed):
        """清除前缀完成回调"""
        if fail == 0:
            messagebox.showinfo("完成",
                f"已清除 {fixed} 个收藏夹的名称前缀，云同步完成。",
                parent=self.root)
        else:
            messagebox.showwarning("部分完成",
                f"已清除 {fixed} 个前缀（本地）。\n"
                f"云同步：成功 {ok}，失败 {fail}。\n"
                f"失败的部分会由 Steam 后台自动同步。",
                parent=self.root)
        try:
            self._lib_load_collections()
        except Exception:
            pass

    def _batch_save_collections(self, col_ids, title="云同步",
                                 batch_size=10, on_done=None):
        """分批调用 SaveCollection 触发云同步（带进度窗口）

        每批 batch_size 个，间隔 500ms，避免超时。
        """
        if not col_ids:
            if on_done:
                on_done(0, 0)
            return

        import threading

        pw = ProgressWindow(self.root, f"☁️ {title}",
            f"☁️ 正在同步 {len(col_ids)} 个收藏夹到云端...",
            maximum=len(col_ids))
        self._center_window(pw.win)

        def sync_thread():
            import json as _json
            ok_count = 0
            fail_count = 0
            total = len(col_ids)

            for i in range(0, total, batch_size):
                batch = col_ids[i:i + batch_size]
                batch_json = _json.dumps(batch)

                pw.update(min(i + len(batch), total),
                          f"{min(i + len(batch), total)}/{total}")

                if not self._cef_bridge or not self._cef_bridge.is_connected():
                    fail_count += len(batch)
                    continue

                ret = self._cef_bridge._eval_js(f'''
(async function() {{
    var ids = {batch_json};
    var results = await Promise.all(ids.map(function(id) {{
        var col = collectionStore.GetCollection(id);
        if (!col) return {{ok: false}};
        return collectionStore.SaveCollection(col)
            .then(function() {{ return {{ok: true}}; }})
            .catch(function() {{ return {{ok: false}}; }});
    }}));
    var ok = 0, fail = 0;
    for (var i = 0; i < results.length; i++) {{
        if (results[i] && results[i].ok) ok++; else fail++;
    }}
    return {{ok: ok, fail: fail}};
}})()
''', timeout=30)

                if isinstance(ret, dict):
                    ok_count += ret.get('ok', 0)
                    fail_count += ret.get('fail', 0)
                else:
                    fail_count += len(batch)

                if i + batch_size < total:
                    import time
                    time.sleep(0.3)

            def finish():
                pw.close()
                if on_done:
                    on_done(ok_count, fail_count)
            try:
                self.root.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=bg_thread(sync_thread), daemon=True).start()

    def _show_steam_data_menu(self):
        """弹出 Steam 数据源配置菜单（带状态指示）"""
        has_key = bool(self._config.get("steam_web_api_key", ""))
        has_cookie = bool(
            self._collections_core and
            self._collections_core.get_saved_cookie())
        has_igdb = bool(self._config.get("igdb_client_id", ""))

        s1 = "✅" if has_key else "⚠️"
        s2 = "✅" if has_cookie else "💡"
        s3 = "✅" if has_igdb else "⚠️"

        menu = tk.Menu(self._settings_win, tearoff=0)
        menu.add_command(label=f"{s1} Steam API Key",
                         command=lambda: build_steam_data_settings_ui(self))
        menu.add_command(label=f"{s2} Cookie",
                         command=lambda: build_cookie_manager_ui(self))
        menu.add_command(label=f"{s3} IGDB 凭证",
                         command=lambda: build_igdb_credentials_ui(self))

        btn = self._steam_data_btn
        x = btn.winfo_rootx()
        y = btn.winfo_rooty()
        menu_h = menu.yposition("end") + 30
        # 延迟弹出，让按钮先完成鼠标释放动画
        self._settings_win.after(1, lambda: menu.tk_popup(x, y - menu_h))

    def _ui_steam_data_settings(self):
        """Steam 数据源配置：Steam Web API Key、Cookie、IGDB 凭证"""
        build_steam_data_settings_ui(self)

    def _ui_api_key_settings(self):
        """API Key 与 AI 配置管理窗口 — 支持多令牌管理"""
        build_ai_settings_ui(self)

    def _ui_manage_cache(self):
        """弹出本地缓存数据管理窗口"""
        build_cache_manager_ui(self)

    def _open_directory(self, path):
        """跨平台打开目录"""
        try:
            if platform.system() == "Darwin":
                os.system(f'open "{path}"')
            elif platform.system() == "Windows":
                os.startfile(path)
            else:
                os.system(f'xdg-open "{path}"')
        except Exception:
            pass

    def _ui_show_about(self):
        """弹出关于作者窗口"""
        import updater
        about = tk.Toplevel(self.root)
        about.title("关于")
        about.resizable(False, False)

        tk.Label(about, text=f"SteamShelf v{updater.__version__}",
                 font=("", 12, "bold")).pack(padx=20, pady=(15, 8))

        info_frame = tk.Frame(about)
        info_frame.pack(padx=20, pady=(0, 5))

        tk.Label(info_frame, text="作者: ", font=("", 10),
                 anchor=tk.E).grid(row=0, column=0, sticky=tk.E)
        author_link = tk.Label(info_frame, text="dtq1997", font=("", 10, "underline"),
                               fg="#1a73e8", cursor="hand2")
        author_link.grid(row=0, column=1, sticky=tk.W)
        author_link.bind("<Button-1>",
                         lambda e: webbrowser.open("https://steamcommunity.com/id/dtq1997/"))

        tk.Label(info_frame, text="邮箱: ", font=("", 10),
                 anchor=tk.E).grid(row=1, column=0, sticky=tk.E)
        tk.Label(info_frame, text="919130201@qq.com", font=("", 10),
                 fg="#555").grid(row=1, column=1, sticky=tk.W)

        tk.Label(info_frame, text="", font=("", 10),
                 anchor=tk.E).grid(row=2, column=0, sticky=tk.E)
        tk.Label(info_frame, text="dtq1997@pku.edu.cn", font=("", 10),
                 fg="#555").grid(row=2, column=1, sticky=tk.W)

        motto_label = tk.Label(about, text="「总有一天人人都会控大喷菇的」",
                               font=("", 10), fg="#5599cc", cursor="hand2")
        motto_label.pack(pady=(5, 3))
        motto_label.bind("<Button-1>",
                         lambda e: webbrowser.open("https://aweidao1.com/t/986949"))

        ttk.Button(about, text="是的", command=about.destroy).pack(pady=(5, 15))
        self._center_window(about)

    def _ui_open_dir(self):
        d = self.current_account['notes_dir']
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        self._open_folder(d)

    def _open_config_dir(self):
        """打开配置文件所在目录"""
        d = self._CONFIG_DIR
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        self._open_folder(d)

    # ────────────────────── Cookie / IGDB 凭证管理（来自软件 A） ──────────────────────

    def open_cookie_manager_ui(self):
        """打开全局 Cookie 管理界面"""
        build_cookie_manager_ui(self)

    def open_igdb_credentials_ui(self):
        """打开 IGDB API 凭证管理界面"""
        build_igdb_credentials_ui(self)

    @staticmethod
    def _open_folder(d):
        """跨平台打开文件夹"""
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(d)
            elif system == "Darwin":
                os.system(f'open "{d}"')
            else:
                os.system(f'xdg-open "{d}" 2>/dev/null || open "{d}" 2>/dev/null')
        except Exception:
            messagebox.showinfo("目录路径", f"路径:\n{d}")
