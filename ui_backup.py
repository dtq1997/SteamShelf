"""
ui_backup.py — 备份管理界面（BackupMixin）

从 _legacy_A/ui_backup.py 移植。
引用映射：self.core → self._collections_core

宿主协议：BackupHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import BackupHost  # noqa: F401

import os
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk


class BackupMixin:
    """备份管理界面（Mixin，self 指向 SteamToolboxMain 实例）"""

    def open_backup_manager_ui(self):
        """打开备份管理界面"""
        if not self._ensure_collections_core():
            return
        if not self._collections_core.backup_manager:
            messagebox.showerror("错误", "备份管理器未初始化。",
                                 parent=self.root)
            return

        bk_win = tk.Toplevel(self.root)
        bk_win.title("管理收藏夹备份")

        # 当前账号信息
        account_frame = tk.Frame(bk_win, bg="#f0f0f0", pady=8)
        account_frame.pack(fill="x")
        tk.Label(account_frame,
                 text=f"📂 当前账号: {self.current_account.persona_name} ({self.current_account.friend_code})",
                 font=("微软雅黑", 10, "bold"), bg="#f0f0f0").pack(side="left", padx=15)

        # 当前文件信息
        current_frame = tk.LabelFrame(bk_win, text="📄 当前使用的文件",
                                       font=("微软雅黑", 10, "bold"), padx=10, pady=10)
        current_frame.pack(fill="x", padx=15, pady=(10, 5))

        if os.path.exists(self.current_account.storage_path):
            file_size = os.path.getsize(self.current_account.storage_path)
            file_mtime = datetime.fromtimestamp(
                os.path.getmtime(self.current_account.storage_path))

            # 统计收藏夹数量
            try:
                data = self._collections_core.load_json()
                statics = self._collections_core.get_static_collections(data) if data else []
                col_count = len(statics)
            except Exception:
                col_count = "?"

            info_text = (
                f"路径: {self.current_account.storage_path}\n"
                f"大小: {file_size:,} 字节 | "
                f"修改时间: {file_mtime.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"收藏夹数: {col_count}")
            tk.Label(current_frame, text=info_text, font=("微软雅黑", 9),
                     justify="left", wraplength=650).pack(anchor="w")

        # 手动创建备份
        manual_frame = tk.Frame(bk_win)
        manual_frame.pack(fill="x", padx=15, pady=5)

        desc_var = tk.StringVar(value="")
        tk.Label(manual_frame, text="备份描述（可选）:",
                 font=("微软雅黑", 9)).pack(side="left")
        desc_entry = tk.Entry(manual_frame, textvariable=desc_var,
                              width=30, font=("微软雅黑", 9))
        desc_entry.pack(side="left", padx=5)

        def do_manual_backup():
            desc = desc_var.get().strip()
            backup_path = self._collections_core.backup_manager.create_backup(
                description=desc if desc else "手动备份")
            if backup_path:
                messagebox.showinfo("成功",
                    f"✅ 备份已创建:\n{os.path.basename(backup_path)}",
                    parent=bk_win)
                refresh_backup_list()
            else:
                messagebox.showerror("错误", "❌ 备份创建失败。",
                                     parent=bk_win)

        ttk.Button(manual_frame, text="💾 立即创建备份",
                   command=do_manual_backup).pack(side="left", padx=10)

        # 备份列表
        list_frame = tk.LabelFrame(bk_win, text="📚 备份历史",
                                    font=("微软雅黑", 10, "bold"), padx=10, pady=10)
        list_frame.pack(fill="both", expand=True, padx=15, pady=5)

        columns = ("filename", "time", "size", "description")
        tree = ttk.Treeview(list_frame, columns=columns,
                            show="headings", height=10)
        tree.heading("filename", text="文件名")
        tree.heading("time", text="创建时间")
        tree.heading("size", text="大小")
        tree.heading("description", text="描述")

        tree.column("filename", width=250)
        tree.column("time", width=140)
        tree.column("size", width=80)
        tree.column("description", width=180)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical",
                                   command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def refresh_backup_list():
            for item in tree.get_children():
                tree.delete(item)
            backups = self._collections_core.backup_manager.list_backups()
            for b in backups:
                size_str = f"{b['size']:,} B"
                if b['size'] > 1024:
                    size_str = f"{b['size'] / 1024:.1f} KB"
                tree.insert("", "end", values=(
                    b['filename'],
                    b['created_at'].strftime("%Y-%m-%d %H:%M:%S"),
                    size_str,
                    b['description']
                ))

        refresh_backup_list()

        # 操作按钮
        btn_frame = tk.Frame(bk_win)
        btn_frame.pack(fill="x", padx=15, pady=10)

        def get_selected_backup():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请先选择一个备份。",
                                       parent=bk_win)
                return None
            item = tree.item(selected[0])
            return item['values'][0]

        def do_view_diff():
            filename = get_selected_backup()
            if not filename:
                return
            self._show_diff_window(filename)

        def do_restore():
            filename = get_selected_backup()
            if not filename:
                return
            if messagebox.askyesno("确认恢复",
                    f"确定要恢复到此备份吗？\n\n{filename}\n\n"
                    "当前文件将在恢复前自动备份。",
                    parent=bk_win):
                if self._collections_core.backup_manager.restore_backup(filename):
                    messagebox.showinfo("成功", "✅ 已成功恢复备份！",
                                        parent=bk_win)
                    refresh_backup_list()
                else:
                    messagebox.showerror("错误", "❌ 恢复失败。",
                                         parent=bk_win)

        def do_delete():
            filename = get_selected_backup()
            if not filename:
                return
            if messagebox.askyesno("确认删除",
                    f"确定要删除此备份吗？\n\n{filename}\n\n此操作不可恢复。",
                    parent=bk_win):
                if self._collections_core.backup_manager.delete_backup(filename):
                    messagebox.showinfo("成功", "✅ 备份已删除。",
                                        parent=bk_win)
                    refresh_backup_list()
                else:
                    messagebox.showerror("错误", "❌ 删除失败。",
                                         parent=bk_win)

        ttk.Button(btn_frame, text="🔍 查看差异", command=do_view_diff,
                  width=12).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="⏪ 恢复此备份", command=do_restore,
                  width=12).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="🗑 删除备份", command=do_delete,
                  width=12).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="🔄 刷新列表", command=refresh_backup_list,
                  width=12).pack(side="right", padx=5)

    def _show_diff_window(self, backup_filename):
        """显示备份与当前文件的差异详情"""
        diff_result = self._collections_core.backup_manager.compare_with_current(
            backup_filename)

        if 'error' in diff_result:
            messagebox.showerror("错误",
                f"比较失败: {diff_result['error']}", parent=self.root)
            return

        diff_win = tk.Toplevel(self.root)
        diff_win.title(f"差异对比: {backup_filename} ↔ 当前文件")

        # 摘要信息
        summary = diff_result['summary']
        summary_frame = tk.Frame(diff_win, bg="#e8f4f8", pady=10)
        summary_frame.pack(fill="x")

        summary_text = (
            f"📊 变化摘要:  新增 {summary['total_added']} 个收藏夹  |  "
            f"删除 {summary['total_removed']} 个  |  "
            f"修改 {summary['total_modified']} 个  |  "
            f"未变 {summary['total_unchanged']} 个")
        tk.Label(summary_frame, text=summary_text,
                 font=("微软雅黑", 10, "bold"), bg="#e8f4f8").pack()

        # 创建 Notebook 用于分类显示
        notebook = ttk.Notebook(diff_win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # --- 新增的收藏夹 ---
        if diff_result['added_collections']:
            added_frame = tk.Frame(notebook)
            notebook.add(added_frame,
                text=f"➕ 新增 ({len(diff_result['added_collections'])})")

            added_text = tk.Text(added_frame, font=("微软雅黑", 9), wrap="word")
            added_scroll = ttk.Scrollbar(added_frame, orient="vertical",
                                          command=added_text.yview)
            added_text.configure(yscrollcommand=added_scroll.set)
            added_text.pack(side="left", fill="both", expand=True)
            added_scroll.pack(side="right", fill="y")

            added_text.tag_config("title", foreground="#2e7d32",
                                   font=("微软雅黑", 10, "bold"))
            added_text.tag_config("info", foreground="#666")

            for col in diff_result['added_collections']:
                col_type = "🔄 动态" if col['is_dynamic'] else "📁 静态"
                added_text.insert("end", f"• {col['name']}\n", "title")
                added_text.insert("end",
                    f"   {col_type} | 游戏数: {col['game_count']}\n\n", "info")

            added_text.config(state="disabled")

        # --- 删除的收藏夹 ---
        if diff_result['removed_collections']:
            removed_frame = tk.Frame(notebook)
            notebook.add(removed_frame,
                text=f"➖ 删除 ({len(diff_result['removed_collections'])})")

            removed_text = tk.Text(removed_frame, font=("微软雅黑", 9),
                                    wrap="word")
            removed_scroll = ttk.Scrollbar(removed_frame, orient="vertical",
                                            command=removed_text.yview)
            removed_text.configure(yscrollcommand=removed_scroll.set)
            removed_text.pack(side="left", fill="both", expand=True)
            removed_scroll.pack(side="right", fill="y")

            removed_text.tag_config("title", foreground="#c62828",
                                     font=("微软雅黑", 10, "bold"))
            removed_text.tag_config("info", foreground="#666")

            for col in diff_result['removed_collections']:
                col_type = "🔄 动态" if col['is_dynamic'] else "📁 静态"
                removed_text.insert("end", f"• {col['name']}\n", "title")
                removed_text.insert("end",
                    f"   {col_type} | 游戏数: {col['game_count']}\n\n", "info")

            removed_text.config(state="disabled")

        # --- 修改的收藏夹 ---
        if diff_result['modified_collections']:
            modified_frame = tk.Frame(notebook)
            notebook.add(modified_frame,
                text=f"✏️ 修改 ({len(diff_result['modified_collections'])})")

            modified_text = tk.Text(modified_frame, font=("微软雅黑", 9),
                                     wrap="word")
            modified_scroll = ttk.Scrollbar(modified_frame, orient="vertical",
                                             command=modified_text.yview)
            modified_text.configure(yscrollcommand=modified_scroll.set)
            modified_text.pack(side="left", fill="both", expand=True)
            modified_scroll.pack(side="right", fill="y")

            modified_text.tag_config("title", foreground="#1565c0",
                                      font=("微软雅黑", 10, "bold"))
            modified_text.tag_config("name_change", foreground="#6a1b9a")
            modified_text.tag_config("added", foreground="#2e7d32")
            modified_text.tag_config("removed", foreground="#c62828")
            modified_text.tag_config("info", foreground="#666")

            for col in diff_result['modified_collections']:
                if col['name_changed']:
                    modified_text.insert("end",
                        f"• {col['old_name']} → {col['new_name']}\n",
                        "name_change")
                else:
                    modified_text.insert("end",
                        f"• {col['new_name']}\n", "title")

                modified_text.insert("end",
                    f"   游戏数: {col['old_game_count']} → "
                    f"{col['new_game_count']}\n", "info")

                if col['added_games']:
                    added_preview = col['added_games'][:10]
                    modified_text.insert("end",
                        f"   ➕ 新增 {len(col['added_games'])} 个: ", "added")
                    modified_text.insert("end",
                        f"{', '.join(map(str, added_preview))}")
                    if len(col['added_games']) > 10:
                        modified_text.insert("end", " ... 等")
                    modified_text.insert("end", "\n")

                if col['removed_games']:
                    removed_preview = col['removed_games'][:10]
                    modified_text.insert("end",
                        f"   ➖ 移除 {len(col['removed_games'])} 个: ",
                        "removed")
                    modified_text.insert("end",
                        f"{', '.join(map(str, removed_preview))}")
                    if len(col['removed_games']) > 10:
                        modified_text.insert("end", " ... 等")
                    modified_text.insert("end", "\n")

                modified_text.insert("end", "\n")

            modified_text.config(state="disabled")

        # --- 未变化的收藏夹 ---
        if diff_result['unchanged_collections']:
            unchanged_frame = tk.Frame(notebook)
            notebook.add(unchanged_frame,
                text=f"⚪ 未变 ({len(diff_result['unchanged_collections'])})")

            unchanged_text = tk.Text(unchanged_frame, font=("微软雅黑", 9),
                                      wrap="word")
            unchanged_scroll = ttk.Scrollbar(unchanged_frame,
                orient="vertical", command=unchanged_text.yview)
            unchanged_text.configure(yscrollcommand=unchanged_scroll.set)
            unchanged_text.pack(side="left", fill="both", expand=True)
            unchanged_scroll.pack(side="right", fill="y")

            unchanged_text.tag_config("title", foreground="#666",
                                       font=("微软雅黑", 9))
            unchanged_text.tag_config("info", foreground="#999")

            for col in diff_result['unchanged_collections']:
                col_type = "🔄 动态" if col['is_dynamic'] else "📁 静态"
                unchanged_text.insert("end", f"• {col['name']}\n", "title")
                unchanged_text.insert("end",
                    f"   {col_type} | 游戏数: {col['game_count']}\n\n", "info")

            unchanged_text.config(state="disabled")

        # 关闭按钮
        ttk.Button(diff_win, text="关闭", command=diff_win.destroy,
                  width=10).pack(pady=10)
