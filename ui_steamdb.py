"""
ui_steamdb.py — SteamDB 列表导入界面（SteamDBMixin）

从 _legacy_A/ui_steamdb.py 移植。
引用映射：self.core → self._collections_core

宿主协议：SteamDBHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import SteamDBHost  # noqa: F401

import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from utils import sanitize_filename


class SteamDBMixin:
    """SteamDB 列表导入界面（Mixin，self 指向 SteamToolboxMain 实例）"""

    def steamdb_sync_ui(self, target_col=None):
        """从 SteamDB 列表页面获取游戏"""
        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return

        merged_ids = []
        merge_stats = []

        db_win = tk.Toplevel(self.root)
        if target_col:
            db_win.title(f"从 SteamDB 更新「{target_col[1]}」")
        else:
            db_win.title("从 SteamDB 列表页面获取游戏")

        if not target_col:
            tk.Label(db_win,
                     text="使用指南：\n"
                          "1. 在浏览器打开 SteamDB 列表页面，右键 →「另存为」保存完整网页源代码。\n"
                          "2. 如需合并多个列表，重复保存即可。\n"
                          "3. 点击下方按钮选择所有已保存的 HTML 文件。",
                     justify="left", font=("微软雅黑", 9),
                     wraplength=500).pack(padx=20, pady=(15, 5))

        status_var = tk.StringVar(value="尚未选择文件。")
        status_label = tk.Label(db_win, textvariable=status_var,
                                font=("微软雅黑", 9), fg="gray")
        status_label.pack(padx=20, anchor="w")

        name_var = tk.StringVar(value="SteamDB List")
        name_frame = tk.Frame(db_win)
        name_frame.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(name_frame, text="收藏夹 / 文件名称：",
                 font=("微软雅黑", 9)).pack(side="left")
        tk.Entry(name_frame, textvariable=name_var, width=35,
                 font=("微软雅黑", 9)).pack(side="left", padx=5)

        def do_select_files():
            nonlocal merged_ids, merge_stats
            file_paths = filedialog.askopenfilenames(
                initialdir=self._last_dir('steamdb_import'),
                title="选择 SteamDB 源代码文件 (可多选)",
                filetypes=[("HTML files", "*.html"),
                           ("Text files", "*.txt"),
                           ("All files", "*.*")])
            if not file_paths:
                return
            self._save_dir('steamdb_import', file_paths[0])

            all_raw_ids = []
            merge_stats.clear()
            for path in file_paths:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    page_ids = self._collections_core.extract_ids_from_steamdb_html(content)
                    if page_ids:
                        all_raw_ids.extend(page_ids)
                        merge_stats.append(
                            f"• {os.path.basename(path)}: {len(page_ids)} 个")
                    else:
                        merge_stats.append(
                            f"• {os.path.basename(path)}: 未提取到 ID，已跳过")
                except Exception as e:
                    merge_stats.append(
                        f"• {os.path.basename(path)}: 读取失败 ({e})")

            merged_ids.clear()
            merged_ids.extend(list(dict.fromkeys(all_raw_ids)))

            if merged_ids:
                status_var.set(
                    f"✅ 已从 {len(file_paths)} 个文件中提取并合并 "
                    f"{len(merged_ids)} 个唯一 AppID"
                    f"（原始 {len(all_raw_ids)} 个）。")
                status_label.config(fg="green")
                if len(file_paths) == 1:
                    name_var.set(
                        os.path.splitext(os.path.basename(file_paths[0]))[0])
            else:
                status_var.set("❌ 所选文件中均未提取到有效的 AppID。")
                status_label.config(fg="red")

        select_lbl = tk.Label(db_win,
            text="📂 选择 SteamDB HTML 文件（可多选合并）",
            font=("微软雅黑", 10, "bold"), bg="#4a90d9", fg="white",
            padx=15, pady=8, cursor="hand2", relief="raised", bd=1)
        select_lbl.pack(pady=10)
        select_lbl.bind("<Enter>", lambda e: select_lbl.config(relief="groove"))
        select_lbl.bind("<Leave>", lambda e: select_lbl.config(relief="raised"))
        select_lbl.bind("<Button-1>", lambda e: do_select_files())

        disclaimer = self._collections_core.disclaimer

        def do_create():
            if not merged_ids:
                messagebox.showwarning("错误",
                    "请先选择文件并提取 AppID。", parent=db_win)
                return
            name = simpledialog.askstring("新建收藏夹", "请输入收藏夹名称：",
                                          initialvalue=name_var.get(),
                                          parent=db_win)
            if name:
                filtered = self._ask_filter_owned(
                    list(merged_ids), parent=db_win)
                if filtered is None:
                    return
                self._collections_core.add_static_collection(
                    data, name, filtered)
                self._save_and_sync(
                    data, backup_description=f"从 SteamDB 创建收藏夹: {name}")
                detail = '\n'.join(merge_stats)
                messagebox.showinfo("录入成功",
                    f"已建立新收藏夹。本次共录入 {len(filtered)} 个 AppID。\n\n"
                    f"各文件明细：\n{detail}" + disclaimer,
                    parent=db_win)
                db_win.destroy()

        def do_export_txt():
            if not merged_ids:
                messagebox.showwarning("错误",
                    "请先选择文件并提取 AppID。", parent=db_win)
                return
            name = simpledialog.askstring("导出设置",
                "请输入生成的 TXT 文件名：",
                initialvalue=sanitize_filename(name_var.get()),
                parent=db_win)
            if not name:
                return
            save_path = filedialog.asksaveasfilename(
                initialdir=self._last_dir('steamdb_export'),
                title="保存 AppID 列表",
                defaultextension=".txt",
                initialfile=f"{sanitize_filename(name)}.txt",
                filetypes=[("Text files", "*.txt")])
            if save_path:
                self._save_dir('steamdb_export', save_path)
                with open(save_path, 'w', encoding='utf-8') as f:
                    for aid in merged_ids:
                        f.write(f"{aid}\n")
                detail = '\n'.join(merge_stats)
                messagebox.showinfo("成功",
                    f"已成功导出 {len(merged_ids)} 个 AppID。\n\n"
                    f"各文件明细：\n{detail}" + disclaimer,
                    parent=db_win)

        def do_update():
            if not merged_ids:
                messagebox.showwarning("错误",
                    "请先选择文件并提取 AppID。", parent=db_win)
                return
            all_cols = self._collections_core.get_all_collections_with_refs(data)
            if not all_cols:
                messagebox.showwarning("提示", "未找到任何收藏夹。",
                                       parent=db_win)
                return
            sources = {"SteamDB 列表": {
                "name": "SteamDB 列表", "ids": list(merged_ids)}}

            def on_done():
                self._save_and_sync(
                    data, backup_description="从 SteamDB 更新收藏夹")
                db_win.destroy()

            self.show_batch_update_mapping(
                data, all_cols, sources, on_done, parent_to_close=db_win)

        def do_target_update():
            if not merged_ids:
                messagebox.showwarning("错误",
                    "请先选择文件并提取 AppID。", parent=db_win)
                return
            col_id, col_name = target_col
            all_cols = self._collections_core.get_all_collections_with_refs(data)
            entry = None
            for c in all_cols:
                if c.get('id') == col_id:
                    entry = c['entry_ref']
                    break
            if not entry:
                messagebox.showerror("错误", "未找到目标收藏夹。", parent=db_win)
                return
            mode = mode_combo.get()
            mode_map = {"增量": "incremental",
                        "增量+辅助": "incremental_aux",
                        "替换": "replace"}
            mode_key = mode_map.get(mode, "incremental")
            if mode == "替换":
                old_c, new_c = \
                    self._collections_core.perform_replace_update(
                        data, entry, list(merged_ids))
                result_msg = f"🔄 替换更新完成\n{old_c} → {new_c}"
                updated = True
            else:
                create_aux = (mode == "增量+辅助")
                a, r, t, updated = \
                    self._collections_core.perform_incremental_update(
                        data, entry, list(merged_ids), col_name,
                        create_aux=create_aux)
                result_msg = (f"✅「{col_name}」已更新\n"
                              f"新增: {a}, 移除: {r}, 总计: {t}")
            self._save_and_sync(data,
                backup_description=f"从 SteamDB 更新: {col_name}")
            db_win.destroy()
            self._ui_refresh()
            if updated:
                messagebox.showinfo("更新完成",
                    result_msg + disclaimer, parent=self.root)
            else:
                messagebox.showinfo("已是最新",
                    f"「{col_name}」已是最新，无需更新。", parent=self.root)
            self._ask_bind_source(col_id, 'steamdb', {},
                                  col_name, update_mode=mode_key)

        btn_frame = tk.Frame(db_win)
        btn_frame.pack(pady=15)
        if target_col:
            mode_frame = tk.Frame(btn_frame)
            mode_frame.pack(side="left", padx=(0, 8))
            tk.Label(mode_frame, text="模式：",
                     font=("微软雅黑", 9)).pack(side="left")
            mode_combo = ttk.Combobox(mode_frame,
                values=["增量", "增量+辅助", "替换"],
                width=8, state="readonly")
            mode_combo.set("增量")
            mode_combo.pack(side="left")

            ttk.Button(btn_frame, text="🔄 更新",
                       command=do_target_update, width=10).pack(
                           side="left", padx=5)
            ttk.Button(btn_frame, text="取消",
                       command=db_win.destroy, width=8).pack(
                           side="left", padx=5)
        else:
            ttk.Button(btn_frame, text="📁 建立为新收藏夹", command=do_create,
                       width=15).pack(side="left", padx=5)
            ttk.Button(btn_frame, text="📥 导出为 TXT 文件", command=do_export_txt,
                       width=18).pack(side="left", padx=5)
            ttk.Button(btn_frame, text="🔄️ 更新现有收藏夹", command=do_update,
                       width=15).pack(side="left", padx=5)

        self._center_window(db_win)
