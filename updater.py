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

__version__ = "5.7.2"

UPDATE_SOURCES = [
    "https://gitee.com/dtq1997/SteamShelf/releases/download/latest/version.json",
    "https://github.com/dtq1997/SteamShelf/releases/download/latest/version.json",
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
    """检查更新，返回 dict 或 None

    返回: {has_update, version, changelog, download_urls} 或 None（无更新/失败）
    """
    import urllib.request
    current = parse_version(__version__)
    last_err = None

    for source_url in UPDATE_SOURCES:
        try:
            req = urllib.request.Request(source_url, headers={
                "User-Agent": f"SteamShelf/{__version__}"})
            resp = urlopen(req, timeout=timeout)
            data = json.loads(resp.read().decode("utf-8"))
            remote = parse_version(data["version"])
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
            return None  # 已是最新
        except Exception as e:
            last_err = e
            continue

    if last_err:
        print(f"[更新] 检查失败: {last_err}")
    return None


def download_update(urls, dest_path, progress_cb=None) -> bool:
    """从 urls 列表降级下载到 dest_path，返回是否成功

    progress_cb(downloaded_bytes, total_bytes) — total 可能为 0（未知）
    """
    import urllib.request
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": f"SteamShelf/{__version__}"})
            resp = urlopen(req, timeout=60)
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
            return True
        except Exception as e:
            print(f"[更新] 下载失败 {url}: {e}")
            continue
    return False


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
        bat_content = (
            '@echo off\r\n'
            'timeout /t 2 /nobreak >nul\r\n'
            f'powershell -Command "Expand-Archive -Force \'{zp}\' \'{ad}\'"\r\n'
            f'start "" "{exe_path}"\r\n'
            'del "%~f0"\r\n'
        )
        with open(bat_path, "w", encoding="gbk") as f:
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
