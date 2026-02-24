# SteamShelf

Steam 游戏库管理工具 — 收藏夹云同步、AI 游戏说明、多源推荐、社区分享。

解决了 Steam 平台两个长期未开放的技术难题：**收藏夹没有公开 API** 和 **Steam Cloud 不支持自定义数据存储**。

<!-- 截图占位：后续补充 -->

---

## 核心技术：两个云同步难题的解法

### 难题一：收藏夹没有公开 API

**问题**：Steam 没有任何公开 API 来管理用户的游戏收藏夹（分类）。Web API、Steamworks SDK、Steam Community 均不提供此功能。用户只能在 Steam 客户端内手动操作。

**解法：CEF Bridge — 通过 Chrome DevTools Protocol 直接调用 Steam 内部 JS**

Steam 客户端的 UI 基于 Chromium Embedded Framework (CEF) 渲染。SteamShelf 利用这一点：

```
SteamShelf → WebSocket → Steam CEF (端口 8080) → SharedJSContext → collectionStore
```

1. 通过 `http://127.0.0.1:8080/json` 发现 Steam 的 CEF 调试目标
2. 找到 `SharedJSContext`（Steam 所有页面共享的 JS 上下文）
3. 建立 WebSocket 连接，通过 Chrome DevTools Protocol 的 `Runtime.evaluate` 执行 JS
4. 直接操作 Steam 内部对象 `collectionStore`：
   - `collectionStore.m_cloudStorageMap.StoreObject()` — 写入收藏夹数据
   - `collectionStore.SaveCollection()` — 触发 Steam 原生云同步
   - `collectionStore.DeleteCollection()` — 删除收藏夹
5. Steam 自动将变更同步到所有设备，与手动操作完全一致

**额外挑战**：获取当前用户的 SteamID3（收藏夹操作的必要参数）。由于 Steam 内部 JS 对象结构随版本更新变化，SteamShelf 实现了 7 种回退检测方法，从 `collectionStore.m_cloudStorageMap` 到 `appStore` 到 URL 解析，确保跨版本兼容。

> 相关代码：[`cef_bridge.py`](cef_bridge.py) — WebSocket 连接 + JS 执行 + 7 种 SteamID3 检测

### 难题二：Steam Cloud 不支持自定义数据存储

**问题**：Steam Cloud 只为游戏存档设计，没有通用的用户数据存储 API。第三方程序无法将自定义数据写入 Steam Cloud。

**解法：子进程隔离的 Steamworks API 调用**

SteamShelf 借用 AppID 2371090（Steam Game Notes）的 Cloud 配额，通过 Steamworks SDK 的 `ISteamRemoteStorage::FileWrite` 将笔记数据写入 Steam Cloud：

```
主进程 ──命令队列──→ 子进程（加载 libsteam_api）──→ Steamworks API ──→ Steam Cloud
        ←结果队列──                                                    ↓
                                                              子进程退出 = OS 释放 dylib + IPC
```

**为什么必须用子进程？**

Python `ctypes.CDLL` 加载 `libsteam_api` 后，Steam 客户端通过 IPC 管道检测到连接，认为 Game Notes 正在运行。问题在于：

- 调用 `SteamAPI_Shutdown()` 不够 — `ctypes` 不会自动 `dlclose`，dylib 和 IPC 连接始终驻留在进程内存中
- 手动 `dlclose` 不可靠 — macOS 的 dyld 缓存和引用计数机制导致卸载不彻底
- **唯一可靠的释放方式**：让持有 dylib 的进程退出，OS 保证回收一切资源

因此所有 Steamworks 调用在 `multiprocessing` 子进程中执行。主进程通过 Queue 发送命令（`file_write` / `file_delete` / `batch_file_write`），子进程处理后返回结果。上传完成后子进程退出，Steam 立即检测到 Game Notes 已停止运行。

**优化**：MD5 哈希追踪脏状态，仅上传有变更的文件；批量写入模式每 10 个文件调用一次 `RunCallbacks`，减少 IPC 开销。

> 相关代码：[`cloud_uploader.py`](cloud_uploader.py) — 子进程架构 + Steamworks API 封装

---

## 功能概览

### 游戏库管理
- 统一游戏列表：已安装 + 收藏夹 + 笔记，支持多维度排序和搜索
- 收藏夹操作：创建、编辑、删除、合并，通过 CEF Bridge 实时同步到 Steam
- 筛选表达式：用类 SQL 语法定义动态收藏夹（如 `type:game AND rating > 80`），自动保存为 Steam 分类并持续更新

### AI 游戏说明
- 多模型支持：Claude、OpenAI、DeepSeek、Google Gemini、本地 Ollama
- 联网检索 + 训练数据双模式，自动标注确信度、信息量、质量评级
- 批量生成 + 行内生成，笔记通过 Steam Cloud 跨设备同步

### 多源游戏推荐
- Steam 250 排行榜（按类型、标签、年份）
- Steam 鉴赏家推荐列表
- IGDB 数据库搜索
- SteamDB 评分排行

### 社区分享
- 发布和订阅游戏分类方案（基于 Supabase）
- 订阅自动同步：远端更新后本地自动拉取

### 其他
- Steam Cloud 笔记同步（子进程隔离架构）
- 笔记导入/导出（JSON / 纯文本 / 批量）
- 收藏夹备份与恢复
- 自动更新（多镜像降级下载，Windows 自动重启）

---

## 安装与使用

### 下载 Release（推荐）

从 [Releases](https://github.com/dtq1997/SteamShelf/releases) 下载对应平台的压缩包：

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows | `SteamShelf_win.zip` | 免安装，解压即用 |
| macOS | `SteamShelf_mac.zip` | 解压后运行 `.app` |
| 源码 | `SteamShelf_source.zip` | 需要 Python 3.9+ |

### 从源码运行

```bash
# 克隆仓库
git clone https://github.com/dtq1997/SteamShelf.git
cd SteamShelf

# 安装依赖
pip install -r requirements.txt

# 启动
python ui_main.py
```

### CEF Bridge 前置条件

收藏夹云同步功能需要 Steam 开启 CEF 远程调试：

1. 在 Steam 安装目录创建空文件 `.cef-enable-remote-debugging`
2. 重启 Steam

SteamShelf 首次连接时会自动引导完成此步骤。

---

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│                   UI 层 (tkinter)                   │
│  SteamToolboxMain = 13 个 Mixin 多继承组合          │
│  LibraryMixin | CloudMixin | CuratorMixin | ...     │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────┐
│                       数据层                        │
│  core_collections.py  — 收藏夹读写 + CEF 队列      │
│  core_notes.py        — 笔记管理 + 脏状态追踪      │
│  ai_generator.py      — 多模型 AI 生成             │
│  config_manager.py    — 统一配置 + 缓存持久化      │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────┐
│                     基础设施层                      │
│  cef_bridge.py       — CEF WebSocket 桥接          │
│  cloud_uploader.py   — Steamworks 子进程隔离       │
│  account_manager.py  — Steam 账号扫描与识别        │
│  utils.py            — HTTP / 排序 / 公共工具      │
└─────────────────────────────────────────────────────┘
```

- **跨平台**：Windows / macOS / Linux
- **42 个 Python 文件**，无外部 UI 框架依赖（纯 tkinter）
- **Mixin 组合模式**：每个功能模块独立开发，通过多继承组合到主类，共享 `self` 实例

---

## 许可证

MIT License
