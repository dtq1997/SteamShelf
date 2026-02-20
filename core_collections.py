"""
core_collections.py — 收藏夹核心业务逻辑

来源：软件A 的 core.py + core_igdb.py + core_scraper.py
改动：
- 去除 from tkinter import messagebox 依赖（改为返回错误或抛异常）
- 使用统一的 ConfigManager 替代 A 原有的 load_config() / save_config()
- 使用统一的 utils.urlopen() 替代 A 原有的 SSL 上下文
- CEF 队列相关逻辑保留
"""

import json
import os
import secrets
import shutil
import time
import urllib.error
import urllib.request

from account_manager import SteamAccount
from local_storage import BackupManager
from config_manager import ConfigManager
from utils import urlopen, get_ssl_context

# 延迟导入 Mixin，避免循环依赖
from core_igdb import IGDBMixin
from core_scraper import ScraperMixin


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止自动重定向，以便检测 302 等重定向响应"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CollectionsCore(IGDBMixin, ScraperMixin):
    """收藏夹核心类，UI 无关

    来自软件A的 SteamToolboxCore，重命名以避免与笔记核心类冲突。
    """

    def __init__(self, account: SteamAccount, config_mgr: ConfigManager = None):
        self.current_account: SteamAccount = account

        # 备份管理器（仅在有 storage_path 时可用）
        self.backup_manager = None
        if self.current_account.storage_path:
            self.backup_manager = BackupManager(self.current_account.storage_path)

        # 使用统一配置管理器
        self._config_mgr = config_mgr or ConfigManager()

        # 数据目录
        self.data_dir = self._config_mgr._CONFIG_DIR
        os.makedirs(self.data_dir, exist_ok=True)

        # 迁移旧版文件
        self.migrate_old_files()

        self.induce_suffix = "(删除这段字以触发云同步)"
        self.disclaimer = ("\n\n(若其中包含未拥有的游戏、重复条目或是 DLC，"
                           "会导致 Steam 收藏夹内显示的数目偏少。)")

        # SSL 上下文
        self.ssl_context = get_ssl_context()

        # CEF 桥接（由 UI 层设置）
        self.cef = None  # type: Optional['CEFBridge']
        self._pending_cef_ops = []

    # ==================== CEF 同步队列 ====================

    @property
    def _cef_active(self):
        """CEF 是否处于活跃连接状态"""
        return self.cef is not None and self.cef.is_connected()

    def queue_cef_upsert(self, col_id, name, added, removed=None):
        """记录一个待同步的 upsert 操作"""
        self._pending_cef_ops.append({
            "action": "upsert",
            "col_id": col_id,
            "name": name,
            "added": added,
            "removed": removed or []
        })

    def queue_cef_delete(self, col_id, name=""):
        """记录一个待同步的 delete 操作"""
        self._pending_cef_ops.append({
            "action": "delete",
            "col_id": col_id,
            "name": name
        })

    def pop_pending_cef_ops(self):
        """取出并清空待同步队列"""
        ops = self._pending_cef_ops[:]
        self._pending_cef_ops = []
        return ops

    def migrate_old_files(self):
        """将旧版散落在主目录的文件迁移到统一数据目录"""
        home = os.path.expanduser("~")
        migrations = [
            (".steam_toolbox_config.json", "config.json"),
            (".steam_toolbox_igdb_cache.json", "igdb_cache.json"),
        ]
        for old_name, new_name in migrations:
            old_path = os.path.join(home, old_name)
            new_path = os.path.join(self.data_dir, new_name)
            if os.path.exists(old_path) and not os.path.exists(new_path):
                try:
                    shutil.move(old_path, new_path)
                except Exception:
                    pass

    def _fetch_page(self, url, headers, timeout=30, max_redirects=5):
        """发送 HTTP GET 请求，手动处理重定向

        Returns:
            (html_content, final_url, error): 成功时 error 为 None
        """
        current_url = url
        for _ in range(max_redirects):
            try:
                req = urllib.request.Request(current_url, headers=headers)
                with urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode('utf-8'), resp.geturl(), None
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    redirect_url = e.headers.get('Location', '')
                    if redirect_url:
                        if redirect_url.startswith('/'):
                            from urllib.parse import urlparse
                            parsed = urlparse(current_url)
                            redirect_url = f"{parsed.scheme}://{parsed.netloc}{redirect_url}"
                        current_url = redirect_url
                        continue
                return None, current_url, f"HTTP 错误 {e.code}"
            except urllib.error.URLError as e:
                return None, current_url, f"网络错误：{str(e.reason)}"
            except Exception as e:
                return None, current_url, f"请求失败：{str(e)}"
        return None, current_url, "重定向次数过多"

    @staticmethod
    def next_version(data):
        """扫描全部条目，返回下一个可用的全局版本号（字符串）"""
        max_ver = 0
        for entry in data:
            try:
                v = int(entry[1].get("version", "0"))
                if v > max_ver:
                    max_ver = v
            except (ValueError, IndexError, TypeError):
                continue
        return str(max_ver + 1)

    def add_static_collection(self, data, name, app_ids):
        col_id = f"uc-{secrets.token_hex(6)}"
        storage_key = f"user-collections.{col_id}"
        actual_name = name if self._cef_active else name + self.induce_suffix
        val_obj = {"id": col_id, "name": actual_name, "added": app_ids, "removed": []}
        new_entry = [storage_key, {
            "key": storage_key,
            "timestamp": int(time.time()),
            "value": json.dumps(val_obj, ensure_ascii=False, separators=(',', ':')),
            "version": self.next_version(data),
            "conflictResolutionMethod": "custom",
            "strMethodId": "union-collections"
        }]
        data.append(new_entry)
        self.queue_cef_upsert(col_id, actual_name, app_ids)
        return col_id

    # ==================== 来源缓存 ====================

    @property
    def _sources_key(self) -> str:
        """按账号隔离的来源缓存配置键"""
        fc = self.current_account.get('friend_code', 'unknown')
        return f"collection_sources_{fc}"

    def _get_all_sources(self, config=None) -> dict:
        """获取当前账号的所有来源缓存（自动迁移旧全局数据）"""
        if config is None:
            config = self.load_config()
        key = self._sources_key
        sources = config.get(key)
        if sources is not None:
            return sources
        # 迁移：旧版全局 collection_sources → 按账号隔离
        old = config.get("collection_sources")
        if old:
            config[key] = dict(old)
            del config["collection_sources"]
            self.save_config(config)
            return config[key]
        return {}

    def save_collection_source(self, col_id, source_type, source_params,
                               source_display_name, update_mode):
        """保存收藏夹的来源信息（按 col_id 绑定，改名不影响）"""
        config = self.load_config()
        sources = config.setdefault(self._sources_key, {})
        sources[col_id] = {
            "source_type": source_type,
            "source_params": source_params,
            "source_display_name": source_display_name,
            "update_mode": update_mode,
            "last_updated": time.time(),
        }
        self.save_config(config)

    def get_collection_source(self, col_id):
        """获取收藏夹的缓存来源信息，无则返回 None"""
        return self._get_all_sources().get(col_id)

    def remove_collection_source(self, col_id):
        """删除收藏夹的缓存来源信息"""
        config = self.load_config()
        sources = config.get(self._sources_key, {})
        if col_id in sources:
            del sources[col_id]
            self.save_config(config)

    # ==================== 配置管理（委托给 ConfigManager）====================

    def load_config(self):
        """加载全局配置（返回活引用，修改后调 save_config 持久化）"""
        return self._config_mgr.raw

    def save_config(self, config=None):
        """持久化全局配置到磁盘"""
        self._config_mgr.save()

    def get_saved_cookie(self):
        """获取已保存的 Cookie"""
        return self._config_mgr.get_steam_cookie()

    def save_cookie(self, cookie_value):
        """保存 Cookie"""
        self._config_mgr.set_steam_cookie(cookie_value)

    def clear_saved_cookie(self):
        """清除已保存的 Cookie"""
        self._config_mgr.clear_steam_cookie()
