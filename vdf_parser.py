"""VDF 解析工具 — 从 remotecache.vdf 解析笔记文件同步状态

从 ui_app.py 分离，使 VDF 解析逻辑可独立测试。
"""

import os
import re


def parse_remotecache_syncstates(notes_dir: str) -> dict:
    """解析 remotecache.vdf 获取每个笔记文件的 syncstate

    Args:
        notes_dir: 笔记目录路径，如 .../userdata/<uid>/2371090/remote/
                   remotecache.vdf 位于其父目录

    Returns:
        {app_id: syncstate_int}
        例如 {'570': 3} 表示 notes_570 正在上传
        syncstate=1 表示已同步，syncstate=3 表示上传中
    """
    if not notes_dir:
        return {}
    vdf_path = os.path.join(os.path.dirname(notes_dir), 'remotecache.vdf')
    if not os.path.isfile(vdf_path):
        return {}
    try:
        with open(vdf_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except (IOError, OSError):
        return {}

    result = {}
    # 简易 VDF 解析：提取每个 "notes_<appid>" 块中的 "syncstate" 值
    block_pat = re.compile(
        r'"(notes_(?:shortcut_)?[^"]+)"\s*\{([^}]*)\}', re.DOTALL)
    sync_pat = re.compile(r'"syncstate"\s+"(\d+)"')
    for m in block_pat.finditer(content):
        fname = m.group(1)  # e.g. "notes_570"
        block = m.group(2)
        sm = sync_pat.search(block)
        if sm:
            syncstate = int(sm.group(1))
            # 从文件名提取 app_id（去掉 notes_ 前缀）
            if fname.startswith("notes_"):
                aid = fname[6:]  # "notes_570" → "570"
            else:
                continue
            result[aid] = syncstate
    return result


def is_app_uploading(notes_dir: str, app_id: str,
                     cached_syncstates: dict = None) -> bool:
    """判断指定 app_id 的笔记是否正在上传中（syncstate=3）

    Args:
        notes_dir: 笔记目录路径
        app_id: 游戏 AppID
        cached_syncstates: 可选，已缓存的 syncstate 字典。
                           如果提供则直接查询，避免重复解析 VDF 文件。
    """
    if cached_syncstates is not None:
        return cached_syncstates.get(app_id) == 3
    syncstates = parse_remotecache_syncstates(notes_dir)
    return syncstates.get(app_id) == 3
