"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     STEAM TOOLBOX UNIFIED v1.0                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
================================================================================
【AI 协作系统提示词 / System Prompt for AI Maintainers】
如果你（AI）正在处理此文件，请务必遵守以下开发者习惯与规则：

【元规则 - 最高优先级】
0. 【提示词协作】：允许 AI 根据与用户的交流情况，自发在本导言区增加或修改提示词，
   但每次增加或修改提示词前必须先与用户讨论并获得同意。
   AI 必须严格遵照本导言区的所有提示词运作，本导言区规则优先级最高。
   【主动更新】：如果用户提出的需求反映了某些更具一般性的要求，AI 应主动将其整合
   为新的规则添加到本导言区，并在回复中明确告知用户具体修改了哪些内容。
   【及时响应】：当用户明确要求修改导言区内容时，AI 必须立即配合执行修改，
   不得拖延或遗漏。导言区是用户与 AI 之间的"协作契约"，保持其准确和最新
   是最高优先级任务之一。

================================================================================
【项目目标】
本程序是 SteamShelf，整合了两个独立软件：
- 软件 A: Steam 库管理助手 — 账号选择 + CEF 连接 + 收藏夹管理
- 软件 B: Steam 笔记管理器 — 笔记管理 + AI 生成 + Cloud 上传

整合后使用标签页结构，两个功能共享同一个统一账号系统（SteamAccount 类）。
核心用途：由 AI 撰写客观的"游戏说明"笔记，帮助用户的 Steam 好友快速判断
库中的游戏是否适合自己。详见下方【AI 撰写游戏说明笔记的指引】。
也可用于手动创建其他类型的笔记（攻略、日志等），以及管理 Steam 收藏夹/分类。

================================================================================
【项目文件架构】

本项目采用 分层架构 + Mixin UI 组合 的设计模式。
整合后所有文件位于 unified/ 目录。

── 入口 ──
main.py              — 程序入口 + 导言区（本文件）
CHANGELOG.md         — 更新日志（独立文件，减少导言区 token 消耗）
HANDOFF.md           — 项目交接文档（当前状态、架构、待办）

── 公共工具层 ──
utils.py             — 公共工具函数（SSL 上下文、HTTP 请求封装等）
                       ⚠️ 所有 HTTP 请求必须使用 utils.urlopen()，禁止各文件自行实现

── 统一账号层 ──
account_manager.py   — 🔵 统一账号模型（A+B 合并重写）
                       包含：SteamAccount（统一类，兼容 dict 访问）
                       包含：SteamAccountScanner（扫描、库列表、收藏夹、在线游戏等）
                       关键设计：SteamAccount 实现了 get()/__getitem__()/__contains__()
                       使 B 的 account['friend_code'] 调用零改动兼容

── 数据层（无 UI 依赖，可独立测试） ──
core_notes.py        — 🟢 笔记核心读写逻辑（B 的 core.py 重命名）
                       包含：SteamNotesManager, is_ai_note(), extract_ai_*() 等
                       包含：CONFIDENCE_EMOJI, INFO_VOLUME_EMOJI, QUALITY_EMOJI 等常量
core_collections.py  — 🔵 收藏夹核心逻辑（A 的 core.py 重构，去 tkinter 依赖）
                       包含：CollectionsCore（CEF 队列机制）
core_scraper.py      — 🟡 网页抓取（A 引入，去 tkinter 依赖）
core_igdb.py         — 🟢 IGDB 数据查询（A 直接复制）
cloud_uploader.py    — Steam Cloud 直接上传（Steamworks API 封装，子进程隔离）
                       包含：SteamCloudUploader
steam_data.py        — Steam 数据获取（游戏详情、评测、名称）
config_manager.py    — 🟡 配置持久化（~/.steam_toolkit/config.json，B 扩展）
                       新增收藏夹相关配置 + 旧配置迁移
vdf_parser.py        — VDF 文件解析（remotecache.vdf 同步状态）

