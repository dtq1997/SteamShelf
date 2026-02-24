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

__version__ = "5.10.5"

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


def _build_update_bat(pid, zip_ps, tmp_dir_ps, app_dir, exe_path, err_log):
    """生成健壮的更新批处理脚本

    策略：taskkill 强杀（PID + 进程名）→ 循环等进程退出 →
          解压到临时目录 → xcopy 覆盖（失败重试3次）→ 清理
    每步写调试日志到 %TEMP%\\SteamShelf_update_debug.txt。
    """
    dbg = os.path.join(tempfile.gettempdir(),
                       "SteamShelf_update_debug.txt").replace("/", "\\")
    exe_name = os.path.basename(exe_path)
    lines = [
        '@echo off',
        f'echo [%date% %time%] update.bat started >> "{dbg}"',
        f'echo PID={pid} zip={zip_ps} >> "{dbg}"',
        f'echo app_dir={app_dir} >> "{dbg}"',
        f'echo exe_path={exe_path} >> "{dbg}"',
        '',
        'REM === Kill by PID + image name ===',
        f'echo [%date% %time%] taskkill /F /PID {pid} >> "{dbg}"',
        f'taskkill /F /PID {pid} >nul 2>&1',
        f'echo [%date% %time%] taskkill /F /IM {exe_name} >> "{dbg}"',
        f'taskkill /F /IM {exe_name} >nul 2>&1',
        '',
        'REM === Loop-wait until process is gone (max 30s) ===',
        f'echo [%date% %time%] waiting for process exit >> "{dbg}"',
        'set /a _wait=0',
        ':wait_loop',
        f'tasklist /FI "IMAGENAME eq {exe_name}" 2>nul '
        f'| find /i "{exe_name}" >nul 2>&1',
        'if errorlevel 1 goto wait_done',
        'if %_wait% geq 30 goto wait_done',
        'timeout /t 1 /nobreak >nul',
        'set /a _wait+=1',
        f'echo [%date% %time%] still waiting (%_wait%s) >> "{dbg}"',
        'goto wait_loop',
        ':wait_done',
        f'echo [%date% %time%] process wait done (%_wait%s) >> "{dbg}"',
        '',
        'REM === Expand-Archive ===',
        f'if exist "{tmp_dir_ps}" rd /s /q "{tmp_dir_ps}" >nul 2>&1',
        f'echo [%date% %time%] Expand-Archive start >> "{dbg}"',
        f"powershell -NoProfile -Command \""
        f"Expand-Archive -Force '{zip_ps}' '{tmp_dir_ps}'"
        f"\"",
        f'echo [%date% %time%] Expand-Archive errorlevel='
        f'%errorlevel% >> "{dbg}"',
        'if %errorlevel% neq 0 (',
        f'  echo [SteamShelf] 解压到临时目录失败。> "{err_log}"',
        f'  echo 可能原因：安全软件拦截或磁盘空间不足。>> "{err_log}"',
        f'  echo [%date% %time%] FAIL: extract >> "{dbg}"',
        f'  start notepad "{dbg}"',
        f'  start "" "{exe_path}"',
        '  del "%~f0"',
        '  exit /b 1',
        ')',
        '',
        'REM === xcopy with retry (max 3 attempts) ===',
        'set /a _try=0',
        ':xcopy_retry',
        'set /a _try+=1',
        f'echo [%date% %time%] xcopy attempt %_try% >> "{dbg}"',
        f'xcopy "{tmp_dir_ps}\\*" "{app_dir}\\" /E /Y /R /Q >nul 2>&1',
        f'echo [%date% %time%] xcopy errorlevel='
        f'%errorlevel% >> "{dbg}"',
        'if %errorlevel% equ 0 goto xcopy_ok',
        'if %_try% geq 3 goto xcopy_fail',
        f'echo [%date% %time%] xcopy failed, retry in 5s >> "{dbg}"',
        'timeout /t 5 /nobreak >nul',
        'goto xcopy_retry',
        ':xcopy_fail',
        f'echo [SteamShelf] 复制文件失败（重试3次）。> "{err_log}"',
        f'echo 文件可能被安全软件或其他进程占用。>> "{err_log}"',
        f'echo [%date% %time%] FAIL: xcopy after 3 tries >> "{dbg}"',
        f'start notepad "{dbg}"',
        f'start "" "{exe_path}"',
        'del "%~f0"',
        'exit /b 1',
        ':xcopy_ok',
        '',
        'REM === Cleanup and restart ===',
        f'echo [%date% %time%] cleanup tmp_dir >> "{dbg}"',
        f'rd /s /q "{tmp_dir_ps}" >nul 2>&1',
        f'echo [%date% %time%] starting exe >> "{dbg}"',
        f'start "" "{exe_path}"',
        f'echo [%date% %time%] SUCCESS >> "{dbg}"',
        f'start notepad "{dbg}"',
        'del "%~f0"',
    ]
    return '\r\n'.join(lines) + '\r\n'


def apply_update_and_restart(zip_path, app_dir=None):
    """写批处理脚本 → 启动 → 退出当前进程

    仅 Windows + frozen exe 时使用批处理；其他情况仅提示用户手动替换。
    """
    if app_dir is None:
        app_dir = get_app_dir()

    if sys.platform == "win32" and getattr(sys, 'frozen', False):
        import time as _time
        exe_path = sys.executable
        pid = os.getpid()
        bat_path = os.path.join(app_dir, "_update.bat")
        tmp_dir = os.path.join(tempfile.gettempdir(), "SteamShelf_update_tmp")
        err_log = os.path.join(tempfile.gettempdir(),
                               "SteamShelf_update_err.txt")
        dbg_path = os.path.join(tempfile.gettempdir(),
                                "SteamShelf_update_debug.txt")

        # Python 侧调试日志（bat 会 append 到同一文件）
        def _dbg(msg):
            try:
                with open(dbg_path, "a", encoding="utf-8") as _f:
                    _f.write(f"[PY {_time.strftime('%H:%M:%S')}] {msg}\n")
            except Exception:
                pass

        _dbg(f"apply_update_and_restart START")
        _dbg(f"  zip_path={zip_path}")
        _dbg(f"  app_dir={app_dir}")
        _dbg(f"  exe_path={exe_path}")
        _dbg(f"  pid={pid}")
        _dbg(f"  bat_path={bat_path}")

        # 转义 PowerShell 单引号
        zp = zip_path.replace("'", "''")
        td = tmp_dir.replace("'", "''")
        bat_content = _build_update_bat(
            pid, zp, td, app_dir, exe_path, err_log)

        _dbg(f"writing bat ({len(bat_content)} bytes)")
        with open(bat_path, "w", encoding="gbk", newline='') as f:
            f.write(bat_content)
        _dbg(f"bat written OK, exists={os.path.exists(bat_path)}")

        _dbg("launching subprocess.Popen cmd /c ...")
        proc = subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=subprocess.CREATE_NO_WINDOW)
        _dbg(f"Popen OK, child pid={proc.pid}")

        # 等待 1 秒确保子进程已启动
        _dbg("sleeping 1s before os._exit(0)")
        _time.sleep(1)
        _dbg("calling os._exit(0)")
        os._exit(0)
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
    # 清理更新临时目录
    tmp_dir = os.path.join(tempfile.gettempdir(), "SteamShelf_update_tmp")
    if os.path.isdir(tmp_dir):
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
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
