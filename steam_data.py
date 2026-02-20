"""Steam 数据获取 — 游戏详情、评测、名称等

从 ai_generator.py 分离，使 AI 生成逻辑与 Steam 数据获取逻辑解耦。
"""

import json
import re

try:
    import urllib.request
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

from utils import urlopen


def get_game_name_from_steam(app_id: str) -> str:
    """通过 Steam Store API 获取游戏名称"""
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc=US&l=schinese"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "SteamNotesGen/6.0"
        })
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        app_data = data.get(str(app_id), {})
        if app_data.get("success"):
            return app_data["data"].get("name", f"AppID {app_id}")
    except Exception:
        pass
    return f"AppID {app_id}"


def get_app_name_and_type(app_id: str) -> tuple:
    """通过 Steam Store API 同时获取游戏名称、类型和精简详情

    Returns: (name, type_str, detail_dict)
             detail_dict 包含 genres/developers/publishers/release_date 等
             API 返回 success=false（已下架）→ detail={}
             网络错误 → detail=None
    """
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc=US&l=schinese"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "SteamNotesGen/6.0"
        })
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        app_data = data.get(str(app_id), {})
        if app_data.get("success"):
            d = app_data.get("data", {})
            name = d.get("name", f"AppID {app_id}")
            type_str = d.get("type", "")
            detail = _extract_detail(d)
            return name, type_str, detail
        return f"AppID {app_id}", "", {}  # API 响应了但 success=false
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return f"AppID {app_id}", "", "rate_limited"
        return f"AppID {app_id}", "", None
    except Exception:
        return f"AppID {app_id}", "", None


def _extract_detail(d: dict) -> dict:
    """从 appdetails 响应中提取精简详情（只保留有用字段，压缩存储）"""
    detail = {}
    # 字符串/布尔字段
    for key in ("is_free",):
        if key in d:
            detail[key] = d[key]
    # 列表字段（直接存）
    for key in ("developers", "publishers"):
        v = d.get(key)
        if v:
            detail[key] = v
    # genres: [{id, description}] → ["Action", "RPG"]
    genres = d.get("genres")
    if genres:
        detail["genres"] = [g["description"] for g in genres
                            if "description" in g]
    # categories: [{id, description}] → ["Single-player", "Multi-player"]
    cats = d.get("categories")
    if cats:
        detail["categories"] = [c["description"] for c in cats
                                if "description" in c]
    # platforms: {windows, mac, linux} → "W,M,L"
    plat = d.get("platforms")
    if plat:
        parts = []
        if plat.get("windows"): parts.append("W")
        if plat.get("mac"): parts.append("M")
        if plat.get("linux"): parts.append("L")
        if parts:
            detail["platforms"] = ",".join(parts)
    # metacritic
    mc = d.get("metacritic")
    if mc and "score" in mc:
        detail["metacritic"] = mc["score"]
    # release_date
    rd = d.get("release_date")
    if rd and rd.get("date"):
        detail["release_date"] = rd["date"]
    # price (整数，单位分)
    po = d.get("price_overview")
    if po and "final" in po:
        detail["price"] = po["final"]
        detail["currency"] = po.get("currency", "")
    return detail


def get_game_details_from_steam(app_id: str) -> dict:
    """通过 Steam Store API 获取游戏的详细信息（名称、开发商、类型、简介等）

    Returns: dict with keys: name, developers, publishers, genres,
             categories, short_description, release_date, metacritic,
             recommendations, etc. 若失败返回空 dict。
    """
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc=US&l=schinese"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "SteamNotesGen/6.0"
        })
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        app_data = data.get(str(app_id), {})
        if app_data.get("success"):
            return app_data.get("data", {})
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise
        pass
    except Exception:
        pass
    return {}


