"""配置管理器 — 持久化配置读写

统一管理 ~/.steam_toolkit/config.json 的读写，包括：
- AI 令牌管理（来自软件B）
- 上传哈希管理（来自软件B）
- 游戏名称缓存（来自软件B）
- Steam Cookie 管理（来自软件A）
- IGDB API 凭证管理（来自软件A）
- 通用配置读写

合并后首次启动时自动迁移旧配置：
  ~/.steam_notes_gen/config.json → 合入
  ~/.steam_toolbox/config.json   → 合入
"""

import base64
import json
import os
import shutil


class ConfigManager:
    """统一配置管理：读写 ~/.steam_toolkit/config.json

    配置拆分为两个文件：
    - config.json: 设置项（~10KB，启动时同步加载）
    - cache_games.json: 游戏缓存（~25MB，延迟加载）
    """

    _CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".steam_toolkit")
    _CONFIG_FILE = os.path.join(_CONFIG_DIR, "config.json")
    _CACHE_FILE = os.path.join(_CONFIG_DIR, "cache_games.json")

    # 需要拆分到 cache 文件的键
    _CACHE_KEYS = frozenset({
        "game_name_cache", "app_type_cache", "app_detail_cache",
        "game_name_bulk_cache_ts",
    })

    # 旧版配置路径（用于迁移）
    _OLD_B_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".steam_notes_gen")
    _OLD_A_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".steam_toolbox")

    def __init__(self):
        self._migrate_old_configs()
        self._caches_loaded = False
        self._config = self._load_settings()

    # ── 旧配置迁移 ──

    def _migrate_old_configs(self):
        """首次启动时从旧版配置目录迁移配置"""
        if os.path.exists(self._CONFIG_FILE):
            return  # 已有新配置，无需迁移

        os.makedirs(self._CONFIG_DIR, exist_ok=True)
        merged = {}

        # 迁移软件B旧配置
        old_b = os.path.join(self._OLD_B_CONFIG_DIR, "config.json")
        if os.path.exists(old_b):
            try:
                with open(old_b, 'r', encoding='utf-8') as f:
                    merged.update(json.load(f))
            except (json.JSONDecodeError, IOError, OSError):
                pass

        # 迁移软件A旧配置（覆盖同名键）
        old_a = os.path.join(self._OLD_A_CONFIG_DIR, "config.json")
        if os.path.exists(old_a):
            try:
                with open(old_a, 'r', encoding='utf-8') as f:
                    a_config = json.load(f)
                # A 的配置键不会与 B 冲突（A 用 steam_cookie_encoded / igdb_*）
                merged.update(a_config)
            except (json.JSONDecodeError, IOError, OSError):
                pass

        # 迁移A的 IGDB 缓存文件
        old_igdb_cache = os.path.join(self._OLD_A_CONFIG_DIR, "igdb_cache.json")
        new_igdb_cache = os.path.join(self._CONFIG_DIR, "igdb_cache.json")
        if os.path.exists(old_igdb_cache) and not os.path.exists(new_igdb_cache):
            try:
                shutil.copy2(old_igdb_cache, new_igdb_cache)
            except OSError:
                pass

        if merged:
            try:
                with open(self._CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
            except (IOError, OSError):
                pass

    # ── 通用读写 ──

    def _load_settings(self) -> dict:
        """仅加载设置文件（不含缓存，~10KB，<1ms）"""
        data = self._read_json(self._CONFIG_FILE)
        # 一次性迁移：旧 config.json 含缓存键 → 拆分到 cache 文件
        cache_in_settings = {k: data.pop(k) for k in list(data)
                             if k in self._CACHE_KEYS}
        if cache_in_settings:
            if not os.path.exists(self._CACHE_FILE):
                self._write_json(self._CACHE_FILE, cache_in_settings)
            self._write_json(self._CONFIG_FILE, data)
        return data

    def load_caches(self):
        """延迟加载游戏缓存（~25MB），合并到 _config 字典"""
        if self._caches_loaded:
            return
        self._caches_loaded = True
        cache = self._read_json(self._CACHE_FILE)
        if cache:
            self._config.update(cache)

    def _ensure_caches(self):
        """确保缓存已加载（供 getter 方法调用）"""
        if not self._caches_loaded:
            self.load_caches()

    @staticmethod
    def _read_json(path) -> dict:
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            pass
        return {}

    @staticmethod
    def _write_json(path, data):
        """原子写入 JSON 文件"""
        tmp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except (IOError, OSError) as e:
            print(f"[配置] ⚠️ 写入失败 {path}: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def save(self):
        """保存设置到 config.json（不含缓存，快速）"""
        settings = {k: v for k, v in self._config.items()
                    if k not in self._CACHE_KEYS}
        self._write_json(self._CONFIG_FILE, settings)

    def save_caches(self):
        """保存缓存到 cache_games.json"""
        cache = {k: self._config[k] for k in self._CACHE_KEYS
                 if k in self._config}
        if cache:
            self._write_json(self._CACHE_FILE, cache)

    def get(self, key: str, default=None):
        """获取配置值"""
        return self._config.get(key, default)

    def set(self, key: str, value):
        """设置配置值并保存"""
        self._config[key] = value
        self.save()

    def delete(self, key: str):
        """删除配置值并保存"""
        if key in self._config:
            del self._config[key]
            self.save()

    @property
    def raw(self) -> dict:
        """直接访问底层配置字典（用于批量操作后手动调用 save()）"""
        return self._config

    # ── API Key / 令牌管理 ──

    def get_saved_key(self, key_name: str) -> str:
        """获取已保存的 API Key"""
        return self._config.get(key_name, "")

    def set_saved_key(self, key_name: str, value: str):
        """保存 API Key"""
        if value:
            self._config[key_name] = value
        elif key_name in self._config:
            del self._config[key_name]
        self.save()

    def clear_saved_key(self, key_name: str):
        """清除已保存的 API Key"""
        if key_name in self._config:
            del self._config[key_name]
            self.save()

    def get_ai_tokens(self, providers_info: dict = None) -> list:
        """获取已保存的 AI 令牌列表（含向后兼容）

        Args:
            providers_info: SteamAIGenerator.PROVIDERS 字典，
                            用于向后兼容时查找提供商信息。
                            如果为 None，则跳过旧格式迁移中的提供商查找。

        每个令牌: {name, key, provider, model, api_url}
        """
        tokens = self._config.get("ai_tokens", [])
        if tokens:
            return tokens
        # 向后兼容：从旧的单 key 配置迁移
        old_key = (self._config.get("ai_api_key") or
                   self._config.get("anthropic_api_key") or "")
        if old_key:
            prov = self._config.get("ai_provider", "anthropic")
            pinfo = (providers_info or {}).get(prov, {})
            return [{
                "name": pinfo.get("name", prov),
                "key": old_key,
                "provider": prov,
                "model": self._config.get("ai_model",
                                           pinfo.get("default_model", "")),
                "api_url": self._config.get("ai_api_url", ""),
            }]
        return []

    def save_ai_tokens(self, tokens: list, active_index: int = 0):
        """保存 AI 令牌列表"""
        self._config["ai_tokens"] = tokens
        self._config["ai_active_token_index"] = active_index
        # 同步旧字段（保持向后兼容）
        if tokens and 0 <= active_index < len(tokens):
            t = tokens[active_index]
            self._config["ai_api_key"] = t.get("key", "")
            self._config["anthropic_api_key"] = t.get("key", "")
            self._config["ai_provider"] = t.get("provider", "anthropic")
            self._config["ai_model"] = t.get("model", "")
            self._config["ai_api_url"] = t.get("api_url", "")
        self.save()

    def get_active_token_index(self) -> int:
        """获取当前激活的令牌索引"""
        return self._config.get("ai_active_token_index", 0)

    # ── 上传哈希管理 ──

    def get_uploaded_hashes(self, friend_code: str) -> dict:
        """获取指定账号的上传哈希"""
        return self._config.get(f"uploaded_hashes_{friend_code}", {})

    def save_uploaded_hashes(self, friend_code: str, hashes: dict):
        """保存指定账号的上传哈希"""
        self._config[f"uploaded_hashes_{friend_code}"] = hashes
        self.save()

    # ── 游戏名称缓存 ──

    def get_name_cache(self) -> dict:
        """获取持久化的游戏名称缓存"""
        self._ensure_caches()
        return self._config.get("game_name_cache", {})

    def save_name_cache(self, cache: dict):
        """保存游戏名称缓存"""
        self._config["game_name_cache"] = cache
        self.save_caches()

    def get_type_cache(self) -> dict:
        """获取持久化的游戏类型缓存"""
        self._ensure_caches()
        return self._config.get("app_type_cache", {})

    def save_type_cache(self, cache: dict):
        """保存游戏类型缓存"""
        self._config["app_type_cache"] = cache
        self.save_caches()

    def get_detail_cache(self) -> dict:
        """获取持久化的游戏详情缓存"""
        self._ensure_caches()
        return self._config.get("app_detail_cache", {})

    def save_detail_cache(self, cache: dict):
        """保存游戏详情缓存"""
        self._config["app_detail_cache"] = cache
        self.save_caches()

    def get_bulk_cache_timestamp(self) -> float:
        """获取全量名称列表的缓存时间戳"""
        self._ensure_caches()
        return self._config.get("game_name_bulk_cache_ts", 0)

    def set_bulk_cache_timestamp(self, ts: float):
        """设置全量名称列表的缓存时间戳"""
        self._config["game_name_bulk_cache_ts"] = ts
        self.save_caches()

    # ── 收藏夹相关配置（来自软件A）──

    def get_steam_cookie(self) -> str:
        """获取已保存的 Steam Cookie（base64 混淆存储）"""
        encoded = self._config.get("steam_cookie_encoded", "")
        if encoded:
            try:
                return base64.b64decode(encoded.encode()).decode()
            except Exception:
                pass
        return ""

    def set_steam_cookie(self, value: str):
        """保存 Steam Cookie"""
        if value:
            self._config["steam_cookie_encoded"] = base64.b64encode(value.encode()).decode()
        else:
            self._config.pop("steam_cookie_encoded", None)
        self.save()

    def clear_steam_cookie(self):
        """清除已保存的 Steam Cookie"""
        self._config.pop("steam_cookie_encoded", None)
        self.save()

    def get_igdb_credentials(self) -> dict:
        """获取 IGDB API 凭证

        Returns: {'client_id': str, 'client_secret': str} 或空 dict
        """
        cid = self._config.get("igdb_client_id", "")
        csecret = self._config.get("igdb_client_secret", "")
        if cid or csecret:
            return {"client_id": cid, "client_secret": csecret}
        return {}

    def set_igdb_credentials(self, creds: dict):
        """保存 IGDB API 凭证"""
        self._config["igdb_client_id"] = creds.get("client_id", "")
        self._config["igdb_client_secret"] = creds.get("client_secret", "")
        self.save()

    def clear_igdb_credentials(self):
        """清除 IGDB API 凭证"""
        self._config.pop("igdb_client_id", None)
        self._config.pop("igdb_client_secret", None)
        self.save()
