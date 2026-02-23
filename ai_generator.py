"""AI 笔记生成器 — 支持 Anthropic (Claude) 和 OpenAI 兼容 API"""

import html as _html
import json
import re
import ssl
import time
import urllib.parse
from datetime import datetime

try:
    import urllib.request
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

from utils import urlopen as _urlopen
from core_notes import WARN_GOOGLE_UNAVAIL, WARN_AITOOL_UNAVAIL


# ── 客户端搜索：Startpage（Google 代理）HTML 抓取 ──

def _strip_html_tags(text):
    """去除 HTML 标签和 CSS emotion 块"""
    text = re.sub(r'\.css-[a-z0-9]+\{[^}]*\}(?:@media[^{]*\{[^}]*\})?', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return _html.unescape(text).strip().lstrip('}').strip()


def _search_startpage(query, max_results=5):
    """用 Startpage（Google 代理）搜索，返回 [{url, title, content}]"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = ("https://www.startpage.com/do/search?q="
           + urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
    })
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    results = []
    parts = page.split('data-testid="gl-title-link"')
    for i, part in enumerate(parts[1:max_results + 1], 1):
        prev = parts[i - 1][-500:]
        href_m = re.search(r'href="(https?://[^"]+)"[^<]*$', prev)
        title_m = re.search(r'>(.*?)</a>', part, re.DOTALL)
        snip_m = re.search(r'<p[^>]*>(.*?)</p>', part, re.DOTALL)
        if href_m and title_m:
            results.append({
                "url": href_m.group(1),
                "title": _strip_html_tags(title_m.group(1)),
                "content": (_strip_html_tags(snip_m.group(1))
                            if snip_m else ""),
            })
    return results


def _search_ddg_lite(query, max_results=5):
    """用 DuckDuckGo Lite 搜索，返回 [{url, title, content}]"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = ("https://lite.duckduckgo.com/lite/?q="
           + urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
    })
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    results = []
    # DDG Lite: <a href="//duckduckgo.com/l/?uddg=REAL_URL&rut=..."
    #            class='result-link'>Title</a>
    # 摘要: <td class='result-snippet'>...</td>
    links = re.findall(
        r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*class=['\"]result-link['\"]"
        r"[^>]*>(.*?)</a>",
        page, re.DOTALL)
    snippets = re.findall(
        r"<td\s+class=['\"]result-snippet['\"]>(.*?)</td>",
        page, re.DOTALL)
    for i, (raw_href, title_raw) in enumerate(links):
        if len(results) >= max_results:
            break
        # 从 DDG 重定向 URL 提取真实 URL
        m = re.search(r'uddg=([^&]+)', raw_href)
        real_url = urllib.parse.unquote(m.group(1)) if m else raw_href
        if real_url.startswith('//'):
            real_url = 'https:' + real_url
        # 跳过广告和 DDG 内部链接
        if 'duckduckgo.com' in real_url:
            continue
        snip = _strip_html_tags(snippets[i]) if i < len(snippets) else ""
        results.append({
            "url": real_url,
            "title": _strip_html_tags(title_raw),
            "content": snip,
        })
    return results


def _search_bing(query, max_results=5):
    """用 Bing RSS 搜索，返回 [{url, title, content}]"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = ("https://www.bing.com/search?format=rss&q="
           + urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
    })
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    results = []
    items = re.findall(r'<item>(.*?)</item>', page, re.DOTALL)
    for item in items[:max_results]:
        title_m = re.search(r'<title>(.*?)</title>', item)
        link_m = re.search(r'<link>(.*?)</link>', item)
        desc_m = re.search(r'<description>(.*?)</description>', item,
                           re.DOTALL)
        if title_m and link_m:
            results.append({
                "url": _html.unescape(link_m.group(1)),
                "title": _html.unescape(title_m.group(1)),
                "content": (_strip_html_tags(_html.unescape(desc_m.group(1)))
                            if desc_m else ""),
            })
    return results


def _search_web(query, max_results=5):
    """多引擎降级搜索：DDG Lite → Startpage → Bing

    相关性过滤：提取查询词，要求结果标题至少命中 2 个。
    全部被过滤时，返回命中最多的那组结果（兜底，AI 可自行过滤噪音）。
    """
    last_err = None
    best_results, best_hits = None, -1
    qwords = {w.lower() for w in re.findall(r'[a-zA-Z]{3,}', query)}
    qwords |= {query[i:i+2] for i in range(len(query) - 1)
                if all('\u4e00' <= c <= '\u9fff' for c in query[i:i+2])}
    for fn in (_search_ddg_lite, _search_startpage, _search_bing):
        try:
            results = fn(query, max_results)
            if not results:
                continue
            if len(qwords) >= 2:
                combined = ' '.join(r['title'].lower() for r in results)
                hits = sum(1 for w in qwords if w in combined)
                if hits >= 2:
                    return results
                if hits > best_hits:
                    best_results, best_hits = results, hits
            else:
                return results
        except Exception as e:
            last_err = e
    # 全部被过滤：返回命中最多的结果（兜底）
    if best_results:
        return best_results
    if last_err:
        raise last_err
    return []


# 默认系统提示词 — 来自导言区的【AI 撰写游戏说明笔记的指引】
AI_SYSTEM_PROMPT = """你是一个 Steam 游戏介绍撰写助手。请根据用户提供的游戏信息，撰写一段客观的"游戏说明"笔记。

目标读者：不一定了解独立游戏或单机游戏的普通玩家。
目的：让读者快速判断这个游戏是否符合自己的兴趣。

撰写规则（必须全部遵守）：
1. 客观描述：不能照抄商店页面的商业化宣传语，要客观地告诉读者这个游戏是什么、玩起来是什么感觉。
2. "现在打开会怎样"：必须具体描述"如果我现在立刻打开这个游戏，前几分钟会看到什么、做什么"。要写到读者脑中能浮现画面的程度——比如"打开后先是一段过场动画，然后进入角色创建，选完职业后直接被打进一片雪原，没有任何提示，你需要自己摸索怎么活下去"。❌ 绝对禁止用"上手难度适中""需要一定学习成本""有一定门槛"这类模糊概括代替具体描述。你必须回答的是"我会看到什么界面、做什么操作、遇到什么状况"，而非"难不难"。
3. 认知资源与时间需求：必须说明这个游戏需要怎样的注意力投入，让读者知道自己需要为它腾出怎样的精力和时间。是否需要大段连续时间、每局/每次游玩大概多久。
4. 网络口碑：必须提及这个游戏在网络上是否受欢迎、大致评价如何。
5. 缺点与不适人群：必须有一定篇幅介绍缺点，以及明确说明不适合什么样的人玩。
6. 不用术语、说人话：禁止使用读者可能不懂的术语而不加解释。例如不能直接说"ASCII 风格画面"或"1-bit 风格"，而应该用没玩过游戏的人都能理解的语言描述（如"画面几乎完全由彩色文字符号构成——你的角色是一个@，怪物是字母，墙壁是#号"）。术语不必刻意回避或删除，解释清楚即可。
7. 无需强调性价比：这些游戏已在用户库中，属于免费可玩，绝对禁止提及任何与价格相关的内容。禁止使用的词汇包括但不限于：价格、售价、原价、打折、折扣、性价比、值不值、定价、促销、半价、特惠、入手、购买建议。即使参考资料中大量提到这些内容，你也必须完全忽略——读者已经拥有这个游戏，任何价格讨论都是无意义的。
8. 适合的游玩情景：适合自己一个人单独玩？还是适合跟另一个朋友一起玩？适合跟一大群朋友玩？适合跟什么类型的人玩？适合什么场合——比如睡前放松、通勤途中、还是周末空出一整个下午？诸如此类。

⚠️ 关键格式要求（最高优先级）：
- 输出必须是【纯文本单行】，即整段说明写在同一行内，禁止换行。
- 禁止使用任何 BBCode 标签（[p] [h1] [b] 等全部禁止）。
- 禁止使用分段式的小标题（如"初次打开的体验："、"认知资源："等），
  而应将所有信息自然融入一段连贯的叙述中，像朋友聊天一样娓娓道来。
- 可以使用 emoji 辅助排版: 📌✅⚠️🗺️⚔️📝🎯，
  但要克制，不要每句话都加 emoji。
- 注意控制长度，建议 200-500 字左右。
- 这段纯文本将同时作为笔记的标题和内容显示在 Steam 客户端中，
  所以第一句话应该具有概括性（如"XXX 是一个……的游戏"），让人一眼能抓住重点。

📋 完成后自查清单（输出前在心里逐条核对，有遗漏必须补上）：
□ 是否具体描述了"现在打开前几分钟会看到什么、做什么"？（不是"上手难度如何"，而是具体场景）
□ 是否说明了注意力投入程度和单次游玩时长？
□ 是否提到了网络口碑/社区评价？
□ 是否有缺点和不适合的人群？
□ 是否所有术语都附带了通俗解释，没有让不懂游戏的读者感到困惑？
□ 是否有提到适合的游玩情景（跟谁玩、什么场合）？
□ 是否全文都是自然连贯的叙述，没有分段标题？
□ 是否纯文本单行，没有换行？
□ 第一句话是否有概括性？
□ 【关键】全文是否完全没有提及价格、性价比、售价、打折等与钱有关的内容？

请直接输出纯文本内容，不要输出任何解释、前缀、标签或格式符号。"""


# ── Step 1 统一搜索系统提示词（local 和 ai_web 共用） ──
# 使用时将 {tool_name} 替换为实际工具名：
#   ai_web → "web_search"，local → "search_internet"
AI_SEARCH_SYSTEM_PROMPT = """\
你是一个游戏信息收集助手。请使用联网搜索工具（{tool_name}）\
搜索关于指定 Steam 游戏的信息，然后用简体中文整理搜索到的所有有效信息。

🔍 搜索策略（非常重要）：
- ⚠️ 必须用【游戏名称】搜索，绝对不要用 AppID 或数字编号搜索
- 🔑 优先使用双引号精确搜索：先搜 "完整游戏名" game 来精确匹配。\
如果结果太少，去掉游戏名中的特殊符号（引号、分号、冒号等）后再用双引号搜一次。\
这对名字通用或容易与其他事物混淆的游戏尤其重要
- ⚠️ 消歧义：游戏名可能是常见英文单词（如 Wingspan、Limbo、Inside），\
必须在搜索词中加 game 或 Steam 来排除非游戏结果
- 必须用英文游戏名搜索至少一次（英文搜索结果通常最丰富）
- 用中文游戏名也搜索一次
- 如果游戏可能是日本开发/日文受众（如日式 RPG、视觉小说、同人游戏），\
也用日文名搜索（搜索「ゲーム名 レビュー」或「ゲーム名 感想」）
- 根据游戏的开发商/发行商国籍，判断哪种语言搜索更可能获得有效信息
- 不要仅依赖中文搜索结果，很多独立/小众游戏几乎没有中文资料
- 搜索游戏的实际游玩体验（游戏名 + review / gameplay）
- 搜索社区口碑和争议（游戏名 + reddit / 讨论）
- 搜索通关时长（游戏名 + how long to beat）
- 特别注意搜索该游戏的缺点和负面评价

📊 搜索结果质量判断：
- 游戏名称可能与其他事物同名，搜索结果中可能有大量不相关内容，这些不算有效信息
- 如果搜索结果大部分都是不相关信息，也应视为"网络有效信息不足"

整理时请涵盖以下方面（有多少写多少，没搜到的跳过）：
- 游戏核心玩法和体验是什么
- 打开游戏后前几分钟具体会看到什么、做什么
- 社区口碑和评价（好评和差评都要）
- 大致游玩时长和每次游玩时长
- 缺点和常见抱怨
- 适合什么类型的玩家

⚠️ 最终整理必须使用简体中文，即使搜索结果是英文或日文。"""


class SteamAIGenerator:
    """使用 AI API 生成游戏说明笔记 — 支持 Anthropic (Claude) 和 OpenAI 兼容 API"""

    # ── 已知 API 提供商配置 ──
    PROVIDERS = {
        'anthropic': {
            'name': 'Anthropic (Claude)',
            'api_url': 'https://api.anthropic.com/v1/messages',
            'models': [
                'claude-opus-4-6',
                'claude-opus-4-5-20251101-thinking',
                'claude-sonnet-4-5-20250929',
                'claude-haiku-4-5-20251001',
            ],
            'default_model': 'claude-sonnet-4-5-20250929',
            'key_prefix': 'sk-ant-',
        },
        'openai': {
            'name': 'OpenAI',
            'api_url': 'https://api.openai.com/v1/chat/completions',
            'models': [
                'gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-mini',
                'gpt-4.1-nano', 'o3-mini',
            ],
            'default_model': 'gpt-4o-mini',
            'key_prefix': 'sk-',
        },
        'deepseek': {
            'name': 'DeepSeek',
            'api_url': 'https://api.deepseek.com/v1/chat/completions',
            'models': ['deepseek-chat', 'deepseek-reasoner'],
            'default_model': 'deepseek-chat',
            'key_prefix': 'sk-',
        },
        'openai_compat': {
            'name': '自定义 (OpenAI 兼容)',
            'api_url': '',
            'models': [],
            'default_model': '',
            'key_prefix': '',
        },
    }

    # ── 高级参数默认值 ──
    DEFAULT_WEB_SEARCH_MAX_USES = 3       # 联网搜索次数上限
    DEFAULT_THINKING_BUDGET = 10000       # thinking 模型思维预算 (tokens)
    DEFAULT_MAX_EXTRA_CONTEXT = 3000      # 参考资料最大字符数
    DEFAULT_MAX_TOKENS = 4096             # 非 thinking 模型最大输出 tokens
    DEFAULT_MAX_TOKENS_THINKING = 16000   # thinking 模型最大输出 tokens
    DEFAULT_TIMEOUT = 120                 # 普通请求超时 (秒)
    DEFAULT_TIMEOUT_WEB_SEARCH = 180      # 联网搜索请求超时 (秒)

    def __init__(self, api_key: str, model: str = None,
                 provider: str = 'anthropic', api_url: str = None,
                 advanced_params: dict = None):
        self.api_key = api_key
        self.provider = provider
        self._last_debug_info = ""
        self.model = model or self.PROVIDERS.get(provider, {}).get(
            'default_model', 'claude-sonnet-4-5-20250929')
        # 允许自定义 API URL（用于 OpenAI 兼容的第三方服务）
        if api_url:
            self.api_url = api_url
        else:
            self.api_url = self.PROVIDERS.get(provider, {}).get(
                'api_url', self.PROVIDERS['anthropic']['api_url'])

        # ── 高级参数（从配置注入，缺省使用类默认值）──
        p = advanced_params or {}
        self.web_search_max_uses = p.get(
            'web_search_max_uses', self.DEFAULT_WEB_SEARCH_MAX_USES)
        self.thinking_budget = p.get(
            'thinking_budget', self.DEFAULT_THINKING_BUDGET)
        self.max_extra_context_chars = p.get(
            'max_extra_context', self.DEFAULT_MAX_EXTRA_CONTEXT)
        self.max_tokens = p.get(
            'max_tokens', self.DEFAULT_MAX_TOKENS)
        self.max_tokens_thinking = p.get(
            'max_tokens_thinking', self.DEFAULT_MAX_TOKENS_THINKING)
        self.timeout = p.get(
            'timeout', self.DEFAULT_TIMEOUT)
        self.timeout_web_search = p.get(
            'timeout_web_search', self.DEFAULT_TIMEOUT_WEB_SEARCH)

    @classmethod
    def detect_provider(cls, api_key: str) -> str:
        """根据 API Key 前缀自动检测提供商
        注意: 仅对明确的前缀（如 sk-ant-）自动切换，
        通用 sk- 前缀不自动切换（可能是中转服务的 Key）。
        """
        key = api_key.strip()
        if key.startswith('sk-ant-'):
            return 'anthropic'
        # 通用 sk- 开头的 Key 不再自动切换，因为中转服务也可能使用 sk- 前缀
        # 用户需要手动选择提供商
        return None  # 返回 None 表示无法自动检测

    # 参考资料最大长度（字符数）— 超过此长度会截断评测文本，
    # 避免大量参考资料"淹没"格式和内容指令
    # （已移至高级参数 DEFAULT_MAX_EXTRA_CONTEXT / self.max_extra_context_chars）
    MAX_EXTRA_CONTEXT_CHARS = 3000  # 保留为类属性兼容旧代码

    def generate_note(self, game_name: str, app_id: str,
                      extra_context: str = "",
                      system_prompt: str = "",
                      use_web_search: bool = False,
                      web_search_mode: str = None) -> tuple:
        """为单个游戏生成笔记内容

        Returns: (text: str, model: str, confidence: str,
                  info_volume: str, is_insufficient: bool, quality: str)

        消息结构设计原则（v6.0）：
        - LLM 对消息的【开头】和【末尾】最为敏感
        - 参考资料（评测、商店详情）放在中间
        - 联网搜索触发指令放在参考资料之前（让模型先搜索再看资料）
        - 内容要求清单和格式要求放在消息【最末尾】（最高优先级位置）
        - 元数据输出格式放在内容要求之前（次优先级）
        """
        # ── 向后兼容：旧值映射到新两模式 ──
        _compat = {"off": "local", "server": "ai_web", "client": "local"}
        if web_search_mode is None:
            web_search_mode = "ai_web" if use_web_search else "local"
        else:
            web_search_mode = _compat.get(web_search_mode, web_search_mode)
        # anthropic 和 openai_compat（代理）支持搜索；openai/deepseek 原生不支持
        _ws_active = self.provider in ('anthropic', 'openai_compat')

        # ── 第一段：任务声明 ──
        user_msg = "请为以下 Steam 游戏撰写游戏说明笔记：\n\n"
        user_msg += f"游戏名称：{game_name}\n"
        user_msg += f"Steam AppID：{app_id}\n"

        # ── 第二段：参考资料（中间位置，被指令包裹）──
        if extra_context:
            # 截断过长的参考资料
            if len(extra_context) > self.max_extra_context_chars:
                extra_context = (extra_context[:self.max_extra_context_chars]
                                 + "\n…（参考资料已截断）")
            user_msg += ("\n"
                         "╔═════ 以下是参考资料（仅供参考，严禁照抄或逐条总结）═════╗\n"
                         f"{extra_context}\n"
                         "╚═════ 参考资料结束 ═════╝\n"
                         "\n"
                         "⚠️ 重要提醒：以上参考资料只是帮你了解这个游戏的素材。\n"
                         "你的任务是用自己的话写一段连贯自然的游戏说明，"
                         "像朋友聊天一样娓娓道来。不要变成「评测摘要」或「信息罗列」。\n")

        # ── 第三段：元数据输出格式 ──
        user_msg += ("\n在你的回复最末尾，用以下格式逐行标注元数据（每行一个标签）：\n"
                     "\n")

        # 信息量评估指引
        if _ws_active:
            user_msg += (
                'INFO_VOLUME:（请综合「上面提供的 Steam 参考资料」和「你的联网搜索结果」'
                '来判断你掌握的「与这个游戏本身直接相关」的有效信息量——'
                '注意，游戏名可能搜出很多不相关的结果，'
                '只有确实在讨论这个游戏本身的玩法、评价、体验等内容才算有效信息。'
                '如果 Steam 参考资料和搜索结果合计有效信息很丰富就写"相当多"，'
                '几乎没有相关信息就写"相当少"。'
                '可选值：相当多 / 较多 / 中等 / 较少 / 相当少）\n')
        else:
            user_msg += (
                'INFO_VOLUME:（请根据上面提供的参考资料（Steam 商店详情 + 玩家评测）'
                '以及你自身训练数据中对这个游戏的了解程度，综合判断你掌握的'
                '「与这个游戏本身直接相关」的有效信息量——注意，有些 Steam 评测可能'
                '是玩笑、与游戏内容无关或信息量极低，这类不算有效信息。'
                '如果有效信息很丰富就写"相当多"，'
                '几乎没有有效信息就写"相当少"。'
                '可选值：相当多 / 较多 / 中等 / 较少 / 相当少）\n')

        user_msg += (
            'INSUFFICIENT:（如果你掌握的有效信息实在太少，以至于你认为'
            '绝对不可能写出一段有意义的、对读者有帮助的游戏说明，就写 true。'
            '只要还能写出大致靠谱的介绍就写 false。这是一个很高的门槛——'
            '只有真的几乎一无所知时才写 true。'
            '⚠️ 特别注意：如果联网搜索信息不足但上面提供的 Steam 评测内容'
            '仍有足够参考价值，你应该基于评测内容撰写说明并写 false。'
            '只有联网搜索和 Steam 评测都严重不足时才写 true。）\n'
            'CONFIDENCE:很高 或 CONFIDENCE:较高 或 CONFIDENCE:中等 '
            '或 CONFIDENCE:较低 或 CONFIDENCE:很低\n'
            '（确信程度取决于你对这个游戏的了解程度——'
            '如果这个游戏你很熟悉、信息确定性高就写"很高"，'
            '如果是比较冷门/不太了解的游戏就写"较低"或"很低"。）\n'
            'QUALITY:相当好 或 QUALITY:较好 或 QUALITY:中等 '
            '或 QUALITY:较差 或 QUALITY:相当差\n'
            '（游戏总体质量是你综合所有信息后对这个游戏质量的客观判断——'
            '包括玩法设计、内容量、完成度、社区口碑等。'
            '如果是口碑极好的精品就写"相当好"，'
            '如果是质量堪忧的游戏就写"较差"或"相当差"。'
            '⚠️ 注意：这是对游戏本身质量的评估，不是对你写的说明的评估。）\n'
            '\n'
            '⚠️ 如果你判定 INSUFFICIENT:true，则不需要输出游戏说明正文，'
            '只需要输出上面四行元数据标签即可。\n'
        )

        # ── 第四段（最末尾 = 最高优先级）：内容要求清单 + 格式要求 ──
        # 这是用户消息的最后部分，LLM 对此最为敏感
        user_msg += (
            "\n"
            "════════════════════════════════════════\n"
            "📋 以下是你【必须遵守】的内容要求和格式要求（最高优先级）：\n"
            "════════════════════════════════════════\n"
            "\n"
            "【内容要求清单】— 缺一不可，输出前逐条自查：\n"
            "□ 第一句话是否有概括性（如「XXX 是一款……的游戏」）？\n"
            "□ 是否具体描述了「现在打开这个游戏，前几分钟会看到什么、做什么」？"
            "（❌ 禁止用「上手难度适中」「有一定门槛」等模糊概括代替！）\n"
            "□ 是否说明了注意力投入程度和单次游玩时长？\n"
            "□ 是否提到了网络口碑 / 社区评价？\n"
            "□ 是否有缺点和不适合的人群？\n"
            "□ 是否说了适合的游玩情景（跟谁玩、什么场合）？\n"
            "□ 是否所有术语都附带了通俗解释？\n"
            "□ 全文是否完全没有提及价格、性价比、打折等与钱相关的内容？\n"
            "\n"
            "【格式要求】— 违反任何一条都是不合格的输出：\n"
            "✦ 纯文本单行，禁止换行\n"
            "✦ 禁止 BBCode 标签（[p] [h1] [b] 等全部禁止）\n"
            "✦ 禁止 Markdown 格式（禁止 **粗体**、## 标题等）\n"
            "✦ 禁止分段式小标题（如「初次打开的体验：」「认知资源：」），"
            "所有信息融入一段连贯叙述\n"
            "✦ 可适度使用 emoji（📌✅⚠️🗺️⚔️📝🎯）但要克制\n"
            "✦ 建议 200-500 字\n"
            "✦ 必须使用简体中文\n"
            "\n"
            "请直接输出游戏说明正文（上述内容清单全部覆盖），"
            "然后在末尾附上四行元数据标签。不要输出任何解释或前缀。"
        )

        prompt = system_prompt.strip() if system_prompt.strip() else AI_SYSTEM_PROMPT

        if self.provider == 'anthropic':
            return self._call_anthropic(prompt, user_msg,
                                        web_search_mode=web_search_mode)
        else:
            return self._call_openai_compat(prompt, user_msg,
                                            use_web_search=_ws_active)

    def _call_anthropic(self, system_prompt: str, user_msg: str,
                        web_search_mode: str = "local") -> tuple:
        """调用 Anthropic (Claude) API

        两步法（搜索-写作分离）：
        Step 1（搜索阶段）：用轻量化提示词让模型专注于搜索和信息收集
        Step 2（写作阶段）：用完整提示词让模型专注于遵循格式/内容要求撰写笔记
        """
        self._last_search_warn = ""

        # ═══════════════════════════════════════════════════════════
        #  联网搜索：强制两步法（搜索-写作分离）
        # ═══════════════════════════════════════════════════════════
        # 根本问题：web_search 会注入大量搜索结果到上下文，导致模型对
        # 格式/内容指令的注意力被严重稀释，退化为"搜索结果摘要器"。
        # 解决：让 Step 1 专注搜索、Step 2 专注写作，互不干扰。

        # ── Step 1：搜索阶段（统一提示词 + 搜索工具）──
        # 只要求模型搜索和整理信息，不要求遵循复杂的格式/内容规则
        _name_match = re.search(r'游戏名称：(.+)', user_msg)
        _appid_match = re.search(r'Steam AppID：(\S+)', user_msg)
        _game_name = _name_match.group(1).strip() if _name_match else "未知游戏"
        _app_id = _appid_match.group(1).strip() if _appid_match else ""

        # 根据模式选择工具名，填充统一搜索提示词
        _tool_name = ("search_internet"
                      if web_search_mode == "local" else "web_search")
        _search_system = AI_SEARCH_SYSTEM_PROMPT.format(tool_name=_tool_name)

        # Step 1 user message：只传游戏名称，不传 AppID（避免搜出垃圾）
        _search_user_msg = (
            f"请搜索以下 Steam 游戏的信息，并整理你的搜索发现：\n\n"
            f"游戏名称：{_game_name}\n"
        )

        # 把 Steam 评测也放进 Step 1（帮助模型了解游戏、减少无效搜索）
        _ref_match = re.search(
            r'(╔═════ 以下是参考资料.*?╚═════ 参考资料结束 ═════╝)',
            user_msg, re.DOTALL)
        if _ref_match:
            _search_user_msg += (
                f"\n以下是已有的 Steam 参考资料，可帮助你了解这个游戏、"
                f"让你的搜索更有针对性：\n{_ref_match.group(1)}\n")

        _search_user_msg += (
            "\n请开始联网搜索，然后用简体中文整理你搜索到的所有有效信息。")

        self._last_debug_info = ""
        _step1_error = ""
        try:
            if web_search_mode == "local":
                step1_result = self._call_anthropic_client_search(
                    _search_system, _search_user_msg)
            else:  # ai_web
                step1_result = self._call_anthropic_inner(
                    _search_system, _search_user_msg, use_web_search=True)
            _step1_text = step1_result[0] or ""
            if not _step1_text.strip():
                _step1_error = "搜索阶段返回空结果"
        except Exception as exc:
            _step1_text = ""
            _step1_error = f"联网搜索出错: {exc}"

        _step1_debug = self._last_debug_info

        # 联网搜索失败 → 记录警告，Step 2 将基于 Steam 数据 + 训练数据生成
        if _step1_error:
            self._last_search_warn = (
                WARN_GOOGLE_UNAVAIL if web_search_mode == "local"
                else WARN_AITOOL_UNAVAIL)
            _step1_text = ""
        elif not getattr(self, '_last_search_actually_used', True):
            # Step 1 没报错但搜索工具未被使用（如 key 无权限）
            self._last_search_warn = (
                WARN_GOOGLE_UNAVAIL if web_search_mode == "local"
                else WARN_AITOOL_UNAVAIL)
            _step1_text = ""  # 清空错误内容，避免污染 Step 2

        # ── Step 2：写作阶段（完整提示词，无 web_search 工具）──
        # 模型全部注意力集中在遵循格式/内容指令上

        # 清理 Step 1 输出中的引用标记
        _cleaned_step1 = re.sub(
            r'\s*\[\s*\d+(?:\s*[,，]\s*\d+)*\s*\]', '', _step1_text)

        # 构造 Step 2 的 user message
        _write_user_msg = (
            f"请为以下 Steam 游戏撰写游戏说明笔记：\n\n"
            f"游戏名称：{_game_name}\n"
        )
        if _app_id:
            _write_user_msg += f"Steam AppID：{_app_id}\n"

        # 联网搜索结果作为参考资料
        _write_user_msg += (
            f"\n╔═════ 联网搜索收集到的信息（仅供参考，严禁照抄或逐条总结）═════╗\n"
            f"{_cleaned_step1}\n"
            f"╚═════ 搜索信息结束 ═════╝\n"
            f"\n"
            f"⚠️ 重要提醒：以上联网搜索信息只是帮你了解这个游戏的素材。\n"
            f"你的任务是用自己的话写一段连贯自然的游戏说明，"
            f"像朋友聊天一样娓娓道来。不要变成「评测摘要」或「信息罗列」。\n"
        )

        # Steam 评测也作为参考资料
        if _ref_match:
            _write_user_msg += f"\n{_ref_match.group(1)}\n"
            _write_user_msg += (
                "\n⚠️ 重要提醒：以上参考资料只是帮你了解这个游戏的素材。\n"
                "你的任务是用自己的话写一段连贯自然的游戏说明。\n")

        # 元数据输出要求（从原始 user_msg 中截取）
        _meta_match = re.search(
            r'(在你的回复最末尾.*?)(?=\n*════)', user_msg, re.DOTALL)
        if _meta_match:
            _write_user_msg += f"\n{_meta_match.group(1)}\n"
        else:
            # 简化版元数据要求
            _write_user_msg += (
                "\n在你的回复最末尾，用以下格式逐行标注元数据（每行一个标签）：\n\n"
                "INFO_VOLUME:（根据上面联网搜索收集到的信息量判断。"
                "可选值：相当多 / 较多 / 中等 / 较少 / 相当少）\n"
                "INSUFFICIENT:（如果信息实在太少无法写出有意义的说明就写 true，"
                "否则写 false。只要还能写出大致靠谱的介绍就写 false。）\n"
                "CONFIDENCE:（很高 / 较高 / 中等 / 较低 / 很低）\n"
                "QUALITY:（相当好 / 较好 / 中等 / 较差 / 相当差）\n"
            )

        # 内容要求清单 + 格式要求（从原始 user_msg 中截取，这是最高优先级位置）
        _checklist_match = re.search(
            r'(════+\n📋 以下是你【必须遵守】.*?)$', user_msg, re.DOTALL)
        if _checklist_match:
            _write_user_msg += f"\n{_checklist_match.group(1)}"
        else:
            # 手动添加简化版内容/格式要求
            _write_user_msg += (
                "\n"
                "════════════════════════════════════════\n"
                "📋 以下是你【必须遵守】的内容要求和格式要求（最高优先级）：\n"
                "════════════════════════════════════════\n"
                "\n"
                "【内容要求清单】— 缺一不可：\n"
                "□ 第一句话有概括性（如「XXX 是一款……的游戏」）\n"
                "□ 具体描述「现在打开这个游戏，前几分钟会看到什么、做什么」\n"
                "□ 说明注意力投入程度和单次游玩时长\n"
                "□ 提到网络口碑 / 社区评价\n"
                "□ 有缺点和不适合的人群\n"
                "□ 适合的游玩情景（跟谁玩、什么场合）\n"
                "□ 所有术语附带通俗解释\n"
                "□ 全文没有提及价格相关内容\n"
                "\n"
                "【格式要求】— 违反任何一条都是不合格的输出：\n"
                "✦ 纯文本单行，禁止换行\n"
                "✦ 禁止 BBCode / Markdown\n"
                "✦ 禁止分段式小标题，所有信息融入一段连贯叙述\n"
                "✦ 禁止引用标记如 [1] [2,3]\n"
                "✦ 可适度使用 emoji 但要克制\n"
                "✦ 建议 200-500 字，必须使用简体中文\n"
                "\n"
                "请直接输出游戏说明正文，然后在末尾附上元数据标签。"
            )

        # Step 2 调用：无 web_search，纯写作提示词
        result = self._call_anthropic_inner(
            system_prompt, _write_user_msg, use_web_search=False)

        # 合并两步调试信息
        self._last_debug_info = (
            "=== Step 1（联网搜索收集信息）===\n"
            + _step1_debug
            + f"\n\nStep 1 输出（前 300 字）：{_step1_text[:300]}…\n"
            + "\n\n=== Step 2（撰写游戏说明）===\n"
            + self._last_debug_info
            + "\n✅ 搜索-写作分离两步法完成。\n"
        )

        return result

    def _build_anthropic_headers(self, is_proxy: bool) -> dict:
        """构建 Anthropic API 请求头"""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SteamNotesGen/5.9",
            "Accept": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        if is_proxy:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_anthropic_payload(self, system_prompt: str, messages: list,
                                 is_thinking: bool, tools: list = None,
                                 is_proxy: bool = False) -> dict:
        """构建 Anthropic API 请求体"""
        payload = {
            "model": self.model,
            "max_tokens": (self.max_tokens_thinking
                           if is_thinking else self.max_tokens),
            "system": system_prompt,
            "messages": messages,
        }
        if is_thinking:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            }
        if tools:
            payload["tools"] = tools
        return payload

    def _call_anthropic_client_search(self, system_prompt: str,
                                      user_msg: str) -> tuple:
        """客户端搜索工具循环：模型发 tool_use → 本地 Startpage 搜索 → 喂回结果"""
        is_thinking = 'thinking' in self.model.lower()
        _default_url = self.PROVIDERS['anthropic']['api_url']
        _is_proxy = (self.api_url != _default_url)

        headers = self._build_anthropic_headers(_is_proxy)

        # 代理防护：将系统提示词注入用户消息（同 _call_anthropic_inner）
        _actual_user_msg = user_msg
        if _is_proxy:
            _actual_user_msg = (
                "【系统指令 — 请严格遵守以下全部要求】\n"
                f"{system_prompt}\n"
                "【系统指令结束】\n\n"
                f"{user_msg}"
            )
        messages = [{"role": "user", "content": _actual_user_msg}]

        # 定义客户端搜索工具
        tool_def = {
            "name": "search_internet",
            "description": "Search the internet using a query string.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "The search query"}
                },
                "required": ["query"],
            },
        }

        debug_parts = []
        self._last_search_actually_used = False
        for turn in range(self.web_search_max_uses + 2):
            payload = self._build_anthropic_payload(
                system_prompt, messages, is_thinking,
                tools=[tool_def])

            req = urllib.request.Request(
                self.api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers, method="POST")

            self._last_debug_info = self._build_debug_info(
                url=self.api_url, headers=headers,
                payload=payload, method="POST")

            with _urlopen(req, timeout=self.timeout_web_search) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            debug_parts.append(
                f"Turn {turn}: stop={data.get('stop_reason')}")

            # 如果模型结束对话，提取最终文本
            if data.get("stop_reason") == "end_turn":
                break

            # 提取 tool_use 块并执行搜索
            content = data.get("content", [])
            messages.append({"role": "assistant", "content": content})

            tool_results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                query = block.get("input", {}).get("query", "")
                debug_parts.append(f"  搜索: {query}")
                try:
                    results = _search_web(query)
                    result_text = "\n".join(
                        f"[{r['title']}]({r['url']}): {r['content']}"
                        for r in results) or "No results found."
                    if results:
                        self._last_search_actually_used = True
                except Exception as e:
                    result_text = f"Search error: {e}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result_text,
                })

            if not tool_results:
                break
            messages.append({"role": "user", "content": tool_results})

        # ── 兜底：模型未调用搜索工具时，直接用本地搜索引擎 ──
        # 代理/中转服务可能不支持 tool_use，导致模型从不调用 search_internet
        fb_text = ""
        if not self._last_search_actually_used:
            _gn = re.search(r'游戏名称：(.+)', user_msg)
            _fallback_q = _gn.group(1).strip() if _gn else ""
            if _fallback_q:
                # 多查询变体：防止单一查询被相关性过滤拦截
                # （如 Bing 对 "Happy Live Show Up game review" 返回词典释义）
                for _fq in [f"{_fallback_q} game review",
                            f"{_fallback_q} Steam game",
                            f'"{_fallback_q}"']:
                    debug_parts.append(f"  兜底搜索: {_fq}")
                    try:
                        fb = _search_web(_fq)
                        if fb:
                            self._last_search_actually_used = True
                            fb_text = "\n".join(
                                f"[{r['title']}]({r['url']}): {r['content']}"
                                for r in fb)
                            debug_parts.append(
                                f"  兜底成功: {len(fb)} 条结果")
                            break
                    except Exception as e:
                        debug_parts.append(f"  兜底失败: {e}")

        self._last_debug_info = "\n".join(debug_parts)

        # 从最终响应提取文本
        text_parts = [b["text"] for b in data.get("content", [])
                      if b.get("type") == "text"]
        full_text = "\n".join(text_parts)

        # 兜底搜索结果追加到模型输出前面
        if fb_text:
            full_text = fb_text + "\n" + full_text

        # 兼容 OpenAI 格式代理
        if not full_text and data.get("choices"):
            full_text = (data["choices"][0]
                         .get("message", {}).get("content", ""))

        return self._extract_confidence(
            full_text, data.get("model", self.model))

    def _call_anthropic_inner(self, system_prompt: str, user_msg: str,
                              use_web_search: bool = False) -> tuple:
        """调用 Anthropic (Claude) API 的内部实现"""
        is_thinking = 'thinking' in self.model.lower()

        # 检测是否通过第三方代理（自定义URL）
        _default_url = self.PROVIDERS['anthropic']['api_url']
        _is_proxy = (self.api_url != _default_url)

        # ── 代理防护：将系统提示词注入用户消息 ──
        # 第三方代理（new-api/one-api 等）在转发 Anthropic 请求时，
        # 经常丢弃或截断 "system" 字段，导致模型完全看不到提示词。
        # 解决方案：代理场景下，将系统提示词作为用户消息的开头注入，
        # 同时保留原始 "system" 字段（兼容正确处理 system 的代理）。
        _actual_user_msg = user_msg
        if _is_proxy:
            _actual_user_msg = (
                "【系统指令 — 请严格遵守以下全部要求】\n"
                f"{system_prompt}\n"
                "【系统指令结束】\n\n"
                f"{user_msg}"
            )

        payload_dict = {
            "model": self.model,
            "max_tokens": self.max_tokens_thinking if is_thinking else self.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": _actual_user_msg}]
        }

        # thinking 模型需要额外参数
        if is_thinking:
            payload_dict["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget
            }

        # Web Search 工具
        if use_web_search:
            payload_dict["tools"] = [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self.web_search_max_uses,
                }
            ]

        payload = json.dumps(payload_dict).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SteamNotesGen/5.9",
            "Accept": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        # 第三方代理（new-api/one-api 等）通常需要 Bearer 认证
        # 同时发送两种认证头以兼容官方 API 和各类代理
        if _is_proxy:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Web Search 需要 beta header
        if use_web_search:
            headers["anthropic-beta"] = "web-search-2025-03-05"

        req = urllib.request.Request(
            self.api_url,
            data=payload,
            headers=headers,
            method="POST",
        )

        # 构建调试信息（在异常时使用）
        self._last_debug_info = self._build_debug_info(
            url=self.api_url, headers=headers, payload=payload_dict,
            method="POST"
        )

        # 联网搜索时 AI 需要更多时间（多次搜索+综合）
        _timeout = self.timeout_web_search if use_web_search else self.timeout
        with _urlopen(req, timeout=_timeout) as resp:
            resp_body = resp.read().decode("utf-8")
            self._last_debug_info += (
                f"\n--- 响应 ---\n"
                f"HTTP 状态码: {resp.status}\n"
                f"响应头: {dict(resp.headers)}\n"
                f"响应体 (前500字): {resp_body[:500]}\n"
            )
            data = json.loads(resp_body)

        content_blocks = data.get("content", [])
        # 检测搜索工具是否真的被成功使用
        # 代理可能返回 web_search_tool_result 但 is_error=True（如 token 不足），
        # 此时搜索块存在但实际未获取到任何信息，不算"已使用"
        if use_web_search:
            search_results = [b for b in content_blocks
                              if b.get("type") == "web_search_tool_result"]
            self._last_search_actually_used = (
                bool(search_results)
                and not all(b.get("is_error") for b in search_results))
        text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]

        # ── 关键：联网搜索时只取最后一个有实质内容的 text block ──
        # 启用 web search 后，API 返回的 content 数组包含多个 text block：
        #   text("Let me search...")  →  tool_use  →  tool_result  →
        #   text("Based on my search...")  →  tool_use  →  tool_result  →
        #   text("《游戏名》是一款……CONFIDENCE:……")   ← 这才是正文
        # 中间的 text block 是 AI 的思考/计划性文字，不是游戏说明。
        # 只有最后一个 text block 包含我们需要的正文和元数据标签。
        if use_web_search and len(text_parts) > 1:
            full_text = self._select_best_text_block(text_parts)
        else:
            full_text = "\n".join(text_parts)

        # 兼容：第三方代理可能返回 OpenAI 格式（choices[0].message.content）
        if not full_text and data.get("choices"):
            choices = data["choices"]
            if choices:
                full_text = choices[0].get("message", {}).get("content", "")

        actual_model = data.get("model", self.model)

        return self._extract_confidence(full_text, actual_model)

    def _call_openai_compat(self, system_prompt: str, user_msg: str,
                            use_web_search: bool = False) -> tuple:
        """调用 OpenAI 兼容 API (OpenAI, DeepSeek, 及其他兼容服务)"""
        payload_dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
        }

        # Web Search 工具（是否可用取决于中转服务商）
        if use_web_search:
            payload_dict["tools"] = [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self.web_search_max_uses,
                }
            ]
        payload = json.dumps(payload_dict).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SteamNotesGen/5.9",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # Web Search 需要 beta header（部分中转会透传给上游 Anthropic）
        if use_web_search:
            headers["anthropic-beta"] = "web-search-2025-03-05"

        req = urllib.request.Request(
            self.api_url,
            data=payload,
            headers=headers,
            method="POST",
        )

        # 构建调试信息
        self._last_debug_info = self._build_debug_info(
            url=self.api_url, headers=headers, payload=payload_dict,
            method="POST"
        )

        _timeout = self.timeout_web_search if use_web_search else self.timeout
        with _urlopen(req, timeout=_timeout) as resp:
            resp_body = resp.read().decode("utf-8")
            self._last_debug_info += (
                f"\n--- 响应 ---\n"
                f"HTTP 状态码: {resp.status}\n"
                f"响应头: {dict(resp.headers)}\n"
                f"响应体 (前500字): {resp_body[:500]}\n"
            )
            data = json.loads(resp_body)

        full_text = ""

        # 优先尝试 OpenAI 格式: data.choices[0].message.content
        choices = data.get("choices", [])
        if choices:
            full_text = choices[0].get("message", {}).get("content", "")

        # 兼容 Anthropic 原生格式（部分中转直接透传）
        if not full_text and data.get("content"):
            content_blocks = data.get("content", [])
            text_parts = [b["text"] for b in content_blocks
                          if b.get("type") == "text"]
            # 联网搜索时只取最后一个有实质内容的 text block（同 _call_anthropic）
            if use_web_search and len(text_parts) > 1:
                full_text = self._select_best_text_block(text_parts)
            else:
                full_text = "\n".join(text_parts)

        actual_model = data.get("model", self.model)

        return self._extract_confidence(full_text, actual_model)

    @staticmethod
    def _select_best_text_block(text_parts: list) -> str:
        """从多个 text block 中选择包含正文的那个。

        联网搜索时 API 返回多个 text block，其中大部分是 AI 的搜索计划
        和思考文字，只有一个（通常是最后一个）包含实际的游戏说明正文。

        选择策略（按优先级）：
        1. 从后往前找第一个同时包含元数据标签和足够中文内容的 block
        2. 从后往前找第一个包含元数据标签的 block
        3. 从后往前找第一个包含足够中文内容（≥30字）的 block
        4. 取最后一个非空 block
        """
        _meta_pattern = re.compile(
            r'(?:CONFIDENCE|QUALITY|INFO_VOLUME|INSUFFICIENT)[:：]')

        # 策略1：同时有元数据标签 + 足够中文（最理想）
        for i in range(len(text_parts) - 1, -1, -1):
            part = text_parts[i]
            if (_meta_pattern.search(part)
                    and len(re.findall(r'[\u4e00-\u9fff]', part)) >= 30):
                return part

        # 策略2：只有元数据标签
        for i in range(len(text_parts) - 1, -1, -1):
            if _meta_pattern.search(text_parts[i]):
                return text_parts[i]

        # 策略3：足够的中文内容
        for i in range(len(text_parts) - 1, -1, -1):
            if (text_parts[i].strip()
                    and len(re.findall(r'[\u4e00-\u9fff]', text_parts[i])) >= 30):
                return text_parts[i]

        # 策略4：最后一个非空 block
        for i in range(len(text_parts) - 1, -1, -1):
            if text_parts[i].strip():
                return text_parts[i]

        return text_parts[-1] if text_parts else ""

    def _build_debug_info(self, url: str, headers: dict, payload: dict,
                          method: str = "POST") -> str:
        """构建调试信息字符串（脱敏）"""
        safe_headers = {}
        for k, v in headers.items():
            if k.lower() in ("x-api-key", "authorization"):
                if len(v) > 16:
                    safe_headers[k] = v[:10] + "..." + v[-4:]
                else:
                    safe_headers[k] = v[:4] + "..."
            else:
                safe_headers[k] = v

        safe_payload = dict(payload)
        if "system" in safe_payload and len(str(safe_payload["system"])) > 200:
            safe_payload["system"] = str(safe_payload["system"])[:200] + "...(截断)"
        if "messages" in safe_payload:
            safe_msgs = []
            for m in safe_payload["messages"]:
                sm = dict(m)
                if len(str(sm.get("content", ""))) > 300:
                    sm["content"] = str(sm["content"])[:300] + "...(截断)"
                safe_msgs.append(sm)
            safe_payload["messages"] = safe_msgs

        lines = [
            "=== API 调试信息 ===",
            f"时间: {datetime.now().isoformat()}",
            f"提供商: {self.provider}",
            f"模型: {self.model}",
            f"API URL: {url}",
            f"HTTP 方法: {method}",
            f"请求头: {json.dumps(safe_headers, ensure_ascii=False, indent=2)}",
            f"请求体: {json.dumps(safe_payload, ensure_ascii=False, indent=2)}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _extract_confidence(full_text: str, actual_model: str) -> tuple:
        """从 AI 输出中提取确信程度、信息量评估、信息不足标记和质量评估
        
        Returns: (text, model, confidence, info_volume, is_insufficient, quality)
        """
        confidence = "中等"
        info_volume = "中等"
        is_insufficient = False
        quality = "中等"

        # ── 先记录第一个元数据标签的位置（用于后续正文定位） ──
        # 必须在剥离元数据之前记录，否则锚点信息会丢失
        _first_meta_pos = None
        _meta_pos_match = re.search(
            r'(?:^|\n)\s*(?:INFO_VOLUME|INSUFFICIENT|CONFIDENCE|QUALITY)[:：]',
            full_text, re.MULTILINE
        )
        if _meta_pos_match:
            _first_meta_pos = _meta_pos_match.start()

        # 提取 INSUFFICIENT 标记
        insuf_match = re.search(
            r'INSUFFICIENT[:：]\s*(true|false|是|否)',
            full_text, re.IGNORECASE
        )
        if insuf_match:
            val = insuf_match.group(1).lower()
            is_insufficient = val in ('true', '是')
            full_text = re.sub(r'\n*INSUFFICIENT[:：].*$', '', full_text,
                               flags=re.MULTILINE).strip()

        # 提取 INFO_VOLUME 标记
        vol_match = re.search(
            r'INFO_VOLUME[:：]\s*(相当多|较多|中等|较少|相当少)',
            full_text
        )
        if vol_match:
            info_volume = vol_match.group(1)
            full_text = re.sub(r'\n*INFO_VOLUME[:：].*$', '', full_text,
                               flags=re.MULTILINE).strip()

        # 提取 QUALITY 标记
        qual_match = re.search(
            r'QUALITY[:：]\s*(相当好|较好|中等|较差|相当差)',
            full_text
        )
        if qual_match:
            quality = qual_match.group(1)
            full_text = re.sub(r'\n*QUALITY[:：].*$', '', full_text,
                               flags=re.MULTILINE).strip()

        # 提取 CONFIDENCE 标记
        conf_match = re.search(
            r'CONFIDENCE[:：]\s*(很高|较高|中等|较低|很低|相当高|相当低)',
            full_text
        )
        if conf_match:
            confidence = conf_match.group(1)
            full_text = re.sub(r'\n*CONFIDENCE[:：].*$', '', full_text,
                               flags=re.MULTILINE).strip()

        # ── 清理第三方代理（中转服务）可能泄露的原始工具调用标记 ──
        # 某些代理未正确拆分 content blocks，将 <function_calls>、<invoke>、
        # <thinking>、<search_results> 等 XML 标签作为纯文本混入 text 块中。
        # 必须在提取正文前彻底清除，否则会出现在最终笔记中。
        # 1. 移除完整的 XML 块（含内容），包括 <parameter> 块
        for tag in ('function_calls', 'invoke', 'thinking', 'search_results',
                     'search_quality_reflection', 'result', 'parameter',
                     'antml:thinking', 'antml:function_calls', 'antml:invoke',
                     'antml:parameter', 'tool_result', 'tool_use',
                     'tool_call', 'tool_calls'):
            full_text = re.sub(
                rf'<{re.escape(tag)}[^>]*>.*?</{re.escape(tag)}>',
                '', full_text, flags=re.DOTALL
            )
        # 2. 移除残余的自闭合或孤立 XML 标签（如 </invoke> </function_calls> 等）
        full_text = re.sub(
            r'</?(?:function_calls|invoke|thinking|parameter|search_results|'
            r'search_quality_reflection|result|antml:\w+|tool_result|tool_use|'
            r'tool_call|tool_calls)[^>]*>',
            '', full_text
        )
        
        # 3. 清除裸露的 JSON 格式工具调用（XML 标签被部分清除后可能残留）
        full_text = re.sub(
            r'\s*\{\s*"name"\s*:\s*"web_search"\s*,\s*"arguments"\s*:\s*\{[^}]*\}\s*\}\s*',
            '', full_text
        )
        
        full_text = full_text.strip()

        # ═══════════════════════════════════════════════════════════════
        # 核心清理：定位中文正文起始位置，丢弃之前的非正文内容
        # ═══════════════════════════════════════════════════════════════
        # 联网搜索时，AI 的输出可能是：
        #   [英文思考/搜索计划] + [中文正文] + [元数据标签]
        # 或者中转服务把所有 text block 合并后变成一大段混合文本。
        #
        # 策略：找到第一个中文字符的位置，然后往前回溯到该句子的起始位置
        # （游戏名可能是英文如 "Hollow Knight 是一款..."），
        # 丢弃之前所有的英文思考/搜索计划文字。
        #
        # 这比逐条正则匹配英文前缀稳定得多，因为不需要穷举 AI 可能说的
        # 每一种英文思考句式。
        
        _first_cn = re.search(r'[\u4e00-\u9fff]', full_text)
        
        if _first_cn and _first_cn.start() > 0:
            # 第一个中文字符之前有非中文内容（可能是思考性前缀）
            _first_cn_pos = _first_cn.start()
            _text_before_cn = full_text[:_first_cn_pos]
            
            # 从第一个中文字符往前回溯，寻找"游戏说明句"的起始位置
            # 游戏名可能是英文（如 "Hollow Knight 是一款..."）
            # 也可能是带特殊字符的（如 ".T.E.S.T: Expected Behaviour 是一款..."）
            #
            # 找最后一个英文句子结尾（句号+空格），排除游戏名中的点号
            _best_boundary = 0
            
            for _sb in re.finditer(
                r'(?<![.A-Z])'     # 前面不是点号或大写字母（排除 .T.E.S.T 等）
                r'\.'              # 句号
                r'(?!\.)'          # 后面不是点号（排除省略号 ...）
                r'\s+',            # 后跟空白
                _text_before_cn
            ):
                _best_boundary = _sb.end()
            
            # 也检查换行作为边界
            for _nl in re.finditer(r'\n\s*', _text_before_cn):
                if _nl.end() > _best_boundary:
                    _best_boundary = _nl.end()
            
            if _best_boundary > 0:
                full_text = full_text[_best_boundary:].lstrip()

        full_text = full_text.strip()

        # 兜底：如果上面的锚点方法没生效（如正文本身就是英文），
        # 逐句清理残余的明显思考性前缀
        _changed = True
        while _changed:
            _changed = False
            for pattern in (
                # 英文思考/计划性句子（宽泛匹配：以常见 AI 思考开头词起始的英文句子）
                r"^(?:I'll |I will |Let me |I need to |I should |I'm going to |"
                r"I have |I now have |The game'?s? |The search |Based on |"
                r"After |Now that |This is |Here'?s? |Looking at |"
                r"The web search |I can see |From the |According to |"
                r"Now I |First,? |Next,? |Finally,? |Overall,? )"
                r"[^\n]*?(?:\.\s*|\n)",
                # 中文思考/计划性句子
                r"^(?:我[来先]|让我|我需要|我[会将要]|接下来我)"
                r"(?:搜索|查[找询]|检索|了解|收集|获取|看看|查一下|搜一下).*?[。.]\s*",
                # "根据搜索结果"类
                r"^根据(?:搜索结果|我的搜索|网络信息|以上信息)[，,].*?[。.]\s*",
                # "搜索结果显示"类
                r"^(?:搜索结果|网络上的信息|综合以上信息)(?:显示|表明|说明)?[，,：:].*?[。.]\s*",
            ):
                new_text = re.sub(pattern, '', full_text, count=1,
                                  flags=re.IGNORECASE).strip()
                if new_text != full_text:
                    full_text = new_text
                    _changed = True

        # ── 后处理：清理 Markdown 格式残留并强制单行 ──
        # 第三方代理 + 联网搜索时，模型可能输出 Markdown 而非纯文本
        # 0. 引用标记 [1] [2,3] [1, 2, 3, 4] 等
        full_text = re.sub(r'\s*\[\s*\d+(?:\s*[,，]\s*\d+)*\s*\]', '', full_text)
        # 1. Markdown 粗体 **text** → text
        full_text = re.sub(r'\*\*(.+?)\*\*', r'\1', full_text)
        # 2. 行尾孤立 * 号（Markdown 列表残留）
        full_text = re.sub(r'\s*\*\s*$', '', full_text, flags=re.MULTILINE)
        # 3. Markdown 标题标记 ## ...
        full_text = re.sub(r'(?:^|\n)\s*#{1,6}\s+', '', full_text)
        # 4. 合并多行为单行（提示词要求纯文本单行）
        full_text = re.sub(r'\s*\n\s*', '', full_text)
        # 5. 清理多余空格
        full_text = re.sub(r'  +', ' ', full_text).strip()

        return full_text, actual_model, confidence, info_volume, is_insufficient, quality

