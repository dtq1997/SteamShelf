"""SteamShelf 自动更新模块

职责：版本检查、下载、应用更新（批处理脚本法）。
所有 HTTP 请求使用 utils.urlopen()。
"""

import json
import os
import subprocess
import sys
import tempfile

from utils import urlopen

__version__ = "5.9.7"

UPDATE_SOURCES = [
    "https://gh-proxy.com/https://github.com/dtq1997/SteamShelf/releases/latest/download/version.json",
    "https://ghfast.top/https://github.com/dtq1997/SteamShelf/releases/latest/download/version.json",
    "https://github.com/dtq1997/SteamShelf/releases/latest/download/version.json",
]


def parse_version(s: str) -> tuple:
    """'5.7.2' → (5, 7, 2)"""
    return tuple(int(x) for x in s.strip().split("."))


def get_platform_key() -> str:
    """返回当前平台的下载键：win32 / darwin / source"""
    if getattr(sys, 'frozen', False):
        if sys.platform == "win32":
            return "win32"
        if sys.platform == "darwin":
            return "darwin"
    return "source"


def get_app_dir() -> str:
    """当前 exe 或脚本所在目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_exe_path() -> str:
    """当前可执行文件路径"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return sys.executable  # python interpreter


def _resolve_platform_urls(download_urls) -> list:
    """从 download_urls 解析当前平台的下载链接列表

    兼容两种格式：
    - list: 旧格式，直接返回
    - dict: 按平台分类 {win32: [...], darwin: [...], source: [...]}
            优先匹配精确平台，回退到 source
    """
    if isinstance(download_urls, list):
        return download_urls
    if isinstance(download_urls, dict):
        key = get_platform_key()
        return download_urls.get(key) or download_urls.get("source", [])
    return []


def check_update(timeout=10):
    """检查更新

    返回:
      - {has_update: True, version, changelog, download_urls} — 有新版本
      - {has_update: False} — 确认无更新（至少一个源成功响应）
      - None — 所有源均失败（网络问题）
    """
    import urllib.request
    current = parse_version(__version__)
    last_err = None
    any_success = False

    for source_url in UPDATE_SOURCES:
        try:
            req = urllib.request.Request(source_url, headers={
                "User-Agent": f"SteamShelf/{__version__}"})
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            remote = parse_version(data["version"])
            any_success = True
            if remote > current:
                urls = _resolve_platform_urls(data.get("download_urls", []))
                if not urls:
                    continue  # 该源没有当前平台的下载链接
                return {
                    "has_update": True,
                    "version": data["version"],
                    "changelog": data.get("changelog", ""),
                    "download_urls": urls,
                    "min_version": data.get("min_version", ""),
                }
            return {"has_update": False}  # 源成功响应，确认无更新
        except Exception as e:
            last_err = e
            continue

    if last_err:
        print(f"[更新] 检查失败: {last_err}")
    return {"has_update": False} if any_success else None


def download_update(urls, dest_path, progress_cb=None,
                    status_cb=None) -> bool:
    """从 urls 列表降级下载到 dest_path，返回是否成功

    progress_cb(downloaded_bytes, total_bytes) — total 可能为 0（未知）
    status_cb(message) — 连接/切换镜像等状态文本
    """
    import time
    import urllib.request
    n = len(urls)
    _last_cb_time = 0.0
    for i, url in enumerate(urls):
        try:
            if status_cb:
                label = _mirror_label(url)
                status_cb(f"连接 {label}...（{i+1}/{n}）")
            req = urllib.request.Request(url, headers={
                "User-Agent": f"SteamShelf/{__version__}"})
            with urlopen(req, timeout=20) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                if status_cb:
                    status_cb(None)  # 清除状态，切换到进度模式
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        # 节流：最多 10 次/秒
                        now = time.monotonic()
                        if progress_cb and now - _last_cb_time >= 0.1:
                            _last_cb_time = now
                            progress_cb(downloaded, total)
                # 最终进度确保 100%
                if progress_cb:
                    progress_cb(downloaded, total)
            # 校验 zip magic bytes，防止代理返回 HTML 错误页
            with open(dest_path, "rb") as f:
                magic = f.read(4)
            if magic[:2] != b'PK':
                print(f"[更新] {url} 返回的不是有效 zip 文件")
                continue
            return True
        except Exception as e:
            print(f"[更新] 下载失败 {url}: {e}")
            continue
    # 全部失败，清理残留的部分下载文件
    try:
        if os.path.exists(dest_path):
            os.remove(dest_path)
    except OSError:
        pass
    return False