── CEF 桥接层（来自软件 A） ──
cef_bridge.py        — 🟢 CEF 连接（A 直接复制）
local_storage.py     — 🟢 备份管理器（A 直接复制）
ui_utils.py          — 🟢 全局弹窗补丁（A 直接复制）

── AI 层 ──
ai_generator.py      — AI 笔记生成（多提供商支持：Anthropic/OpenAI/DeepSeek/自定义）

── UI 层（tkinter，Mixin 模式） ──
ui_main.py           — 🔵 整合主界面骨架（A+B 合并，精简后 ~715 行）
                       包含：SteamToolboxMain（标签页骨架 + 配置 + 游戏名缓存 + 树交互）
ui_intro.py          — 🔵 账号选择界面（从 ui_main.py 拆分）
                       包含：SteamToolboxIntro（账号选择 + CEF 启动）
ui_library.py        — 🔵 库管理标签页（从 ui_main.py 拆分，LibraryMixin）
                       包含：_build_library_tab, _lib_populate_tree, 收藏夹渲染, CEF 连接等 23 个方法
ui_cloud.py          — 🔵 Cloud 上传 + Steam 进程监控（从 ui_main.py 拆分，CloudMixin）
                       包含：_transactional_cloud_upload, _cloud_upload_*, _start_steam_monitor 等方法
ui_notes_viewer.py   — 笔记查看/编辑/创建/删除窗口（NotesViewerMixin）
ui_import_export.py  — 导入/导出/去重对话框（ImportExportMixin）
ui_settings.py       — API 配置、缓存管理、关于（SettingsMixin）
rich_text_editor.py  — BBCode 富文本编辑器组件（独立 Tk 组件）

Mixin 工作方式：各 Mixin 类的方法 self 指向 SteamToolboxMain 实例。
SteamToolboxMain 通过多继承组合所有 Mixin：
  class SteamToolboxMain(LibraryMixin, CloudMixin, NotesViewerMixin, ImportExportMixin, SettingsMixin, ...)
共享以下关键属性：
  self.root              — tk.Tk 主窗口
  self.manager           — SteamNotesManager 实例
  self.cloud_uploader    — SteamCloudUploader 实例（可为 None）
  self.current_account   — SteamAccount 实例（兼容 dict 访问）
  self.accounts          — list[SteamAccount]: 所有已发现的 Steam 账号
  self._config           — dict: 持久化配置（~/.steam_toolkit/config.json）
  self._game_name_cache  — dict: {app_id: name} 游戏名称缓存
  self._games_tree       — ttk.Treeview 游戏列表控件（笔记标签页）
  self._games_data       — list[dict]: 当前显示的游戏数据
  self._collections_core — CollectionsCore 实例（库管理标签页）
  self._cef_bridge       — CEFBridge 实例（库管理用）

依赖方向：main → ui_main → [所有 Mixin] → [ai_generator, core_notes, account_manager, ...]
                                          → utils（公共工具）
禁止循环依赖。UI 层可依赖数据层，数据层不可依赖 UI 层。

================================================================================
【Steam 笔记存储机制 - 关键技术细节】
1. 本地路径:
   <Steam安装目录>/userdata/<用户ID>/2371090/remote/
   其中 2371090 是 Steam Notes 功能的固定 AppID

2. 文件命名:
   - 每个游戏对应: notes_<游戏AppID>  (如 notes_570 = Dota 2)
   - 非 Steam 游戏: notes_shortcut_<游戏名>
   - 没有文件扩展名

3. 文件格式: JSON
   {
     "notes": [
       {
         "id": "<8位随机十六进制字符串>",
         "appid": <游戏AppID数字>,
         "ordinal": 0,
         "time_created": <Unix时间戳秒>,
         "time_modified": <Unix时间戳秒>,
         "title": "笔记标题",
         "content": "[p]正文（支持富文本标签如 [h1][b][list] 等）[/p]"
       }
     ]
   }

