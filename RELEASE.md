# SteamShelf 发版指南

## 给 AI 助手的指令

当用户说"发版"、"更新给用户"、"推新版本"时，执行以下步骤：

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
- 自动更新 `latest` release 的 `version.json`
- 用户客户端启动时检查 `version.json`，发现新版本则顶栏提示

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
1. `https://gitee.com/dtq1997/SteamShelf/releases/download/latest/version.json`
2. `https://github.com/dtq1997/SteamShelf/releases/download/latest/version.json`

Gitee 源需手动同步（从 GitHub Release 下载后上传到 Gitee Release）。

## 故障排查

- **Actions 失败**：去 GitHub Actions 页面看日志，通常是依赖安装或打包问题
- **用户收不到更新**：检查 `latest` release 是否有 `version.json`
- **下载链接 404**：检查 Release 页面的附件是否上传成功
