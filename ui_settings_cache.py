"""ui_settings_cache.py — 缓存管理

从 ui_settings.py 拆分。
"""

import os
import tkinter as tk
from tkinter import messagebox, ttk


def build_cache_manager_ui(app):
    """弹出本地缓存数据管理窗口"""
    cache_win = tk.Toplevel(app.root)
    cache_win.title("🗑️ 本地缓存管理")
    cache_win.resizable(False, False)
    cache_win.transient(app.root)

    tk.Label(cache_win, text="本地缓存数据管理",
             font=("", 12, "bold")).pack(padx=20, pady=(15, 5))
    tk.Label(cache_win, text="缓存数据存储在配置文件中，清理后将在下次使用时重建。",
             font=("", 9), fg="#666").pack(padx=20, pady=(0, 10))

    info_frame = tk.Frame(cache_win, padx=15)
    info_frame.pack(fill=tk.X)

    # 配置文件路径和大小
    config_path = app._CONFIG_FILE
    try:
        config_size = os.path.getsize(config_path) if os.path.exists(config_path) else 0
    except Exception:
        config_size = 0
    size_str = (f"{config_size / 1024 / 1024:.1f} MB" if config_size > 1024 * 1024
                else f"{config_size / 1024:.1f} KB" if config_size > 1024
                else f"{config_size} B")

    path_label = tk.Label(info_frame,
                          text=f"📂 {config_path}  ({size_str})",
                          font=("", 8), fg="#888", cursor="hand2")
    path_label.pack(anchor=tk.W, pady=(0, 8))
    path_label.bind("<Button-1>",
                    lambda e: app._open_directory(app._CONFIG_DIR))

    # 游戏名称缓存
    name_cache = app._config.get("game_name_cache", {})
    name_count = len(name_cache)
    row1 = tk.Frame(info_frame)
    row1.pack(fill=tk.X, pady=2)
    tk.Label(row1, text=f"🎮 游戏名称缓存: {name_count} 条",
             font=("", 10)).pack(side=tk.LEFT)

    def _clear_name_cache():
        app._config.pop("game_name_cache", None)
        app._config.pop("game_name_bulk_cache_ts", None)
        app._game_name_cache = {}
        app._game_name_cache_loaded = False
        app._save_config(app._config)
        name_count_lbl.config(text="0 条")
        _refresh_size()
        messagebox.showinfo("✅", "游戏名称缓存已清除", parent=cache_win)

    ttk.Button(row1, text="清除", width=5,
               command=_clear_name_cache).pack(side=tk.RIGHT)
    name_count_lbl = tk.Label(row1, text="", font=("", 9), fg="#888")

    # 后台获取进度（Store API resolver）
    row_resolve = tk.Frame(info_frame)
    row_resolve.pack(fill=tk.X, pady=2)
    resolve_label = tk.Label(row_resolve, text="", font=("", 9), fg="#4a90d9")
    resolve_label.pack(side=tk.LEFT)
    resolve_bar = ttk.Progressbar(row_resolve, mode='determinate', length=120)
    resolve_bar.pack(side=tk.RIGHT, padx=(0, 5))

    def _count_missing_release():
        games = getattr(app, '_lib_all_games', [])
        dc = getattr(app, '_app_detail_cache', {})
        return sum(1 for g in games
                   if not g.get('rt_release')
                   and str(g['app_id']) not in dc)

    def _trigger_resolve():
        if getattr(app, '_resolve_thread_running', False):
            return
        app._bg_resolve_owned_release_dates()
        cache_win.after(500, _poll_resolve)

    resolve_btn = ttk.Button(row_resolve, text="▶ 补查", width=6,
                             command=_trigger_resolve)

    def _poll_resolve():
        if not cache_win.winfo_exists():
            return
        p = getattr(app, '_resolve_progress', (0, 0))
        running = getattr(app, '_resolve_thread_running', False)
        if running and p[1] > 0:
            resolve_label.config(
                text=f"🔍 后台获取中: {p[0]}/{p[1]}")
            resolve_bar.config(maximum=p[1], value=p[0])
            resolve_bar.pack(side=tk.RIGHT, padx=(0, 5))
            resolve_btn.pack_forget()
        else:
            missing = _count_missing_release()
            if missing > 0:
                resolve_label.config(
                    text=f"📅 {missing} 个游戏缺发行日期")
                resolve_bar.pack_forget()
                resolve_btn.pack(side=tk.RIGHT, padx=(0, 5))
            else:
                resolve_label.config(text="✅ 详情缓存完整")
                resolve_bar.pack_forget()
                resolve_btn.pack_forget()
        if running:
            cache_win.after(1500, _poll_resolve)

    _poll_resolve()

    # 游戏类型缓存
    type_cache = app._config.get("app_type_cache", {})
    type_count = len(type_cache)
    row_type = tk.Frame(info_frame)
    row_type.pack(fill=tk.X, pady=2)
    tk.Label(row_type, text=f"🏷️ 游戏类型缓存: {type_count} 条",
             font=("", 10)).pack(side=tk.LEFT)

    def _clear_type_cache():
        app._config.pop("app_type_cache", None)
        app._app_type_cache = {}
        app._save_config(app._config)
        _refresh_size()
        messagebox.showinfo("✅", "游戏类型缓存已清除", parent=cache_win)

    ttk.Button(row_type, text="清除", width=5,
               command=_clear_type_cache).pack(side=tk.RIGHT)

    # 游戏详情缓存
    detail_cache = app._config.get("app_detail_cache", {})
    detail_count = len(detail_cache)
    row_detail = tk.Frame(info_frame)
    row_detail.pack(fill=tk.X, pady=2)
    tk.Label(row_detail, text=f"📋 游戏详情缓存: {detail_count} 条",
             font=("", 10)).pack(side=tk.LEFT)

    def _clear_detail_cache():
        app._config.pop("app_detail_cache", None)
        app._app_detail_cache = {}
        app._save_config(app._config)
        _refresh_size()
        messagebox.showinfo("✅", "游戏详情缓存已清除", parent=cache_win)

    ttk.Button(row_detail, text="清除", width=5,
               command=_clear_detail_cache).pack(side=tk.RIGHT)

    # 上传哈希记录
    hash_keys = [k for k in app._config if k.startswith("uploaded_hashes_")]
    total_hashes = sum(len(app._config.get(k, {})) for k in hash_keys)
    row2 = tk.Frame(info_frame)
    row2.pack(fill=tk.X, pady=2)
    tk.Label(row2, text=f"☁️ 上传哈希记录: {total_hashes} 条 ({len(hash_keys)} 个账号)",
             font=("", 10)).pack(side=tk.LEFT)

    def _clear_upload_hashes():
        for k in list(app._config.keys()):
            if k.startswith("uploaded_hashes_"):
                del app._config[k]
        app._save_config(app._config)
        # 重建当前 manager 的 dirty 状态
        if app.manager:
            app.manager._uploaded_hashes = {}
            app.manager._dirty_apps = set()
            app.manager._rebuild_dirty_from_hashes()
        _refresh_size()
        messagebox.showinfo("✅", "上传哈希记录已清除（所有笔记将标记为需上传）",
                            parent=cache_win)

    ttk.Button(row2, text="清除", width=5,
               command=_clear_upload_hashes).pack(side=tk.RIGHT)

    # 免费游戏缓存
    free_cache = app._config.get("free_apps_cache", {})
    free_count = len(free_cache)
    row3 = tk.Frame(info_frame)
    row3.pack(fill=tk.X, pady=2)
    tk.Label(row3, text=f"🆓 免费游戏缓存: {free_count} 条",
             font=("", 10)).pack(side=tk.LEFT)

    def _clear_free_cache():
        app._config.pop("free_apps_cache", None)
        app._save_config(app._config)
        _refresh_size()
        messagebox.showinfo("✅", "免费游戏缓存已清除", parent=cache_win)

    ttk.Button(row3, text="清除", width=5,
               command=_clear_free_cache).pack(side=tk.RIGHT)

    # 家庭库扫描缓存
    flib_cache = app._config.get("family_library_cache", {})
    flib_games = len(flib_cache.get("library_games", []))
    flib_family = len(flib_cache.get("family_owned_ids", []))
    row3b = tk.Frame(info_frame)
    row3b.pack(fill=tk.X, pady=2)
    flib_text = (f"👨‍👩‍👧‍👦 家庭库缓存: {flib_games} 款游戏，家庭库 {flib_family} 款"
                 if flib_cache else "👨‍👩‍👧‍👦 家庭库缓存: 无")
    tk.Label(row3b, text=flib_text, font=("", 10)).pack(side=tk.LEFT)

    def _clear_family_lib_cache():
        app._config.pop("family_library_cache", None)
        app._save_config(app._config)
        _refresh_size()
        messagebox.showinfo("✅", "家庭库缓存已清除（下次打开 AI 生成窗口将重新扫描）",
                            parent=cache_win)

    ttk.Button(row3b, text="清除", width=5,
               command=_clear_family_lib_cache).pack(side=tk.RIGHT)

    # 收藏夹来源缓存（按账号隔离）
    _src_key = f"collection_sources_{app.current_account.get('friend_code', 'unknown')}"
    col_sources = app._config.get(_src_key, {})
    source_count = len(col_sources)
    row3c = tk.Frame(info_frame)
    row3c.pack(fill=tk.X, pady=2)
    tk.Label(row3c, text=f"🔗 收藏夹来源缓存: {source_count} 个",
             font=("", 10)).pack(side=tk.LEFT)

    def _view_sources():
        """查看并清理来源缓存"""
        src_win = tk.Toplevel(cache_win)
        src_win.title("🔗 收藏夹来源缓存")
        src_win.resizable(False, False)

        tk.Label(src_win, text="🔗 收藏夹来源缓存",
                 font=("", 12, "bold")).pack(pady=(15, 5))
        tk.Label(src_win, text="带 🔗 标记的收藏夹可右键一键更新",
                 font=("", 9), fg="#666").pack(pady=(0, 10))

        sources = app._config.get(_src_key, {})
        if not sources:
            tk.Label(src_win, text="（暂无来源缓存）",
                     font=("", 10), fg="#999").pack(padx=20, pady=10)
        else:
            list_frame = tk.Frame(src_win, padx=15)
            list_frame.pack(fill=tk.BOTH, expand=True)

            mode_labels = {"incremental_aux": "增量+辅助",
                           "incremental": "增量", "replace": "替换"}
            type_labels = {"steam250": "Steam250",
                           "curator": "Steam 列表",
                           "igdb_category": "IGDB 分类",
                           "igdb_company": "IGDB 公司"}

            for col_id, info in sources.items():
                row = tk.Frame(list_frame)
                row.pack(fill=tk.X, pady=1)
                src_t = type_labels.get(
                    info.get("source_type", ""), "未知")
                mode_t = mode_labels.get(
                    info.get("update_mode", ""), "?")
                disp = info.get("source_display_name", "")
                tk.Label(row, text=f"🔗 {disp}",
                         font=("", 9), anchor=tk.W).pack(
                             side=tk.LEFT, fill=tk.X, expand=True)
                tk.Label(row, text=f"{src_t} | {mode_t}",
                         font=("", 8), fg="#888").pack(side=tk.RIGHT)

        btn_row = tk.Frame(src_win)
        btn_row.pack(pady=(10, 15))

        def _cleanup_orphans():
            """清理指向已删除收藏夹的孤立来源缓存"""
            sources = app._config.get(_src_key, {})
            if not sources:
                messagebox.showinfo("提示", "没有来源缓存。",
                                    parent=src_win)
                return
            # 读取实际存在的收藏夹 ID
            existing_ids = set()
            if hasattr(app, '_collections_core') and app._collections_core:
                data = app._collections_core.load_json()
                if data:
                    import json as _json
                    for entry in data:
                        if entry[0].startswith("user-collections."):
                            meta = entry[1]
                            if not meta.get("is_deleted") and "value" in meta:
                                try:
                                    val = _json.loads(meta['value'])
                                    existing_ids.add(val.get('id', ''))
                                except Exception:
                                    pass

            orphans = [cid for cid in sources if cid not in existing_ids]
            if not orphans:
                messagebox.showinfo("✅", "没有孤立的来源缓存。",
                                    parent=src_win)
                return
            if messagebox.askyesno("确认",
                    f"发现 {len(orphans)} 个孤立缓存"
                    f"（对应收藏夹已删除），是否清理？",
                    parent=src_win):
                for cid in orphans:
                    del sources[cid]
                app._config[_src_key] = sources
                app._save_config(app._config)
                messagebox.showinfo("✅",
                    f"已清理 {len(orphans)} 个孤立缓存。",
                    parent=src_win)
                src_win.destroy()

        ttk.Button(btn_row, text="🧹 清理孤立缓存",
                   command=_cleanup_orphans).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="关闭",
                   command=src_win.destroy).pack(side=tk.LEFT, padx=5)
        app._center_window(src_win)

    def _clear_sources():
        if not col_sources:
            return
        if messagebox.askyesno("确认",
                f"确定清除全部 {source_count} 个来源缓存？\n"
                "清除后收藏夹的 🔗 标记和一键更新功能将失效。",
                parent=cache_win):
            app._config.pop(_src_key, None)
            app._save_config(app._config)
            _refresh_size()
            messagebox.showinfo("✅", "来源缓存已清除。",
                                parent=cache_win)

    ttk.Button(row3c, text="查看", style="Toolbutton",
               command=_view_sources).pack(side=tk.RIGHT, padx=(0, 3))
    ttk.Button(row3c, text="清除", width=5,
               command=_clear_sources).pack(side=tk.RIGHT)

    # AI 令牌配置（不可清除，仅展示）
    tokens = app._config.get("ai_tokens", [])
    family_codes = app._config.get("family_friend_codes", [])
    row4 = tk.Frame(info_frame)
    row4.pack(fill=tk.X, pady=2)
    tk.Label(row4, text=f"🔑 AI 令牌: {len(tokens)} 个  |  "
                       f"👨‍👩‍👧‍👦 家庭组: {len(family_codes)} 人",
             font=("", 10), fg="#555").pack(side=tk.LEFT)

    # 大小刷新
    size_label = tk.Label(info_frame, text="", font=("", 9), fg="#888")
    size_label.pack(anchor=tk.W, pady=(8, 0))

    def _refresh_size():
        try:
            s = os.path.getsize(config_path) if os.path.exists(config_path) else 0
        except Exception:
            s = 0
        ss = (f"{s / 1024 / 1024:.1f} MB" if s > 1024 * 1024
              else f"{s / 1024:.1f} KB" if s > 1024 else f"{s} B")
        size_label.config(text=f"当前配置文件大小: {ss}")
        path_label.config(text=f"📂 {config_path}  ({ss})")

    _refresh_size()

    # 清除全部
    btn_frame = tk.Frame(cache_win)
    btn_frame.pack(pady=(10, 15))

    def _clear_all():
        if not messagebox.askyesno("确认",
                "确定要清除所有缓存数据？\n（AI 令牌和家庭组配置不会被清除）",
                parent=cache_win):
            return
        _clear_name_cache()
        _clear_type_cache()
        _clear_detail_cache()
        for k in list(app._config.keys()):
            if k.startswith("uploaded_hashes_"):
                del app._config[k]
        app._config.pop("free_apps_cache", None)
        app._config.pop("family_library_cache", None)
        app._save_config(app._config)
        if app.manager:
            app.manager._uploaded_hashes = {}
            app.manager._dirty_apps = set()
            app.manager._rebuild_dirty_from_hashes()
        _refresh_size()
        messagebox.showinfo("✅", "所有缓存已清除", parent=cache_win)

    ttk.Button(btn_frame, text="🗑️ 清除全部缓存",
               command=_clear_all).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="关闭",
               command=cache_win.destroy).pack(side=tk.LEFT, padx=5)

    app._center_window(cache_win)