4. 云同步:
   - 使用 Steam Cloud 同步 (AppID 2371090 的 remote 目录)
   - 本程序通过 Steamworks API (ISteamRemoteStorage::FileWrite) 直接上传
   - 在主界面点击「☁️ 连接 Steam Cloud」后，保存即自动上传到云端
   - 需要 Steam 客户端正在运行，且库中有至少一个已安装游戏（需要 libsteam_api）

【确保云同步的操作流程】
1. 启动 Steam → 2. 打开本程序 → 3. 点击「连接 Steam Cloud」→ 4. 正常编辑，保存即上传

================================================================================
【AI 撰写游戏说明笔记的指引】
本程序的核心用途之一是为用户 Steam 库中的游戏生成"游戏说明"笔记。
这些说明的目标读者是：登上用户账号的随机好友，他们不一定了解独立游戏或单机游戏。
说明的目的是：让读者快速判断这个游戏是否符合自己的兴趣。

⚠️ 撰写游戏说明时必须遵守以下规则（最高优先级）：

1. 【客观描述】：不能照抄游戏商店页面的商业化宣传语。必须客观地告诉读者这个
   游戏是什么、玩起来是什么感觉。
2. 【"现在打开会怎样"】：必须具体描述"如果我现在立刻打开这个游戏，前几分钟
   会看到什么、做什么"——要写到读者脑中能浮现画面，而非"上手难度适中"
   "需要一定学习成本"等模糊概括。这是最重要的信息之一。
3. 【认知资源与时间需求】：必须说明这个游戏需要怎样的注意力投入，让读者知道
   自己需要为它腾出怎样的精力和时间。是否需要大段连续时间、每局/每次游玩大概多久。
4. 【网络口碑】：必须提及这个游戏在网络上是否受欢迎、大致评价如何。
5. 【缺点与不适人群】：必须有一定篇幅介绍缺点，以及明确说明不适合什么样的人玩。
6. 【不用术语、说人话】：禁止使用读者可能不懂的术语而不加解释。例如不能直接说
   "ASCII 风格画面"或"1-bit 风格"，而应该用没玩过游戏的人都能理解的语言描述
   （如"画面几乎完全由彩色文字符号构成——你的角色是一个@，怪物是字母，墙壁
   是#号"）。术语不必刻意回避或删除，解释清楚即可。
7. 【无需强调性价比】：这些游戏默认已在用户库中，属于免费可玩，绝对禁止提及
   任何与价格相关的内容（价格、售价、原价、打折、性价比、定价等）。即使参考资料
   （如 Steam 评测）中大量讨论价格，AI 也必须完全忽略这些信息。
8. 【适合的游玩情景】：必须说明适合自己一个人单独玩还是跟朋友一起玩、适合跟
   什么类型的人玩、适合什么场合（如睡前放松、通勤途中、还是周末空出一整个下午）。
9. 【格式与富文本】：AI 生成的笔记采用"标题=内容"模式——输出纯文本单行，
   禁止换行和 BBCode 标签，同时作为笔记标题和内容显示。这样用户在 Steam 客户端
   的笔记列表中无需点进去就能看到全部说明。所有信息应融入一段连贯自然的叙述中，
   禁止使用分段式小标题（如"初次打开的体验："、"认知资源："等）。
   可适度使用 emoji（📌✅⚠️🗺️⚔️📝🎯）但要克制。建议 200-500 字。
   手动创建的笔记仍可使用 BBCode 富文本标签。
