"""社区分类订阅同步引擎

纯数据层，无 tkinter 依赖。
职责：config 读写 + hash 计算 + 变更检测 + Supabase 交互。
"""
from __future__ import annotations

import hashlib
import json
import time


def _supabase_helpers():
    """延迟导入 ui_sharing 的 Supabase helpers，避免循环依赖"""
    from ui_sharing import (_supabase_get, _supabase_patch,
                            _SUPABASE_URL, _SUPABASE_ANON_KEY)
    return _supabase_get, _supabase_patch, _SUPABASE_URL, _SUPABASE_ANON_KEY


class SharingSyncEngine:
    """订阅同步引擎 — 无 UI 依赖"""

    def __init__(self, config: dict, friend_code: str):
        self._config = config
        self._friend_code = friend_code

    # ── config 存取（per-account 隔离） ──

    @property
    def _published_key(self) -> str:
        return f"sharing_published_{self._friend_code}"

    @property
    def _subscriptions_key(self) -> str:
        return f"sharing_subscriptions_{self._friend_code}"

    def get_published(self) -> dict:
        return self._config.get(self._published_key, {})

    def save_published(self, data: dict):
        self._config[self._published_key] = data

    def get_subscriptions(self) -> dict:
        return self._config.get(self._subscriptions_key, {})

    def save_subscriptions(self, data: dict):
        self._config[self._subscriptions_key] = data

    # ── 变更检测 ──

    @staticmethod
    def compute_content_hash(collections: list[dict]) -> str:
        """SHA-256 of canonical JSON (sorted keys, no spaces)"""
        canonical = json.dumps(collections, sort_keys=True,
                               ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]

    # ── 分享者：检测变动 + 上传 ──

    def build_updated_payload(self, share_id, coll_data_cache):
        """比对本地分类与已上传 hash，有变动则返回 (payload, new_hash)"""
        published = self.get_published()
        info = published.get(share_id)
        if not info:
            return None
        col_ids = info.get("local_col_ids", [])
        if not col_ids:
            return None

        collections = []
        all_games = set()
        for cid in col_ids:
            cdata = coll_data_cache.get(cid)
            if not cdata or cdata.get('is_dynamic'):
                continue
            aids = [int(a) for a in cdata.get('owned_app_ids', [])
                    if str(a).isdigit()]
            collections.append({"name": cdata.get('name', cid), "added": aids})
            all_games.update(aids)

        if not collections:
            return None
        new_hash = self.compute_content_hash(collections)
        if new_hash == info.get("content_hash", ""):
            return None  # 无变动

        payload = {
            "collections": collections,
            "game_count": len(all_games),
            "collection_count": len(collections),
            "content_hash": new_hash,
        }
        return payload, new_hash

    def upload_share_update(self, share_id, payload, new_hash):
        """PATCH 变动到 Supabase，成功则更新本地 hash"""
        _get, _patch, _url, _key = _supabase_helpers()
        if not _url:
            return False
        try:
            _patch("shared_collections",
                   f"id=eq.{share_id}",
                   payload, self._friend_code)
            published = self.get_published()
            if share_id in published:
                published[share_id]["content_hash"] = new_hash
                published[share_id]["last_synced"] = time.time()
                self.save_published(published)
            return True
        except Exception as e:
            print(f"[sharing-sync] upload failed: {e}")
            return False

    # ── 订阅者：检查更新 + 下载 + 应用 ──

    def check_subscription_updates(self):
        """批量检查哪些订阅有远端更新，返回 [(share_id, remote_hash)]"""
        _get, _patch, _url, _key = _supabase_helpers()
        if not _url:
            return []
        subs = self.get_subscriptions()
        if not subs:
            return []
        ids = list(subs.keys())
        id_list = ",".join(ids)
        try:
            rows = _get("shared_collections",
                        f"id=in.({id_list})"
                        "&select=id,content_hash")
        except Exception as e:
            print(f"[sharing-sync] check updates failed: {e}")
            return []

        changed = []
        remote_map = {r["id"]: r.get("content_hash", "") for r in rows}
        for sid, info in subs.items():
            remote_hash = remote_map.get(sid)
            if remote_hash is None:
                # 分享已被删除
                changed.append((sid, None))
            elif remote_hash != info.get("content_hash", ""):
                changed.append((sid, remote_hash))
        return changed

    def fetch_share_data(self, share_id):
        """下载完整分享数据（含 collections JSONB）"""
        _get, _patch, _url, _key = _supabase_helpers()
        if not _url:
            return None
        try:
            rows = _get("shared_collections", f"id=eq.{share_id}&limit=1")
            return rows[0] if rows else None
        except Exception as e:
            print(f"[sharing-sync] fetch failed {share_id}: {e}")
            return None

    def apply_update(self, share_id, remote_colls, collections_core, local_data):
        """应用远端更新到本地分类，返回 (updated, added, new_mapping)"""
        subs = self.get_subscriptions()
        info = subs.get(share_id, {})
        col_mapping = dict(info.get("col_mapping", {}))

        updated = 0
        added = 0
        for rc in remote_colls:
            name = rc.get("name", "")
            aids = [int(a) for a in rc.get("added", []) if str(a).isdigit()]
            if not name or not aids:
                continue

            local_id = col_mapping.get(name)
            if local_id:
                # 已有映射 → 更新
                ok = collections_core.update_collection_apps(
                    local_data, local_id, aids)
                if ok:
                    updated += 1
                else:
                    # 本地分类已删除 → 重新创建
                    new_id = collections_core.add_static_collection(
                        local_data, name, aids)
                    col_mapping[name] = new_id
                    added += 1
            else:
                # 新分类 → 创建
                new_id = collections_core.add_static_collection(
                    local_data, name, aids)
                col_mapping[name] = new_id
                added += 1

        # 清理：远端已移除的分类从 mapping 中删除（本地保留）
        remote_names = {rc.get("name", "") for rc in remote_colls}
        for name in list(col_mapping):
            if name not in remote_names:
                del col_mapping[name]

        return updated, added, col_mapping

    # ── 管理方法 ──

    def subscribe(self, share_id, title, author, col_mapping, content_hash):
        """记录订阅关系"""
        subs = self.get_subscriptions()
        subs[share_id] = {
            "title": title,
            "author": author,
            "col_mapping": col_mapping,
            "content_hash": content_hash,
            "last_synced": time.time(),
        }
        self.save_subscriptions(subs)

    def unsubscribe(self, share_id):
        """取消订阅（本地分类保留）"""
        subs = self.get_subscriptions()
        subs.pop(share_id, None)
        self.save_subscriptions(subs)

    def register_published(self, share_id, title, local_col_ids, content_hash):
        """分享上传成功后，记录发布映射"""
        published = self.get_published()
        published[share_id] = {
            "title": title,
            "local_col_ids": local_col_ids,
            "content_hash": content_hash,
            "last_synced": time.time(),
        }
        self.save_published(published)

    def remove_published(self, share_id):
        """删除发布记录"""
        published = self.get_published()
        published.pop(share_id, None)
        self.save_published(published)

    def is_subscribed(self, share_id) -> bool:
        return share_id in self.get_subscriptions()

    def is_published(self, share_id) -> bool:
        return share_id in self.get_published()
