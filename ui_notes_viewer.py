"""笔记查看/编辑/创建/删除窗口（NotesViewerMixin）

宿主协议：NotesViewerHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import NotesViewerHost  # noqa: F401

import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from datetime import datetime

from rich_text_editor import SteamRichTextEditor
from core_notes import is_ai_note, extract_ai_model_from_note, SteamNotesManager
from utils import sanitize_filename


class NotesViewerMixin:
    """笔记查看、编辑、创建、删除相关 UI 方法"""

    def _ui_view_selected(self):
        app_id = self._get_selected_app_id()
        if app_id:
            self._open_notes_viewer(app_id)
        else:
            messagebox.showinfo("提示", "请先在右侧列表中选择一个游戏。")

    def _copy_appid(self, app_id: str):
        """复制AppID到剪贴板（无弹窗）"""
        self._copy_appid_silent(app_id)

    # ────────────────────── 新建笔记 ──────────────────────

    def _ui_create_note(self):
        """新建笔记窗口 — 使用富文本编辑器"""
        win = tk.Toplevel(self.root)
        win.title("📝 新建 Steam 笔记")
        win.resizable(True, True)
        win.grab_set()

        tk.Label(win, text="📝 新建笔记", font=("", 13, "bold")).pack(pady=(15, 10))

        form = tk.Frame(win, padx=20)
        form.pack(fill=tk.X)

        # AppID
        tk.Label(form, text="游戏 AppID:", font=("", 10)).grid(
            row=0, column=0, sticky=tk.W, pady=5)
        app_id_var = tk.StringVar()
        sel_id = self._get_selected_app_id()
        if sel_id:
            app_id_var.set(sel_id)
        tk.Entry(form, textvariable=app_id_var, width=20, font=("", 10)).grid(
            row=0, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        tk.Label(form, text="(如 1245620)", font=("", 9), fg="#888").grid(
            row=0, column=2, sticky=tk.W, padx=5)

        # 标题
        tk.Label(form, text="笔记标题:", font=("", 10)).grid(
            row=1, column=0, sticky=tk.W, pady=5)
        title_var = tk.StringVar()
        tk.Entry(form, textvariable=title_var, width=40, font=("", 10)).grid(
            row=1, column=1, columnspan=2, sticky=tk.W, pady=5, padx=(10, 0))

        # 富文本编辑器
        tk.Label(win, text="笔记内容:", font=("", 10), padx=20).pack(anchor=tk.W)
        editor = SteamRichTextEditor(win, height=12)
        editor.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 5))

        def do_create():
            aid = app_id_var.get().strip()
            title = title_var.get().strip()
            content = editor.get_content()

            if not aid:
                messagebox.showwarning("提示", "请输入游戏 AppID。", parent=win)
                return
            if self.is_app_uploading(aid):
                messagebox.showwarning("☁️⬆ 上传中",
                    f"AppID {aid} 的笔记正在上传到 Steam Cloud，无法新建笔记。\n"
                    "请等待上传完成后再操作。", parent=win)
                return
            if not title:
                messagebox.showwarning("提示", "请输入笔记标题。", parent=win)
                return
            if not content.strip():
                messagebox.showwarning("提示", "请输入笔记内容。", parent=win)
                return

            try:
                self.manager.create_note(aid, title, content)
                messagebox.showinfo("✅ 成功",
                                    f"已为 AppID {aid} 创建笔记:\n「{title}」",
                                    parent=win)
                self._refresh_games_list()
                win.grab_release()
                win.destroy()
            except Exception as e:
                messagebox.showerror("❌ 错误", f"写入失败:\n{e}", parent=win)

        ttk.Button(win, text="✅ 创建笔记", command=do_create).pack(pady=(5, 15))
        self._center_window(win)

    # ────────────────────── 查看/编辑笔记 ──────────────────────

    def _ui_view_notes(self):
        app_id = simpledialog.askstring("查看笔记", "请输入游戏 AppID:",
                                        parent=self.root)
        if app_id and app_id.strip():
            self._open_notes_viewer(app_id.strip())

    def _open_notes_viewer(self, app_id: str, select_index: int = 0):
        """笔记浏览/编辑窗口 — 使用富文本编辑器"""
        data = self.manager.read_notes(app_id)
        notes = data.get("notes", [])

        win = tk.Toplevel(self.root)
        win.title(f"📋 AppID {app_id} 的笔记 ({len(notes)} 条)")
        win.resizable(True, True)

        if not notes:
            tk.Label(win, text=f"📭 AppID {app_id} 暂无笔记",
                     font=("", 12), fg="#888").pack(padx=40, pady=30)
            ttk.Button(win, text="📝 新建一条",
                       command=lambda: [win.destroy(), self._ui_create_note()]).pack(pady=10)
            self._center_window(win)
            return

        # 笔记列表 + 详情
        paned = tk.PanedWindow(win, orient=tk.HORIZONTAL, sashwidth=5)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左: 列表
        left_f = tk.Frame(paned)
        paned.add(left_f, width=250)

        tk.Label(left_f, text="笔记列表", font=("", 10, "bold")).pack(anchor=tk.W)
        note_listbox = tk.Listbox(left_f, width=30, height=15, font=("", 10))
        note_listbox.pack(fill=tk.BOTH, expand=True, pady=5)

        for i, n in enumerate(notes):
            ts = n.get("time_modified", 0)
            t_str = datetime.fromtimestamp(ts).strftime("%m/%d %H:%M") if ts else ""
            ai_mark = "🤖 " if is_ai_note(n) else ""
            note_listbox.insert(tk.END, f"[{i}] {ai_mark}{n.get('title', '(无标题)')[:40]}  {t_str}")
            if is_ai_note(n):
                note_listbox.itemconfig(i, fg="#1a73e8")

        # 检测是否正在上传中
        _is_uploading = self.is_app_uploading(app_id)

        # 上传中横幅警告
        if _is_uploading:
            warn_banner = tk.Frame(win, bg="#fff3cd", padx=10, pady=6)
            warn_banner.pack(fill=tk.X, padx=10, pady=(5, 0), before=paned)
            tk.Label(warn_banner,
                     text="☁️⬆ 该游戏的笔记正在上传到 Steam Cloud，所有编辑功能已锁定。"
                          "请等待上传完成后再修改。",
                     font=("", 10, "bold"), fg="#856404", bg="#fff3cd",
                     wraplength=500, justify=tk.LEFT).pack()

        # 右: 详情（使用富文本编辑器）
        right_f = tk.Frame(paned)
        paned.add(right_f, width=550)

        tk.Label(right_f, text="标题:", font=("", 10)).pack(anchor=tk.W)
        title_entry = tk.Entry(right_f, font=("", 11), width=50)
        title_entry.pack(fill=tk.X, pady=(0, 5))

        tk.Label(right_f, text="内容:", font=("", 10)).pack(anchor=tk.W)
        editor = SteamRichTextEditor(right_f, height=15)
        editor.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        ts_label = tk.Label(right_f, text="", font=("", 9), fg="#888")
        ts_label.pack(anchor=tk.W)

        ai_info_label = tk.Label(right_f, text="", font=("", 9), fg="#1a73e8")
        ai_info_label.pack(anchor=tk.W)

        # 用于跟踪原始文本显示状态
        _raw_mode = {'active': False}
        # 脏检测：快照 + 比较
        _clean = {'title': '', 'content': ''}

        def _is_dirty():
            if _is_uploading:
                return False
            return (title_entry.get().strip() != _clean['title']
                    or editor.get_content() != _clean['content'])

        def _check_dirty_before(action_name="切换"):
            """如果有未保存修改，询问用户。返回 True=可以继续，False=取消"""
            if not _is_dirty():
                return True
            ans = messagebox.askyesnocancel(
                "未保存的修改",
                f"当前笔记有未保存的修改。\n\n"
                f"「是」→ 保存后{action_name}\n"
                f"「否」→ 放弃修改\n"
                f"「取消」→ 返回编辑",
                parent=win)
            if ans is None:
                return False
            if ans:
                do_save()
            return True

        btn_frame = tk.Frame(right_f)
        btn_frame.pack(fill=tk.X, pady=5)

        # 第一行按钮
        btn_row1 = tk.Frame(btn_frame)
        btn_row1.pack(fill=tk.X)

        def do_save():
            idx = note_listbox.curselection()
            if not idx:
                messagebox.showwarning("提示", "请先选择一条笔记。", parent=win)
                return
            # 如果在原始文本模式，先退出
            if _raw_mode['active']:
                do_toggle_raw()
            i = idx[0]
            new_title = title_entry.get().strip()
            new_content = editor.get_content()
            if self.manager.update_note(app_id, i, new_title, new_content):
                notes[i]["title"] = new_title
                notes[i]["content"] = self.manager._wrap_content(new_content)
                notes[i]["time_modified"] = int(time.time())
                _clean['title'] = new_title
                _clean['content'] = new_content
                self._refresh_games_list()
                messagebox.showinfo("✅ 成功", "笔记已保存。\n在主界面点击 ☁️ 上传到 Steam Cloud。", parent=win)

        def do_delete():
            idx = note_listbox.curselection()
            if not idx:
                return
            i = idx[0]
            if messagebox.askyesno("确认", f"确定删除笔记 [{i}] ？", parent=win):
                result = self.manager.delete_note(app_id, i)
                if not result:
                    messagebox.showwarning("提示", "删除失败。", parent=win)
                    return
                # 就地刷新：从内存列表和 listbox 中移除
                notes.pop(i)
                self._refresh_games_list()
                if not notes:
                    win.destroy()
                    return
                note_listbox.delete(i)
                # 重新编号列表项
                for j in range(note_listbox.size()):
                    old_text = note_listbox.get(j)
                    note_listbox.delete(j)
                    note_listbox.insert(j, f"[{j}] {old_text.split('] ', 1)[-1]}")
                new_sel = min(i, len(notes) - 1)
                note_listbox.selection_set(new_sel)
                note_listbox.event_generate("<<ListboxSelect>>")
                win.title(f"📋 AppID {app_id} 的笔记 ({len(notes)} 条)")

        def do_export_single():
            """导出当前选中的单条笔记"""
            idx = note_listbox.curselection()
            if not idx:
                messagebox.showwarning("提示", "请先选择一条笔记。", parent=win)
                return
            i = idx[0]
            note = notes[i]
            title = note.get("title", "untitled")
            safe_name = sanitize_filename(title)
            path = filedialog.asksaveasfilename(
                title="导出笔记", defaultextension=".txt",
                initialdir=self._last_dir('note_export'),
                initialfile=f"{safe_name}.txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                parent=win
            )
            if path:
                self._save_dir('note_export', path)
                try:
                    self.manager.export_single_note(app_id, i, path)
                    messagebox.showinfo("✅ 成功", f"已导出笔记「{title}」到:\n{path}",
                                        parent=win)
                except Exception as e:
                    messagebox.showerror("❌ 错误", f"导出失败:\n{e}", parent=win)

        def do_toggle_raw():
            """就地切换原始文本/富文本显示"""
            idx = note_listbox.curselection()
            if not idx:
                messagebox.showwarning("提示", "请先选择一条笔记。", parent=win)
                return
            i = idx[0]
            if _raw_mode['active']:
                # 原始 → 富文本：重新渲染
                _raw_mode['active'] = False
                raw_toggle_btn.config(text="📄 原始文本")
                editor.set_content(notes[i].get("content", ""))
            else:
                # 富文本 → 原始：显示当前编辑器内容的 BBCode（保留未保存的编辑）
                _raw_mode['active'] = True
                raw_toggle_btn.config(text="👁️ 富文本")
                raw_content = editor.get_content()
                editor._text.config(state=tk.NORMAL)
                editor._text.delete("1.0", tk.END)
                editor._text.insert("1.0", raw_content)

        _save_btn = ttk.Button(btn_row1, text="💾 保存修改", command=do_save)
        _save_btn.pack(side=tk.LEFT, padx=5)
        _del_btn = ttk.Button(btn_row1, text="🗑️ 删除此条", command=do_delete)
        _del_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row1, text="📤 导出此条", command=do_export_single).pack(
            side=tk.LEFT, padx=5)
        raw_toggle_btn = ttk.Button(btn_row1, text="📄 原始文本",
                                      command=do_toggle_raw)
        raw_toggle_btn.pack(side=tk.LEFT, padx=5)

        # 第二行按钮
        btn_row2 = tk.Frame(btn_frame)
        btn_row2.pack(fill=tk.X, pady=(3, 0))

        def do_move(direction):
            idx = note_listbox.curselection()
            if not idx:
                return
            i = idx[0]
            new_i = i + direction
            if new_i < 0 or new_i >= len(notes):
                return
            result = self.manager.move_note(app_id, i, direction)
            if not result:
                # 移动失败，可能是试图移动受保护的同步触发笔记
                messagebox.showwarning("提示",
                    "无法移动该笔记。\n\n"
                    "第一条云同步触发笔记受到保护，不可移动，\n"
                    "也不允许将其他笔记移动到第一位。",
                    parent=win)
                return
            win.destroy()
            self._refresh_games_list()
            self._open_notes_viewer(app_id, select_index=new_i)

        _moveup_btn = ttk.Button(btn_row2, text="🔼 上移", command=lambda: do_move(-1))
        _moveup_btn.pack(side=tk.LEFT, padx=5)
        _movedown_btn = ttk.Button(btn_row2, text="🔽 下移", command=lambda: do_move(1))
        _movedown_btn.pack(side=tk.LEFT, padx=5)

        # 上传中时禁用所有编辑按钮
        if _is_uploading:
            _save_btn.config(state=tk.DISABLED)
            _del_btn.config(state=tk.DISABLED)
            _moveup_btn.config(state=tk.DISABLED)
            _movedown_btn.config(state=tk.DISABLED)
            title_entry.config(state='readonly')
            editor._text.config(state=tk.DISABLED)

        _prev_sel_idx = [None]  # 上一次选中的索引

        def on_select(event=None):
            idx = note_listbox.curselection()
            if not idx:
                return
            i = idx[0]
            # 切换前检查脏状态（跳过首次加载）
            if _prev_sel_idx[0] is not None and i != _prev_sel_idx[0]:
                if not _check_dirty_before("切换"):
                    # 用户取消 → 恢复之前的选择
                    note_listbox.selection_clear(0, tk.END)
                    note_listbox.selection_set(_prev_sel_idx[0])
                    return
            _prev_sel_idx[0] = i
            _raw_mode['active'] = False
            raw_toggle_btn.config(text="📄 原始文本")
            note = notes[i]
            title_entry.delete(0, tk.END)
            title_entry.insert(0, note.get("title", ""))
            editor.set_content(note.get("content", ""))
            # 设置干净快照（content 用 round-trip 后的值，避免 BBCode 归一化导致误报）
            _clean['title'] = note.get("title", "")
            _clean['content'] = editor.get_content()
            ts = note.get("time_modified", 0)
            if ts:
                ts_label.config(text=f"⏰ {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')}")
            if is_ai_note(note):
                model = extract_ai_model_from_note(note)
                ai_info_label.config(
                    text="🤖 AI 生成" + (f" (模型: {model})" if model else ""))
            else:
                ai_info_label.config(text="")

        note_listbox.bind("<<ListboxSelect>>", on_select)
        if notes:
            sel = min(select_index, len(notes) - 1)
            note_listbox.selection_set(sel)
            note_listbox.see(sel)
            note_listbox.event_generate("<<ListboxSelect>>")

        def _on_close():
            if _check_dirty_before("关闭"):
                win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        self._center_window(win)

    # ────────────────────── 删除笔记 ──────────────────────

    def _resolve_app_filtered_notes(self, app_id):
        """解析单个游戏在当前筛选下的笔记，返回 (filtered_notes, nid_list_or_None)"""
        all_notes = self.manager.read_notes(app_id).get("notes", [])
        if not all_notes:
            return [], None
        visible_ids = self._get_visible_note_ids(app_id)
        if visible_ids is not None:
            filtered = [n for n in all_notes if n.get("id") in visible_ids]
            return filtered, [n["id"] for n in filtered]
        return all_notes, None

    def _filter_uploading_apps(self, app_ids):
        """过滤掉上传中的游戏，弹出警告，返回可操作的 app_ids"""
        uploading = [a for a in app_ids if self.is_app_uploading(a)]
        if not uploading:
            return app_ids
        names = ", ".join(self._get_game_name(a) for a in uploading[:5])
        if len(uploading) > 5:
            names += f" 等 {len(uploading)} 个"
        messagebox.showwarning("☁️⬆ 上传中",
            f"以下游戏的笔记正在上传到 Steam Cloud，无法删除：\n{names}\n\n"
            "请等待上传完成后再操作。", parent=self.root)
        return [a for a in app_ids if a not in set(uploading)]

    def _do_delete_filtered(self, aid, nids):
        """执行删除：nids=None 删全部，否则按 ID 删"""
        if nids is not None:
            self.manager.delete_notes_by_ids(aid, nids)
        else:
            self.manager.delete_all_notes(aid)

    def _ui_delete_notes(self):
        app_ids = self._get_selected_app_ids()
        if app_ids:
            app_ids = self._filter_uploading_apps(app_ids)
            if not app_ids:
                return
        if not app_ids:
            app_id = simpledialog.askstring("删除笔记", "请输入游戏 AppID:",
                                            parent=self.root)
            if app_id and app_id.strip():
                app_ids = [app_id.strip()]
            else:
                return

        if len(app_ids) == 1:
            self._ui_delete_notes_single(app_ids[0])
        else:
            self._ui_delete_notes_batch(app_ids)

    def _ui_delete_notes_single(self, app_id):
        """单个游戏删除（尊重筛选）"""
        notes, nids = self._resolve_app_filtered_notes(app_id)
        if not notes:
            messagebox.showinfo("提示", f"AppID {app_id} 暂无匹配笔记。")
            return
        game_name = self._get_game_name(app_id)
        if messagebox.askyesno("确认删除",
                f"确定删除「{game_name}」的 {len(notes)} 条笔记？\n此操作不可撤销。"):
            self._do_delete_filtered(app_id, nids)
            messagebox.showinfo("✅ 成功",
                f"已删除「{game_name}」的 {len(notes)} 条笔记。")
            self._refresh_games_list()

    def _ui_delete_notes_batch(self, app_ids):
        """批量删除（尊重筛选）"""
        total_notes = 0
        valid = {}
        for aid in app_ids:
            notes, nids = self._resolve_app_filtered_notes(aid)
            if notes:
                valid[aid] = nids
                total_notes += len(notes)
        if not valid:
            messagebox.showinfo("提示", "选中的游戏均无笔记。")
            return
        if messagebox.askyesno("确认批量删除",
                f"确定删除 {len(valid)} 个游戏的共 {total_notes} 条笔记？\n"
                f"此操作不可撤销。"):
            for aid, nids in valid.items():
                self._do_delete_filtered(aid, nids)
            messagebox.showinfo("✅ 成功",
                f"已删除 {len(valid)} 个游戏的共 {total_notes} 条笔记。")
            self._refresh_games_list()

    # ────────────────────── API Key 设置 ──────────────────────