10.【AI 声明前缀】：每条 AI 生成的笔记必须自动在开头添加固定前缀：
   "🤖AI: {信息来源} | 相关信息量：{X}{emoji} | 游戏总体质量：{Y}{emoji}
   ⚠️ 以下内容由 {模型名} 生成，该模型对以下内容的确信程度：{Z}{emoji}。"
   前缀必须以 "🤖AI:" 开头，这是程序识别 AI 笔记的唯一标志。
   信息来源为「📡联网检索」或「📚训练数据与Steam评测」，信息量和确信程度
   均配有颜色 emoji（🟢🔵🟡🟠🔴）。信息量由 AI 严格根据搜索结果/评测中
   有效信息的比例判断（相当多/较多/中等/较少/相当少）。
   游戏总体质量由 AI 综合所有信息后对游戏质量的客观判断（玩法设计、内容量、
   完成度、社区口碑等），使用独立 emoji 体系：💎相当好/✨较好/➖中等/
   👎较差/💀相当差。注意：质量评估 ≠ 确信度（确信度是 AI 对自身描述
   准确性的把握，质量是对游戏本身的评价）。
   【联网回退策略】：联网搜索时，若网络有效信息严重不足，AI 应回退到主要
   依靠 Steam 评测内容撰写说明；只有联网搜索和 Steam 评测都严重不足时才
   输出"信息过少"标注性笔记。
   当信息实在过少时，改为生成标注性笔记：
   "🤖AI: ⛔信息过少 {来源} | 相关信息量：{X}{emoji} 该游戏相关信息过少，
   无法生成有效的游戏说明。（由 {模型名} 判定）"
   此声明由程序自动拼接，无需在系统提示词中要求 AI 输出。

================================================================================
【Steam 笔记富文本标签参考】
Steam 笔记 content 字段支持以下富文本标签（类似 BBCode）：
- [p]段落文本[/p]          — 段落（所有正文文本都应包裹在 [p] 中）
- [h1]标题[/h1]            — 一级标题
- [h2]标题[/h2]            — 二级标题
- [h3]标题[/h3]            — 三级标题
- [b]粗体[/b]              — 粗体文本
- [i]斜体[/i]              — 斜体文本
- [u]下划线[/u]            — 下划线文本
- [strike]删除线[/strike]  — 删除线文本
- [list][*]项目一[*]项目二[/list] — 无序列表
- [olist][*]第一[*]第二[/olist]   — 有序列表
- [hr]                     — 水平分隔线
- [code]代码[/code]        — 代码块
- [url]链接[/url]          — URL 链接
- [url=链接]文本[/url]     — 带显示文本的 URL 链接
⚠️ 注意：AI 生成笔记内容时，正文必须使用 [p]...[/p] 包裹，
  否则在 Steam 客户端中可能显示异常。

================================================================================
【富文本编辑器设计原则】
1. 所有笔记编辑和显示区域都使用富文本（WYSIWYG）模式，而非源码模式。
2. 编辑器提供工具栏按钮，对应 Steam 笔记支持的所有富文本功能。
3. 底层存储仍使用 Steam 原生 BBCode 标签格式，编辑器负责双向转换。
4. 编辑器需提供"源码模式"切换按钮，方便高级用户直接编辑 BBCode 源码。
5. 笔记查看器提供"原始文本/富文本"就地切换按钮，用于调试富文本渲染。
6. 【嵌套标签】：BBCode 解析必须支持任意深度的标签嵌套。
7. 【URL 标签】：必须支持 [url]链接[/url] 和 [url=链接]文本[/url] 两种格式。

================================================================================
【导入导出设计原则】
1. 导入操作永远不覆盖已有笔记，导入的内容始终追加在已有笔记之后。
2. 导出分两种模式：单条导出（独立 txt）、批量导出（结构化 txt）。
3. 导入也分两种对应模式：单条导入、批量导入（按 AppID 自动分发）。

================================================================================
【开发规范】
1. 【逻辑稳定性】：核心功能（JSON 读写、笔记文件操作）严禁在非必要情况下改动。
2. 【改动确认】：在尝试重构现有功能或大规模调整 UI 前，必须获得用户明确许可。
3. 【UI 习惯】：与 Steam_Library_Manager 保持一致的风格。
4. 【反馈机制】：操作完成后必须显示明确的成功/失败反馈。
5. 【UI 文本风格】：✅ 表示成功，❌ 表示失败；按钮 emoji + 动宾结构；关键信息红色高亮。
6. 【账号管理】：启动时自动扫描所有 Steam 账号，支持多账号切换。
7. 【窗口大小】：自适应内容大小，禁止固定 geometry()。
8. 【跨平台】：支持 Windows / macOS / Linux / WSL。
9. 【增量修改】：修改代码时必须以原始文件为基础进行增量编辑（如逐处替换），
   严禁整体重新生成文件。这样既节省 token，也避免引入无关变更或丢失已有功能。
