"""核心业务逻辑 — 笔记读写、常量、AI 笔记识别工具函数"""

import hashlib
import json
import os
import random
import re
import time
from datetime import datetime

from cloud_uploader import SteamCloudUploader
from utils import sanitize_filename


NOTES_APPID = "2371090"

# AI 生成笔记的固定前缀标志 — 用于识别哪些笔记是 AI 处理过的
AI_NOTE_PREFIX = "🤖AI:"
# 旧版前缀关键词（v2.x 使用），仍需识别
AI_NOTE_LEGACY_KEYWORD = "以下内容由"

def is_ai_note(note: dict) -> bool:
    """检测一条笔记是否为 AI 生成
    核心逻辑：标题中包含「以下内容由...生成」即为 AI 笔记。
    也兼容新版 🤖AI: 前缀。
    """
    title = note.get("title", "")
    if not title:
        return False
    # 最可靠的方式：只要标题里出现"以下内容由"就是 AI 笔记
    if AI_NOTE_LEGACY_KEYWORD in title and "生成" in title:
        return True
    # 新版前缀（去掉变体选择符后匹配）
    clean = title.replace('\ufe0e', '').replace('\ufe0f', '')
    if clean.startswith("🤖AI:"):
        return True
    return False

def extract_ai_model_from_note(note: dict) -> str:
    """从 AI 笔记标题中提取模型名。
    找「以下内容由 XXX 生成」中的 XXX，这就是模型名。
    """
    title = note.get("title", "")
    if not title:
        return ""
    m = re.search(r'以下内容由\s*(.+?)\s*生成', title)
    return m.group(1).strip() if m else ""

def extract_ai_confidence_from_note(note: dict) -> str:
    """从 AI 笔记标题中提取确信程度。
    找「确信程度：X」中的 X（很高/较高/中等/较低/很低）。
    """
    title = note.get("title", "")
    if not title:
        return ""
    m = re.search(r'确信程度[：:]\s*(很高|较高|中等|较低|很低)', title)
    return m.group(1) if m else ""

def extract_ai_info_volume_from_note(note: dict) -> str:
    """从 AI 笔记标题中提取信息量等级。
    找「相关信息量：X」中的 X（相当多/较多/中等/较少/相当少）。
    v5.7+ 新增，旧版笔记返回空字符串。
    """
    title = note.get("title", "")
    if not title:
        return ""
    m = re.search(r'相关信息量[：:]\s*(相当多|较多|中等|较少|相当少)', title)
    return m.group(1) if m else ""

def extract_ai_info_source_from_note(note: dict) -> str:
    """从 AI 笔记标题中提取信息来源类型。
    返回 "web" (联网检索), "local" (训练数据与Steam评测), 或 "" (旧版笔记)。
    """
    title = note.get("title", "")
    if not title:
        return ""
    if INFO_SOURCE_WEB in title or "联网检索" in title:
        return "web"
    if INFO_SOURCE_LOCAL in title or "训练数据" in title:
        return "local"
    return ""

def extract_ai_quality_from_note(note: dict) -> str:
    """从 AI 笔记标题中提取游戏总体质量评估。
    找「游戏总体质量：X」或旧版「总体质量：X」中的 X（相当好/较好/中等/较差/相当差）。
    v5.9+ 新增，旧版笔记返回空字符串。
    """
    title = note.get("title", "")
    if not title:
        return ""
    m = re.search(r'(?:游戏)?总体质量[：:]\s*(相当好|较好|中等|较差|相当差)', title)
    return m.group(1) if m else ""

def is_insufficient_info_note(note: dict) -> bool:
    """检测是否为"信息过少"标注性笔记。"""
    title = note.get("title", "")
    return INSUFFICIENT_INFO_MARKER in title


# AI 确信度对应 emoji（用于列表显示，直观表示 AI 自评可靠程度）
CONFIDENCE_EMOJI = {
    "很高": "🟢",
    "较高": "🔵",
    "中等": "🟡",
    "较低": "🟠",
    "很低": "🔴",
}

# 信息量等级对应 emoji（用于标注参考信息的充足程度）
INFO_VOLUME_EMOJI = {
    "相当多": "🟢",
    "较多": "🔵",
    "中等": "🟡",
    "较少": "🟠",
    "相当少": "🔴",
}

