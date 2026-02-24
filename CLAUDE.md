# SteamShelf 项目 — AI 协作规则

## 优先级分层（基于实际使用验证）

| 层级 | 含义 | 规则 |
|------|------|------|
| 🔴 铁律 | 从未跳过，跳过必出问题 | 工作流（Pre-mortem→实现→验证→报告）、SSOT、Bug全扫、代码质量数字、UX审视 |
| 🟡 强默认 | 通常遵守，特定条件下可灵活 | 静态检查、行为验证、架构规则、节省Token、主动确认 |
| 🟢 弹性 | 与铁律冲突时可降级 | UI视觉验证（用户手动测试更高效时）、任务过载保护（相关任务可合并）、日志（非调试场景） |

**冲突解决**：铁律 > 强默认 > 弹性。例：「节省Token」要求渐进式阅读，但「Pre-mortem」需要完整上下文时，后者优先。

---

## 🔴 工作流（每次代码修改必须走完）

**阶段 A — Pre-mortem（只分析，不改代码）：**
输出 3-10 行影响分析：
1. **影响路径**：涉及哪些函数/代码路径？
2. **并行路径**：有没有「做同一件事但实现不同」的代码？有 → 必须同时改
3. **状态依赖**：依赖哪些共享状态？可能读到过期值吗？
4. **破坏面**：最可能出错的地方？

**阶段 B — 实现（最小改动）：**
- 写代码，只改必要的部分
- `radon cc <file> -s -n C` 检查复杂度，不达标当场修复

**阶段 C — 验证 + 报告：**

三种验证（按优先级组合）：
1. **静态检查** 🟡（每次必做）：语法 `ast.parse` + 导入链 `import <module>` + radon CC
2. **行为验证** 🟡（涉及逻辑/数据流时）：临时脚本 + assert 关键行为，完成后删除
3. **UI 视觉验证** 🟢（涉及界面时，用户手动测试可替代）：screencapture + PIL 像素分析

验证通过后输出质量报告：
```
📊 质量报告
├─ Pre-mortem: ✅ / ❌
├─ 修改文件: xxx.py (行数)
├─ radon CC: 全部 ≤ C ✅ / ❌
├─ 最长新增方法: _xxx() N行 ✅/❌
├─ 单一真相源: ✅ 已合并 / ⚠️ 差分断言 / ❌ 未覆盖
├─ 回归防线: ✅ / 不涉及
├─ bg_thread / Protocol: ✅ / 不涉及
└─ 状态: ✅ 全部达标 / ❌ 需修复
```

**用户看到的必须是全绿的最终结果。没有质量报告 = 违规。**

## 🔴 单一真相源（SSOT）+ Bug 全扫

**同一逻辑禁止有两套实现。违反此原则是架构 bug，优先级高于功能 bug。**

- 两条路径做同一件事 → 合并为一个函数
- 暂时无法合并 → 差分断言：`assert result_a == result_b`（`STEAMSHELF_DEBUG_EXPR` 环境变量控制）

**Bug 修复必须全扫同类：**
1. 修当前 bug → 2. 提炼模式特征 → 3. grep 全盘扫描 → 4. 一次修完 → 5. 回归免疫（已知坑 + dev-mode 断言）

**只修一处 = 未完成。没有回归防线 = 未完成。**

## 🔴 代码质量红线

- CC ≤ 25 | 方法 ≤ 80行（UI ≤ 120） | 文件 ≤ 800行 | 循环内 ≤ 30行 | 嵌套 ≤ 3层
- 筛选链 > 5 条件 → 提取函数 | 禁止复制粘贴 > 10行
- 新 Mixin → `_protocols.py` 加 Protocol | 新线程 → `bg_thread` | 新进度窗 → `ProgressWindow`

## 🔴 用户体验优先

- **主动审视 UX**：操作路径最短？入口重复？视觉层级清晰？
- **消除冗余入口** | **弹出菜单优于内嵌** | **状态实时刷新** | **分发敏感性**

### 渐进式披露（Progressive Disclosure）

**功能数量不变，可见性分层。**

| 层级 | 触发 | 示例 |
|------|------|------|
| L1 始终可见 | 直接展示（≤5个） | 搜索框、账号名 |
| L2 按需出现 | 右键/快捷键 | 筛选面板、子菜单 |
| L3 条件触发 | 状态满足时 | Cloud上传、AI控件 |

**UI 膨胀审计**：盘点元素 → L1/L2/L3 分层 → 重组（popup/cascade/快捷键）→ 验证可达性

**tkinter 手法**：平铺按钮→`tk_popup` | 相关操作→`add_cascade` | 条件显示→`pack_forget`+`pack` | 高频→快捷键

**触发时机**：新增 UI 元素导致 L1 超 5-7 个时主动重组；用户反馈「太复杂」时启动审计

## 架构

13 个 Mixin 多继承组合到 `SteamToolboxMain`，`self` 指向同一实例。

