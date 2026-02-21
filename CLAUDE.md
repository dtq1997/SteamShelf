# SteamShelf 项目 — AI 协作规则（不可压缩）

## 🔴 自迭代原则（适用于所有任务，不限于编程模式）

**做完任何修改后，AI 必须自主验证结果，不依赖用户手动测试。**
- 能用工具验证的，立即跑工具（静态分析、语法检查、grep 确认引用等）
- 验证发现问题 → 当场修复 → 再次验证 → 循环直到通过
- 用户看到的应该是已验证的最终结果，不是需要人工复核的中间产物

## 🔴 编程模式（最高优先级，不可跳过）

用户说"开始编程"或直接提出代码需求时自动进入编程模式。

**自迭代工作流（写完代码后立即执行，不等用户）：**
1. 对修改文件运行 `python3 -m radon cc <file> -s -n C`
2. 对新增文件运行 `wc -l <file>` 确认行数
3. 检查结果是否全部达标
4. **如有不达标项 → 当场修复 → 回到第 1 步重新检查 → 循环直到全部通过**
5. **端到端验证（不依赖用户手动测试）：**
   - 语法：`python3 -c "import ast; ast.parse(open('<file>').read())"`
   - 导入链：`cd unified && python3 -c "import <modified_module>"` 验证无 ImportError
   - 如涉及数据层：写临时脚本调用修改的函数，验证输入→输出正确
   - GUI 无法无头运行，但可验证到「导入成功 + 构造函数不报错」这一层
6. 全部通过后，输出最终质量报告：

```
📊 质量报告
├─ 修改文件: xxx.py (行数), yyy.py (行数)
├─ radon CC: 全部 ≤ C ✅ / ❌ 列出超标函数
├─ 最长新增方法: _xxx() N行 ✅/❌
├─ bg_thread: 已包装 / 不涉及
├─ Protocol: 已添加 / 不涉及
└─ 状态: ✅ 全部达标 / ❌ 需修复
```

**核心原则：用户看到的报告必须是全绿的最终结果，不是中间状态。**
**没有质量报告 = 违规。AI 应最大程度自主完成验证，减少用户手动测试。**

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
