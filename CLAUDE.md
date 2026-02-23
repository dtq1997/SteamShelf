# SteamShelf 项目 — AI 协作规则（不可压缩）

## 🔴🔴🔴 自反馈迭代（最高优先级，凌驾于所有其他规则）

**做完任何修改后，AI 必须自主验证结果，不依赖用户手动测试。**
**没有验证 = 没有完成。静态检查不能替代行为验证和视觉验证，三者必须同时满足。**

### 三种验证（全部强制，不可省略任何一项）

1. **静态检查**（每次修改必做）
   - 语法：`python3 -c "import ast; ast.parse(open('<file>').read())"`
   - 导入链：`cd unified && python3 -c "import <modified_module>"`
   - 复杂度：`radon cc <file> -s -n C`

2. **行为链路实验**（涉及逻辑/数据流/事件时必做）
   - 写临时脚本，构造最小上下文复现修改涉及的代码路径
   - 用 assert 验证关键行为（如：focus 返回正确项、回调被触发、数据变化符合预期）
   - 验证失败 → 修复 → 重跑 → 循环直到通过
   - 完成后删除临时脚本

3. **UI 视觉验证**（涉及界面改动时必做）
   - 写临时脚本，用与真实 app 相同的布局构建最小测试窗口
   - `screencapture -R` 截图 → PIL 像素分析，量化验证视觉效果
   - 验证失败 → 修复 → 重跑 → 循环直到通过
   - 完成后删除临时脚本和截图

**验证发现问题 → 当场修复 → 再次验证 → 循环直到全部通过。用户看到的必须是已验证的最终结果。**

## 🔴 编程模式（最高优先级，不可跳过）

用户说"开始编程"或直接提出代码需求时自动进入编程模式。

**三阶段工作流（角色分离，每阶段有明确产出）：**

**阶段 A — 调查（只分析，不改代码）：**
1. 执行 Pre-mortem：输出影响分析（详见 Pre-mortem 章节）
2. 产出：影响路径 + 并行路径 + 破坏面预估

**阶段 B — 实现（最小改动）：**
3. 写代码，只改必要的部分
4. 对修改文件运行 `python3 -m radon cc <file> -s -n C`
5. 对新增文件运行 `wc -l <file>` 确认行数
6. 如有不达标项 → 当场修复 → 循环直到通过

**阶段 C — 红队（尝试打破自己的修复）：**
7. **执行顶部「三种验证」**（静态 + 行为链路 + UI 视觉）
8. 检查 Pre-mortem 中识别的并行路径是否仍然一致
9. 尝试构造边界情况打破修复
10. 全部通过后，输出最终质量报告：

```
📊 质量报告
├─ Pre-mortem: 已输出影响分析 ✅ / ❌ 未做
├─ 修改文件: xxx.py (行数), yyy.py (行数)
├─ radon CC: 全部 ≤ C ✅ / ❌ 列出超标函数
├─ 最长新增方法: _xxx() N行 ✅/❌
├─ 单一真相源: ✅ 已合并 / ⚠️ 差分断言覆盖 / ❌ 存在未覆盖并行路径
├─ 回归防线: ✅ 已添加 / 不涉及
├─ bg_thread: 已包装 / 不涉及
├─ Protocol: 已添加 / 不涉及
└─ 状态: ✅ 全部达标 / ❌ 需修复
```

**核心原则：用户看到的报告必须是全绿的最终结果，不是中间状态。**
**没有质量报告 = 违规。AI 应最大程度自主完成验证，减少用户手动测试。**

## 🔴 Pre-mortem（事前验尸，动手前必做）

**写代码之前先输出影响分析，不分析就不准动手。**

每次修改前，AI 必须输出以下分析（简洁即可，3-10 行）：
1. **影响路径**：这次改动涉及哪些函数/代码路径？列出文件名:函数名
2. **并行路径检查**：有没有「做同一件事但实现不同」的并行代码路径？
   - 如果有 → 必须同时修改，或加差分断言确保一致
   - 典型例子：筛选逻辑同时存在于 eval 路径和 UI 迭代路径
3. **状态依赖**：改动是否依赖某个共享状态（缓存、全局变量、UI 控件状态）？
   该状态在什么时机更新？是否可能读到过期值？
4. **破坏面预估**：最可能出错的地方是什么？

**没有 Pre-mortem = 不准开始写代码。这是防止 bug 连锁反应的第一道防线。**

## 🔴 单一真相源（Single Source of Truth）

**同一逻辑禁止有两套实现。违反此原则是架构 bug，优先级高于功能 bug。**