def _format_simple_fields(details: dict, parts: list):
    """提取简单字段（存在即追加），减少 format_game_context 的分支数"""
    # (字段key, 标签, 提取方式)
    # 提取方式: "str"=直接取字符串, "join"=join列表, "desc_join"=取description字段后join
    _SIMPLE_FIELDS = [
        ("name", "游戏名称", "str"),
        ("type", "类型", "str"),
        ("developers", "开发商", "join"),
        ("publishers", "发行商", "join"),
        ("genres", "类型标签", "desc_join"),
        ("categories", "功能特性", "desc_join"),
    ]
    for key, label, mode in _SIMPLE_FIELDS:
        val = details.get(key)
        if not val:
            continue
        if mode == "str":
            parts.append(f"{label}：{val}")
        elif mode == "join":
            parts.append(f"{label}：{', '.join(val)}")
        elif mode == "desc_join":
            names = [item.get("description", "") for item in val]
            parts.append(f"{label}：{', '.join(names)}")


def _format_descriptions(details: dict, parts: list):
    """提取简介和详细描述"""
    short_desc = details.get("short_description", "")
    if short_desc:
        clean_desc = re.sub(r'<[^>]+>', '', short_desc).strip()
        parts.append(f"官方简介：{clean_desc}")

    about = details.get("about_the_game", "") or details.get(
        "detailed_description", "")
    if about:
        clean_about = re.sub(r'<[^>]+>', ' ', about).strip()
        clean_about = re.sub(r'\s+', ' ', clean_about)
        if len(clean_about) > 800:
            clean_about = clean_about[:800] + "…"
        if clean_about and clean_about != (
                re.sub(r'<[^>]+>', '', short_desc).strip() if short_desc
                else ""):
            parts.append(f"详细描述：{clean_about}")


def _format_metadata_fields(details: dict, parts: list):
    """提取评分、日期、平台、语言等元数据字段"""
    mc = details.get("metacritic", {})
    if mc and mc.get("score"):
        parts.append(f"Metacritic 评分：{mc['score']}")

    recs = details.get("recommendations", {})
    if recs and recs.get("total"):
        parts.append(f"Steam 评价数：{recs['total']}")

    rd = details.get("release_date", {})
    if rd and rd.get("date"):
        parts.append(f"发行日期：{rd['date']}")
        if rd.get("coming_soon"):
            parts.append("状态：尚未发售（抢先体验或即将发售）")

    platforms = details.get("platforms", {})
    if platforms:
        plats = [p for p, v in platforms.items() if v]
        if plats:
            parts.append(f"支持平台：{', '.join(plats)}")

    langs = details.get("supported_languages", "")
    if langs:
        clean_langs = re.sub(r'<[^>]+>', '', langs).strip()
        if clean_langs:
            parts.append(f"支持语言：{clean_langs}")

    achieves = details.get("achievements", {})
    if achieves and achieves.get("total"):
        parts.append(f"Steam 成就数：{achieves['total']}")

    dlc = details.get("dlc", [])
    if dlc:
        parts.append(f"DLC 数量：{len(dlc)}")

    content_desc = details.get("content_descriptors", {})
    if content_desc and content_desc.get("notes"):
        parts.append(f"内容警告：{content_desc['notes']}")

    if details.get("is_free"):
        parts.append("价格：免费")

    genres = details.get("genres", [])
    if "Early Access" in str(genres):
        parts.append("⚠️ 该游戏目前处于「抢先体验」阶段")


def format_game_context(details: dict) -> str:
    """将 Steam Store API 返回的游戏详情格式化为 AI 可参考的文本摘要"""
    if not details:
        return ""
    parts = []
    _format_simple_fields(details, parts)
    _format_descriptions(details, parts)
    _format_metadata_fields(details, parts)
    return "\n".join(parts)


