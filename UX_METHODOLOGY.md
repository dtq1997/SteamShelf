# SteamShelf UX 审计方法论（AI + tkinter 专用）

## 三层框架

### 第一层：认知走查（Cognitive Walkthrough）— 发现"用不了"的问题

对每个用户任务的每个步骤，问 4 个问题：

1. **用户会尝试达成正确的目标吗？** — 界面是否让用户知道该做什么
2. **用户会注意到正确的操作入口吗？** — 按钮/菜单是否可见、突出
3. **用户会把这个操作和预期结果关联起来吗？** — 标签文字是否清晰无歧义
4. **操作完成后，用户能看到进展吗？** — 反馈是否及时、可理解

AI 适配：不点击，追踪代码路径。入口控件 → handler → 状态变更 → UI 更新，逐步问 4 个问题。截图验证视觉层面。

### 第二层：启发式评估（Nielsen's 10 Heuristics）— 发现"不好用"的问题

每条启发式 3 个检查项（共 30 项）：

#### H1 系统状态可见性
- 截图：是否有持久状态区域（状态栏/底栏）显示 Loading/Saving/N items/Connected？
- 代码：长操作是否更新 UI 状态（status_var.set / progressbar / after 回调）？
- 代码：数据变更是否同步更新可见计数器/标签？

#### H2 系统与真实世界匹配
- 截图：标签是否用领域术语（Library/Installed）而非内部名（AppID/Cache/SyncState）？
- 代码：内部标识符是否直接显示在 UI 中？
- 截图：日期/时间/单位是否人类可读格式？

#### H3 用户控制与自由
- 截图：模态窗口是否有明显的 Back/Cancel/Close 按钮？
- 代码：破坏性操作是否需要确认？默认焦点是否在非破坏性按钮上？
- 代码：是否有撤销/恢复路径？

#### H4 一致性与标准
- 截图：主要操作位置是否一致（OK/Cancel 顺序、快捷键提示）？
- 代码：同一命令是否在多处用不同 handler 实现？
- 代码：控件选择是否标准（ttk.Combobox 选择、ttk.Treeview 表格）？

#### H5 错误预防
- 代码：输入是否在提交前验证（validatecommand / trace_add / 预检查）？
- 代码：是否有无效状态转换的防护（guard + button disable）？
- 截图：危险操作是否视觉分离/标记？

#### H6 识别而非回忆
- 截图：控件是否自描述（标签、列头、tooltip）？
- 代码：对话框是否预填当前值？
- 截图：当前模式/状态是否可见（筛选器、排序、视图模式）？

#### H7 灵活性与效率
- 代码：高频操作是否有键盘快捷键？
- 代码：是否有 type-to-search？
- 截图/代码：是否有批量操作（多选、全选、批量应用）？

#### H8 美学与极简设计
- 截图：主要内容是否占主导（而非控件面板）？
- 截图：是否有 >3 个控件集群竞争注意力？
- 代码：是否有冗余控件做同一件事？

#### H9 帮助用户识别和恢复错误
- 代码：异常是否以可操作消息呈现？
- 截图：错误对话框是否包含"发生了什么 + 怎么办"？
- 代码：错误后 UI 是否恢复到可用状态（finally 块）？

#### H10 帮助与文档
- 截图：是否有 Help 入口？
- 代码：Help 是否打开实际内容？
- 代码：复杂设置是否有内联提示/tooltip？

### 第三层：交互成本量化（Interaction Cost Analysis）— 发现"太累"的问题

对每个高频任务量化 6 种成本：

1. **动作步数**：完成任务需几次点击/按键（代码追踪 handler 链）
2. **指向精度**：目标控件多大多远（Fitts's Law，截图测量）
3. **视觉搜索**：同区域多少竞争元素（Hick's Law，截图计数）
4. **认知负荷**：需理解多少术语/状态（Miller's Law）
5. **记忆负担**：是否需记住跨窗口信息（代码查状态可见性）
6. **等待时间**：是否阻塞主线程（代码查线程使用）

## UX 定律红旗检测

| 定律 | 红旗模式 | 代码检测 |
|------|---------|---------|
| Fitts's Law | 可点击元素太小 | Label + bind("<Button-1>") 无 padding |
| Hick's Law | 选项过多无分组 | 单菜单 >9 个 add_command |
| Miller's Law | 长列表无搜索/分类 | Treeview 无关联 Entry 过滤 |
| Jakob's Law | 违反平台惯例 | 非标准快捷键绑定 |
| Tesler's Law | 暴露内部复杂度 | 设置面板大量裸 Checkbutton 无分组 |

## tkinter 特有 UX 反模式

1. **Label 伪装成按钮** — 无焦点环、无键盘激活、无视觉反馈
2. **弹窗轰炸** — 常规状态用 messagebox.showinfo 强制点确定
3. **无键盘导航** — 没有 Tab 顺序、Enter/Escape 绑定
4. **固定窗口大小** — resizable(False, False) 不同 DPI 裁切
5. **密集无分组控件** — 大量按钮/选项挤在一个 Frame
6. **隐藏状态** — 筛选/排序生效但界面看不出
7. **macOS 默认字体丑** — 未针对平台调整字体
8. **阻塞主线程** — 网络/文件操作在 button handler 中无线程
9. **无父窗口弹窗** — messagebox 无 parent 导致弹到后面
10. **错误只打印到控制台** — 用户看不到任何反馈

## 执行流程

### Phase 1 — 诊断
1. 截图当前界面所有状态
2. 定义 5-8 个核心用户任务
3. 对每个任务做认知走查 + 交互成本量化
4. 用 Nielsen 10 条做全局扫描
5. 输出：问题清单 + 严重度 + 改进方案

### Phase 2 — 设计
- 局部问题 → 逐个修复
- 结构性问题 → 重新设计信息架构和布局

## 参考来源

- Nielsen Norman Group: 10 Usability Heuristics
- Blackmon & Polson (2002): Cognitive Walkthrough
- Fitts (1954): Motor system predictive model
- Hick (1952): Choice reaction time
- Miller (1956): The Magical Number Seven
- IxDF: Cognitive Walkthrough methodology
- MDPI Electronics 2024: LLM-based UX Testing