# 游戏总体质量评估 emoji（与确信度/信息量使用不同体系，便于直观区分）
QUALITY_EMOJI = {
    "相当好": "💎",
    "较好": "✨",
    "中等": "➖",
    "较差": "👎",
    "相当差": "💀",
}

# 信息来源标签
INFO_SOURCE_WEB = "📡联网检索"
INFO_SOURCE_LOCAL = "📚训练数据与Steam评测"

# 信息源故障标注（用于笔记中标记哪个信息源不可用，可搜索"不可用"筛选）
WARN_STEAM_UNAVAIL = "[⚠️Steam商店不可用]"
WARN_GOOGLE_UNAVAIL = "[⚠️Google不可用]"
WARN_AITOOL_UNAVAIL = "[⚠️联网工具不可用]"

# 信息过少标记关键词（用于识别信息不足的标注性笔记）
INSUFFICIENT_INFO_MARKER = "⛔信息过少"


class SteamNotesManager:
    """Steam 笔记的核心读写逻辑"""

    def __init__(self, notes_dir: str, cloud_uploader: SteamCloudUploader = None,
                 uploaded_hashes: dict = None):
        self.notes_dir = notes_dir
        self.cloud_uploader = cloud_uploader
        self._dirty_apps = set()  # 有本地改动但尚未上传至云的 app_id 集合
        self._uploaded_hashes = uploaded_hashes or {}  # {app_id: md5} 持久化上传记录
        # 扫描缓存：{app_id: {mtime, note_count, notes, ai_info}}
        self._scan_cache = {}
        # 启动时根据持久化哈希重建 dirty 状态
        self._rebuild_dirty_from_hashes()

    @staticmethod
    def _gen_id():
        """生成 8 位随机十六进制 ID，与 Steam 原生格式一致"""
        return ''.join(random.choices('0123456789abcdef', k=8))

    @staticmethod
    def _wrap_content(text: str) -> str:
        """将纯文本包裹为 [p]...[/p] 格式（如果尚未包裹）"""
        stripped = text.strip()
        # 如果已经包含富文本标签，不做处理
        if stripped.startswith('[p]') or stripped.startswith('[h1]') or \
           stripped.startswith('[h2]') or stripped.startswith('[h3]') or \
           stripped.startswith('[list]') or stripped.startswith('[olist]'):
            return stripped
        # 按段落分割并包裹
        paragraphs = stripped.split('\n\n')
        wrapped = []
        for p in paragraphs:
            p = p.strip()
            if p:
                wrapped.append(f'[p]{p}[/p]')
        return ''.join(wrapped) if wrapped else f'[p]{stripped}[/p]'

    def _build_entry(self, app_id: str, title: str, content: str) -> dict:
        """构建一条符合 Steam 原生格式的笔记条目"""
        now = int(time.time())
        return {
            "id": self._gen_id(),
            "appid": int(app_id) if app_id.isdigit() else app_id,
            "ordinal": 0,
            "time_created": now,
            "time_modified": now,
            "title": title,
            "content": self._wrap_content(content),
        }

    def _get_note_file(self, app_id: str) -> str:
        return os.path.join(self.notes_dir, f"notes_{app_id}")

    def read_notes(self, app_id: str) -> dict:
        """读取指定游戏的笔记文件"""
        path = self._get_note_file(app_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError, OSError) as e:
                # 文件损坏：备份后返回空，防止后续写入覆盖导致永久丢失
                try:
                    import shutil
                    shutil.copy2(path, path + ".corrupt")
                except Exception:
                    pass
                print(f"[笔记] ⚠️ notes_{app_id} 解析失败已备份: {e}")
        return {"notes": []}

    def read_notes_cached(self, app_id: str) -> dict:
        """从扫描缓存读取笔记数据，缓存未命中时回退到磁盘读取"""
        cached = self._scan_cache.get(app_id)
        if cached and 'notes' in cached:
            return {"notes": cached['notes']}
        return self.read_notes(app_id)

    def _scan_single_file(self, app_id: str, filepath: str) -> dict:
        """扫描单个笔记文件，返回缓存条目（含笔记数据 + AI 信息）"""
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            return None
        cached = self._scan_cache.get(app_id)
        if cached and cached.get('mtime') == mtime:
            return cached
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, IOError, OSError):
            return None
        notes = data.get("notes", [])
        # 提取 AI 信息（与 scan_ai_notes 相同逻辑）
        ai_info = self._extract_ai_info(notes)
        entry = {
            'mtime': mtime,
            'note_count': len(notes),
            'file_path': filepath,
            'notes': notes,
            'ai_info': ai_info,
        }
        self._scan_cache[app_id] = entry
        return entry

    @staticmethod
    def _extract_ai_info(notes: list) -> dict | None:
        """从笔记列表中提取 AI 元数据（模型、确信度、信息量等）"""
        models, indices, confidences = [], [], []
        info_volumes, info_sources, qualities = [], [], []
        has_insufficient = False
        for i, note in enumerate(notes):
            if not is_ai_note(note):
                continue
            model = extract_ai_model_from_note(note)
            if model and model not in models:
                models.append(model)
            conf = extract_ai_confidence_from_note(note)
            if conf and conf not in confidences:
                confidences.append(conf)
            vol = extract_ai_info_volume_from_note(note)
            if vol and vol not in info_volumes:
                info_volumes.append(vol)
            src = extract_ai_info_source_from_note(note)
            if src and src not in info_sources:
                info_sources.append(src)
            qual = extract_ai_quality_from_note(note)
            if qual and qual not in qualities:
                qualities.append(qual)
            if is_insufficient_info_note(note):
                has_insufficient = True
            indices.append(i)
        if not indices:
            return None
        return {
            'models': models, 'note_indices': indices,
            'note_count': len(indices), 'confidences': confidences,
            'info_volumes': info_volumes, 'info_sources': info_sources,
            'qualities': qualities, 'has_insufficient': has_insufficient,
        }

    def scan_all(self) -> tuple:
        """单次遍历目录，返回 (notes_games, ai_notes_map)。
        使用 mtime 缓存跳过未变化的文件。
        """
        notes_games = {}
        ai_notes_map = {}
        if not os.path.exists(self.notes_dir):
            return notes_games, ai_notes_map
        current_aids = set()
        for f in os.listdir(self.notes_dir):
            if not f.startswith("notes_"):
                continue
            fp = os.path.join(self.notes_dir, f)
            if not os.path.isfile(fp):
                continue
            app_id = f[6:]  # strip "notes_" prefix
            if "::" in app_id:
                continue  # 跳过脏文件（如 notes_525480::lazy）
            current_aids.add(app_id)
            entry = self._scan_single_file(app_id, fp)
            if entry is None:
                continue
            notes_games[app_id] = {
                'app_id': app_id,
                'note_count': entry['note_count'],
                'file_path': entry['file_path'],
            }
            if entry['ai_info']:
                ai_notes_map[app_id] = entry['ai_info']
        # 清理已删除文件的缓存
        for stale in set(self._scan_cache) - current_aids:
            del self._scan_cache[stale]
        return notes_games, ai_notes_map

    def invalidate_scan_cache(self, app_id: str = None):
        """手动失效扫描缓存。app_id=None 时清空全部。"""
        if app_id:
            self._scan_cache.pop(app_id, None)
        else:
            self._scan_cache.clear()

    def write_notes(self, app_id: str, data: dict):
        """写入笔记文件（原子写入），并标记为需要上传到云"""
        os.makedirs(self.notes_dir, exist_ok=True)
        path = self._get_note_file(app_id)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        self._dirty_apps.add(app_id)
        self._scan_cache.pop(app_id, None)

    def cloud_upload(self, app_id: str) -> bool:
        """上传指定 app 的笔记到 Steam Cloud，成功后清除 dirty 标记并记录哈希"""
        if not self.cloud_uploader or not self.cloud_uploader.initialized:
            return False
        path = self._get_note_file(app_id)
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # 上传前记录内容哈希（基于实际上传的内容，非磁盘文件）
        uploaded_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        filename = f"notes_{app_id}"
        if self.cloud_uploader.file_write(filename, content.encode("utf-8")):
            # 上传后对比：如果文件在上传期间被修改，不清除 dirty
            current_hash = self._compute_file_hash(path)
            if current_hash == uploaded_hash:
                self._dirty_apps.discard(app_id)
            self._uploaded_hashes[app_id] = uploaded_hash
            return True
        return False

    def cloud_upload_all_dirty(self) -> tuple:
        """上传所有有改动的笔记到云，返回 (成功数, 失败数)"""
        ok = fail = 0
        for app_id in list(self._dirty_apps):
            if self.cloud_upload(app_id):
                ok += 1
            else:
                fail += 1
        return ok, fail

    def cloud_upload_all_batch(self, progress_callback=None) -> tuple:
        """批量上传所有 dirty 笔记（使用 batch_file_write 消除逐条往返开销）。

        Args:
            progress_callback: 可选回调 (current, total, ok, fail)
        Returns: (ok_count, fail_count)
        """
        if not self.cloud_uploader or not self.cloud_uploader.initialized:
            return 0, 0
        dirty_ids = list(self._dirty_apps)
        if not dirty_ids:
            return 0, 0
        # 准备文件列表，同时记录上传内容的哈希（用于上传后对比）
        file_list = []
        id_list = []  # [(app_id, path, uploaded_hash), ...]
        for app_id in dirty_ids:
            path = self._get_note_file(app_id)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            content_bytes = content.encode("utf-8")
            uploaded_hash = hashlib.md5(content_bytes).hexdigest()
            file_list.append((f"notes_{app_id}", content_bytes))
            id_list.append((app_id, path, uploaded_hash))
        if not file_list:
            return 0, 0
        ok, fail = self.cloud_uploader.batch_file_write(
            file_list, progress_callback=progress_callback)
        # 更新 dirty 状态：batch 不报告逐条成败，
        # 仅当全部成功时才标记所有为已同步；有失败则保守保留 dirty
        if fail == 0:
            for app_id, path, uploaded_hash in id_list:
                # 对比：如果文件在上传期间被修改，不清除 dirty
                current_hash = self._compute_file_hash(path)
                if current_hash == uploaded_hash:
                    self._dirty_apps.discard(app_id)
                self._uploaded_hashes[app_id] = uploaded_hash
        return ok, fail

    def is_dirty(self, app_id: str) -> bool:
        return app_id in self._dirty_apps

    def dirty_count(self) -> int:
        return len(self._dirty_apps)

    @staticmethod
    def _compute_file_hash(filepath: str) -> str:
        """计算文件内容的 MD5 哈希（text 模式读取 + UTF-8 编码，与上传路径一致）"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return hashlib.md5(f.read().encode("utf-8")).hexdigest()
        except Exception:
            return ""

    def _rebuild_dirty_from_hashes(self):
        """根据持久化的上传哈希与本地文件对比，重建 dirty 状态"""
        if not os.path.exists(self.notes_dir):
            return
        for f in os.listdir(self.notes_dir):
            fp = os.path.join(self.notes_dir, f)
            if not os.path.isfile(fp) or not f.startswith("notes_"):
                continue
            app_id = f[6:]  # strip "notes_"
            if "::" in app_id:
                continue
            local_hash = self._compute_file_hash(fp)
            stored_hash = self._uploaded_hashes.get(app_id, "")
            if not stored_hash or local_hash != stored_hash:
                self._dirty_apps.add(app_id)

    def mark_as_synced(self, app_id: str) -> bool:
        """手动将指定 app 标记为已同步（记录当前文件哈希，清除 dirty 状态）"""
        path = self._get_note_file(app_id)
        if not os.path.exists(path):
            return False
        self._uploaded_hashes[app_id] = self._compute_file_hash(path)
        self._dirty_apps.discard(app_id)
        return True

    def get_uploaded_hashes(self) -> dict:
        """返回当前上传哈希表（供外部持久化）"""
        return dict(self._uploaded_hashes)

    def create_note(self, app_id: str, title: str, content: str) -> dict:
        """创建一条笔记（始终追加）"""
        entry = self._build_entry(app_id, title, content)
        data = self.read_notes(app_id)
        data["notes"].append(entry)
        self.write_notes(app_id, data)
        return self.read_notes(app_id)

    def update_note(self, app_id: str, index: int, title: str, content: str):
        """更新指定索引的笔记"""
        data = self.read_notes(app_id)
        notes = data.get("notes", [])
        if 0 <= index < len(notes):
            notes[index]["title"] = title
            notes[index]["content"] = self._wrap_content(content)
            notes[index]["time_modified"] = int(time.time())
            self.write_notes(app_id, data)
            return True
        return False

    def delete_note(self, app_id: str, index: int) -> bool:
        """删除指定索引的笔记
        
        Returns: True if deleted, False if invalid index
        """
        data = self.read_notes(app_id)
        notes = data.get("notes", [])
        if 0 <= index < len(notes):
            notes.pop(index)
            self.write_notes(app_id, data)
            return True
        return False

    def delete_notes_by_ids(self, app_id: str, note_ids: list) -> int:
        """删除指定游戏中特定 ID 的笔记

        Args:
            app_id: 游戏 AppID
            note_ids: 要删除的笔记 ID 列表

        Returns: 实际删除的数量
        """
        data = self.read_notes(app_id)
        notes = data.get("notes", [])
        ids_set = set(note_ids)
        original_len = len(notes)
        data["notes"] = [n for n in notes if n.get("id", "") not in ids_set]
        deleted = original_len - len(data["notes"])
        if deleted > 0:
            if data["notes"]:
                self.write_notes(app_id, data)
            else:
                # 所有笔记都被删除了，直接删除文件
                self.delete_all_notes(app_id)
        return deleted

    def delete_all_notes(self, app_id: str) -> bool:
        """删除指定游戏的所有笔记"""
        path = self._get_note_file(app_id)
        if os.path.exists(path):
            os.remove(path)
            self._dirty_apps.discard(app_id)
            self._scan_cache.pop(app_id, None)
            # 同时从 Steam Cloud 删除
            if self.cloud_uploader and self.cloud_uploader.initialized:
                self.cloud_uploader.file_delete(f"notes_{app_id}")
            return True
        return False

    def move_note(self, app_id: str, index: int, direction: int) -> bool:
        """移动笔记顺序。direction: -1=上移, +1=下移
        
        Returns: True if moved, False if invalid move
        """
        data = self.read_notes(app_id)
        notes = data.get("notes", [])
        new_index = index + direction
        
        # 检查索引是否有效
        if not (0 <= index < len(notes) and 0 <= new_index < len(notes)):
            return False
        
        # 执行移动
        notes[index], notes[new_index] = notes[new_index], notes[index]
        self.write_notes(app_id, data)
        return True

    def list_all_games(self) -> list:
        """列出所有有笔记的游戏 [{app_id, note_count, file_path}]"""
        notes_games, _ = self.scan_all()
        return sorted(notes_games.values(), key=lambda g: g['app_id'])

    # ── 批量导出格式标记 ──
    BATCH_EXPORT_HEADER = "# Steam Notes Batch Export"
    BATCH_APP_HEADER = "===APP_ID:"
    BATCH_NOTE_SEP = "---===NOTE_SEPARATOR===---"

    def export_single_note(self, app_id: str, note_index: int, output_path: str):
        """导出单条笔记为独立文件，内容为 BBCode 源码"""
        data = self.read_notes(app_id)
        notes = data.get("notes", [])
        if 0 <= note_index < len(notes):
            note = notes[note_index]
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(note.get("content", ""))

    def export_batch(self, app_ids: list, output_path: str, note_filter=None):
        """批量导出多个游戏的笔记为一个结构化文件
        note_filter: 可选的过滤函数，接受 note dict，返回 True 表示导出
        """
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"{self.BATCH_EXPORT_HEADER}\n")
            f.write(f"# 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# 包含游戏数: {len(app_ids)}\n\n")

            for app_id in app_ids:
                data = self.read_notes(app_id)
                notes = data.get("notes", [])
                if note_filter:
                    notes = [n for n in notes if note_filter(n)]
                if not notes:
                    continue
                f.write(f"{self.BATCH_APP_HEADER}{app_id}===\n")
                f.write(f"# 笔记数量: {len(notes)}\n\n")
                for i, note in enumerate(notes):
                    if i > 0:
                        f.write(f"\n{self.BATCH_NOTE_SEP}\n\n")
                    f.write(f"## {note.get('title', '(无标题)')}\n\n")
                    f.write(note.get("content", "") + "\n")
                f.write("\n")

    def import_single_note(self, app_id: str, title: str, file_path: str) -> dict:
        """从文件导入单条笔记（始终追加）"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        entry = self._build_entry(app_id, title, content)
        data = self.read_notes(app_id)
        data["notes"].append(entry)
        self.write_notes(app_id, data)
        # 重新读取以返回最新数据
        return self.read_notes(app_id)

    @staticmethod
    def parse_batch_file(file_path: str) -> dict:
        """解析批量导出文件但不写入。
        Returns: {app_id: [entry_dict, ...], ...}
        每个 entry_dict 包含 title, content (原始文本，尚未 build_entry)
        """
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        result = {}
        app_sections = re.split(r'===APP_ID:(\S+?)===', content)
        i = 1
        while i < len(app_sections) - 1:
            app_id = app_sections[i].strip()
            section = app_sections[i + 1]
            i += 2

            note_blocks = section.split(SteamNotesManager.BATCH_NOTE_SEP)
            entries = []
            for block in note_blocks:
                lines = block.strip().split('\n')
                title = None
                content_lines = []
                for line in lines:
                    if line.startswith('# '):
                        continue
                    if line.startswith('## ') and title is None:
                        title = line[3:].strip()
                        continue
                    content_lines.append(line)
                body = '\n'.join(content_lines).strip()
                if title and body:
                    entries.append({"title": title, "content": body})
                elif title:
                    entries.append({"title": title, "content": ""})

            if entries:
                result[app_id] = entries

        return result

    def apply_batch_import(self, parsed: dict, ai_policy: str = "append",
                           per_app_policy: dict = None) -> dict:
        """将解析后的数据写入笔记文件。
        parsed: {app_id: [{title, content}, ...]}
        ai_policy: 全局 AI 冲突策略
            "append"  — AI 笔记追加在已有笔记之后
            "replace" — 删除已有 AI 笔记，再写入新 AI 笔记
            "skip_ai" — 跳过导入文件中的 AI 笔记（仅导入非 AI 笔记）
        per_app_policy: {app_id: "replace"/"append"/"skip"} 逐一覆盖全局策略
        Returns: {app_id: imported_count, ...}
        """
        if per_app_policy is None:
            per_app_policy = {}
        results = {}

        for app_id, entries in parsed.items():
            policy = per_app_policy.get(app_id, ai_policy)
            data = self.read_notes(app_id)
            existing = data.get("notes", [])

            to_import = []
            for e in entries:
                note = self._build_entry(app_id, e["title"], e["content"])
                is_ai = is_ai_note(note)
                if is_ai and policy == "skip_ai":
                    continue
                to_import.append((note, is_ai))

            if policy == "replace":
                # 移除已有 AI 笔记
                existing = [n for n in existing if not is_ai_note(n)]

            for note, _ in to_import:
                existing.append(note)

            data["notes"] = existing
            self.write_notes(app_id, data)
            imported = len(to_import)
            if imported > 0:
                results[app_id] = imported

        return results

    def export_individual_files(self, app_ids: list, output_dir: str,
                               note_filter=None) -> tuple:
        """逐条导出：每条笔记导出为独立 txt 文件（文件名=笔记标题，内容=BBCode 源码）

        note_filter: 可选的过滤函数，接受 note dict，返回 True 表示导出
        为避免文件名冲突，同名笔记自动追加序号后缀。
        Returns: (total_files: int, total_notes: int)
        """
        os.makedirs(output_dir, exist_ok=True)
        used_names = {}  # {safe_name: count} 用于去重
        total_files = 0
        total_notes = 0
        for app_id in app_ids:
            data = self.read_notes(app_id)
            notes = data.get("notes", [])
            if note_filter:
                notes = [n for n in notes if note_filter(n)]
            for note in notes:
                total_notes += 1
                title = note.get("title", "untitled")
                content = note.get("content", title)
                safe_name = sanitize_filename(title)
                # 去重：如果同名则追加序号
                if safe_name in used_names:
                    used_names[safe_name] += 1
                    final_name = f"{safe_name}_{used_names[safe_name]}"
                else:
                    used_names[safe_name] = 0
                    final_name = safe_name
                filepath = os.path.join(output_dir, f"{final_name}.txt")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                total_files += 1
        return total_files, total_notes

    def scan_ai_notes(self) -> dict:
        """扫描所有笔记，识别 AI 处理过的游戏（委托给 scan_all）"""
        _, ai_notes_map = self.scan_all()
        return ai_notes_map

    def backfill_ai_note_dates(self) -> tuple:
        """为所有缺少生成日期的 AI 笔记补上日期（使用 time_created）。

        Returns: (updated_apps: int, updated_notes: int)
        """
        import re as _re
        date_pattern = _re.compile(r'📅生成于 \d{4}-\d{2}-\d{2}')
        updated_apps = 0
        updated_notes = 0
        if not os.path.exists(self.notes_dir):
            return 0, 0
        for f in os.listdir(self.notes_dir):
            fp = os.path.join(self.notes_dir, f)
            if not os.path.isfile(fp) or not f.startswith("notes_"):
                continue
            app_id = f[6:]
            if "::" in app_id:
                continue
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            notes = data.get("notes", [])
            changed = False
            for note in notes:
                if not is_ai_note(note):
                    continue
                title = note.get("title", "")
                if date_pattern.search(title):
                    continue  # 已有日期
                ts = note.get("time_created", note.get("time_modified", 0))
                if ts:
                    date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                else:
                    date_str = "未知日期"
                suffix = f" 📅生成于 {date_str}"
                note["title"] = title + suffix
                # 更新 content：在最后一个 [/p] 前插入日期
                content = note.get("content", "")
                if content.rstrip().endswith("[/p]"):
                    note["content"] = content.rstrip()[:-4] + suffix + "[/p]"
                else:
                    note["content"] = content + suffix
                changed = True
                updated_notes += 1
            if changed:
                self.write_notes(app_id, data)
                updated_apps += 1
        return updated_apps, updated_notes

    def find_duplicate_notes(self) -> list:
        """扫描所有笔记，找到标题+内容完全相同的重复项。

        Returns: [{app_id, title, content, indices: [int], count: int}, ...]
        每个条目代表一组重复笔记（同一游戏内），indices 为该组所有副本的索引。
        """
        duplicates = []
        if not os.path.exists(self.notes_dir):
            return duplicates
        for f in os.listdir(self.notes_dir):
            fp = os.path.join(self.notes_dir, f)
            if not os.path.isfile(fp) or not f.startswith("notes_"):
                continue
            app_id = f.replace("notes_", "")
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                notes = data.get("notes", [])
                # 按 (title, content) 分组
                seen = {}  # {(title, content): [index, ...]}
                for i, note in enumerate(notes):
                    key = (note.get("title", ""), note.get("content", ""))
                    if key not in seen:
                        seen[key] = []
                    seen[key].append(i)
                for (title, content), indices in seen.items():
                    if len(indices) > 1:
                        duplicates.append({
                            'app_id': app_id,
                            'title': title,
                            'content': content,
                            'indices': indices,
                            'count': len(indices),
                        })
            except Exception:
                continue
        return duplicates

    def delete_duplicate_notes(self, app_id: str, indices_to_remove: list) -> int:
        """删除指定游戏中的重复笔记（按索引列表，从大到小删除避免索引偏移）

        Returns: 实际删除的数量
        """
        data = self.read_notes(app_id)
        notes = data.get("notes", [])
        removed = 0
        for idx in sorted(indices_to_remove, reverse=True):
            if 0 <= idx < len(notes):
                notes.pop(idx)
                removed += 1
        if removed > 0:
            data["notes"] = notes
            if notes:
                self.write_notes(app_id, data)
            else:
                # 没有笔记了，删除文件
                path = self._get_note_file(app_id)
                if os.path.exists(path):
                    os.remove(path)
                self._dirty_apps.discard(app_id)
                self._scan_cache.pop(app_id, None)
                if self.cloud_uploader and self.cloud_uploader.initialized:
                    self.cloud_uploader.file_delete(f"notes_{app_id}")
        return removed
