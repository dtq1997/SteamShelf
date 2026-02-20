"""Steam Cloud 上传器 — 通过 Steamworks API 直接上传文件到 Steam Cloud

架构说明（v2 子进程隔离）：
所有 Steamworks API 调用（Init / FileWrite / FileDelete / Shutdown）均在独立子进程中运行。
主进程通过 multiprocessing.Queue 发送命令并接收结果。

为什么需要子进程？
Python ctypes.CDLL 加载 libsteam_api 后，Steam 客户端通过 IPC 管道检测到连接，
认为 AppID 2371090（Steam Game Notes）正在运行。即使调用 SteamAPI_Shutdown()，
由于 ctypes 不会自动 dlclose，dylib 和 IPC 连接始终驻留在进程内存中。
手动 dlclose 在 macOS/Linux 上也不可靠（引用计数、dyld 缓存等原因）。
唯一可靠的释放方式是让持有 dylib 的进程退出——OS 保证回收一切资源。
"""

import ctypes
import glob
import multiprocessing as mp
import os
import platform
import subprocess
import tempfile


# ═══════════════════════════════════════════════════════════════════════════════
#  子进程工作函数（模块级，可被 multiprocessing spawn 序列化）
# ═══════════════════════════════════════════════════════════════════════════════

def _worker_init_steam(dll, result_queue):
    """初始化 Steam API + 获取 RemoteStorage 接口 + 获取登录用户。

    Returns:
        (remote_storage, ver_used, logged_in_friend_code) 或 None（失败时已向 result_queue 发送错误）
    """
    # 初始化 Steam API
    ok = False
    init_msg = ""
    try:
        func = dll.SteamInternal_SteamAPI_Init
        func.restype = ctypes.c_int
        err_msg = ctypes.create_string_buffer(1024)
        result = func(None, ctypes.byref(err_msg))
        ok = (result == 0)
        if not ok:
            init_msg = f"Init 失败: {err_msg.value.decode('utf-8', errors='replace')}"
    except AttributeError:
        try:
            func = dll.SteamAPI_Init
            func.restype = ctypes.c_bool
            ok = func()
            if not ok:
                init_msg = "SteamAPI_Init 返回 false"
        except AttributeError:
            result_queue.put(("init_fail", "找不到 Init 函数"))
            return None

    if not ok:
        result_queue.put(("init_fail", init_msg))
        return None

    # 获取 ISteamRemoteStorage 接口
    remote_storage = None
    ver_used = ""
    for ver in ["v016", "v014", "v013"]:
        try:
            func = getattr(dll, f"SteamAPI_SteamRemoteStorage_{ver}")
            func.restype = ctypes.c_void_p
            ptr = func()
            if ptr:
                remote_storage = ptr
                ver_used = ver
                break
        except AttributeError:
            continue

    if not remote_storage:
        result_queue.put(("init_fail", "无法获取 RemoteStorage 接口"))
        try:
            dll.SteamAPI_Shutdown()
        except Exception:
            pass
        return None

    # 获取当前登录的 Steam 账号 ID
    logged_in_friend_code = _worker_get_logged_in_user(dll)

    return remote_storage, ver_used, logged_in_friend_code


def _worker_get_logged_in_user(dll):
    """获取当前登录的 Steam 用户好友代码"""
    try:
        steam_user = None
        for ver in ["v023", "v021", "v020"]:
            try:
                func = getattr(dll, f"SteamAPI_SteamUser_{ver}")
                func.restype = ctypes.c_void_p
                ptr = func()
                if ptr:
                    steam_user = ptr
                    break
            except AttributeError:
                continue
        if steam_user:
            get_id_func = dll.SteamAPI_ISteamUser_GetSteamID
            get_id_func.restype = ctypes.c_uint64
            get_id_func.argtypes = [ctypes.c_void_p]
            steam_id64 = get_id_func(steam_user)
            if steam_id64 and steam_id64 > 76561197960265728:
                return str(steam_id64 - 76561197960265728)
    except Exception:
        pass
    return None