共享属性：`self.root`(Tk) | `self.manager`(NotesManager) | `self.cloud_uploader` | `self.current_account`(SteamAccount, 兼容 dict) | `self._config` | `self._game_name_cache` | `self._cef_bridge` | `self._collections_core`

依赖：UI层 → 数据层 → utils。**禁止循环依赖。** HTTP 请求**必须用 `utils.urlopen()`**。

## 文件职责速查

| 职责 | 文件 |
|------|------|
| 入口+导言区 | `main.py` |
| 公共工具/HTTP/排序 | `utils.py` |
| 账号模型 | `account_manager.py` |
| 笔记读写 | `core_notes.py` |
| 收藏夹核心 | `core_collections.py` |
| CEF 连接 | `cef_bridge.py` |
| AI 生成 | `ai_generator.py` |
| Cloud 上传 | `cloud_uploader.py` |
| 主界面(13 Mixin) | `ui_main.py` |
| 账号选择 | `ui_intro.py` |
| 库-游戏列表 | `ui_library.py` |
| 库-收藏夹树 | `ui_library_collections.py` |
| 收藏夹来源更新 | `ui_library_source_update.py` |
| 收藏夹导入/导出 | `ui_collection_ops.py` |
| 笔记查看/编辑 | `ui_notes_viewer.py` |
| 导入/导出/去重 | `ui_import_export.py` |
| 设置(4文件) | `ui_settings*.py` |
| 推荐来源 | `ui_recommend*.py` |
| 鉴赏家 | `ui_curator.py` |
| 行内AI / AI搜索 | `ui_ai_inline_gen.py` / `ui_ai_search.py` |
| 社区分享 | `ui_sharing.py` |
| 工具类 | `ui_utils.py` |
| Protocol | `_protocols.py` |

## 已知坑

- Tcl `children` 不能 `*展开` 33000+ 项 → `tuple(item_ids)`
- macOS `locale.strxfrm` 对 CJK 崩溃 → `pypinyin`
- `<<TreeviewSelect>>` 异步 → 设 flag 后 `update_idletasks()`
- macOS `<Button-1>` 干扰 heading command → 只拦截 separator/indicator
- `SteamAccount` 有 dict 协议 → 直接 `account['key']`
- `CollectionsCore` 接受 `SteamAccount` 对象，不是路径
- CEF Bridge ≠ Steam Cloud，两套独立机制
- `_eval_filter_expression` 候选集必须含 notes-only + uploading 游戏

## 🔴 发版流程（每次发版必须走完）

**触发条件**：用户说"推版本"/"发版"/"bump version" 时执行。

**步骤：**
1. `updater.py` 的 `__version__` bump 版本号
2. `git add` 所有改动文件 → `git commit`（commit message 第一行即 changelog）
3. `git push origin master`
4. `git tag -a v{版本号} -m "changelog 内容"`（tag subject = changelog，CI 用 `%(contents:subject)` 提取）
5. `git push origin v{版本号}` → 触发 CI
6. `gh run watch` 等 CI 全绿
7. `gh release view v{版本号}` 确认四个资产：win.zip + mac.zip + source.zip + version.json
8. 下载 version.json 验证：版本号正确 + changelog 正确 + 三平台镜像 URL 齐全
9. `curl` 验证至少一个国内镜像返回正确 version.json

**CI 自动完成的事（不需要手动做）：**
- 构建 win/mac/source 三平台包
- 生成 version.json（含 gh-proxy + ghfast + github 三源镜像 URL）
- 创建 GitHub Release 并上传所有资产

**禁止事项：**
- 禁止手动 `gh release create`（会和 CI 冲突）
- 禁止在 push tag 前手动创建 release
- tag message 不要把 Co-Authored-By 放在第一行（会变成 changelog）

**更新管线架构（只读参考）：**
```
用户端: UPDATE_SOURCES(3镜像) → check_update() → version.json
  → 三态返回: 有更新 / 无更新 / 网络失败
  → download_update() → zip magic 校验 → apply_update_and_restart()
  → Windows: bat 脚本 + Expand-Archive（失败弹 notepad）
  → macOS/源码: 返回 zip 路径提示手动替换
```

## 🟡 弹性规则

**主动确认**：不确定时问用户，不猜。需求模糊先描述理解再动手。

**任务保护**：单轮 ≤ 2 个独立需求（相关任务可合并）。多文件联动被打断时警告风险。

**日志**：调试时优先看日志不靠猜。错误路径记完整 traceback，不静默吞异常。

**节省 Token**：
- 大文件（>200行）先 Grep 定位再精准 Read（offset+limit）
- 需要全局理解时可读全文（Pre-mortem 优先于省 token）
- 复杂重构先 plan mode | 增量 Edit 不整体重写 | 先查文件职责表再定位

## 项目上下文

- 架构权威源：`main.py` 导言区（第31-113行）
- 跨会话桥梁：`HANDOFF.md`（大改动后必须更新）
- 新会话先读 `HANDOFF.md` + `main.py` 导言区
- 中文为主，技术术语英文 | 语气直接 | AI 有权提出独立判断
