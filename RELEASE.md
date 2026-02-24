# SteamShelf 发版指南

## ⛔ 绝对禁止（违反此规则 = 发版失败）

1. **禁止 `gh release create`** — 手动创建的 release 没有 Win/Mac 构建包，新用户无法下载
2. **唯一正确路径：`git tag` + `git push --tags`** — 由 GitHub Actions 自动构建三平台 + 创建 release
3. **发版完成的定义：Release 页面同时存在 `SteamShelf_win.zip` + `SteamShelf_mac.zip` + `SteamShelf_source.zip` + `version.json`** — 缺任何一个都不算完成
4. **`_scripts/` 目录含敏感数据（API key）** — 任何打包操作必须排除

## 给 AI 助手的指令

当用户说"发版"、"更新给用户"、"推新版本"、"推更新"时，执行以下步骤：

### 步骤 1：确定版本号

读取 `updater.py` 第 15 行的 `__version__`，patch +1。
例如 `"5.7.2"` → `"5.8.0"`（有大改动升 minor），`"5.7.2"` → `"5.7.3"`（小修复升 patch）。
不确定就问用户。

### 步骤 2：写更新日志

运行 `git log --oneline <上次tag>..HEAD` 查看改动，用中文写 3-5 行 changelog。

### 步骤 3：改版本号

编辑 `updater.py` 第 15 行：
```python
__version__ = "新版本号"
```

### 步骤 4：提交 + 打标签 + 推送

```bash
git add -A
git commit -m "release: v新版本号"
git tag -a v新版本号 -m "更新日志内容（多行）"
git push && git push --tags
```

### 步骤 5：确认

告诉用户：已推送，GitHub Actions 正在自动打包三个平台（约 5-10 分钟）。
给用户链接：`https://github.com/dtq1997/SteamShelf/actions`

## 自动化原理

- GitHub Actions 工作流：`.github/workflows/release.yml`
- 推送 `v*` 标签时自动触发
- 并行打包 Windows exe + macOS app + 源码 zip
- 自动创建 GitHub Release 页面
- `version.json` 和三个 zip 放在同一个 Release 里
- GitHub 自动标记最新 Release 为 "Latest"
- 用户客户端通过 `/releases/latest/download/version.json` 检查更新（GitHub 内置重定向）

## 关键文件

| 文件 | 作用 |
|------|------|
| `updater.py` | 版本号 + 检查/下载/应用更新逻辑 |
| `ui_updater.py` | 更新提示 UI（顶栏标签 + 弹窗 + 下载进度） |
| `.github/workflows/release.yml` | GitHub Actions 自动打包脚本 |
| `requirements.txt` | 打包依赖清单 |
| `SteamShelf.spec` | PyInstaller 打包配置 |

## 更新源

客户端按顺序尝试（`updater.py` UPDATE_SOURCES）：
1. `https://gitee.com/dtq1997/SteamShelf/releases/download/latest/version.json`（Gitee 需手动同步）
2. `https://github.com/dtq1997/SteamShelf/releases/latest/download/version.json`（GitHub 内置重定向）

## 故障排查

- **Actions 失败**：去 GitHub Actions 页面看日志，通常是依赖安装或打包问题
- **用户收不到更新**：检查最新 Release 里是否有 `version.json`
- **下载链接 404**：检查 Release 页面的附件是否上传成功