10.【UI 按钮布局】：当按钮或筛选控件较多时应分多行排列，确保所有控件可见。
   同一行内的 Combobox 总数不宜超过 3 个，超出时应拆分到下一行。
11.【调试信息规范】：调试/诊断窗口的文本必须可选中/可复制，提供"📋 复制"按钮。
   API Key 等敏感信息在调试输出中必须脱敏（仅显示前6位...后4位）。
12.【版本管理】：每次生成新版本文件时，必须递增版本号并在 CHANGELOG.md 添加条目。
13.【配置持久化】：AI 令牌配置保存为 ai_tokens 列表（每项含 name/key/provider/
   model/api_url），并通过 ai_active_token_index 记录默认令牌。
14.【AI 笔记识别】：AI 生成的笔记以固定前缀 "🤖AI:" 开头。同时兼容旧版 "⚠️ 以下内容由"。
15.【笔记创建统一性】：无论手动还是 AI 生成，笔记文件创建都必须使用同一 create_note 方法。
16.【Steam 分类集成】：AI 批量生成支持按 Steam 收藏夹筛选游戏。
17.【延迟上传与 dirty 状态跟踪】：所有笔记改动仅写入本地并标记 dirty，
   用户显式点击上传按钮后才调用 Steamworks API 上传到 Steam Cloud。
18.【列表行内操作按钮】：每个条目右侧提供行内小按钮（如 📋 复制 AppID）。
19.【批量任务生命周期】：AI 批量生成支持暂停/继续/停止，关闭时检查未上传笔记。
20.【启动性能优化】：主界面加载时仅用缓存快速显示，后台线程完成网络请求。
21.【家庭组游戏筛选】：支持录入家庭组成员好友代码，筛选家庭组并集。
22.【缓存管理】：主界面提供缓存管理窗口。
23.【上传中状态保护】：syncstate=3 的笔记全面禁止修改。
24.【公共工具统一】：所有 HTTP 请求使用 utils.urlopen()，禁止各文件自行实现
   _get_ssl_context() / _urlopen()，避免代码重复。
25.【整合架构】：程序入口为 ui_main.main()，经过 SteamToolboxIntro 账号选择后
   进入 SteamToolboxMain 标签页主界面。库管理和笔记管理共享同一个 SteamAccount。
26.【收藏夹加载】：SteamAccountScanner.get_collections(userdata_path) 接受
   userdata_path 参数，返回 list[dict]（每项含 name/app_ids/is_dynamic），
   不是 dict。app_ids 为 int 列表，查缓存时需转 str。
27.【强制质量报告】：每次完成代码修改后，AI 必须对修改过的文件运行
   `radon cc <file> -s -n C` 和 `wc -l <file>`，并输出格式化质量报告：
   📊 质量报告 → 修改文件/行数/radon CC/最长新增方法/bg_thread/Protocol/状态
   报告中有 ❌ 时必须当场修复。用户看不到报告 = AI 没遵守规则。
28.【任务过载保护】：单轮对话不得同时处理超过 2 个独立功能需求。
   用户一次提 3+ 个需求时，AI 必须主动拆分并告知用户。
29.【中断保护】：多文件联动修改进行中被打断时，AI 必须告知风险和剩余步骤。

================================================================================
【更新日志】→ 详见 CHANGELOG.md
================================================================================
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def _setup_logging():
    """配置日志：未捕获异常 + bg_thread 异常写入 crash.log"""
    log_dir = os.path.expanduser("~/.steam_toolkit")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "crash.log")

    handler = RotatingFileHandler(
        log_path, maxBytes=512_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"))

    logger = logging.getLogger("steamshelf")
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)

    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


from ui_main import main

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # PyInstaller macOS 子进程必需
    _setup_logging()
    main()
