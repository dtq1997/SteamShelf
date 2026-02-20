"""
core_igdb.py — IGDB API 交互 Mixin

包含：IGDB 维度定义、API 凭证管理、访问令牌、分类列表获取、
缓存管理、公司搜索、游戏查询。
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request


class IGDBMixin:
    """IGDB API 交互（Mixin，self 指向 SteamToolboxCore 实例）"""

    # ==================== IGDB 维度定义 ====================
    IGDB_DIMENSIONS = {
        "genres":              {"endpoint": "/v4/genres",              "game_field": "genres",              "icon": "🏷️", "name": "游戏类型",  "label": "🏷️ 类型"},
        "themes":              {"endpoint": "/v4/themes",              "game_field": "themes",              "icon": "🎭", "name": "游戏主题",  "label": "🎭 主题"},
        "keywords":            {"endpoint": "/v4/keywords",            "game_field": "keywords",            "icon": "🔑", "name": "关键词",    "label": "🔑 关键词"},
        "game_modes":          {"endpoint": "/v4/game_modes",          "game_field": "game_modes",          "icon": "🎮", "name": "游戏模式",  "label": "🎮 模式"},
        "player_perspectives": {"endpoint": "/v4/player_perspectives", "game_field": "player_perspectives", "icon": "👁",  "name": "视角",      "label": "👁 视角"},
        "franchises":          {"endpoint": "/v4/franchises",          "game_field": "franchises",          "icon": "📚", "name": "游戏系列",  "label": "📚 系列"},
    }

    # IGDB 网站 URL 路径映射（用于生成浏览链接）
    IGDB_URL_PATHS = {
        "genres": "genres",
        "themes": "themes",
        "keywords": "categories",
        "game_modes": "game_modes",
        "player_perspectives": "player_perspectives",
        "franchises": "franchises",
    }

    # 所有需要在 step2 批量查询的 game 字段（逗号拼接）
    IGDB_GAME_FIELDS = ",".join(dim["game_field"] for dim in IGDB_DIMENSIONS.values())

    # ==================== IGDB API 相关函数 ====================
    def get_igdb_credentials(self):
        """获取已保存的 IGDB API 凭证"""
        config = self.load_config()
        client_id = config.get("igdb_client_id", "")
        encoded_secret = config.get("igdb_client_secret_encoded", "")
        client_secret = ""
        if encoded_secret:
            try:
                client_secret = base64.b64decode(encoded_secret.encode()).decode()
            except Exception:
                pass
        return client_id, client_secret

    def save_igdb_credentials(self, client_id, client_secret):
        """保存 IGDB API 凭证（Client Secret 简单混淆存储）"""
        config = self.load_config()
        config["igdb_client_id"] = client_id
        if client_secret:
            config["igdb_client_secret_encoded"] = base64.b64encode(client_secret.encode()).decode()
        else:
            config.pop("igdb_client_secret_encoded", None)
        self.save_config(config)

    def clear_igdb_credentials(self):
        """清除 IGDB API 凭证"""
        config = self.load_config()
        config.pop("igdb_client_id", None)
        config.pop("igdb_client_secret_encoded", None)
        config.pop("igdb_access_token", None)
        config.pop("igdb_token_expires_at", None)
        self.save_config(config)

    def get_igdb_access_token(self, force_refresh=False):
        """获取 IGDB API 的访问令牌（带缓存）"""
        client_id, client_secret = self.get_igdb_credentials()
        if not client_id or not client_secret:
            return None, "未配置 IGDB API 凭证"

        config = self.load_config()
        cached_token = config.get("igdb_access_token", "")
        expires_at = config.get("igdb_token_expires_at", 0)

        # 检查缓存的令牌是否仍然有效（提前 300 秒过期）
        current_time = int(time.time())
        if not force_refresh and cached_token and expires_at > current_time + 300:
            return cached_token, None

        # 请求新的访问令牌
        token_url = f"https://id.twitch.tv/oauth2/token?client_id={client_id}&client_secret={client_secret}&grant_type=client_credentials"

        try:
            req = urllib.request.Request(token_url, method='POST')
            with urllib.request.urlopen(req, timeout=15, context=self.ssl_context) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            access_token = data.get("access_token", "")
            expires_in = data.get("expires_in", 0)

            if not access_token:
                return None, "获取访问令牌失败：响应中无 access_token"

            # 缓存令牌
            config["igdb_access_token"] = access_token
            config["igdb_token_expires_at"] = current_time + expires_in
            self.save_config(config)

            return access_token, None

        except urllib.error.HTTPError as e:
            return None, f"HTTP 错误 {e.code}：获取 IGDB 令牌失败"
        except urllib.error.URLError as e:
            return None, f"网络错误：{str(e.reason)}"
        except Exception as e:
            return None, f"获取令牌失败：{str(e)}"

    def fetch_igdb_dimension_list(self, dimension, progress_callback=None):
        """获取 IGDB 某个维度的条目列表（名称+ID）

        对于小维度（genres/themes/game_modes/player_perspectives）：全量拉取。
        对于大维度（keywords/franchises）：只拉取本地缓存中有数据的条目。

        Args:
            dimension: 维度名称，如 'genres', 'themes', 'keywords', ...
            progress_callback: 进度回调

        Returns:
            (list_of_items, error): items = [{'id': ..., 'name': ...}, ...]
        """
        dim_info = self.IGDB_DIMENSIONS.get(dimension)
        if not dim_info:
            return [], f"未知维度: {dimension}"

        client_id, _ = self.get_igdb_credentials()
        access_token, error = self.get_igdb_access_token()
        if error:
            return [], error

        if progress_callback:
            progress_callback(0, 0, f"正在获取{dim_info['name']}列表...", "")

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        }

        url = f"https://api.igdb.com{dim_info['endpoint']}"

        # 大维度（keywords/franchises）：只查缓存中存在的 ID，避免拉取几万条无用数据
        is_large = dimension in ("keywords", "franchises")

        if is_large:
            cache = self.load_igdb_cache()
            dim_cache = cache.get(dimension, {})
            cached_ids = [k for k in dim_cache.keys() if isinstance(dim_cache.get(k), dict) and "steam_ids" in dim_cache[k]]
            if not cached_ids:
                return [], None

            all_items = []
            batch_size = 500
            total = len(cached_ids)
            for i in range(0, total, batch_size):
                batch = cached_ids[i:i + batch_size]
                ids_str = ",".join(batch)
                body = f"fields id,name,slug; where id = ({ids_str}); limit {batch_size};"
                try:
                    req = urllib.request.Request(url, data=body.encode('utf-8'), headers=headers, method='POST')
                    with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as resp:
                        batch_items = json.loads(resp.read().decode('utf-8'))
                        all_items.extend(batch_items)
                except urllib.error.HTTPError as e:
                    return [], f"HTTP 错误 {e.code}：获取{dim_info['name']}列表失败"
                except urllib.error.URLError as e:
                    return [], f"网络错误：{str(e.reason)}"
                except Exception as e:
                    return [], f"获取失败：{str(e)}"

                if progress_callback:
                    progress_callback(min(i + batch_size, total), total,
                                      f"正在获取{dim_info['name']}名称...",
                                      f"{len(all_items)}/{total}")
                time.sleep(0.28)
        else:
            # 小维度：一次性全量拉取
            all_items = []
            offset = 0
            limit = 500
            while True:
                body = f"fields id,name,slug; limit {limit}; offset {offset}; sort name asc;"
                try:
                    req = urllib.request.Request(url, data=body.encode('utf-8'), headers=headers, method='POST')
                    with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as resp:
                        batch = json.loads(resp.read().decode('utf-8'))
                except urllib.error.HTTPError as e:
                    return [], f"HTTP 错误 {e.code}：获取{dim_info['name']}列表失败"
                except urllib.error.URLError as e:
                    return [], f"网络错误：{str(e.reason)}"
                except Exception as e:
                    return [], f"获取失败：{str(e)}"

                if not batch:
                    break
                all_items.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
                time.sleep(0.28)

        all_items.sort(key=lambda x: x.get('name', ''))
        return all_items, None

    # ==================== IGDB 本地缓存 ====================

    IGDB_CACHE_EXPIRY_DAYS = 7  # 缓存有效期（天）

    def get_igdb_cache_path(self):
        """获取 IGDB 缓存文件路径"""
        return os.path.join(self.data_dir, "igdb_cache.json")

    def load_igdb_cache(self):
        """加载 IGDB 缓存"""
        path = self.get_igdb_cache_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_igdb_cache(self, cache):
        """保存 IGDB 缓存"""
        path = self.get_igdb_cache_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    def get_igdb_dimension_cache(self, dimension, item_id):
        """获取某个维度下某个条目的缓存数据

        Args:
            dimension: 维度名称，如 'genres', 'themes', 'keywords', ...
            item_id: 条目 ID

        Returns:
            (steam_ids, cached_at_timestamp) 或 (None, None)
        """
        cache = self.load_igdb_cache()
        dim_data = cache.get(dimension, {})
        item_key = str(item_id)
        if item_key in dim_data:
            entry = dim_data[item_key]
            return entry.get("steam_ids", []), entry.get("cached_at", 0)
        return None, None

    def set_igdb_dimension_cache(self, dimension, item_id, steam_ids):
        """写入某个维度下某个条目的缓存数据"""
        cache = self.load_igdb_cache()
        if dimension not in cache:
            cache[dimension] = {}
        cache[dimension][str(item_id)] = {
            "steam_ids": steam_ids,
            "cached_at": time.time(),
        }
        self.save_igdb_cache(cache)

    def is_igdb_cache_valid(self, cached_at):
        """判断缓存是否仍然有效"""
        if not cached_at:
            return False
        age_seconds = time.time() - cached_at
        return age_seconds < self.IGDB_CACHE_EXPIRY_DAYS * 86400

    def get_igdb_dimension_game_counts(self, dimension):
        """获取某维度下各条目的 Steam 游戏数量（从本地缓存读取）

        Args:
            dimension: 维度名称

        Returns:
            dict: {item_id(int): count(int)}，无缓存则返回空字典
        """
        cache = self.load_igdb_cache()
        dim_data = cache.get(dimension, {})
        if not isinstance(dim_data, dict):
            return {}
        result = {}
        for item_key, entry in dim_data.items():
            if isinstance(entry, dict) and "steam_ids" in entry:
                try:
                    result[int(item_key)] = len(entry["steam_ids"])
                except (ValueError, TypeError):
                    pass
        return result

    def get_igdb_cache_summary(self):
        """获取缓存摘要信息，用于 UI 显示

        Returns:
            dict: {'dimensions': {dim: {'count': int, 'games': int}}, 'total_steam_games': int,
                   'newest_at': float, 'is_full_dump': bool}
                  如果无缓存则返回 None
        """
        cache = self.load_igdb_cache()
        if not cache:
            return None

        meta = cache.get("_meta", {})
        is_full_dump = meta.get("type") == "full_dump"

        # 新格式：按维度分区
        dim_stats = {}
        all_timestamps = []
        total_items = 0

        for dim_name in self.IGDB_DIMENSIONS:
            dim_data = cache.get(dim_name, {})
            if not isinstance(dim_data, dict):
                continue
            count = len(dim_data)
            games = sum(len(entry.get("steam_ids", [])) for entry in dim_data.values() if isinstance(entry, dict))
            timestamps = [entry.get("cached_at", 0) for entry in dim_data.values()
                          if isinstance(entry, dict) and entry.get("cached_at")]
            if count > 0:
                dim_stats[dim_name] = {'count': count, 'games': games}
                total_items += count
                all_timestamps.extend(timestamps)

        # 兼容旧格式（无维度分区，genre_id 直接在顶层）
        if not dim_stats:
            old_entries = {k: v for k, v in cache.items() if k != "_meta" and isinstance(v, dict) and "steam_ids" in v}
            if old_entries:
                total_genres = len(old_entries)
                total_games = sum(len(entry.get("steam_ids", [])) for entry in old_entries.values())
                timestamps = [entry.get("cached_at", 0) for entry in old_entries.values() if entry.get("cached_at")]
                if not timestamps:
                    return None
                return {
                    'total_genres': total_genres,
                    'total_games': total_games,
                    'oldest_at': min(timestamps),
                    'newest_at': max(timestamps),
                    'is_full_dump': is_full_dump,
                    'total_steam_games': meta.get("total_steam_games", 0),
                    'dimensions': {'genres': {'count': total_genres, 'games': total_games}},
                }

        if not all_timestamps:
            return None

        return {
            'dimensions': dim_stats,
            'total_items': total_items,
            'oldest_at': min(all_timestamps) if all_timestamps else 0,
            'newest_at': max(all_timestamps) if all_timestamps else 0,
            'is_full_dump': is_full_dump,
            'total_steam_games': meta.get("total_steam_games", 0),
            # 向后兼容字段
            'total_genres': dim_stats.get('genres', {}).get('count', 0),
            'total_games': sum(d['games'] for d in dim_stats.values()),
        }

    # ==================== IGDB API 请求 ====================

    def igdb_api_request(self, url, body, headers):
        """发送 IGDB API 请求，自动处理速率限制和重试"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, data=body.encode('utf-8'), headers=headers, method='POST')
                with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as resp:
                    return json.loads(resp.read().decode('utf-8')), None
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(1.5)
                    continue
                return None, f"HTTP 错误 {e.code}"
            except urllib.error.URLError as e:
                return None, f"网络错误：{str(e.reason)}"
            except Exception as e:
                return None, f"请求失败：{str(e)}"
        return None, "达到最大重试次数（速率限制）"

    def build_igdb_full_cache(self, progress_callback=None, cancel_flag=None):
        """下载 IGDB 中所有有 Steam 关联的游戏及其多维度分类信息，存入本地缓存。

        Args:
            progress_callback: fn(current, total, phase_str, detail_str)
            cancel_flag: list[bool]，cancel_flag[0]=True 时中止

        Returns:
            (genre_map, error): genre_map = {genre_id: [steam_app_ids]}（向后兼容），error = str | None
        """
        client_id, _ = self.get_igdb_credentials()
        access_token, error = self.get_igdb_access_token()
        if error:
            return {}, error

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        }

        # 第1步：获取所有 Steam 关联
        game_to_steam, error = self._igdb_fetch_steam_associations(
            headers, progress_callback, cancel_flag)
        if error:
            return {}, error

        # 第2步：批量查询多维度分类
        dim_maps, error = self._igdb_fetch_dimension_data(
            headers, game_to_steam, progress_callback, cancel_flag)
        if error:
            return {}, error

        # 第3步：写入缓存并返回
        return self._igdb_write_cache(dim_maps, game_to_steam, progress_callback)

    def _igdb_fetch_steam_associations(self, headers, progress_callback, cancel_flag):
        """第1步：遍历 external_games 获取所有 Steam→IGDB 关联。
        返回 (game_to_steam, error)。"""
        if progress_callback:
            progress_callback(0, 0, "正在估算数据量...", "")

        max_ext_id = 0
        body = "fields id; where external_game_source = 1; sort id desc; limit 1;"
        results, err = self.igdb_api_request(
            "https://api.igdb.com/v4/external_games", body, headers)
        if results:
            max_ext_id = results[0].get('id', 0)
        time.sleep(0.28)

        game_to_steam = {}
        last_id = 0
        limit = 500

        while True:
            if cancel_flag and cancel_flag[0]:
                return {}, "用户取消"

            if progress_callback:
                step1_pct = (last_id / max_ext_id * 50) if max_ext_id > 0 else 0
                progress_callback(int(step1_pct), 100,
                                  "正在下载 Steam 游戏列表...",
                                  f"已获取 {len(game_to_steam)} 个游戏")

            body = (f"fields id,uid,game; "
                    f"where external_game_source = 1 & id > {last_id}; "
                    f"sort id asc; limit {limit};")

            results, err = self.igdb_api_request(
                "https://api.igdb.com/v4/external_games", body, headers)

            if err:
                return {}, f"下载 Steam 游戏列表失败：{err}"
            if not results:
                break

            for item in results:
                uid = item.get('uid', '')
                game_id = item.get('game')
                ext_id = item.get('id', 0)
                if uid and uid.isdigit() and game_id:
                    game_to_steam[int(game_id)] = int(uid)
                if ext_id > last_id:
                    last_id = ext_id

            if len(results) < limit:
                break
            time.sleep(0.28)

        if not game_to_steam:
            return {}, "未找到任何 Steam 游戏"
        return game_to_steam, None

    def _igdb_fetch_dimension_data(self, headers, game_to_steam, progress_callback, cancel_flag):
        """第2步：批量查询游戏的多维度分类信息。
        返回 (dim_maps, error)。"""
        all_game_ids = list(game_to_steam.keys())
        dim_maps = {dim: {} for dim in self.IGDB_DIMENSIONS}
        batch_size = 500
        limit = 500
        total_batches = (len(all_game_ids) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            if cancel_flag and cancel_flag[0]:
                return {}, "用户取消"

            if progress_callback:
                step2_pct = 50 + (batch_idx / total_batches * 50) if total_batches > 0 else 50
                progress_callback(int(step2_pct), 100,
                                  "正在下载游戏分类信息...",
                                  f"进度 {batch_idx + 1}/{total_batches}（共 {len(all_game_ids)} 个游戏）")

            batch = all_game_ids[batch_idx * batch_size: (batch_idx + 1) * batch_size]
            ids_str = ",".join(str(gid) for gid in batch)

            body = (f"fields id,{self.IGDB_GAME_FIELDS}; "
                    f"where id = ({ids_str}); "
                    f"limit {limit};")

            results, err = self.igdb_api_request(
                "https://api.igdb.com/v4/games", body, headers)

            if err:
                time.sleep(0.28)
                continue

            if results:
                for item in results:
                    gid = item.get('id')
                    if not gid or gid not in game_to_steam:
                        continue
                    steam_id = game_to_steam[gid]
                    for dim_name, dim_info in self.IGDB_DIMENSIONS.items():
                        field_name = dim_info['game_field']
                        item_ids = item.get(field_name, [])
                        if item_ids:
                            for item_id in item_ids:
                                dim_maps[dim_name].setdefault(item_id, set()).add(steam_id)

            time.sleep(0.28)

        return dim_maps, None

    def _igdb_write_cache(self, dim_maps, game_to_steam, progress_callback):
        """第3步：将维度数据写入缓存，返回 (genre_map, error)。"""
        cache = {}
        now = time.time()

        for dim_name, dim_data in dim_maps.items():
            cache[dim_name] = {}
            for item_id, steam_ids_set in dim_data.items():
                cache[dim_name][str(item_id)] = {
                    "steam_ids": sorted(steam_ids_set),
                    "cached_at": now,
                }

        cache["_game_to_steam"] = {str(k): v for k, v in game_to_steam.items()}

        dim_summary = ", ".join(f"{self.IGDB_DIMENSIONS[d]['name']} {len(dim_maps[d])}" for d in dim_maps if dim_maps[d])
        cache["_meta"] = {
            "type": "full_dump",
            "cached_at": now,
            "total_steam_games": len(game_to_steam),
            "dimensions": list(self.IGDB_DIMENSIONS.keys()),
        }
        self.save_igdb_cache(cache)

        if progress_callback:
            progress_callback(100, 100,
                              "✅ 下载完成",
                              f"共 {len(game_to_steam)} 个 Steam 游戏（{dim_summary}）")

        # 返回值保持 genre_map 形式以兼容旧调用
        result = {}
        for dim_name, dim_data in dim_maps.items():
            for item_id, sids in dim_data.items():
                if dim_name == "genres":
                    result[item_id] = sorted(sids)
        return result, None

    def fetch_igdb_games_by_dimension(self, dimension, item_id, item_name, progress_callback=None, force_refresh=False):
        """根据维度和条目 ID 获取该条目下所有游戏的 Steam AppID

        优先使用本地全量缓存。如果缓存不存在或已过期，则自动触发全量构建。

        Args:
            dimension: 维度名称，如 'genres', 'themes', 'keywords', ...
            item_id: 条目 ID
            item_name: 条目名称（用于显示）
        """
        if not force_refresh:
            cached_ids, cached_at = self.get_igdb_dimension_cache(dimension, item_id)
            if cached_ids is not None and self.is_igdb_cache_valid(cached_at):
                if progress_callback:
                    age_hours = (time.time() - cached_at) / 3600
                    progress_callback(len(cached_ids), len(cached_ids),
                                      "使用本地缓存",
                                      f"{item_name}: {len(cached_ids)} 个游戏（缓存于 {age_hours:.0f} 小时前）")
                return cached_ids, None

            # 该条目无缓存，但全量缓存可能已构建（只是该条目确实没有 Steam 游戏）
            cache = self.load_igdb_cache()
            meta = cache.get("_meta", {})
            if meta.get("type") == "full_dump" and self.is_igdb_cache_valid(meta.get("cached_at", 0)):
                if progress_callback:
                    age_hours = (time.time() - meta["cached_at"]) / 3600
                    progress_callback(0, 0,
                                      "使用本地缓存", f"{item_name}: 0 个 Steam 游戏（缓存于 {age_hours:.0f} 小时前）")
                return [], None

        # === 缓存不存在或已过期：触发下载 ===
        if progress_callback:
            progress_callback(0, 0, "本地数据不完整，正在从 IGDB 下载...", "首次下载约需 5-8 分钟")

        genre_map, error = self.build_igdb_full_cache(progress_callback)
        if error:
            return [], error

        # 从刚构建的缓存中返回结果
        cached_ids, _ = self.get_igdb_dimension_cache(dimension, item_id)
        return cached_ids if cached_ids else [], None

    # ==================== IGDB 公司搜索 ====================

    def search_igdb_companies(self, query):
        """搜索 IGDB 公司（开发商/发行商）

        Args:
            query: 搜索关键词

        Returns:
            (list_of_companies, error): companies = [{'id': ..., 'name': ..., 'slug': ...}, ...]
        """
        if not query or len(query.strip()) < 2:
            return [], "搜索关键词至少 2 个字符"

        client_id, _ = self.get_igdb_credentials()
        access_token, error = self.get_igdb_access_token()
        if error:
            return [], error

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        }

        body = f'fields id,name,slug; where name ~ *"{query.strip()}"*; limit 30;'
        results, err = self.igdb_api_request("https://api.igdb.com/v4/companies", body, headers)
        if err:
            return [], err

        return results or [], None

    def count_igdb_company_steam_games(self, company_ids):
        """批量统计多个公司各自关联的 Steam 游戏数量

        使用单次批量查询 involved_companies + 本地 game_to_steam 缓存，
        避免为每个公司单独发起 API 请求。

        Args:
            company_ids: 公司 ID 列表

        Returns:
            dict: {company_id: steam_game_count}
        """
        if not company_ids:
            return {}

        client_id, _ = self.get_igdb_credentials()
        access_token, error = self.get_igdb_access_token()
        if error:
            return {}

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        }

        # 批量查询所有公司的 involved_companies
        company_games = {cid: set() for cid in company_ids}
        ids_str = ",".join(str(cid) for cid in company_ids)
        offset = 0
        limit = 500

        while True:
            body = (f"fields game,company; "
                    f"where company = ({ids_str}); "
                    f"limit {limit}; offset {offset};")
            results, err = self.igdb_api_request(
                "https://api.igdb.com/v4/involved_companies", body, headers)
            if err or not results:
                break
            for item in results:
                cid = item.get('company')
                gid = item.get('game')
                if cid and gid and cid in company_games:
                    company_games[cid].add(int(gid))
            if len(results) < limit:
                break
            offset += limit
            time.sleep(0.28)

        # 用本地缓存的 game_to_steam 映射计算 Steam 游戏数
        cache = self.load_igdb_cache()
        game_to_steam = cache.get("_game_to_steam", {})

        counts = {}
        for cid, game_ids in company_games.items():
            steam_count = sum(1 for gid in game_ids if game_to_steam.get(str(gid)))
            counts[cid] = steam_count

        return counts

    def fetch_igdb_games_by_company(self, company_id, company_name, progress_callback=None):
        """获取某公司关联的所有 Steam 游戏

        策略：查 involved_companies → 获取 game IDs → 用本地 game_to_steam 映射转换

        Args:
            company_id: IGDB 公司 ID
            company_name: 公司名称（用于显示）
            progress_callback: 进度回调

        Returns:
            (steam_ids, error)
        """
        client_id, _ = self.get_igdb_credentials()
        access_token, error = self.get_igdb_access_token()
        if error:
            return [], error

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        }

        if progress_callback:
            progress_callback(0, 0, f"正在查询 {company_name} 的游戏列表...", "")

        # 查询 involved_companies 获取该公司参与的所有游戏
        game_ids = set()
        offset = 0
        limit = 500

        while True:
            body = (f"fields game; "
                    f"where company = {company_id}; "
                    f"limit {limit}; offset {offset};")
            results, err = self.igdb_api_request(
                "https://api.igdb.com/v4/involved_companies", body, headers)

            if err:
                return [], f"查询公司游戏失败：{err}"
            if not results:
                break

            for item in results:
                gid = item.get('game')
                if gid:
                    game_ids.add(int(gid))

            if len(results) < limit:
                break
            offset += limit
            time.sleep(0.28)

        if not game_ids:
            return [], None

        if progress_callback:
            progress_callback(50, 100, "正在匹配 Steam 游戏...", f"共 {len(game_ids)} 个 IGDB 游戏")

        # 尝试用本地 game_to_steam 映射
        cache = self.load_igdb_cache()
        game_to_steam = cache.get("_game_to_steam", {})

        steam_ids = set()
        unmapped_ids = []

        for gid in game_ids:
            steam_id = game_to_steam.get(str(gid))
            if steam_id:
                steam_ids.add(int(steam_id))
            else:
                unmapped_ids.append(gid)

        # 对于未映射的游戏，通过 API 查询 external_games
        if unmapped_ids:
            batch_size = 500
            for i in range(0, len(unmapped_ids), batch_size):
                batch = unmapped_ids[i:i + batch_size]
                ids_str = ",".join(str(gid) for gid in batch)
                body = (f"fields uid,game; "
                        f"where external_game_source = 1 & game = ({ids_str}); "
                        f"limit {batch_size};")
                results, err = self.igdb_api_request(
                    "https://api.igdb.com/v4/external_games", body, headers)
                if results:
                    for item in results:
                        uid = item.get('uid', '')
                        if uid and uid.isdigit():
                            steam_ids.add(int(uid))
                time.sleep(0.28)

        if progress_callback:
            progress_callback(100, 100, "✅ 查询完成",
                              f"{company_name}: {len(steam_ids)} 个 Steam 游戏")

        return sorted(steam_ids), None


