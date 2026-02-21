"""
ui_collection_ops.py — 分类导入/导出/更新操作界面（CollectionOpsMixin）

从 _legacy_A/ui_collection_ops.py 移植。
引用映射：self.core → self._collections_core

宿主协议：CollectionOpsHost（见 _protocols.py）
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _protocols import CollectionOpsHost  # noqa: F401

import json
import os
import re
import threading

from account_manager import SteamAccountScanner
from utils import sanitize_filename
from ui_utils import ProgressWindow, bg_thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class CollectionOpsMixin:
    """分类导入/导出/更新操作界面（Mixin，self 指向 SteamToolboxMain 实例）"""

    def import_collection(self, target_col=None):
        """批量导入：选择 TXT（多个 AppID 列表）或 JSON（结构化分类）

        target_col: (col_id, col_name) 时直接更新目标分类
        """
        if not self._ensure_collections_core():
            return

        if target_col:
            paths = filedialog.askopenfilenames(
                initialdir=self._last_dir('coll_import'),
                title=f"选择文件更新「{target_col[1]}」",
                filetypes=[("文本 AppID 列表", "*.txt")])
            if not paths:
                return
            self._save_dir('coll_import', paths[0])
            all_ids = []
            for p in paths:
                with open(p, 'r', encoding='utf-8') as f:
                    all_ids.extend(int(line.strip()) for line in f
                                   if line.strip().isdigit())
            if not all_ids:
                messagebox.showwarning("错误", "文件中未找到有效的 AppID。",
                                       parent=self.root)
                return
            data = self._collections_core.load_json()
            if data is None:
                return
            col_id, col_name = target_col
            all_cols = self._collections_core.get_all_collections_with_refs(data)
            entry = None
            for c in all_cols:
                if c.get('id') == col_id:
                    entry = c['entry_ref']
                    break
            if not entry:
                messagebox.showerror("错误", "未找到目标分类。",
                                     parent=self.root)
                return
            disclaimer = self._collections_core.disclaimer
            a, r, t, updated = self._collections_core.perform_incremental_update(
                data, entry, all_ids, col_name, create_aux=True)
            self._save_and_sync(data,
                backup_description=f"从文件更新: {col_name}")
            self._ui_refresh()
            if updated:
                messagebox.showinfo("更新完成",
                    f"✅「{col_name}」已更新\n新增: {a}, 移除: {r}, 总计: {t}"
                    + disclaimer, parent=self.root)
            else:
                messagebox.showinfo("已是最新",
                    f"「{col_name}」已是最新，无需更新。",
                    parent=self.root)
            return

        paths = filedialog.askopenfilenames(
            initialdir=self._last_dir('coll_import'),
            title="选择文件",
            filetypes=[("文本 AppID 列表", "*.txt"),
                       ("JSON 结构化分类", "*.json")])

        if not paths:
            return
        self._save_dir('coll_import', paths[0])

        data = self._collections_core.load_json()
        if data is None:
            return

        existing = self._collections_core.get_all_collections_ordered(data)
        self._original_col_ids = {c['id'] for c in existing}

        import_echo = [""]

        for path in paths:
            filename = os.path.basename(path)
            try:
                ext = os.path.splitext(path)[1].lower()
                if ext == ".txt":
                    count, err = self._collections_core.import_collections_appid_list(
                        path, data)
                elif ext == ".json":
                    count, err = self._collections_core.import_collections_structured(
                        path, data)
                else:
                    count, err = 0, "不支持的文件格式。"
            except Exception as e:
                import_echo.append(f"❌ {filename}: {e}")
            else:
                if err:
                    import_echo.append(f"❌ {filename}: {err}")
                else:
                    import_echo.append(f"✅ {filename}: {count} 个 AppID。")

        self._ui_mark_dirty(data)
        self._ui_refresh()
        result_text = "\n".join(import_echo)
        messagebox.showinfo("导入完成",
            f"导入结果：{result_text}\n\n"
            "最后请点击「💾 储存更改」写入文件。",
            parent=self.root)

    def export_static_collection(self):
        """批量导出：使用左侧勾选的分类，三种格式可选"""
        if not self._ensure_collections_core():
            return
        selected = self._ui_get_selected()
        if not selected:
            messagebox.showwarning("提示",
                "请先在左侧选择要导出的分类。", parent=self.root)
            return

        fmt_win = tk.Toplevel(self.root)
        fmt_win.title("批量导出分类")
        fmt_win.resizable(False, False)

        tk.Label(fmt_win,
            text=f"已选中 {len(selected)} 个分类，请选择导出格式：",
            font=("微软雅黑", 10), pady=10).pack(padx=20)

        def export_merged_appid():
            fmt_win.destroy()
            unique_ids = self._collections_core.export_collections_appid_list(
                selected)
            if not unique_ids:
                messagebox.showwarning("提示",
                    "选中的分类没有可导出的 AppID。", parent=self.root)
                return
            save_path = filedialog.asksaveasfilename(
                initialdir=self._last_dir('coll_export'),
                title="保存合并 AppID 列表",
                defaultextension=".txt", initialfile="merged_appids.txt",
                filetypes=[("Text files", "*.txt")])
            if save_path:
                self._save_dir('coll_export', save_path)
                with open(save_path, 'w', encoding='utf-8') as f:
                    for aid in unique_ids:
                        f.write(f"{aid}\n")
                messagebox.showinfo("✅ 导出成功",
                    f"已导出 {len(unique_ids)} 个去重 AppID。\n"
                    f"（来自 {len(selected)} 个分类）",
                    parent=self.root)

        def export_multiple_txt():
            fmt_win.destroy()
            dest_dir = filedialog.askdirectory(
                initialdir=self._last_dir('coll_export'),
                title="选择保存导出文件的文件夹")
            if not dest_dir:
                return
            self._save_dir('coll_export', dest_dir)
            count = 0
            for col in selected:
                safe_name = sanitize_filename(col['name'])
                app_ids = col.get('added', [])
                if not app_ids:
                    continue
                with open(os.path.join(dest_dir, f"{safe_name}.txt"),
                          'w', encoding='utf-8') as f:
                    for aid in app_ids:
                        f.write(f"{aid}\n")
                count += 1
            messagebox.showinfo("✅ 导出成功",
                f"共导出 {count} 个 TXT 文件到：\n{dest_dir}",
                parent=self.root)

        def export_structured_json():
            fmt_win.destroy()
            export_data = self._collections_core.export_collections_structured(
                selected)
            save_path = filedialog.asksaveasfilename(
                initialdir=self._last_dir('coll_export'),
                title="保存分类结构化数据",
                defaultextension=".json",
                initialfile="exported_collections.json",
                filetypes=[("JSON files", "*.json")])
            if save_path:
                self._save_dir('coll_export', save_path)
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, ensure_ascii=False, indent=2)
                messagebox.showinfo("✅ 导出成功",
                    f"已导出 {len(selected)} 个分类的完整结构。\n"
                    "（含名称、分类信息及动态逻辑）",
                    parent=self.root)

        tk.Button(fmt_win,
            text="📄 合并为单个 AppID 列表（TXT）\n"
                 "所有选中分类的 AppID 去重合并",
            command=export_merged_appid, font=("微软雅黑", 9),
            width=36, height=3, justify="left").pack(padx=20, pady=(5, 5))
        tk.Button(fmt_win,
            text="📁 导出为多个 TXT 文件\n"
                 "每个分类一个文件，动态分类仅导出额外添加部分",
            command=export_multiple_txt, font=("微软雅黑", 9),
            width=36, height=3, justify="left").pack(padx=20, pady=(0, 5))
        tk.Button(fmt_win,
            text="📦 导出为结构化数据（JSON）\n"
                 "含名称、分类、动态逻辑，可用于完整还原",
            command=export_structured_json, font=("微软雅黑", 9),
            width=36, height=3, justify="left").pack(padx=20, pady=(0, 5))
        ttk.Button(fmt_win, text="取消", command=fmt_win.destroy,
                  width=10).pack(pady=(0, 10))

    def update_static_collection(self):
        """批量更新：选择来源格式（TXT 或 JSON），然后映射到目标分类"""
        if not self._ensure_collections_core():
            return

        fmt_win = tk.Toplevel(self.root)
        fmt_win.title("批量更新分类")
        fmt_win.resizable(False, False)

        tk.Label(fmt_win, text="请选择用于更新的来源文件格式：",
                 font=("微软雅黑", 10), pady=10).pack(padx=20)

        def update_from_txt():
            fmt_win.destroy()
            txt_paths = filedialog.askopenfilenames(
                initialdir=self._last_dir('coll_import'),
                title="选择 AppID 列表 (TXT)",
                filetypes=[("Text files", "*.txt")])
            if not txt_paths:
                return
            self._save_dir('coll_import', txt_paths[0])
            data = self._collections_core.load_json()
            if data is None:
                return
            all_cols = self._collections_core.get_all_collections_with_refs(data)
            if not all_cols:
                messagebox.showwarning("提示", "未找到任何分类。",
                                       parent=self.root)
                return

            sources = {}
            for p in txt_paths:
                file_title = os.path.splitext(os.path.basename(p))[0]
                with open(p, 'r', encoding='utf-8') as f:
                    ids = [int(line.strip()) for line in f
                           if line.strip().isdigit()]
                sources[file_title] = {"name": file_title, "ids": ids}

            existing = self._collections_core.get_all_collections_ordered(data)
            self._original_col_ids = {c['id'] for c in existing}

            def on_done():
                self._ui_mark_dirty(data)
                self._ui_refresh()

            self.show_batch_update_mapping(data, all_cols, sources, on_done)

        def update_from_json():
            fmt_win.destroy()
            path = filedialog.askopenfilename(
                initialdir=self._last_dir('coll_import'),
                title="选择结构化分类文件（JSON）",
                filetypes=[("JSON files", "*.json")])
            if not path:
                return
            self._save_dir('coll_import', path)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    import_data = json.load(f)
                if import_data.get("format") != "steam_collections_structured":
                    messagebox.showerror("格式错误",
                        "文件不是有效的结构化分类文件。", parent=self.root)
                    return
                src_cols = import_data.get("collections", [])
                if not src_cols:
                    messagebox.showerror("无数据",
                        "文件中没有分类数据。", parent=self.root)
                    return
            except json.JSONDecodeError:
                messagebox.showerror("格式错误",
                    "文件不是有效的 JSON。", parent=self.root)
                return
            except Exception as e:
                messagebox.showerror("读取失败",
                    f"读取文件出错：{e}", parent=self.root)
                return

            data = self._collections_core.load_json()
            if data is None:
                return
            all_cols = self._collections_core.get_all_collections_with_refs(data)
            if not all_cols:
                messagebox.showwarning("提示", "未找到任何分类。",
                                       parent=self.root)
                return

            existing = self._collections_core.get_all_collections_ordered(data)
            self._original_col_ids = {c['id'] for c in existing}

            sources = {}
            for i, src in enumerate(src_cols):
                key = src.get("name", f"分类 {i + 1}")
                sources[key] = {"name": key, "ids": src.get("added", [])}

            def on_done():
                self._ui_mark_dirty(data)
                self._ui_refresh()

            self.show_batch_update_mapping(data, all_cols, sources, on_done)

        tk.Button(fmt_win,
            text="📄 从 TXT 文件更新\n选择多个 AppID 列表文件",
            command=update_from_txt, font=("微软雅黑", 9),
            width=32, height=3, justify="left").pack(padx=20, pady=(5, 5))
        tk.Button(fmt_win,
            text="📦 从 JSON 文件更新\n使用结构化分类数据",
            command=update_from_json, font=("微软雅黑", 9),
            width=32, height=3, justify="left").pack(padx=20, pady=(0, 5))
        ttk.Button(fmt_win, text="取消", command=fmt_win.destroy,
                  width=10).pack(pady=(0, 10))

    def open_friend_sync_ui(self):
        """批量同步 Steam 用户游戏库"""
        if not self._ensure_collections_core():
            return
        data = self._collections_core.load_json()
        if data is None:
            return

        sync_win = tk.Toplevel(self.root)
        sync_win.title("批量同步 Steam 用户游戏库")

        tk.Label(sync_win,
            text="1. 请输入对方的 Steam 好友代码（每行一个）",
            font=("微软雅黑", 10, "bold")).pack(pady=(15, 0))
        codes_text = tk.Text(sync_win, height=8, width=60)
        codes_text.pack(padx=20, pady=5)

        tk.Label(sync_win,
            text="2. 生成的分类名称 (每行一个)",
            font=("微软雅黑", 10, "bold")).pack(pady=(10, 0))
        names_text = tk.Text(sync_win, height=8, width=60)
        names_text.pack(padx=20, pady=5)

        def generate_default_names():
            raw_ids = re.findall(r'\d+', codes_text.get("1.0", "end"))
            names_text.delete("1.0", "end")
            for rid in raw_ids:
                names_text.insert("end", f"好友代码 [{rid}]\n")

        def commit_import():
            codes = re.findall(r'\d+', codes_text.get("1.0", "end"))
            names = [n.strip() for n in
                     names_text.get("1.0", "end").strip().split('\n')
                     if n.strip()]
            for i, cid in enumerate(codes):
                cname = names[i] if i < len(names) else f"好友代码 [{cid}]"
                self._collections_core.add_dynamic_collection(data, cname, cid)
            if codes:
                self._save_and_sync(
                    data, backup_description="同步好友游戏库")
                sync_win.destroy()

        btn_frame = tk.Frame(sync_win)
        btn_frame.pack(pady=20)
        ttk.Button(btn_frame, text="✨ 生成默认名称",
                   command=generate_default_names,
                   width=18).pack(side="left", padx=10)
        ttk.Button(btn_frame, text="开始导入",
                   command=commit_import,
                   width=18).pack(side="left", padx=10)

    def show_batch_update_mapping(self, data, all_cols, sources, on_done,
                                  parent_to_close=None,
                                  saved_mappings_key=None):
        """通用的批量更新映射界面"""
        up_win = tk.Toplevel(self.root)
        up_win.title("批量更新分类")

        tk.Label(up_win, text="请为每个来源选择目标分类和更新模式：",
                 font=("微软雅黑", 10, "bold")).pack(pady=(15, 10))

        mapping_frame = tk.Frame(up_win)
        mapping_frame.pack(fill="both", expand=True, padx=30, pady=(0, 10))

        target_names = ["（跳过）"] + [c['display_name'] for c in all_cols]
        mode_options = ["增量+辅助", "增量", "替换"]
        combo_vars = {}

        # 加载上次保存的映射选择
        saved_mappings = {}
        if saved_mappings_key:
            config = self._collections_core.load_config()
            saved_mappings = config.get(saved_mappings_key, )

        max_target_len = max(len(n) for n in target_names) if target_names else 20

        def _create_row(parent, key, d):
            row_frame = tk.Frame(parent)
            row_frame.pack(fill="x", pady=5)
            display_name = d['name']
            if len(display_name) > 50:
                display_name = display_name[:47] + "…"
            tk.Label(row_frame,
                text=f"📦 {display_name} ({len(d['ids'])} 个)",
                font=("微软雅黑", 9), anchor="w").pack(side="left")
            tk.Label(row_frame, text="→",
                     font=("微软雅黑", 9)).pack(side="left", padx=10)
            combo = ttk.Combobox(row_frame, values=target_names,
                width=max(30, max_target_len + 2), state="readonly")
            last_sel = saved_mappings.get(key, "")
            if last_sel and last_sel in target_names:
                combo.set(last_sel)
            else:
                combo.set("（跳过）")
            combo.pack(side="left")
            mode_combo = ttk.Combobox(row_frame, values=mode_options,
                                       width=6, state="readonly")
            mode_combo.set("增量")
            mode_combo.pack(side="left", padx=(5, 0))
            combo_vars[key] = (combo, mode_combo)
            return row_frame

        if len(sources) <= 8:
            for key, d in sources.items():
                _create_row(mapping_frame, key, d)
        else:
            canvas = tk.Canvas(mapping_frame, height=300)
            scrollbar = ttk.Scrollbar(mapping_frame, orient="vertical",
                                       command=canvas.yview)
            scrollable_frame = tk.Frame(canvas)
            scrollable_frame.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)

            def _on_mw(event):
                if event.delta:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                elif event.num == 4:
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    canvas.yview_scroll(1, "units")

            for w in (canvas, scrollable_frame, up_win):
                w.bind("<MouseWheel>", _on_mw)
                w.bind("<Button-4>", _on_mw)
                w.bind("<Button-5>", _on_mw)
            for key, d in sources.items():
                row = _create_row(scrollable_frame, key, d)
                row.bind("<MouseWheel>", _on_mw)
                row.bind("<Button-4>", _on_mw)
                row.bind("<Button-5>", _on_mw)
            scrollable_frame.update_idletasks()
            canvas.config(width=scrollable_frame.winfo_reqwidth())

        disclaimer = self._collections_core.disclaimer

        def confirm_update():
            update_count = 0
            skipped_count = 0
            results = []

            if saved_mappings_key:
                config = self._collections_core.load_config()
                current_mappings = {}
                for key, (combo, _) in combo_vars.items():
                    sel = combo.get()
                    if sel != "（跳过）":
                        current_mappings[key] = sel
                config[saved_mappings_key] = current_mappings
                self._collections_core.save_config(config)

            for key, (combo, mode_combo) in combo_vars.items():
                selected_display = combo.get()
                if selected_display == "（跳过）":
                    continue
                target = None
                for c in all_cols:
                    if c['display_name'] == selected_display:
                        target = c
                        break
                if not target:
                    continue
                source_data = sources[key]
                mode = mode_combo.get()
                col_id = target.get('id', '')
                if mode == "替换":
                    old_count, new_count = self._collections_core.perform_replace_update(
                        data, target['entry_ref'], source_data['ids'])
                    results.append(
                        f"🔄 {source_data['name']} → {target['name']}\n"
                        f"   替换: {old_count} → {new_count}")
                    update_count += 1
                else:
                    create_aux = (mode == "增量+辅助")
                    a, r, t, updated = self._collections_core.perform_incremental_update(
                        data, target['entry_ref'], source_data['ids'],
                        target['name'], create_aux=create_aux)
                    if updated:
                        results.append(
                            f"✅ {source_data['name']} → {target['name']}\n"
                            f"   新增: {a}, 移除: {r}, 总计: {t}")
                        update_count += 1
                    else:
                        results.append(
                            f"⏭️ {source_data['name']} → {target['name']}\n"
                            "   已是最新，跳过")
                        skipped_count += 1

                # 缓存来源信息（按 col_id 绑定，改名不影响）
                mode_map = {"增量+辅助": "incremental_aux",
                            "增量": "incremental", "替换": "replace"}
                if col_id and source_data.get('source_type'):
                    self._collections_core.save_collection_source(
                        col_id,
                        source_data['source_type'],
                        source_data.get('source_params', {}),
                        source_data.get('name', ''),
                        mode_map.get(mode, 'incremental'))

            if update_count > 0:
                result_text = "\n".join(results)
                messagebox.showinfo("更新完成",
                    f"已更新 {update_count} 个分类，"
                    f"跳过 {skipped_count} 个：\n\n"
                    f"{result_text}" + disclaimer,
                    parent=up_win)
                up_win.destroy()
                if parent_to_close:
                    parent_to_close.destroy()
                on_done()
            elif skipped_count > 0:
                result_text = "\n".join(results)
                messagebox.showinfo("全部已是最新",
                    f"所有选中的分类都已是最新。\n\n{result_text}",
                    parent=up_win)
                up_win.destroy()
            else:
                messagebox.showwarning("提示", "未选择任何目标分类。",
                                       parent=up_win)

        btn_row = tk.Frame(up_win)
        btn_row.pack(pady=15)
        ttk.Button(btn_row, text="✅ 确认更新", command=confirm_update,
                  width=15).pack(side="left", padx=10)
        ttk.Button(btn_row, text="取消", command=up_win.destroy,
                  width=10).pack(side="left", padx=10)

    # ────────────────────── 账号分类一键导入 ──────────────────────

    @staticmethod
    def _parse_account_collections(path):
        """解析 cloud-storage-namespace-1.json，提取所有分类

        Returns: list[dict] 每项含 id, name, app_ids, is_dynamic
        """
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        collections = []
        for entry in data:
            key = entry[0] if isinstance(entry, list) else ""
            meta = entry[1] if isinstance(entry, list) and len(entry) > 1 else {}
            if not key.startswith("user-collections."):
                continue
            if meta.get("is_deleted") is True or "value" not in meta:
                continue
            try:
                val = json.loads(meta['value'])
                col_id = key.replace("user-collections.", "")
                collections.append({
                    "id": col_id,
                    "name": val.get("name", "未命名"),
                    "app_ids": [int(x) for x in val.get("added", [])
                                if str(x).isdigit()],
                    "is_dynamic": "filterSpec" in val,
                })
            except Exception:
                continue
        collections.sort(key=lambda c: c['name'].lower())
        return collections

    def import_from_account(self):
        """从其他 Steam 账号一键导入所有分类"""
        if not self._ensure_collections_core():
            return

        win = tk.Toplevel(self.root)
        win.title("👤 从其他账号导入分类")
        win.resizable(False, False)

        tk.Label(win, text="👤 从其他账号导入分类",
                 font=("", 12, "bold")).pack(pady=(15, 5))
        tk.Label(win, text="选择来源账号或手动指定 JSON 文件",
                 font=("", 9), fg="#666").pack(pady=(0, 10))

        # 扫描其他账号
        all_accounts = SteamAccountScanner.scan_accounts()
        current_fc = self.current_account.get('friend_code', '')
        other_accounts = [a for a in all_accounts
                          if a.friend_code != current_fc]

        frame = tk.Frame(win, padx=20)
        frame.pack(fill=tk.X)

        def _open_from_account(acct):
            storage = acct.storage_path
            if not storage or not os.path.exists(storage):
                messagebox.showwarning("文件不存在",
                    f"未找到该账号的分类文件：\n{storage}",
                    parent=win)
                return
            try:
                colls = self._parse_account_collections(storage)
            except Exception as e:
                messagebox.showerror("解析失败", str(e), parent=win)
                return
            if not colls:
                messagebox.showinfo("提示", "该账号没有分类。", parent=win)
                return
            win.destroy()
            self._show_import_preview(colls, acct.persona_name)

        for acct in other_accounts:
            row = tk.Frame(frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Button(row, text=f"👤 {acct.persona_name}",
                       width=20,
                       command=lambda a=acct: _open_from_account(a)
                       ).pack(side=tk.LEFT)
            tk.Label(row, text=f"ID: {acct.friend_code}",
                     font=("", 8), fg="#888").pack(side=tk.LEFT, padx=8)

        if not other_accounts:
            tk.Label(frame, text="（未发现其他账号）",
                     font=("", 9), fg="#999").pack(pady=5)

        ttk.Separator(win, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=20, pady=8)

        def _open_from_file():
            path = filedialog.askopenfilename(
                title="选择 cloud-storage-namespace-1.json",
                initialdir=self._last_dir('account_import'),
                filetypes=[("JSON", "*.json")],
                parent=win)
            if not path:
                return
            self._save_dir('account_import', path)
            try:
                colls = self._parse_account_collections(path)
            except Exception as e:
                messagebox.showerror("解析失败", str(e), parent=win)
                return
            if not colls:
                messagebox.showinfo("提示", "文件中没有分类。", parent=win)
                return
            win.destroy()
            source_name = os.path.basename(os.path.dirname(
                os.path.dirname(os.path.dirname(path))))
            self._show_import_preview(colls, source_name or "文件")

        ttk.Button(win, text="📁 从文件选择...",
                   command=_open_from_file).pack(padx=20, pady=(0, 5))
        ttk.Button(win, text="关闭",
                   command=win.destroy).pack(pady=(5, 15))
        self._center_window(win)

    def _show_import_preview(self, collections, source_name):
        """显示导入预览窗口：勾选要导入的分类"""
        win = tk.Toplevel(self.root)
        win.title(f"导入预览 — 来自 {source_name}")

        tk.Label(win, text=f"来源：{source_name}  |  共 {len(collections)} 个分类",
                 font=("", 11, "bold")).pack(pady=(15, 5))

        # 获取当前账号已有的分类名称（用于冲突检测）
        existing_names = set()
        try:
            userdata = self.current_account.get('userdata_path', '')
            for c in SteamAccountScanner.get_collections(userdata):
                existing_names.add(c.get('name', ''))
        except Exception:
            pass

        # 冲突模式
        conflict_frame = tk.Frame(win, padx=20)
        conflict_frame.pack(fill=tk.X)
        tk.Label(conflict_frame, text="同名分类处理：",
                 font=("", 9)).pack(side=tk.LEFT)
        conflict_var = tk.StringVar(value="skip")
        ttk.Radiobutton(conflict_frame, text="跳过", variable=conflict_var,
                         value="skip").pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(conflict_frame, text="合并", variable=conflict_var,
                         value="merge").pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(conflict_frame, text="新建副本", variable=conflict_var,
                         value="copy").pack(side=tk.LEFT, padx=4)

        # 列表区域（带滚动条）
        list_frame = tk.Frame(win)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)

        canvas = tk.Canvas(list_frame, height=350)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical",
                                   command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def _on_mw(event):
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")
        for w in (canvas, inner, win):
            w.bind("<MouseWheel>", _on_mw)
            w.bind("<Button-4>", _on_mw)
            w.bind("<Button-5>", _on_mw)

        check_vars = []
        for coll in collections:
            var = tk.BooleanVar(value=True)
            check_vars.append(var)
            row = tk.Frame(inner)
            row.pack(fill=tk.X, pady=1)
            row.bind("<MouseWheel>", _on_mw)

            is_dup = coll['name'] in existing_names
            icon = "🔄" if coll['is_dynamic'] else "📁"
            dup_mark = " ⚠️同名" if is_dup else ""
            text = f"{icon} {coll['name']} ({len(coll['app_ids'])}){dup_mark}"

            cb = ttk.Checkbutton(row, text=text, variable=var)
            cb.pack(side=tk.LEFT)
            if is_dup:
                cb.configure(style="Warning.TCheckbutton")

        # 统计标签
        stat_var = tk.StringVar()

        def _update_stat(*_):
            n = sum(1 for v in check_vars if v.get())
            dup = sum(1 for v, c in zip(check_vars, collections)
                      if v.get() and c['name'] in existing_names)
            stat_var.set(f"已选 {n}/{len(collections)}"
                         + (f"（{dup} 个同名）" if dup else ""))
        for v in check_vars:
            v.trace_add("write", _update_stat)
        _update_stat()

        stat_label = tk.Label(win, textvariable=stat_var,
                              font=("", 9), fg="#666")
        stat_label.pack(pady=(0, 5))

        # 按钮
        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(0, 15))

        def _select_all():
            for v in check_vars:
                v.set(True)

        def _deselect_all():
            for v in check_vars:
                v.set(False)

        def _do_import():
            selected = [c for c, v in zip(collections, check_vars) if v.get()]
            if not selected:
                messagebox.showwarning("提示", "请至少选择一个分类。",
                                       parent=win)
                return
            win.destroy()
            self._execute_bulk_import(selected, conflict_var.get(),
                                       existing_names)

        ttk.Button(btn_frame, text="全选",
                   command=_select_all).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="取消全选",
                   command=_deselect_all).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="✅ 开始导入",
                   command=_do_import).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_frame, text="取消",
                   command=win.destroy).pack(side=tk.LEFT, padx=3)

        self._center_window(win)

    def _execute_bulk_import(self, collections, conflict_mode, existing_names):
        """执行批量导入：本地创建 + 分批云同步（带进度条）"""
        data = self._collections_core.load_json()
        if data is None:
            return

        # 合并模式：获取已有分类 name→(entry, val) 映射
        existing_map = {}
        if conflict_mode == 'merge':
            for entry in data:
                key = entry[0] if isinstance(entry, list) else ""
                meta = entry[1] if isinstance(entry, list) and len(entry) > 1 else {}
                if not key.startswith("user-collections."):
                    continue
                if meta.get("is_deleted") or "value" not in meta:
                    continue
                try:
                    val = json.loads(meta['value'])
                    existing_map[val.get('name', '')] = (entry, val)
                except Exception:
                    pass

        created_ids = []
        skipped = 0
        merged = 0

        for coll in collections:
            name = coll['name']
            app_ids = coll['app_ids']
            is_dup = name in existing_names

            if is_dup:
                if conflict_mode == 'skip':
                    skipped += 1
                    continue
                elif conflict_mode == 'merge' and name in existing_map:
                    entry, val = existing_map[name]
                    old_set = set(val.get('added', []))
                    combined = list(old_set | set(app_ids))
                    if len(combined) > len(old_set):
                        val['added'] = combined
                        entry[1]['value'] = json.dumps(
                            val, ensure_ascii=False, separators=(',', ':'))
                        import time as _time
                        entry[1]['timestamp'] = int(_time.time())
                        entry[1]['version'] = \
                            self._collections_core.next_version(data)
                        col_id = val.get('id', '')
                        if col_id:
                            self._collections_core.queue_cef_upsert(
                                col_id, name, combined)
                            created_ids.append(col_id)
                    merged += 1
                    continue
                else:  # copy
                    name = name + " (导入)"

            if coll['is_dynamic']:
                skipped += 1
                continue

            col_id = self._collections_core.add_static_collection(
                data, name, app_ids)
            created_ids.append(col_id)

        # 保存本地文件
        self._collections_core.save_json(
            data, backup_description="账号分类批量导入")

        total_created = len(created_ids)
        if total_created == 0:
            messagebox.showinfo("导入完成",
                f"跳过 {skipped} 个，合并 {merged} 个，无新建分类。",
                parent=self.root)
            self._ui_refresh()
            return

        # CEF 云同步
        cef_ops = self._collections_core.pop_pending_cef_ops()
        if not cef_ops or not self._cef_bridge or \
                not self._cef_bridge.is_connected():
            self._ui_refresh()
            msg = f"✅ 本地导入完成：新建 {total_created} 个"
            if skipped:
                msg += f"，跳过 {skipped} 个"
            if merged:
                msg += f"，合并 {merged} 个"
            if not self._cef_bridge or not self._cef_bridge.is_connected():
                msg += "\n\n⚠️ CEF 未连接，云同步将由 Steam 后台完成。"
            messagebox.showinfo("导入完成", msg, parent=self.root)
            return

        self._do_bulk_import_sync(
            cef_ops, total_created, skipped, merged)

    def _do_bulk_import_sync(self, cef_ops, total_created, skipped, merged):
        """分批 CEF 云同步，带进度条（后台线程）"""
        total = len(cef_ops)

        pw = ProgressWindow(self.root, "☁️ 正在同步到 Steam 云端",
            f"☁️ 正在同步 {total} 个分类到云端...",
            maximum=total, grab=True)
        pw.win.update_idletasks()
        self._center_window(pw.win)

        def progress_cb(current, total_n, name, text):
            pw.update(value=current, status=text)

        def sync_thread():
            bridge = self._cef_bridge
            if not bridge or not bridge.is_connected():
                self.root.after(0, pw.close)
                return
            success, fail, errors = bridge.batch_sync_collections(
                cef_ops, progress_callback=progress_cb)

            def finish():
                pw.close()

                self._ui_refresh()

                parts = [f"新建 {total_created} 个"]
                if skipped:
                    parts.append(f"跳过 {skipped} 个")
                if merged:
                    parts.append(f"合并 {merged} 个")

                if fail == 0:
                    messagebox.showinfo("✅ 导入完成",
                        f"{'，'.join(parts)}。\n"
                        f"云同步全部成功（{success}/{total}）。",
                        parent=self.root)
                else:
                    err_text = "\n".join(errors[:10])
                    messagebox.showwarning("⚠️ 导入完成（部分同步失败）",
                        f"{'，'.join(parts)}。\n"
                        f"云同步：成功 {success}，失败 {fail}。\n\n"
                        f"{err_text}\n\n"
                        "失败的部分会由 Steam 后台自动同步。",
                        parent=self.root)

            self.root.after(0, finish)

        threading.Thread(target=bg_thread(sync_thread), daemon=True).start()