def get_game_reviews_from_steam(app_id: str, num_per_lang: int = 10) -> dict:
    """通过 Steam appreviews API 获取玩家评测文本和评分摘要。

    - 使用 purchase_type=steam 过滤非 Steam 购买来源
    - 返回后再过滤 received_for_free=true 的评测
    - 分别获取中文和英文的「最有帮助」评测

    Returns: dict with keys:
        'query_summary': {review_score, review_score_desc, total_positive,
                          total_negative, total_reviews}
        'reviews': list of dicts with keys: text, voted_up, playtime,
                   language, helpful_count
        若失败返回空 dict。
    """
    result = {'query_summary': {}, 'reviews': []}

    for lang in ('schinese', 'english'):
        url = (
            f"https://store.steampowered.com/appreviews/{app_id}"
            f"?json=1&language={lang}&filter=toprated"
            f"&purchase_type=steam&num_per_page={num_per_lang}"
        )
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "SteamNotesGen/6.0"
            })
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") != 1:
                continue

            qs = data.get("query_summary", {})
            if not result['query_summary'] and qs:
                result['query_summary'] = {
                    'review_score': qs.get('review_score', 0),
                    'review_score_desc': qs.get('review_score_desc', ''),
                    'total_positive': qs.get('total_positive', 0),
                    'total_negative': qs.get('total_negative', 0),
                    'total_reviews': qs.get('total_reviews', 0),
                }

            for r in data.get("reviews", []):
                if r.get("received_for_free", False):
                    continue
                review_text = r.get("review", "").strip()
                if not review_text:
                    continue
                author = r.get("author", {})
                result['reviews'].append({
                    'text': review_text,
                    'voted_up': r.get("voted_up", True),
                    'playtime': round(
                        author.get("playtime_forever", 0) / 60, 1),
                    'language': lang,
                    'helpful_count': r.get("votes_up", 0),
                })
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise
            continue
        except Exception:
            continue

    return result


def format_review_context(reviews_data: dict,
                          max_reviews: int = 8,
                          max_chars_per_review: int = 300) -> str:
    """将 Steam 评测数据格式化为 AI 可参考的文本摘要。

    包含好评率、评价等级、以及好评和差评的代表性文本摘录。
    """
    if not reviews_data:
        return ""
    parts = []

    # ── 评分摘要 ──
    qs = reviews_data.get('query_summary', {})
    if qs:
        desc = qs.get('review_score_desc', '')
        pos = qs.get('total_positive', 0)
        neg = qs.get('total_negative', 0)
        total = qs.get('total_reviews', 0)
        if total > 0:
            pct = round(pos / total * 100, 1)
            parts.append(
                f"Steam 评价等级：{desc}（好评率 {pct}%，"
                f"共 {total} 条评价，{pos} 好评 / {neg} 差评）")
        elif desc:
            parts.append(f"Steam 评价等级：{desc}")

    # ── 评测文本摘录 ──
    reviews = reviews_data.get('reviews', [])
    if not reviews:
        return "\n".join(parts)

    # ── 玩家游玩时长统计 ──
    playtimes = sorted([r['playtime'] for r in reviews if r['playtime'] > 0])
    if playtimes:
        median_pt = playtimes[len(playtimes) // 2]
        min_pt = playtimes[0]
        max_pt = playtimes[-1]
        parts.append(
            f"评测者游玩时长：中位数 {median_pt}h，"
            f"范围 {min_pt}h ~ {max_pt}h（共 {len(playtimes)} 人）")

    positive = sorted(
        [r for r in reviews if r['voted_up']],
        key=lambda r: r['helpful_count'], reverse=True)
    negative = sorted(
        [r for r in reviews if not r['voted_up']],
        key=lambda r: r['helpful_count'], reverse=True)

    n_pos = min(max(max_reviews * 2 // 3, 1), len(positive))
    n_neg = min(max(max_reviews - n_pos, 1), len(negative))
    if n_neg < max_reviews - n_pos and len(positive) > n_pos:
        n_pos = min(max_reviews - n_neg, len(positive))

    selected = ([('+', r) for r in positive[:n_pos]]
                + [('-', r) for r in negative[:n_neg]])

    if selected:
        parts.append(
            "\n--- 以下是真实玩家评测摘录（供参考，请勿照抄）---")
        for tag, r in selected:
            text = r['text']
            if len(text) > max_chars_per_review:
                text = text[:max_chars_per_review] + "…"
            text = ' '.join(text.split())
            emoji = '👍' if tag == '+' else '👎'
            pt = (f"{r['playtime']}h"
                  if r['playtime'] > 0 else "未知时长")
            parts.append(f"{emoji} [{pt}] {text}")

    return "\n".join(parts)