- 发现两条代码路径做同一件事 → 立即合并为一个函数，所有调用方共用
- 暂时无法合并 → 必须加差分断言（dev-mode 下自动运行）：
  ```python
  if os.environ.get('STEAMSHELF_DEBUG_EXPR'):
      assert result_path_a == result_path_b, \
          f"路径分叉: A={len(result_path_a)}, B={len(result_path_b)}"
  ```
- 质量报告中标注：「单一真相源：✅ 已合并 / ⚠️ 差分断言覆盖 / ❌ 存在未覆盖的并行路径」

## 🔴 Bug 修复：同类全扫 + 回归免疫

修一个 bug 时，必须假设同类 bug 存在于其他代码路径。
**修完后必须留下永久性防线，确保同类 bug 不会复发。**

**强制工作流：**
1. 修当前 bug，确认修复有效
2. 提炼 bug 的模式特征（如：`.after(0,` 在非 root Toplevel 上、`_lib_populate_tree()` 绕过筛选状态）
3. 全盘 grep 该模式，逐个分析每个调用点
4. 一次性修完所有同类 bug，不留漏网
5. **回归免疫**：在「已知坑」章节添加该 bug 的模式描述，并在代码中加入
   dev-mode 断言或自动检查，确保未来不会复发
6. 质量报告中列出所有修复点 + 确认安全的调用点 + 回归防线

**只修一处 = 未完成。没有回归防线 = 未完成。**

## 🔴 日志与调试模式（开发基础设施）

**日志是 AI 自迭代的眼睛——没有日志就是盲调。**

- 关键代码路径（数据加载、状态切换、异步回调）必须有日志输出
- 日志应包含：时间戳、函数名、关键参数值、执行结果
- 提供 debug 模式开关（环境变量或配置项），开启后输出详细日志，关闭后静默
- 错误路径必须记录完整 traceback，不能静默吞掉异常
- 异步/多线程场景额外记录线程名和回调链路，方便追踪时序问题
- **调试时优先看日志，不要靠猜。如果日志不够用，先补日志再调 bug。**

## 🔴 代码质量红线

- 函数 CC ≤ 25，超过必须拆分（`radon cc <file> -s -n C`）
- 新增方法 ≤ 80 行（UI 构建 ≤ 120 行）
- 新增文件 ≤ 800 行
- 循环内逻辑 ≤ 30 行，嵌套 ≤ 3 层
- 筛选链 > 5 条件 → 提取 `_should_include_*()`
- 禁止复制粘贴 > 10 行
- 新增 Mixin → `_protocols.py` 加 Protocol
- 新增后台线程 → `bg_thread` 装饰器
- 新增进度窗口 → `ProgressWindow` 类

## 🔴 任务过载保护

单轮不超过 2 个独立功能需求。超过时主动拆分并告知用户。

## 🔴 中断保护

多文件联动修改被打断时，必须警告风险并建议完成或回滚。

## 🔴 主动确认原则

**有任何不确定的地方，立即向用户确认，不要猜测后动手。**
- 需求理解模糊时，先用简短文字描述自己的理解，请用户确认再动手
- UI 改动影响范围不明确时（如按钮位置变化可能挤压其他元素），主动说明潜在影响
- 多种实现方案时，列出选项让用户选择，而非自行决定

## 🔴 用户体验优先

用户的需求方向始终是朝最优使用体验走的，但表述不一定精确。AI 必须：
- **主动审视 UX 合理性**：每次 UI 改动前思考——操作路径是否最短？入口是否重复？视觉层级是否清晰？
- **消除冗余入口**：同一功能不应在多处重复出现，整合后及时清理旧入口
- **弹出菜单优于内嵌展开**：设置类按钮优先用 `tk.Menu` + `tk_popup`（参考「创建分类」按钮），而非 inline 展开面板
- **状态指示要实时**：顶部栏的状态标签必须挂到定时器动态刷新，不能只在初始化时设置一次
- **分发敏感性**：面向广泛用户分发的软件，UI 文字和图标不能包含敏感/尴尬内容（如「成人内容」字样）

## 架构（防止往错误文件写代码）

Mixin 模式：12 个 Mixin 通过多继承组合到 `SteamToolboxMain`，所有 Mixin 方法的 `self` 指向同一个 `SteamToolboxMain` 实例。