def _worker_handle_command(cmd, dll, remote_storage, result_queue):
    """处理单条命令（file_write / file_delete / batch_file_write / flush）"""
    if cmd[0] == "file_write":
        _, filename, data = cmd
        try:
            func = dll.SteamAPI_ISteamRemoteStorage_FileWrite
            func.restype = ctypes.c_bool
            func.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_int32]
            res = func(remote_storage, filename.encode("utf-8"),
                       data, len(data))
            try:
                dll.SteamAPI_RunCallbacks()
            except Exception:
                pass
            result_queue.put(("write_result", bool(res)))
        except Exception:
            result_queue.put(("write_result", False))

    elif cmd[0] == "file_delete":
        _, filename = cmd
        try:
            func = dll.SteamAPI_ISteamRemoteStorage_FileDelete
            func.restype = ctypes.c_bool
            func.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            res = func(remote_storage, filename.encode("utf-8"))
            try:
                dll.SteamAPI_RunCallbacks()
            except Exception:
                pass
            result_queue.put(("delete_result", bool(res)))
        except Exception:
            result_queue.put(("delete_result", False))

    elif cmd[0] == "batch_file_write":
        _, file_list = cmd  # file_list: [(filename, data), ...]
        fw_func = dll.SteamAPI_ISteamRemoteStorage_FileWrite
        fw_func.restype = ctypes.c_bool
        fw_func.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                            ctypes.c_void_p, ctypes.c_int32]
        cb_func = dll.SteamAPI_RunCallbacks
        ok = 0
        fail = 0
        total = len(file_list)
        for i, (filename, data) in enumerate(file_list):
            try:
                res = fw_func(remote_storage, filename.encode("utf-8"),
                              data, len(data))
                if res:
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
            if (i + 1) % 10 == 0 or i == total - 1:
                try:
                    cb_func()
                except Exception:
                    pass
                result_queue.put(("batch_progress", i + 1, total, ok, fail))
        result_queue.put(("batch_done", ok, fail))

    elif cmd[0] == "flush":
        # 持续调用 RunCallbacks 一段时间，帮助 Steam 处理待同步文件
        import time as _time
        duration = cmd[1] if len(cmd) > 1 else 3.0
        end = _time.time() + duration
        while _time.time() < end:
            try:
                dll.SteamAPI_RunCallbacks()
            except Exception:
                break
            _time.sleep(0.1)
        result_queue.put(("flush_done",))


def _worker_process_main(cmd_queue, result_queue, dylib_path, work_dir, app_id):
    """在独立子进程中运行：加载 dylib → 初始化 Steam API → 循环处理命令 → 退出。

    进程退出后 OS 自动释放 dylib、关闭 IPC 连接，
    Steam 客户端必然检测到 Game Notes 已停止运行。
    """
    import os

    # 1. 准备工作目录
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "steam_appid.txt"), "w") as f:
        f.write(app_id)
    os.chdir(work_dir)

    # 2. 加载 dylib
    try:
        dll = ctypes.CDLL(dylib_path)
    except OSError as e:
        result_queue.put(("init_fail", f"加载 dylib 失败: {e}"))
        return

    # 3. 初始化 Steam API + 获取接口
    init_result = _worker_init_steam(dll, result_queue)
    if init_result is None:
        return
    remote_storage, ver_used, logged_in_friend_code = init_result

    # 4. 通知主进程初始化成功
    result_queue.put(("init_ok", ver_used, logged_in_friend_code))

    # 5. 命令循环
    while True:
        try:
            cmd = cmd_queue.get(timeout=1)
        except Exception:
            continue
        if cmd[0] == "shutdown":
            break
        _worker_handle_command(cmd, dll, remote_storage, result_queue)

    # 6. 优雅退出
    try:
        dll.SteamAPI_RunCallbacks()
    except Exception:
        pass
    try:
        dll.SteamAPI_Shutdown()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  主进程代理类（对外接口与旧版完全兼容）
