"""导入/导出/去重对话框（ImportExportMixin）

宿主协议：ImportExportHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import ImportExportHost  # noqa: F401

import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime

from core_notes import (
    is_ai_note, SteamNotesManager,
    extract_ai_confidence_from_note, extract_ai_info_volume_from_note,
    extract_ai_info_source_from_note, extract_ai_quality_from_note,
    is_insufficient_info_note,
    CONFIDENCE_EMOJI, QUALITY_EMOJI, INFO_VOLUME_EMOJI,
)

_AI_PREFIX_RE = re.compile(
    r'🤖AI:\s*(?:⛔信息过少\s*)?'
    r'(?:(?:📡联网检索|📚训练数据与Steam评测)\s*\|\s*)?'
    r'(?:相关信息量[：:]\s*(?:相当多|较多|中等|较少|相当少)[🟢🔵🟡🟠🔴]?\s*(?:\|\s*)?)?'
    r'(?:游戏总体质量[：:]\s*(?:相当好|较好|中等|较差|相当差)[💎✨➖👎💀]?\s*)?'
    r'(?:⚠️\s*)?'
    r'(?:以下内容由.+?确信程度[：:]\s*(?:很高|较高|中等|较低|很低)[🟢🔵🟡🟠🔴]?[。.]\s*)?'
)
_BBCODE_RE = re.compile(r'\[/?[a-z0-9*]+(?:=[^\]]*)?\]')
_SECTION_RE = re.compile(r'\s*(⚔️|⚠️|📅|📌)')


def _render_ai_notes(txt, notes):
    """将 AI 笔记列表渲染到 Text 控件（与 AI 预览窗口相同风格）"""
    txt.config(state=tk.NORMAL)
    txt.delete("1.0", tk.END)
    for i, note in enumerate(notes):
        if i > 0:
            txt.insert(tk.END, "\n" + "─" * 50 + "\n\n")
        content = note.get("content", note.get("title", ""))
        tag = f"n{i}"
        # 元数据头
        is_insuf = is_insufficient_info_note(note)
        conf = extract_ai_confidence_from_note(note)
        src = extract_ai_info_source_from_note(note)
        vol = extract_ai_info_volume_from_note(note)
        qual = extract_ai_quality_from_note(note)
        emoji = CONFIDENCE_EMOJI.get(conf, "🤖")
        header = "⛔ 信息过少" if is_insuf else f"{emoji} AI 笔记"
        if conf:
            header += f"（确信度: {conf}）"
        txt.insert(tk.END, header, f"{tag}_h")
        meta = []
        if src == "web":
            meta.append("📡联网")
        elif src == "local":
            meta.append("📚本地")
        if vol:
            meta.append(f"信息量:{vol}{INFO_VOLUME_EMOJI.get(vol, '')}")
        if qual:
            meta.append(f"质量:{qual}{QUALITY_EMOJI.get(qual, '')}")
        if meta:
            txt.insert(tk.END, f" [{' | '.join(meta)}]", f"{tag}_m")
            txt.tag_config(f"{tag}_m", foreground="#888", font=("", 9))
        txt.insert(tk.END, "\n")
        txt.tag_config(f"{tag}_h",
                       foreground="#cc3333" if is_insuf else "#1a73e8",
                       font=("", 10, "bold"))
        # 正文
        body = _BBCODE_RE.sub('', content).strip()
        m = _AI_PREFIX_RE.match(body)
        if m and m.end() > 0:
            body = body[m.end():]
        body = _SECTION_RE.sub('\n\n\u3000\u3000\\1', body)
        body = '\u3000\u3000' + body.strip()
        txt.insert(tk.END, body + "\n")
    txt.config(state=tk.DISABLED)


class ImportExportMixin:
    """导入、导出、去重相关 UI 方法"""

    def _ui_export_dialog(self):
        """导出对话框：选择两种导出模式（支持笔记级选择）"""
        sel = self._games_tree.selection()
        if not sel:
            messagebox.showinfo("提示",
                "请先在列表中选择要导出的游戏。\n"
                "💡 可点击「✅ 全选」一键选中全部，或 Ctrl+点击多选。\n"
                "💡 可通过筛选器选择 AI/非AI 笔记后再全选导出。",
                parent=self.root)
            return

        # 分析选择：游戏行 → 全部笔记；笔记子节点 → 仅选中的笔记
        partial_note_ids = {}  # {app_id: set(note_ids) | None}  None=全部
        for iid in sel:
            if "::n::" in iid:
                aid, nid = iid.split("::n::")
                if aid not in partial_note_ids or partial_note_ids[aid] is not None:
                    partial_note_ids.setdefault(aid, set()).add(nid)
            else:
                # 游戏行：使用当前筛选条件决定导出哪些笔记
                aid = self._iid_to_app_id(iid)
                visible = self._get_visible_note_ids(aid)
                partial_note_ids[aid] = visible

        aids = list(partial_note_ids.keys())
        if not aids:
            messagebox.showinfo("提示", "请先在列表中选择要导出的游戏或笔记。",
                                parent=self.root)
            return

        # 构建 note_filter
        allowed_note_ids = set()
        for aid, nids in partial_note_ids.items():
            for n in self.manager.read_notes(aid).get("notes", []):
                if nids is None or n.get("id") in nids:
                    allowed_note_ids.add(n["id"])
        total_notes = len(allowed_note_ids)
        note_filter = lambda n, _ids=allowed_note_ids: n.get("id") in _ids

        if total_notes == 0:
            messagebox.showinfo("提示", "选中的游戏没有笔记可导出。", parent=self.root)
            return

        win = tk.Toplevel(self.root)
        win.title("📤 导出笔记")
        win.resizable(False, False)
        win.grab_set()
        win.transient(self.root)

        tk.Label(win, text="📤 导出笔记", font=("", 13, "bold")).pack(pady=(15, 5))

        summary = f"已选中 {total_notes} 条笔记（来自 {len(aids)} 个游戏）"
        tk.Label(win, text=summary, font=("", 10), fg="#666").pack(pady=(0, 10))

        mode_frame = tk.LabelFrame(win, text="选择导出模式", font=("", 10),
                                    padx=15, pady=10)
        mode_frame.pack(fill=tk.X, padx=20, pady=5)

        mode_var = tk.IntVar(value=2)

        # 模式一：逐条导出
        tk.Radiobutton(mode_frame,
                       text="📄 逐条导出为多个文件",
                       variable=mode_var, value=1, font=("", 10)).pack(anchor=tk.W)
        tk.Label(mode_frame,
                 text="每条笔记保存为独立 .txt 文件（文件名=笔记标题，内容=BBCode）",
                 font=("", 9), fg="#888").pack(anchor=tk.W, padx=25, pady=(0, 5))

        ttk.Separator(mode_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 模式二：合并导出
        tk.Radiobutton(mode_frame,
                       text="📦 合并导出为单个文件（可跨账号导入）",
                       variable=mode_var, value=2, font=("", 10)).pack(anchor=tk.W)
        tk.Label(mode_frame,
                 text="所有笔记写入一个结构化 .txt 文件，可在其他账号上直接导入还原",
                 font=("", 9), fg="#888").pack(anchor=tk.W, padx=25, pady=(0, 5))

        def do_export():
            if mode_var.get() == 1:
                output_dir = filedialog.askdirectory(
                    title="选择保存目录（每条笔记一个文件）",
                    initialdir=self._last_dir('note_export'),
                    parent=win)
                if not output_dir:
                    return
                self._save_dir('note_export', output_dir)
                try:
                    n_files, n_notes = self.manager.export_individual_files(
                        aids, output_dir, note_filter=note_filter)
                    messagebox.showinfo("✅ 成功",
                        f"已导出 {n_files} 个文件到:\n{output_dir}",
                        parent=win)
                    win.grab_release()
                    win.destroy()
                except Exception as e:
                    messagebox.showerror("❌ 错误", f"导出失败:\n{e}", parent=win)
            else:
                path = filedialog.asksaveasfilename(
                    title="保存合并导出文件", defaultextension=".txt",
                    initialdir=self._last_dir('note_export'),
                    initialfile=f"steam_notes_batch_{datetime.now().strftime('%Y%m%d')}.txt",
                    filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                    parent=win)
                if not path:
                    return
                self._save_dir('note_export', path)
                try:
                    self.manager.export_batch(aids, path, note_filter=note_filter)
                    messagebox.showinfo("✅ 成功",
                        f"已导出 {len(aids)} 个游戏的 {total_notes} 条笔记到:\n{path}",
                        parent=win)
                    win.grab_release()
                    win.destroy()
                except Exception as e:
                    messagebox.showerror("❌ 错误", f"导出失败:\n{e}", parent=win)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(10, 15))
        def _close_export():
            win.grab_release()
            win.destroy()

        ttk.Button(btn_frame, text="📤 确认导出",
                   command=do_export).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消",
                   command=_close_export).pack(side=tk.LEFT, padx=5)
        win.protocol("WM_DELETE_WINDOW", _close_export)
        self._center_window(win)

    def _ui_dedup_notes(self):
        """笔记去重功能：扫描所有笔记中的重复项，供用户选择删除"""
        duplicates = self.manager.find_duplicate_notes()

        win = tk.Toplevel(self.root)
        win.title("🔍 笔记去重")
        win.resizable(True, True)
        win.grab_set()
        win.transient(self.root)

        tk.Label(win, text="🔍 笔记去重", font=("", 13, "bold")).pack(pady=(15, 5))

        if not duplicates:
            tk.Label(win, text="✅ 没有发现重复的笔记！",
                     font=("", 11), fg="#2a7f2a").pack(padx=40, pady=20)
            def _close_dedup():
                win.grab_release()
                win.destroy()
            ttk.Button(win, text="关闭", command=_close_dedup).pack(pady=(0, 15))
            win.protocol("WM_DELETE_WINDOW", _close_dedup)
            self._center_window(win)
            return

        total_dup_notes = sum(d['count'] - 1 for d in duplicates)
        total_groups = len(duplicates)
        tk.Label(win,
                 text=f"发现 {total_groups} 组重复笔记，"
                      f"共 {total_dup_notes} 条可删除的副本",
                 font=("", 10), fg="#c0392b").pack(pady=(0, 10))

        # 重复列表
        list_frame = tk.Frame(win)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        columns = ("game", "title_preview", "copies")
        tree = ttk.Treeview(list_frame, columns=columns, show="headings",
                             height=15, selectmode="extended")
        tree.heading("game", text="游戏")
        tree.heading("title_preview", text="笔记标题 (前50字)")
        tree.heading("copies", text="副本数")
        tree.column("game", width=200, minwidth=100)
        tree.column("title_preview", width=350, minwidth=150)
        tree.column("copies", width=70, minwidth=50, anchor=tk.CENTER)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.config(yscrollcommand=tree_scroll.set)

        # 存储数据映射
        dup_map = {}  # {iid: dup_entry}
        for i, d in enumerate(duplicates):
            game_name = self._get_game_name(d['app_id'])
            title_preview = d['title'][:50] + ("..." if len(d['title']) > 50 else "")
            iid = f"dup_{i}"
            tree.insert("", tk.END, iid=iid,
                        values=(game_name, title_preview, d['count']))
            dup_map[iid] = d

        # 预览区
        preview_frame = tk.LabelFrame(win, text="选中笔记预览", font=("", 10),
                                       padx=10, pady=5)
        preview_frame.pack(fill=tk.X, padx=15, pady=5)
        preview_text = tk.Text(preview_frame, height=4, font=("", 9), wrap=tk.WORD,
                                state=tk.DISABLED)
        preview_text.pack(fill=tk.X)

        def _on_select(event):
            sel = tree.selection()
            if not sel:
                return
            d = dup_map.get(sel[0])
            if not d:
                return
            preview_text.config(state=tk.NORMAL)
            preview_text.delete("1.0", tk.END)
            game_name = self._get_game_name(d['app_id'])
            preview_text.insert(tk.END, f"🎮 {game_name} (AppID: {d['app_id']})\n")
            preview_text.insert(tk.END, f"📝 标题: {d['title'][:100]}\n")
            preview_text.insert(tk.END, f"🔢 总副本数: {d['count']} (可删除 {d['count'] - 1} 条)\n")
            preview_text.insert(tk.END, f"📄 索引位置: {d['indices']}")
            preview_text.config(state=tk.DISABLED)
        tree.bind("<<TreeviewSelect>>", _on_select)

        # 按钮区
        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(10, 15))

        def _delete_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("提示", "请先选择要去重的条目。", parent=win)
                return
            total_to_remove = 0
            for iid in sel:
                d = dup_map.get(iid)
                if d:
                    total_to_remove += d['count'] - 1
            if not messagebox.askyesno("确认删除",
                    f"将为选中的 {len(sel)} 组重复笔记各保留 1 条，\n"
                    f"删除 {total_to_remove} 条副本。\n\n确定继续？",
                    parent=win):
                return
            removed_total = 0
            for iid in sel:
                d = dup_map.get(iid)
                if d:
                    # 保留第一条（索引最小），删除其余
                    indices_to_remove = d['indices'][1:]
                    removed = self.manager.delete_duplicate_notes(
                        d['app_id'], indices_to_remove)
                    removed_total += removed
            messagebox.showinfo("✅ 完成",
                f"已删除 {removed_total} 条重复笔记。", parent=win)
            self._refresh_games_list()
            win.grab_release()
            win.destroy()

        def _delete_all():
            total_to_remove = sum(d['count'] - 1 for d in duplicates)
            if not messagebox.askyesno("确认删除全部重复",
                    f"将为所有 {total_groups} 组重复笔记各保留 1 条，\n"
                    f"共删除 {total_to_remove} 条副本。\n\n确定继续？",
                    parent=win):
                return
            removed_total = 0
            for d in duplicates:
                indices_to_remove = d['indices'][1:]
                removed = self.manager.delete_duplicate_notes(
                    d['app_id'], indices_to_remove)
                removed_total += removed
            messagebox.showinfo("✅ 完成",
                f"已删除 {removed_total} 条重复笔记。", parent=win)
            self._refresh_games_list()
            win.grab_release()
            win.destroy()

        def _close_dedup():
            win.grab_release()
            win.destroy()

        ttk.Button(btn_frame, text="🗑️ 删除选中组的副本",
                   command=_delete_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="🗑️ 全部去重",
                   command=_delete_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="取消",
                   command=_close_dedup).pack(side=tk.LEFT, padx=(15, 4))
        win.protocol("WM_DELETE_WINDOW", _close_dedup)
        self._center_window(win)

    def _ui_import(self):
        """导入笔记窗口 — 支持单条导入和批量导入"""
        path = filedialog.askopenfilename(
            title="选择要导入的文件",
            initialdir=self._last_dir('note_import'),
            filetypes=[("文本文件", "*.txt"), ("Markdown", "*.md"), ("所有文件", "*.*")]
        )
        if not path:
            return
        self._save_dir('note_import', path)

        # 检测文件是否为批量导出格式
        is_batch_format = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line == SteamNotesManager.BATCH_EXPORT_HEADER:
                    is_batch_format = True
        except Exception:
            pass

        win = tk.Toplevel(self.root)
        win.title("📥 导入笔记")
        win.resizable(False, False)
        win.grab_set()

        fname = os.path.splitext(os.path.basename(path))[0]
        tk.Label(win, text=f"文件: {os.path.basename(path)}", font=("", 9),
                 fg="#555").pack(pady=(10, 10))

        # 导入模式
        mode_var = tk.IntVar(value=2 if is_batch_format else 1)

        mode_frame = tk.LabelFrame(win, text="导入模式", font=("", 10), padx=15, pady=10)
        mode_frame.pack(fill=tk.X, padx=20, pady=10)

        # 模式一: 单条导入
        mode1_frame = tk.Frame(mode_frame)
        mode1_frame.pack(fill=tk.X, anchor=tk.W)
        tk.Radiobutton(mode1_frame, text="单条导入：整个文件作为一条笔记导入",
                       variable=mode_var, value=1, font=("", 10)).pack(anchor=tk.W)

        # 模式一的 AppID 和标题
        single_form = tk.Frame(mode_frame, padx=25)
        single_form.pack(fill=tk.X, pady=(0, 5))

        tk.Label(single_form, text="目标 AppID:", font=("", 9)).grid(
            row=0, column=0, sticky=tk.W, pady=2)
        single_app_id_var = tk.StringVar()
        sel_id = self._get_selected_app_id()
        if sel_id:
            single_app_id_var.set(sel_id)
        tk.Entry(single_form, textvariable=single_app_id_var, width=15,
                 font=("", 9)).grid(row=0, column=1, sticky=tk.W, padx=(10, 0), pady=2)

        tk.Label(single_form, text="笔记标题:", font=("", 9)).grid(
            row=1, column=0, sticky=tk.W, pady=2)
        single_title_var = tk.StringVar(value=fname)
        tk.Entry(single_form, textvariable=single_title_var, width=30,
                 font=("", 9)).grid(row=1, column=1, sticky=tk.W, padx=(10, 0), pady=2)

        ttk.Separator(mode_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # 模式二: 批量导入
        mode2_text = "批量导入：按导出格式解析，自动分配到各 AppID"
        if is_batch_format:
            mode2_text += "  ✅ 已检测到批量导出格式"
        tk.Radiobutton(mode_frame, text=mode2_text,
                       variable=mode_var, value=2, font=("", 10)).pack(anchor=tk.W)

        tk.Label(mode_frame, text="（无需指定 AppID，文件中已包含 AppID 信息）",
                 font=("", 9), fg="#888").pack(anchor=tk.W, padx=25)

        def do_import():
            try:
                if mode_var.get() == 2:
                    # 批量导入：去重 → AI 冲突检测
                    parsed = SteamNotesManager.parse_batch_file(path)
                    if not parsed:
                        messagebox.showwarning("提示",
                            "未在文件中识别到有效笔记。\n"
                            "如果这不是批量导出格式文件，请切换到单条导入。",
                            parent=win)
                        return

                    # 第一步：字面去重
                    total_skipped = 0
                    filtered = {}
                    for aid, entries in parsed.items():
                        existing_set = set()
                        for n in self.manager.read_notes(aid).get("notes", []):
                            existing_set.add(
                                (n.get("title", ""), n.get("content", "")))
                        kept = []
                        for e in entries:
                            if (e["title"], e["content"]) in existing_set:
                                total_skipped += 1
                            else:
                                kept.append(e)
                        if kept:
                            filtered[aid] = kept

                    if total_skipped > 0 and not filtered:
                        messagebox.showinfo("ℹ️ 全部重复",
                            f"导入文件中的所有 {total_skipped} 条笔记"
                            f"与已有笔记完全重复，已全部跳过。",
                            parent=win)
                        return

                    if total_skipped > 0:
                        remaining = sum(len(v) for v in filtered.values())
                        messagebox.showinfo("ℹ️ 已跳过重复",
                            f"已自动跳过 {total_skipped} 条完全重复的笔记，"
                            f"剩余 {remaining} 条将继续导入。",
                            parent=win)

                    # 第二步：询问是否过滤未入库游戏的笔记
                    owned = self._get_owned_app_ids_set()
                    if owned is not None and filtered:
                        unowned_aids = {a for a in filtered if int(a) not in owned}
                        if unowned_aids:
                            n_unowned = len(unowned_aids)
                            n_notes = sum(len(filtered[a]) for a in unowned_aids)
                            r = messagebox.askyesnocancel(
                                "📥 筛选已入库游戏",
                                f"待导入笔记涉及 {len(filtered)} 个游戏，"
                                f"其中 {n_unowned} 个不在你的库中"
                                f"（共 {n_notes} 条笔记）。\n\n"
                                "是否只导入已入库游戏的笔记？\n\n"
                                "是 → 只导入已入库游戏的笔记\n"
                                "否 → 全部导入（未入库游戏的笔记会保留，"
                                "日后入库时自动显示）\n"
                                "取消 → 取消导入",
                                parent=win)
                            if r is None:
                                return
                            if r:
                                for a in unowned_aids:
                                    del filtered[a]
                                if not filtered:
                                    messagebox.showinfo("提示",
                                        "过滤后没有需要导入的笔记。",
                                        parent=win)
                                    return

                    # 第三步：AI 笔记冲突检测
                    conflicts = {}
                    for aid, entries in filtered.items():
                        incoming_ai = [e for e in entries
                                       if is_ai_note({"title": e["title"],
                                                       "content": e["content"]})]
                        if not incoming_ai:
                            continue
                        existing_ai = [n for n in
                                       self.manager.read_notes(aid).get("notes", [])
                                       if is_ai_note(n)]
                        if existing_ai:
                            conflicts[aid] = {
                                "existing_ai": existing_ai,
                                "incoming_ai": incoming_ai
                            }

                    if conflicts:
                        self._ui_import_conflict(win, filtered, conflicts)
                    else:
                        filtered, skipped = self._filter_uploading_for_import(filtered)
                        if skipped:
                            messagebox.showwarning("⚠️ 跳过上传中",
                                f"已跳过 {skipped} 个正在上传的游戏。", parent=win)
                        if filtered:
                            results = self.manager.apply_batch_import(filtered)
                            self._show_import_result(win, results)
                else:
                    # 单条导入
                    aid = single_app_id_var.get().strip()
                    if not aid:
                        messagebox.showwarning("提示", "请输入目标游戏 AppID。",
                                               parent=win)
                        return
                    title = single_title_var.get().strip() or fname
                    # 字面重复检测
                    existing_notes = self.manager.read_notes(aid).get("notes", [])
                    with open(path, "r", encoding="utf-8") as f_content:
                        file_content = f_content.read()
                    for n in existing_notes:
                        if n.get("title", "") == title and \
                                n.get("content", "") == file_content:
                            messagebox.showinfo("ℹ️ 重复",
                                f"该笔记与 AppID {aid} 中已有笔记完全重复，"
                                "已跳过导入。", parent=win)
                            return
                    self.manager.import_single_note(aid, title, path)
                    messagebox.showinfo("✅ 成功",
                        f"已导入为 AppID {aid} 的笔记:\n「{title}」",
                        parent=win)
                    self._refresh_games_list()
                    win.grab_release()
                    win.destroy()
            except Exception as e:
                messagebox.showerror("❌ 错误", f"导入失败:\n{e}", parent=win)

        def _close_import():
            win.grab_release()
            win.destroy()

        _imp_btn_frame = tk.Frame(win)
        _imp_btn_frame.pack(pady=(5, 15))
        ttk.Button(_imp_btn_frame, text="✅ 确认导入", command=do_import).pack(side=tk.LEFT, padx=5)
        ttk.Button(_imp_btn_frame, text="取消", command=_close_import).pack(side=tk.LEFT, padx=5)
        win.protocol("WM_DELETE_WINDOW", _close_import)
        self._center_window(win)

    def _filter_uploading_for_import(self, parsed: dict) -> tuple:
        """过滤掉正在上传的 app，返回 (filtered_dict, skipped_count)"""
        uploading = [aid for aid in parsed if self.is_app_uploading(aid)]
        if not uploading:
            return parsed, 0
        filtered = {k: v for k, v in parsed.items() if k not in uploading}
        return filtered, len(uploading)

    def _show_import_result(self, parent_win, results: dict):
        """显示导入结果的可滚动窗口"""
        if not results:
            messagebox.showinfo("提示", "没有笔记被导入。", parent=parent_win)
            return
        total = sum(results.values())
        result_win = tk.Toplevel(parent_win)
        result_win.title("✅ 导入成功")
        result_win.resizable(False, True)
        result_win.grab_set()
        tk.Label(result_win,
                 text=f"✅ 已导入 {total} 条笔记到 {len(results)} 个游戏",
                 font=("", 12, "bold"), fg="#2a7f2a").pack(pady=(15, 5))
        txt_frame = tk.Frame(result_win)
        txt_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        scrollbar = tk.Scrollbar(txt_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        result_text = tk.Text(txt_frame, width=50,
                              height=min(len(results) + 1, 20),
                              font=("", 10), wrap=tk.WORD,
                              yscrollcommand=scrollbar.set)
        result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=result_text.yview)
        for aid, cnt in results.items():
            game_name = self._get_game_name(aid)
            if game_name.startswith("AppID "):
                result_text.insert(tk.END, f"  AppID {aid}: {cnt} 条\n")
            else:
                result_text.insert(tk.END, f"  {game_name} ({aid}): {cnt} 条\n")
        result_text.config(state=tk.DISABLED)

        def _close_result():
            result_win.grab_release()
            result_win.destroy()
            self._refresh_games_list()
            parent_win.destroy()
        ttk.Button(result_win, text="✅ 确认",
                   command=_close_result).pack(pady=(5, 15))
        result_win.protocol("WM_DELETE_WINDOW", _close_result)
        self._center_window(result_win)

    def _ui_import_conflict(self, import_win, parsed: dict, conflicts: dict):
        """AI 笔记冲突处理主窗口
        parsed: 完整的解析数据 {app_id: [{title, content}, ...]}
        conflicts: {app_id: {existing_ai: [note_dict], incoming_ai: [entry_dict]}}
        """
        cwin = tk.Toplevel(import_win)
        cwin.title("⚠️ AI 笔记冲突处理")
        cwin.resizable(True, True)
        cwin.grab_set()
        cwin.transient(import_win)

        # ── 头部 ──
        tk.Label(cwin, text="⚠️ 检测到 AI 笔记冲突",
                 font=("", 14, "bold"), fg="#c0392b").pack(pady=(15, 5))

        n_conflict = len(conflicts)
        n_total = len(parsed)
        n_safe = n_total - n_conflict
        tk.Label(cwin,
                 text=f"共 {n_total} 个游戏待导入，其中 {n_conflict} 个存在 AI 笔记冲突"
                      f"（{n_safe} 个无冲突将正常导入）",
                 font=("", 10), fg="#666").pack(pady=(0, 10))

        # ── 冲突列表 ──
        list_frame = tk.LabelFrame(cwin, text=f"冲突游戏列表 ({n_conflict})",
                                   font=("", 10), padx=10, pady=5)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        list_inner = tk.Frame(list_frame)
        list_inner.pack(fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(list_inner)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        conflict_text = tk.Text(list_inner, width=60,
                                height=min(n_conflict + 1, 12),
                                font=("", 10), wrap=tk.WORD,
                                yscrollcommand=scrollbar.set)
        conflict_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=conflict_text.yview)

        for aid, info in conflicts.items():
            game_name = self._get_game_name(aid)
            n_exist = len(info["existing_ai"])
            n_incoming = len(info["incoming_ai"])
            if game_name.startswith("AppID "):
                conflict_text.insert(tk.END,
                    f"  AppID {aid}: 已有 {n_exist} 条 AI ↔ 导入 {n_incoming} 条 AI\n")
            else:
                conflict_text.insert(tk.END,
                    f"  {game_name} ({aid}): "
                    f"已有 {n_exist} 条 AI ↔ 导入 {n_incoming} 条 AI\n")
        conflict_text.config(state=tk.DISABLED)

        # ── 操作说明 ──
        tk.Label(cwin,
                 text="请选择冲突的处理方式：",
                 font=("", 10, "bold")).pack(pady=(10, 5))

        desc_frame = tk.Frame(cwin, padx=20)
        desc_frame.pack(fill=tk.X)
        for icon, label, desc in [
            ("🔄", "全部替换", "删除已有 AI 笔记，写入导入文件中的 AI 笔记"),
            ("➕", "全部追加", "保留已有 AI 笔记，导入的 AI 笔记追加在后面"),
            ("⏭️", "跳过 AI",  "不导入文件中的 AI 笔记（仅导入非 AI 笔记）"),
            ("🔍", "逐一处理", "逐个游戏对比新旧 AI 笔记，分别选择替换/追加/跳过"),
        ]:
            tk.Label(desc_frame, text=f"  {icon} {label} — {desc}",
                     font=("", 9), fg="#555", anchor=tk.W).pack(anchor=tk.W)

        # ── 按钮 ──
        btn_frame = tk.Frame(cwin)
        btn_frame.pack(pady=(15, 15))

        def _do_apply(policy):
            try:
                safe, skipped = self._filter_uploading_for_import(parsed)
                results = self.manager.apply_batch_import(safe, ai_policy=policy)
            except Exception as e:
                cwin.grab_release()
                cwin.destroy()
                messagebox.showerror("❌ 错误", f"导入失败:\n{e}",
                                     parent=import_win)
                return
            cwin.grab_release()
            cwin.destroy()
            self._show_import_result(import_win, results)

        def _do_cancel():
            cwin.grab_release()
            cwin.destroy()

        def _do_one_by_one():
            cwin.grab_release()
            cwin.destroy()
            self._ui_import_one_by_one(import_win, parsed, conflicts)

        ttk.Button(btn_frame, text="🔄 全部替换",
                   command=lambda: _do_apply("replace")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="➕ 全部追加",
                   command=lambda: _do_apply("append")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="⏭️ 跳过 AI",
                   command=lambda: _do_apply("skip_ai")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="🔍 逐一处理",
                   command=_do_one_by_one).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="取消导入",
                   command=_do_cancel).pack(side=tk.LEFT, padx=(15, 4))

        cwin.protocol("WM_DELETE_WINDOW", _do_cancel)
        self._center_window(cwin)

    def _ui_import_one_by_one(self, import_win, parsed: dict, conflicts: dict):
        """逐一处理每个冲突游戏的 AI 笔记，左右对比"""
        conflict_list = list(conflicts.items())
        per_app_policy = {}  # {app_id: "replace"/"append"/"skip_ai"}
        current_idx = [0]

        owin = tk.Toplevel(import_win)
        owin.title("🔍 逐一处理 AI 笔记冲突")
        owin.resizable(True, True)
        owin.grab_set()
        owin.transient(import_win)
        owin.geometry("900x560")

        # ── 顶部进度 ──
        progress_label = tk.Label(owin, font=("", 11, "bold"))
        progress_label.pack(pady=(10, 0))

        game_label = tk.Label(owin, font=("", 12, "bold"), fg="#1a73e8")
        game_label.pack(pady=(2, 8))

        # ── 左右对比面板 ──
        compare_frame = tk.Frame(owin)
        compare_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        compare_frame.columnconfigure(0, weight=1)
        compare_frame.columnconfigure(1, weight=0)
        compare_frame.columnconfigure(2, weight=1)
        compare_frame.rowconfigure(1, weight=1)

        tk.Label(compare_frame, text="📋 已有 AI 笔记（本地）",
                 font=("", 10, "bold"), fg="#c0392b").grid(
                     row=0, column=0, sticky=tk.W, padx=5)
        tk.Label(compare_frame, text="📥 导入 AI 笔记（文件）",
                 font=("", 10, "bold"), fg="#27ae60").grid(
                     row=0, column=2, sticky=tk.W, padx=5)

        # 左侧
        left_frame = tk.Frame(compare_frame)
        left_frame.grid(row=1, column=0, sticky="nsew", padx=(5, 2))
        left_scroll = tk.Scrollbar(left_frame)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        left_text = tk.Text(left_frame, font=("", 9), wrap=tk.WORD,
                            bg="#fff5f5", yscrollcommand=left_scroll.set)
        left_text.pack(fill=tk.BOTH, expand=True)
        left_scroll.config(command=left_text.yview)

        # 分隔
        tk.Frame(compare_frame, width=2, bg="#ccc").grid(
            row=1, column=1, sticky="ns", padx=2)

        # 右侧
        right_frame = tk.Frame(compare_frame)
        right_frame.grid(row=1, column=2, sticky="nsew", padx=(2, 5))
        right_scroll = tk.Scrollbar(right_frame)
        right_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        right_text = tk.Text(right_frame, font=("", 9), wrap=tk.WORD,
                             bg="#f5fff5", yscrollcommand=right_scroll.set)
        right_text.pack(fill=tk.BOTH, expand=True)
        right_scroll.config(command=right_text.yview)

        # ── 底部按钮 ──
        btn_frame = tk.Frame(owin)
        btn_frame.pack(pady=(8, 12))

        def _show_current():
            idx = current_idx[0]
            aid, info = conflict_list[idx]
            game_name = self._get_game_name(aid)
            progress_label.config(text=f"冲突 {idx + 1} / {len(conflict_list)}")
            game_label.config(text=f"🎮 {game_name} (AppID: {aid})")
            _render_ai_notes(left_text, info["existing_ai"])
            _render_ai_notes(right_text, info["incoming_ai"])

        def _choose(policy):
            aid = conflict_list[current_idx[0]][0]
            per_app_policy[aid] = policy
            current_idx[0] += 1
            if current_idx[0] >= len(conflict_list):
                _finish()
            else:
                _show_current()

        def _finish():
            owin.grab_release()
            owin.destroy()
            safe, skipped = self._filter_uploading_for_import(parsed)
            results = self.manager.apply_batch_import(
                safe, ai_policy="append",
                per_app_policy=per_app_policy)
            self._show_import_result(import_win, results)

        def _cancel_remaining():
            # 将剩余冲突全部设为 skip_ai
            for j in range(current_idx[0], len(conflict_list)):
                aid = conflict_list[j][0]
                per_app_policy[aid] = "skip_ai"
            _finish()

        def _do_close():
            owin.grab_release()
            owin.destroy()

        ttk.Button(btn_frame, text="🔄 替换",
                   command=lambda: _choose("replace")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="➕ 追加",
                   command=lambda: _choose("append")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="⏭️ 跳过",
                   command=lambda: _choose("skip_ai")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="⏩ 跳过剩余全部",
                   command=_cancel_remaining).pack(side=tk.LEFT, padx=(15, 4))
        ttk.Button(btn_frame, text="取消导入",
                   command=_do_close).pack(side=tk.LEFT, padx=(15, 4))

        owin.protocol("WM_DELETE_WINDOW", _do_close)
        _show_current()
        self._center_window(owin)

