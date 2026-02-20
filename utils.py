"""公共工具函数 — SSL 上下文、HTTP 请求封装、Steam 排序等

消除原先在 ui.py / ai_generator.py / account_manager.py 中各自重复的
_get_ssl_context() 和 _urlopen() 实现。
"""

import locale
import re
import ssl


# ═══════════════════════════════════════════════════════════
# Steam 排序键 — 匹配 Steam 客户端的 localeCompare('zh-CN')
# ═══════════════════════════════════════════════════════════
#
# Steam 库界面使用 Chromium ICU 的 zh-CN locale 默认 localeCompare:
#   Intl.Collator('zh-CN', {sensitivity:'variant', numeric:false})
#
# 特征：
#   - 中文按拼音排序
#   - 小写排在大写前 (a < A)
#   - 数字按字典序 (10 < 2)
#   - 空格排最前
#   - ASCII 符号顺序与码点完全不同
#
# 实现策略（按优先级）：
#   1. PyICU — 与 Chromium 使用同一 ICU 库，结果完全一致
#   2. locale.strxfrm — Linux/Windows zh_CN locale 可能匹配
#   3. pypinyin — 将中文转拼音后排序（近似）
#   4. ASCII 映射表 + 码点 — 最后兜底，CJK 按码点

# 探针实测的 Steam ASCII 排序顺序（U+0020 ~ U+007E）
_STEAM_ASCII_ORDER = (
    " _-,;:!?.'\""
    "()[]{}@*/\\&#%`^+<=>|~$"
    "0123456789"
    "aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ"
)
_STEAM_CHAR_RANK = {c: i for i, c in enumerate(_STEAM_ASCII_ORDER)}

_SORT_METHOD = 'codepoint'  # 'icu' | 'locale' | 'pypinyin' | 'codepoint'

# --- Level 1: PyICU（与 Chromium 完全一致）---
_icu_collator = None
try:
    import icu as _icu_mod
    _icu_collator = _icu_mod.Collator.createInstance(_icu_mod.Locale('zh_CN'))
    if (_icu_collator.compare(' ', '_') < 0
            and _icu_collator.compare('a', 'A') < 0
            and _icu_collator.compare('a', 'z') < 0):
        _SORT_METHOD = 'icu'
    else:
        _icu_collator = None
except (ImportError, Exception):
    pass

# --- Level 2: locale.strxfrm（需通过验证 + CJK 不崩溃）---
if _SORT_METHOD == 'codepoint':
    _orig_locale = locale.getlocale(locale.LC_COLLATE)
    _locale_candidates = [
        'zh_CN.UTF-8', 'zh_CN.utf8', 'zh_CN',
        'Chinese_China.UTF-8', 'Chinese_China.utf8',
        '',
    ]
    for _loc in _locale_candidates:
        try:
            locale.setlocale(locale.LC_COLLATE, _loc)
            if (locale.strcoll(' ', '_') < 0
                    and locale.strcoll('a', 'A') < 0
                    and locale.strcoll('a', 'z') < 0):
                locale.strxfrm('安')  # macOS 会在此崩溃
                _SORT_METHOD = 'locale'
                break
        except (locale.Error, OSError, Exception):
            continue
    if _SORT_METHOD == 'codepoint':
        # 没找到合适的 locale，恢复原始设置
        try:
            locale.setlocale(locale.LC_COLLATE, _orig_locale)
        except Exception:
            pass

# --- Level 3: pypinyin（中文转拼音，近似排序）---
_pypinyin_mod = None
if _SORT_METHOD == 'codepoint':
    try:
        import pypinyin as _pypinyin_mod
        _SORT_METHOD = 'pypinyin'
    except ImportError:
        pass


def _pypinyin_sort_key(name):
    """用 pypinyin 将中文转拼音后生成排序键（近似匹配 Steam）"""
    result = []
    for char in name:
        if char in _STEAM_CHAR_RANK:
            result.append(_STEAM_CHAR_RANK[char])
        elif '\u4e00' <= char <= '\u9fff' or '\u3400' <= char <= '\u4dbf':
            py = _pypinyin_mod.pinyin(
                char, style=_pypinyin_mod.Style.TONE3,
                errors='default')[0][0]
            for pc in py:
                lc = pc.lower()
                if lc in _STEAM_CHAR_RANK:
                    result.append(_STEAM_CHAR_RANK[lc])
                else:
                    result.append(ord(pc) + 200)
            # 同音字按码点区分
            result.append(ord(char) + 0x10000)
        else:
            result.append(ord(char) + 200)
    return tuple(result)


def steam_sort_key(name: str):
    """生成与 Steam 客户端一致的排序键

    用法：
        items.sort(key=lambda x: steam_sort_key(x['name']))
    """
    if not name:
        return b'' if _SORT_METHOD == 'icu' else ()
    if _SORT_METHOD == 'icu':
        return _icu_collator.getSortKey(name).getByteArray()
    if _SORT_METHOD == 'locale':
        return locale.strxfrm(name)
    if _SORT_METHOD == 'pypinyin':
        return _pypinyin_sort_key(name)
    # 最后兜底：ASCII 映射表 + 码点
    return tuple(_STEAM_CHAR_RANK.get(c, ord(c) + 200) for c in name)

try:
    import urllib.request
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False


def sanitize_filename(name: str) -> str:
    """清洗文件名，替换系统禁止的特殊字符，处理空值和长度限制"""
    sanitized = re.sub(r'[\\/:*?"<>|]', '_', name)
    sanitized = sanitized.strip('. ')
    if not sanitized:
        sanitized = "untitled"
    return sanitized[:200]


def get_ssl_context():
    """获取 SSL 上下文，macOS Python 安装后未运行证书脚本时自动 fallback"""
    # 优先使用 certifi 提供的 CA 证书（解决 macOS 缺少根证书的问题）
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        ctx = ssl.create_default_context()
        return ctx
    except Exception:
        pass
    ctx = ssl._create_unverified_context()
    return ctx


def urlopen(req, timeout=30):
    """封装 urlopen，自动处理 SSL 证书问题"""
    return urllib.request.urlopen(req, timeout=timeout, context=get_ssl_context())