# ═══════════════════════════════════════════════════════════════════════════════

class SteamCloudUploader:
    """通过 Steamworks SDK 的 ISteamRemoteStorage::FileWrite 直接上传文件到 Steam Cloud。
    
    所有 Steamworks API 调用在独立子进程中执行。
    shutdown() 终止子进程后，Steam 客户端会立即检测到 Game Notes 已停止运行。
    """
    
    def __init__(self):
        self._worker = None  # multiprocessing.Process
        self._cmd_queue = None  # 主进程 → 子进程
        self._result_queue = None  # 子进程 → 主进程
        self._work_dir = None  # 临时目录（含 steam_appid.txt）
        self.initialized = False
        self.logged_in_friend_code = None  # 当前登录的 Steam 账号 32 位 ID
        self._dylib_path = None
        self._init_error = None
    
    def auto_init(self, steam_path: str, app_id: str = "2371090") -> tuple:
        """自动查找 dylib 并在子进程中初始化 Steam API。
        Returns: (success: bool, message: str)
        """
        # 1. 查找 dylib
        dylib = self._find_dylib(steam_path)
        if not dylib:
            self._init_error = "未找到 libsteam_api"
            return False, "未找到 libsteam_api.dylib/.so/.dll"
        self._dylib_path = dylib
        
        # 2. 创建临时目录（子进程中写 steam_appid.txt 并 chdir）
        self._work_dir = tempfile.mkdtemp(prefix="steam_cloud_")
        
        # 3. 启动子进程
        self._cmd_queue = mp.Queue()
        self._result_queue = mp.Queue()
        self._worker = mp.Process(
            target=_worker_process_main,
            args=(self._cmd_queue, self._result_queue,
                  dylib, self._work_dir, app_id),
            daemon=True,
        )
        self._worker.start()
        
        # 4. 等待初始化结果
        try:
            result = self._result_queue.get(timeout=30)
        except Exception:
            self._init_error = "初始化超时"
            self._kill_worker()
            return False, self._init_error
        
        if result[0] == "init_ok":
            _, ver_used, friend_code = result
            self.initialized = True
            self.logged_in_friend_code = friend_code
            return True, f"OK ({ver_used})"
        else:
            self._init_error = result[1]
            self._kill_worker()
            return False, self._init_error
    
    def _find_dylib(self, steam_path: str) -> str:
        """在 Steam 安装目录中搜索 libsteam_api"""
        system = platform.system()
        if system == "Darwin":
            name = "libsteam_api.dylib"
        elif system == "Linux":
            name = "libsteam_api.so"
        else:
            name = "steam_api.dll"
        
        common = os.path.join(steam_path, "steamapps", "common")
        if os.path.isdir(common):
            for found in glob.glob(os.path.join(common, "**", name), recursive=True):
                return found
        
        # macOS: 也搜索 /Applications/Steam.app
        if system == "Darwin":
            for found in glob.glob(
                os.path.join("/Applications/Steam.app", "**", name), recursive=True
            ):
                return found
        return None
    
    def file_write(self, filename: str, data: bytes) -> bool:
        """调用 ISteamRemoteStorage::FileWrite 上传文件到 Steam Cloud"""
        if not self.initialized or not self._worker_alive():
            return False
        try:
            self._cmd_queue.put(("file_write", filename, data))
            result = self._result_queue.get(timeout=30)
            return result[1] if result[0] == "write_result" else False
        except Exception:
            return False
    
    def file_delete(self, filename: str) -> bool:
        """调用 ISteamRemoteStorage::FileDelete 从 Steam Cloud 删除文件"""
        if not self.initialized or not self._worker_alive():
            return False
        try:
            self._cmd_queue.put(("file_delete", filename))
            result = self._result_queue.get(timeout=30)
            return result[1] if result[0] == "delete_result" else False
        except Exception:
            return False

    def flush_callbacks(self, duration: float = 3.0) -> bool:
        """让子进程持续调用 RunCallbacks 一段时间，帮助 Steam 处理待同步文件。

        Args:
            duration: 持续时间（秒），默认 3 秒
        Returns:
            True 如果 flush 正常完成
        """
        if not self.initialized or not self._worker_alive():
            return False
        try:
            self._cmd_queue.put(("flush", duration))
            result = self._result_queue.get(timeout=duration + 5)
            return result[0] == "flush_done"
        except Exception:
            return False

    @staticmethod
    def is_steam_running() -> bool:
        """检测 Steam 客户端是否正在运行（跨平台）"""
        system = platform.system()
        try:
            if system == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                    capture_output=True, text=True, timeout=5)
                return "steam.exe" in result.stdout.lower()
            elif system == "Darwin":
                result = subprocess.run(
                    ["pgrep", "-x", "steam_osx"],
                    capture_output=True, timeout=5)
                if result.returncode == 0:
                    return True
                # fallback: 也检查 Steam Helper 进程
                result = subprocess.run(
                    ["pgrep", "-f", "Steam.app"],
                    capture_output=True, timeout=5)
                return result.returncode == 0
            else:  # Linux
                result = subprocess.run(
                    ["pgrep", "-x", "steam"],
                    capture_output=True, timeout=5)
                return result.returncode == 0
        except Exception:
            return True  # 检测失败时保守地认为 Steam 在运行

    def shutdown(self):
        """断开 Steam Cloud 连接，终止子进程。
        
        子进程退出后 OS 自动释放 dylib 和 IPC 连接，
        Steam 客户端将立即检测到 Game Notes 已停止运行。
        """
        if self._worker and self._worker.is_alive():
            # 先尝试优雅退出（发 shutdown 命令让子进程调用 SteamAPI_Shutdown）
            try:
                self._cmd_queue.put(("shutdown",))
                self._worker.join(timeout=5)
            except Exception:
                pass
            # 超时则强杀
            if self._worker.is_alive():
                self._worker.terminate()
                self._worker.join(timeout=3)
        self._worker = None
        self._cmd_queue = None
        self._result_queue = None
        self.initialized = False
        self.logged_in_friend_code = None
        # 清理临时目录
        self._cleanup_work_dir()
    
    def _worker_alive(self) -> bool:
        """检查子进程是否仍在运行"""
        return self._worker is not None and self._worker.is_alive()

    def is_alive(self) -> bool:
        """公开接口：检查子进程是否仍在运行（避免外部访问私有 _worker_alive）"""
        return self._worker_alive()
    
    def _kill_worker(self):
        """强制终止子进程"""
        if self._worker and self._worker.is_alive():
            try:
                self._worker.terminate()
                self._worker.join(timeout=3)
            except Exception:
                pass
        self._worker = None
        self._cleanup_work_dir()
    
    def batch_file_write(self, file_list: list, progress_callback=None) -> tuple:
        """批量上传文件到 Steam Cloud（一次性发送，消除逐条 Queue 往返开销）。

        Args:
            file_list: [(filename, data_bytes), ...]
            progress_callback: 可选回调 (current, total, ok, fail)
        Returns: (ok_count, fail_count)
        """
        if not self.initialized or not self._worker_alive():
            return 0, len(file_list)
        try:
            self._cmd_queue.put(("batch_file_write", file_list))
            # 持续读取进度消息，直到收到 batch_done
            while True:
                result = self._result_queue.get(timeout=600)
                if result[0] == "batch_progress":
                    _, current, total, ok, fail = result
                    if progress_callback:
                        progress_callback(current, total, ok, fail)
                elif result[0] == "batch_done":
                    _, ok, fail = result
                    return ok, fail
                else:
                    # 意外消息，忽略
                    continue
        except Exception:
            return 0, len(file_list)

    def _cleanup_work_dir(self):
        """清理临时目录"""
        if self._work_dir and os.path.exists(self._work_dir):
            try:
                import shutil
                shutil.rmtree(self._work_dir, ignore_errors=True)
            except Exception:
                pass
            self._work_dir = None