def _mirror_label(url: str) -> str:
    """从下载 URL 提取简短镜像名"""
    if 'gh-proxy' in url:
        return 'gh-proxy'
    if 'ghfast' in url:
        return 'ghfast'
    if 'github.com' in url:
        return 'GitHub'
    return url.split('/')[2][:20]


def apply_update_and_restart(zip_path, app_dir=None):
    """写批处理脚本 → 启动 → 退出当前进程

    仅 Windows + frozen exe 时使用批处理；其他情况仅提示用户手动替换。
    """
    if app_dir is None:
        app_dir = get_app_dir()

    if sys.platform == "win32" and getattr(sys, 'frozen', False):
        exe_path = sys.executable
        bat_path = os.path.join(app_dir, "_update.bat")
        zp = zip_path.replace("'", "''")
        ad = app_dir.replace("'", "''")
        err_log = os.path.join(tempfile.gettempdir(), "SteamShelf_update_err.txt")
        # Defender 排除：用 base64 编码避免 bat→PS→PS 三层引号嵌套
        import base64
        defender_ps = f"Add-MpPreference -ExclusionPath '{app_dir}'"
        defender_b64 = base64.b64encode(
            defender_ps.encode('utf-16-le')).decode('ascii')
        bat_content = (
            '@echo off\r\n'
            'timeout /t 2 /nobreak >nul\r\n'
            # 尝试添加 Defender 排除（弹 UAC，用户拒绝则跳过）
            f'powershell -NoProfile -Command "Start-Process powershell'
            f" -Verb RunAs -ArgumentList '-NoProfile',"
            f"'-EncodedCommand','{defender_b64}'"
            f'" 2>nul\r\n'
            # 解压更新
            f'powershell -Command "Expand-Archive -Force \'{zp}\' \'{ad}\'"\r\n'
            'if %errorlevel% neq 0 (\r\n'
            f'  echo SteamShelf 更新解压失败，请手动下载新版本覆盖安装。> "{err_log}"\r\n'
            f'  start notepad "{err_log}"\r\n'
            f'  start "" "{exe_path}"\r\n'
            '  del "%~f0"\r\n'
            '  exit /b 1\r\n'
            ')\r\n'
            f'start "" "{exe_path}"\r\n'
            'del "%~f0"\r\n'
        )
        with open(bat_path, "w", encoding="gbk", newline='') as f:
            f.write(bat_content)
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=subprocess.CREATE_NO_WINDOW)
        sys.exit(0)
    else:
        # 非 Windows 或源码运行：返回 zip 路径让 UI 层提示手动替换
        return zip_path


def cleanup_update():
    """启动时清理上次更新残留文件"""
    app_dir = get_app_dir()
    for name in ("_update.bat",):
        p = os.path.join(app_dir, name)
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    # 清理 .old 文件
    for f in os.listdir(app_dir):
        if f.endswith(".old"):
            try:
                os.remove(os.path.join(app_dir, f))
            except Exception:
                pass


def get_temp_zip_path() -> str:
    """返回临时 zip 文件路径"""
    return os.path.join(tempfile.gettempdir(), "SteamShelf_update.zip")


def get_download_zip_path(version: str) -> str:
    """返回用户友好的下载路径：~/Downloads/SteamShelf_vX.X.X.zip"""
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return os.path.join(downloads, f"SteamShelf_v{version}.zip")


def reveal_in_file_manager(path: str):
    """在系统文件管理器中显示文件"""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        # Linux: 打开所在目录
        subprocess.Popen(["xdg-open", os.path.dirname(path)])