共享属性（所有 Mixin 可直接用 self 访问）：
- `self.root` — tk.Tk 主窗口
- `self.manager` — SteamNotesManager
- `self.cloud_uploader` — SteamCloudUploader（可为 None）
- `self.current_account` — SteamAccount（兼容 dict 访问：`account['friend_code']`）
- `self._config` — 持久化配置 dict
- `self._game_name_cache` — {app_id: name}
- `self._cef_bridge` — CEFBridge（可选）
- `self._collections_core` — CollectionsCore（可选）

依赖方向：UI 层 → 数据层 → utils。**禁止循环依赖。数据层不可依赖 UI 层。**

所有 HTTP 请求**必须用 `utils.urlopen()`**，禁止各文件自行实现。

## 文件职责速查（防止探索浪费 token）

| 职责 | 文件 |
|------|------|
| 入口+导言区规则 | `main.py` |
| 公共工具/HTTP/排序 | `utils.py` |
| 统一账号模型 | `account_manager.py` |
| 笔记读写 | `core_notes.py` |
| 收藏夹核心 | `core_collections.py` |
| CEF 连接 | `cef_bridge.py` |
| AI 生成 | `ai_generator.py` |
| Cloud 上传 | `cloud_uploader.py` |
| 主界面骨架(12 Mixin 聚合) | `ui_main.py` |
| 账号选择 | `ui_intro.py` |
| 库管理-游戏列表+筛选 | `ui_library.py` |
| 库管理-收藏夹树+事件 | `ui_library_collections.py` |
| 收藏夹来源更新 | `ui_library_source_update.py` |
| 收藏夹导入/导出/更新 | `ui_collection_ops.py` |
| 笔记查看/编辑 | `ui_notes_viewer.py` |
| 导入/导出/去重 | `ui_import_export.py` |
| 设置入口 | `ui_settings.py`（拆为 4 文件：`_ai`/`_steam`/`_cache`） |
| 推荐来源 | `ui_recommend.py` + `ui_recommend_igdb.py` |
| 鉴赏家 | `ui_curator.py` |
| 行内 AI 生成 | `ui_ai_inline_gen.py` |
| AI 搜索 | `ui_ai_search.py` |
| 工具类+bg_thread+ProgressWindow | `ui_utils.py` |
| Mixin Protocol 定义 | `_protocols.py` |

## 已知坑（防止重复踩）

- Tcl `children` 不能 `*展开` 33000+ 项 → 必须用 `tuple(item_ids)`
- macOS `locale.strxfrm` 对 CJK 崩溃 → 用 `pypinyin`（Level 3）
- `<<TreeviewSelect>>` 是异步事件 → 设 flag 后需 `update_idletasks()`
- macOS `<Button-1>` 实例绑定干扰 ttk heading command → 只拦截 separator/indicator
- `SteamAccount` 实现了 dict 协议 → `account['key']` 直接可用，别重新包装
- `CollectionsCore` 构造函数接受 `SteamAccount` 对象，不是路径字符串
- CEF Bridge（收藏夹）和 Steam Cloud（笔记上传）是两套独立同步机制，不可合并
- `_eval_filter_expression` 的候选集必须与 `ui_library.py:1133-1155` 的 merged_games 构建一致（含 notes-only + uploading 游戏），否则 eval 结果会少于 UI 显示

## 项目上下文

- 架构权威源：`main.py` 导言区（第31-113行）
- 跨会话桥梁：`HANDOFF.md`（大改动后必须更新）
- 协作偏好：`COLLAB_PREFS.md`
- 新会话开头先读 `HANDOFF.md` + `main.py` 导言区恢复上下文

## 🔴 节省 Token（强制工作流，不可降级为建议）

- **渐进式阅读（禁止一次性通读大文件）**：
  - 本项目多个文件超 800 行，一次性 Read 整个文件会浪费大量上下文
  - **侦察优先**：先用 Grep 定位关键词/函数所在行号，或用 `radon cc` 等静态工具定位问题
  - **精准读取**：用 Read 的 `offset` + `limit` 参数只读目标区域，单次不超过 500 行
  - **按需扩展**：需要更多上下文时再调整 offset 继续读取
  - 只有确认文件较小（< 200 行）或需要全局理解时才允许读取整个文件
- **复杂重构必须先进 plan mode**：规划好再动手，避免走弯路浪费 token
- **给具体范围**：指定文件名、函数名、行号，不做全局搜索
- **增量编辑**：用 Edit 工具逐处修改，禁止整体重写文件
- **上方「文件职责速查」表就是为了避免探索浪费**——先查表再定位

## 工作方式

- 中文为主，技术术语保留英文
- 语气直接，不客套
- AI 有权提出独立判断和建议，用户期望主动性而非被动执行
