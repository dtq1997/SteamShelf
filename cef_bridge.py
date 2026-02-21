# cef_bridge.py — Steam CEF 远程调试桥接器
#
# 通过 Chrome DevTools Protocol 连接 Steam 的 SharedJSContext，
# 调用 collectionStore API 实现收藏夹操作并自动触发云同步。
#
# 依赖：websocket-client（pip install websocket-client）

import json
import os
import socket
import threading
import time
import urllib.request
from typing import Optional, Callable, List, Tuple


class CEFBridge:
    """Steam CEF 远程调试桥接器"""

    CEF_PORT = 8080

    def __init__(self):
        self.ws = None
        self._msg_counter = 0
        self._connected = False
        self._ws_lock = threading.Lock()

    # ==================== 连接管理 ====================

    @classmethod
    def is_available(cls) -> bool:
        """检测 CEF 调试端口是否可用且 SharedJSContext 已就绪"""
        try:
            # 必须用 127.0.0.1 而非 localhost —— macOS 上 localhost 可能解析为
            # IPv6 (::1)，而 Steam CEF 只监听 IPv4 127.0.0.1，导致连接失败。
            # 同时绕过系统代理，避免 Bad Gateway 错误。
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(f"http://127.0.0.1:{cls.CEF_PORT}/json")
            with opener.open(req, timeout=2) as resp:
                targets = json.loads(resp.read().decode('utf-8'))
                return any("SharedJSContext" in t.get("title", "") for t in targets)
        except Exception:
            return False

    @classmethod
    def is_port_open(cls) -> bool:
        """仅检测 CEF 端口是否有响应（不要求 SharedJSContext 就绪）
        
        用于区分"Steam 还没启动"和"Steam 在启动中但还没完全就绪"
        """
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(f"http://127.0.0.1:{cls.CEF_PORT}/json")
            with opener.open(req, timeout=2) as resp:
                data = resp.read()
                return len(data) > 0
        except Exception:
            return False

    # ==================== 诊断工具 ====================

    @classmethod
    def diagnose(cls) -> dict:
        """全面诊断 CEF 连接状况，返回详细调试信息"""
        import platform as _platform

        system = _platform.system()
        result = {
            'steam_running': False,
            'steam_processes': [],
            'tcp_port_open': False,
            'tcp_error': None,
            'http_reachable': False,
            'http_status': None,
            'http_error': None,
            'targets': None,
            'shared_js_ready': False,
            'cef_arg_detected': None,
            'cef_file_exists': None,
            'cef_file_path': None,
            'platform': system,
            'summary': '',
        }

        file_exists, file_path = cls.is_cef_debugging_enabled()
        result['cef_file_exists'] = file_exists
        result['cef_file_path'] = file_path

        cls._diagnose_detect_processes(system, result)
        cls._diagnose_tcp_port(result)
        cls._diagnose_http(result)
        cls._diagnose_generate_summary(result)

        return result

    @classmethod
    def _diagnose_detect_processes(cls, system, result):
        """诊断步骤1：检测 Steam 进程（平台相关）"""
        import subprocess
        _CEF_ARGS = ('-cef-enable-debugging', '--cef-remote-debugging-port', '-devtools-port')
        try:
            if system == "Darwin":
                ps = subprocess.run(['pgrep', '-lf', 'steam'],
                                    capture_output=True, text=True, timeout=5)
                lines = [line.strip() for line in ps.stdout.strip().split('\n') if line.strip()]
                steam_lines = [line for line in lines
                               if any(kw in line.lower() for kw in ['steam_osx', 'steamwebhelper', 'steam helper'])]
                result['steam_processes'] = steam_lines
                result['steam_running'] = len(steam_lines) > 0
                result['cef_arg_detected'] = any(
                    any(arg in line for arg in _CEF_ARGS) for line in lines)
            elif system == "Windows":
                ps = subprocess.run(['tasklist', '/fi', 'imagename eq steam.exe'],
                                    capture_output=True, text=True, timeout=5)
                result['steam_running'] = 'steam.exe' in ps.stdout.lower()
                if result['steam_running']:
                    result['steam_processes'] = ['steam.exe (running)']
                try:
                    wmic = subprocess.run(
                        ['wmic', 'process', 'where', "name='steam.exe'", 'get', 'commandline'],
                        capture_output=True, text=True, timeout=5)
                    result['cef_arg_detected'] = any(arg in wmic.stdout for arg in _CEF_ARGS)
                except Exception:
                    pass
            elif system == "Linux":
                ps = subprocess.run(['pgrep', '-la', 'steam'],
                                    capture_output=True, text=True, timeout=5)
                lines = [line.strip() for line in ps.stdout.strip().split('\n') if line.strip()]
                result['steam_processes'] = lines
                result['steam_running'] = len(lines) > 0
                result['cef_arg_detected'] = any(
                    any(arg in line for arg in _CEF_ARGS) for line in lines)
        except Exception as e:
            result['steam_processes'] = [f'(检测失败: {e})']

    @classmethod
    def _diagnose_tcp_port(cls, result):
        """诊断步骤2：TCP 端口探测"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            try:
                err = sock.connect_ex(('127.0.0.1', cls.CEF_PORT))
            finally:
                sock.close()
            if err == 0:
                result['tcp_port_open'] = True
            else:
                result['tcp_error'] = f"connect_ex 返回错误码 {err}"
        except Exception as e:
            result['tcp_error'] = str(e)

    @classmethod
    def _diagnose_http(cls, result):
        """诊断步骤3：HTTP 请求检测 CEF targets"""
        if not result['tcp_port_open']:
            result['http_error'] = "TCP 端口未开放，跳过 HTTP 请求"
            return
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(f"http://127.0.0.1:{cls.CEF_PORT}/json")
            with opener.open(req, timeout=3) as resp:
                result['http_status'] = resp.status
                data = resp.read().decode('utf-8')
                targets = json.loads(data)
                result['http_reachable'] = True
                result['targets'] = [t.get('title', '(无标题)') for t in targets]
                result['shared_js_ready'] = any(
                    "SharedJSContext" in t.get("title", "") for t in targets)
        except urllib.error.URLError as e:
            result['http_error'] = f"URLError: {e.reason}"
        except Exception as e:
            result['http_error'] = str(e)

    @classmethod
    def _diagnose_generate_summary(cls, result):
        """诊断步骤4：根据检测结果生成摘要"""
        if not result['steam_running']:
            result['summary'] = "❌ 未检测到 Steam 进程。请确认 Steam 已启动。"
        elif not result['tcp_port_open']:
            reasons = []
            if not result['cef_file_exists']:
                reasons.append(f"• ⭐ .cef-enable-remote-debugging 文件不存在！\n"
                               f"  这是最可能的原因。点击「云同步模式启动」会自动创建此文件并重启 Steam。\n"
                               f"  预期路径: {result['cef_file_path'] or '未知'}")
            if result['cef_arg_detected'] is False:
                reasons.append("• 命令行参数 --cef-remote-debugging-port 未检测到")
            reasons.append("• macOS 防火墙可能阻止了 localhost 连接")
            reasons.append("• Steam 可能尚未完全启动")
            reasons.append(f"• TCP 错误: {result['tcp_error'] or '无'}")
            result['summary'] = (f"⚠️ Steam 正在运行，但 TCP 端口 {cls.CEF_PORT} 未开放。\n"
                                 f"可能原因：\n" + '\n'.join(reasons))
        elif not result['http_reachable']:
            result['summary'] = (f"⚠️ TCP 端口 {cls.CEF_PORT} 可连，但 HTTP 请求失败。\n"
                                 f"错误: {result['http_error']}")
        elif not result['shared_js_ready']:
            target_list = '\n'.join(f"  • {t}" for t in (result['targets'] or []))
            result['summary'] = (f"⚠️ CEF 端口可用，但未找到 SharedJSContext。\n"
                                 f"当前 targets:\n{target_list}\n"
                                 f"Steam 可能还在加载中，请稍候。")
        else:
            result['summary'] = "✅ CEF 端口可用，SharedJSContext 已就绪。"

    @classmethod
    def detect_steam_process(cls) -> dict:
        """轻量级检测：只检查 Steam 进程是否运行"""
        import platform as _platform
        import subprocess

        system = _platform.system()
        info = {'running': False, 'process_names': [], 'cef_arg': None}

        try:
            if system == "Darwin":
                ps = subprocess.run(['pgrep', '-lf', 'steam'],
                                    capture_output=True, text=True, timeout=3)
                for line in ps.stdout.strip().split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    low = line.lower()
                    if 'steam_osx' in low:
                        info['running'] = True
                        info['process_names'].append('steam_osx')
                        if ('-cef-enable-debugging' in line
                                or '--cef-remote-debugging-port' in line
                                or '-devtools-port' in line):
                            info['cef_arg'] = True
                        elif info['cef_arg'] is None:
                            info['cef_arg'] = False
                    elif 'steamwebhelper' in low:
                        info['process_names'].append('steamwebhelper')
            elif system == "Windows":
                ps = subprocess.run(['tasklist', '/fi', 'imagename eq steam.exe'],
                                    capture_output=True, text=True, timeout=3)
                if 'steam.exe' in ps.stdout.lower():
                    info['running'] = True
                    info['process_names'].append('steam.exe')
            elif system == "Linux":
                ps = subprocess.run(['pgrep', '-la', 'steam'],
                                    capture_output=True, text=True, timeout=3)
                for line in ps.stdout.strip().split('\n'):
                    if line.strip() and 'steam' in line.lower():
                        info['running'] = True
                        info['process_names'].append('steam')
                        if ('-cef-enable-debugging' in line
                                or '--cef-remote-debugging-port' in line
                                or '-devtools-port' in line):
                            info['cef_arg'] = True
        except Exception:
            pass

        return info

    @staticmethod
    def get_steam_launch_command() -> Optional[str]:
        """获取当前平台用 CEF 调试模式启动 Steam 的命令

        Returns:
            命令字符串，或 None（不支持的平台）
        """
        import platform as _platform
        system = _platform.system()
        # macOS 上 --cef-remote-debugging-port 不会被传递给 steamwebhelper，
        # 必须用 -cef-enable-debugging 才能真正开启 CEF 调试端口。
        if system == "Darwin":
            return (f'"/Users/$USER/Library/Application Support/Steam/'
                    f'Steam.AppBundle/Steam/Contents/MacOS/steam_osx"'
                    f' -cef-enable-debugging -devtools-port {CEFBridge.CEF_PORT}')
        elif system == "Windows":
            return (f'"C:\\Program Files (x86)\\Steam\\steam.exe"'
                    f' -cef-enable-debugging -devtools-port {CEFBridge.CEF_PORT}')
        elif system == "Linux":
            return f'steam -cef-enable-debugging -devtools-port {CEFBridge.CEF_PORT}'
        return None

    @classmethod
    def _get_steam_data_dir(cls) -> Optional[str]:
        """获取 Steam 数据目录（用于放置 .cef-enable-remote-debugging 文件）

        Returns:
            路径字符串，或 None
        """
        import platform as _platform
        system = _platform.system()
        home = os.path.expanduser("~")

        if system == "Darwin":
            candidates = [
                os.path.join(home, "Library/Application Support/Steam"),
            ]
        elif system == "Windows":
            candidates = [
                os.path.expandvars(r"%ProgramFiles(x86)%\Steam"),
                os.path.expandvars(r"%ProgramFiles%\Steam"),
                r"C:\Program Files (x86)\Steam",
                r"C:\Steam",
                r"D:\Steam",
            ]
        elif system == "Linux":
            candidates = [
                os.path.join(home, ".steam/steam"),
                os.path.join(home, ".local/share/Steam"),
            ]
        else:
            return None

        for p in candidates:
            if os.path.isdir(p):
                return p
        return None

    @classmethod
    def ensure_cef_debugging_enabled(cls) -> Tuple[bool, str]:
        """确保 .cef-enable-remote-debugging 文件存在

        Steam 通过检测此文件来决定是否启动 CEF 远程调试。
        这比命令行参数更可靠，因为命令行参数不一定会被传递给 steamwebhelper 子进程。

        Returns:
            (success, message)
        """
        steam_dir = cls._get_steam_data_dir()
        if not steam_dir:
            return False, "未找到 Steam 数据目录"

        flag_path = os.path.join(steam_dir, ".cef-enable-remote-debugging")
        if os.path.exists(flag_path):
            return True, f"CEF 调试标记文件已存在: {flag_path}"

        try:
            with open(flag_path, 'w'):
                pass  # 创建空文件
            return True, f"已创建 CEF 调试标记文件: {flag_path}"
        except Exception as e:
            return False, f"创建标记文件失败: {e}\n路径: {flag_path}"

    @classmethod
    def is_cef_debugging_enabled(cls) -> Tuple[bool, Optional[str]]:
        """检查 .cef-enable-remote-debugging 文件是否存在

        Returns:
            (exists, file_path_or_none)
        """
        steam_dir = cls._get_steam_data_dir()
        if not steam_dir:
            return False, None

        flag_path = os.path.join(steam_dir, ".cef-enable-remote-debugging")
        return os.path.exists(flag_path), flag_path

    @staticmethod
    def launch_steam_with_cef() -> Tuple[bool, str]:
        """尝试以 CEF 调试模式启动 Steam（会先关闭已运行的 Steam）

        === macOS 关键注意事项 ===
        
        1. 参数问题：
           --cef-remote-debugging-port=8080 在 macOS 上无效！
           steam_osx 不会把此参数传递给 steamwebhelper 子进程。
           必须用 -cef-enable-debugging（启用开关）+ -devtools-port（指定端口）。
        
        2. Dock 劫持问题：
           macOS 的 Dock 会监控应用状态。如果 Steam 图标被固定在 Dock 上，
           当 killall 杀掉 Steam 进程后，macOS Launch Services 可能会
           通过 Dock 图标自动重启 Steam——但不带任何命令行参数！
           这会导致 Steam 重启后 CEF 端口不开放。
           
           解决方案：使用 `open -a Steam --args` 来启动。
           这会复用 Dock 已有的 Steam 实例位置，同时传递参数。
           如果 Steam 已被杀掉，`open` 命令会正确启动新实例并传递参数。
           为避免 Dock 抢先重启，kill 之后要快速用 open 命令启动。

        Returns:
            (success, message)
        """
        import platform as _platform
        import subprocess
        system = _platform.system()

        # 正确的启动参数
        steam_args = ['-cef-enable-debugging', '-devtools-port', str(CEFBridge.CEF_PORT)]

        # === 同时创建 .cef-enable-remote-debugging 文件（备用机制） ===
        flag_ok, flag_msg = CEFBridge.ensure_cef_debugging_enabled()

        # 先关闭已运行的 Steam
        try:
            if system == "Darwin":
                # macOS: 用 osascript 优雅退出 Steam，避免 Dock 检测到崩溃后自动重启
                # quit 比 killall -9 更好，因为 -9 是强制杀死，系统可能认为是崩溃
                subprocess.run(
                    ['osascript', '-e', 'tell application "Steam" to quit'],
                    capture_output=True, timeout=5)
                import time as _time
                _time.sleep(3)
                # 如果优雅退出不够，再强制杀
                for name in ['steam_osx', 'Steam Helper', 'steamwebhelper']:
                    subprocess.run(['killall', '-9', name],
                                   capture_output=True, timeout=5)
            elif system == "Windows":
                subprocess.run(['taskkill', '/f', '/im', 'steam.exe'], capture_output=True, timeout=5)
            elif system == "Linux":
                subprocess.run(['killall', '-9', 'steam'], capture_output=True, timeout=5)
        except Exception:
            pass

        # 等待 Steam 完全退出
        import time as _time
        _time.sleep(3)

        # 启动 Steam
        try:
            if system == "Darwin":
                # === macOS 关键：用 open -a 而非直接调用 steam_osx ===
                #
                # 为什么不能用 subprocess.Popen([steam_osx_path, args...])？
                # 因为 macOS 的 Launch Services / Dock 有自己的应用管理：
                #   - 如果 Dock 上固定了 Steam，Dock 会监控 Steam 进程
                #   - killall 杀掉 Steam 后，Dock 可能自动重启 Steam（不带参数）
                #   - 直接调用 steam_osx 二进制文件时，会出现两个 Steam 图标
                #     （一个是 Dock 的，一个是新进程的），且 Dock 的那个可能抢先
                #
                # `open -a Steam --args ...` 的优势：
                #   - 通过 Launch Services 正规渠道启动，只有一个图标
                #   - --args 后面的参数会被正确传递给 steam_osx
                #   - 如果 Steam 已经在运行，open 会将其带到前台（但不传参数）
                #     所以必须确保 Steam 已完全退出后再调用
                #
                # 备选方案：如果 open -a 不管用（比如 Steam 没有注册为 .app），
                # 回退到直接调用 steam_osx 二进制文件
                
                # 再次确认 Steam 已完全退出（检查进程是否还在）
                for _ in range(5):
                    check = subprocess.run(['pgrep', '-x', 'steam_osx'],
                                           capture_output=True, timeout=3)
                    if check.returncode != 0:  # 没有找到进程，已完全退出
                        break
                    _time.sleep(1)
                else:
                    # 5 秒后还没退出，最后一次强制杀
                    subprocess.run(['killall', '-9', 'steam_osx'],
                                   capture_output=True, timeout=3)
                    _time.sleep(2)

                # 方法1: open -a Steam --args（推荐）
                result = subprocess.run(
                    ['open', '-a', 'Steam', '--args'] + steam_args,
                    capture_output=True, text=True, timeout=10)
                
                if result.returncode != 0:
                    # 方法2: 直接调用二进制文件（回退）
                    steam_candidates = [
                        os.path.expanduser(
                            "~/Library/Application Support/Steam/"
                            "Steam.AppBundle/Steam/Contents/MacOS/steam_osx"),
                        "/Applications/Steam.app/Contents/MacOS/steam_osx",
                    ]
                    steam_bin = None
                    for candidate in steam_candidates:
                        if os.path.exists(candidate):
                            steam_bin = candidate
                            break
                    if not steam_bin:
                        return False, (
                            f"未找到 Steam，尝试过:\n"
                            f"  • open -a Steam (失败: {result.stderr.strip()})\n"
                            + "\n".join(f"  • {c}" for c in steam_candidates))
                    subprocess.Popen([steam_bin] + steam_args, start_new_session=True)

            elif system == "Windows":
                steam_paths = [
                    r"C:\Program Files (x86)\Steam\steam.exe",
                    r"D:\Steam\steam.exe",
                    r"D:\Program Files (x86)\Steam\steam.exe",
                ]
                launched = False
                for path in steam_paths:
                    if os.path.exists(path):
                        subprocess.Popen([path] + steam_args)
                        launched = True
                        break
                if not launched:
                    cmd = CEFBridge.get_steam_launch_command()
                    return False, f"未找到 Steam，请手动运行:\n{cmd}"
            elif system == "Linux":
                subprocess.Popen(['steam'] + steam_args)
            else:
                return False, f"不支持的平台: {system}"

            msg = "Steam 正在启动，请等待 Steam 完全加载后会自动进入主界面。"
            if flag_ok:
                msg += f"\n({flag_msg})"
            return True, msg

        except FileNotFoundError:
            cmd = CEFBridge.get_steam_launch_command()
            return False, f"启动失败，请手动运行:\n{cmd or '(未知平台)'}"
        except Exception as e:
            return False, f"启动失败: {e}"

    def connect(self) -> Tuple[bool, str]:
        """建立 WebSocket 连接到 SharedJSContext

        Returns:
            (success, error_message)
        """
        try:
            import websocket
        except ImportError:
            return False, "缺少 websocket-client 库，请运行: pip install websocket-client"

        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(f"http://127.0.0.1:{self.CEF_PORT}/json")
            with opener.open(req, timeout=3) as resp:
                targets = json.loads(resp.read().decode('utf-8'))

            shared = [t for t in targets if "SharedJSContext" in t.get("title", "")]
            if not shared:
                return False, "未找到 SharedJSContext 页面"

            ws_url = shared[0]["webSocketDebuggerUrl"]
            self.ws = websocket.create_connection(ws_url, timeout=10)
            self._connected = True
            return True, ""
        except Exception as e:
            self._connected = False
            return False, f"CEF 连接失败: {e}"

    def disconnect(self):
        """断开连接"""
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self.ws is not None

    # ==================== JS 执行 ====================

    def _eval_js(self, expression: str, timeout: int = 15):
        """执行 JS 表达式并返回结果值（线程安全）"""
        with self._ws_lock:
            if not self.is_connected():
                return {"error": "未连接 CEF"}
            self._msg_counter += 1
            msg_id = self._msg_counter

            try:
                self.ws.send(json.dumps({
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                    }
                }))
            except Exception as e:
                self.disconnect()
                return {"error": f"发送失败（连接可能已断开）: {e}"}

            self.ws.settimeout(timeout)
            deadline = time.time() + timeout
            try:
                while True:
                    if time.time() > deadline:
                        return {"error": f"等待响应超时 ({timeout}s)"}
                    resp = json.loads(self.ws.recv())
                    if resp.get("id") == msg_id:
                        res = resp.get("result", {})
                        if "exceptionDetails" in res:
                            exc = res["exceptionDetails"]
                            err_msg = exc.get("exception", {}).get("description",
                                      exc.get("exception", {}).get("value",
                                      exc.get("text", "未知错误")))
                            return {"error": str(err_msg)}
                        result = res.get("result", {})
                        return result.get("value", result.get("description"))
            except Exception as e:
                self.disconnect()
                return {"error": f"超时或连接断开: {e}"}
            finally:
                try:
                    self.ws.settimeout(None)
                except Exception:
                    pass

    # ==================== 收藏夹操作 ====================

    def create_or_update_collection(self, col_id: str, name: str,
                                    added: List[int], removed: List[int] = None
                                    ) -> Tuple[bool, str]:
        """创建或更新单个收藏夹（StoreObject + SaveCollection）

        Returns:
            (success, error_message)
        """
        removed = removed or []
        data_json = json.dumps({
            "id": col_id,
            "name": name,
            "added": added,
            "removed": removed
        }, ensure_ascii=False)

        js = f"""
(async function() {{
    try {{
        var data = {data_json};
        await collectionStore.m_cloudStorageMap.StoreObject(data.id, data);
        var col = collectionStore.GetCollection(data.id);
        if (col) {{
            await collectionStore.SaveCollection(col);
            return {{ok: true}};
        }} else {{
            return {{ok: false, error: 'GetCollection 返回 null'}};
        }}
    }} catch(e) {{
        return {{ok: false, error: e.message}};
    }}
}})()
"""
        result = self._eval_js(js, timeout=10)
        if isinstance(result, dict):
            if result.get("error"):
                return False, result["error"]
            if result.get("ok"):
                return True, ""
            return False, result.get("error", "未知错误")
        return False, f"意外返回值: {result}"

    def delete_collection(self, col_id: str) -> Tuple[bool, str]:
        """删除单个收藏夹

        Returns:
            (success, error_message)
        """
        js = f"""
(async function() {{
    try {{
        await collectionStore.DeleteCollection('{col_id}');
        return {{ok: true}};
    }} catch(e) {{
        return {{ok: false, error: e.message}};
    }}
}})()
"""
        result = self._eval_js(js, timeout=10)
        if isinstance(result, dict):
            if result.get("error"):
                return False, result["error"]
            if result.get("ok"):
                return True, ""
        return False, f"删除失败: {result}"

    def get_logged_in_steam_id3(self) -> Optional[int]:
        """获取 Steam 当前登录用户的 SteamID3（好友代码）

        尝试多种方法获取，按可靠性排序。
        Steam 客户端更新可能会改变内部 API，所以这里尽可能多地尝试不同路径。

        Returns:
            int（SteamID3）或 None（获取失败）
        """
        # ---- 第一阶段：先做一轮轻量级探测 ----
        result = self._eval_js(r"""
(function() {
    try {
        // === 辅助函数 ===
        function sid64toSid3(sid64str) {
            if (!sid64str) return null;
            var s = String(sid64str);
            if (s.length < 14) return null;
            try {
                var base = BigInt('76561197960265728');
                var id3 = Number(BigInt(s) - base);
                if (id3 > 0 && id3 < 4294967296) return id3;
            } catch(e) {}
            return null;
        }
        function isValidId3(n) {
            return typeof n === 'number' && n > 0 && n < 4294967296 && Number.isInteger(n);
        }
        function tryGet(fn) {
            try { return fn(); } catch(e) { return undefined; }
        }

        // ========== 方法0: collectionStore.m_cloudStorageMap 中的 namespace 路径 ==========
        // 这是最可靠的方法之一：namespace-1 的存储路径中包含用户的 SteamID3
        // 路径格式类似：/userdata/<SteamID3>/config/cloudstorage/...
        try {
            if (typeof collectionStore !== 'undefined' && collectionStore.m_cloudStorageMap) {
                var csm = collectionStore.m_cloudStorageMap;
                // 尝试获取 namespace 信息中的 steamid
                // m_cloudStorageMap.m_strSteamId 或类似属性
                var possibleIdFields = [
                    'strSteamId', 'm_strSteamId', 'm_strSteamID', 'steamid',
                    'm_steamid', 'strSteamID', 'm_nAccountID', 'm_unAccountID',
                    'accountid', 'nAccountID'
                ];
                for (var fi = 0; fi < possibleIdFields.length; fi++) {
                    try {
                        var val = csm[possibleIdFields[fi]];
                        if (val !== undefined && val !== null && val !== '') {
                            var id3 = sid64toSid3(String(val));
                            if (id3) return {id: id3, method: 'collectionStore.m_cloudStorageMap.' + possibleIdFields[fi]};
                            if (isValidId3(Number(val)))
                                return {id: Number(val), method: 'collectionStore.m_cloudStorageMap.' + possibleIdFields[fi]};
                        }
                    } catch(e) {}
                }
            }
        } catch(e) {}

        // ========== 方法1: loginStore — 多种路径 ==========
        try {
            if (typeof loginStore !== 'undefined') {
                // 1a. loginStore.m_strAccountName 存在时，loginStore 通常也有 steamid
                var directFields = [
                    'm_steamid', 'm_strSteamID', 'm_strSteamId', 'steamid',
                    'm_AccountID', 'm_nAccountID', 'm_unAccountID', 'accountid'
                ];
                for (var di = 0; di < directFields.length; di++) {
                    try {
                        var val = loginStore[directFields[di]];
                        if (val !== undefined && val !== null && val !== '') {
                            var id3 = sid64toSid3(String(val));
                            if (id3) return {id: id3, method: 'loginStore.' + directFields[di]};
                            if (isValidId3(Number(val)))
                                return {id: Number(val), method: 'loginStore.' + directFields[di]};
                        }
                    } catch(e) {}
                }

                // 1b. loginStore.m_vecLoginUsers
                var users = loginStore.m_vecLoginUsers;
                if (users && users.length > 0) {
                    for (var i = 0; i < users.length; i++) {
                        var u = users[i];
                        if (!u) continue;
                        // 尝试所有可能的属性名
                        var sid64 = u.steamid || u.m_strSteamID || u.m_steamid
                                    || u.strSteamID || u.steamID || u.SteamID;
                        if (sid64) {
                            var id3 = sid64toSid3(String(sid64));
                            if (id3) return {id: id3, method: 'loginStore.m_vecLoginUsers[' + i + '].steamid'};
                        }
                        var accId = u.accountid || u.m_AccountID || u.m_unAccountID || u.accountId;
                        if (accId && isValidId3(Number(accId))) {
                            return {id: Number(accId), method: 'loginStore.m_vecLoginUsers[' + i + '].accountid'};
                        }
                    }
                }
            }
        } catch(e) {}

        // ========== 方法2: SteamClient.User — 直接调用原生方法 ==========
        // 新版 Steam 的 SteamClient 子模块使用原生 C++ 绑定，
        // 方法不会出现在 Object.keys() 中，必须直接按名字调用。
        try {
            if (typeof SteamClient !== 'undefined' && SteamClient.User) {
                var user = SteamClient.User;
                // 尝试直接调用已知的方法名（这些是 native binding，不可枚举）
                var methodsToTry = [
                    'GetSteamID', 'GetCurrentSteamID', 'GetSteamId',
                    'GetAccountID', 'GetCurrentAccountID'
                ];
                for (var mi = 0; mi < methodsToTry.length; mi++) {
                    try {
                        var fn = user[methodsToTry[mi]];
                        if (typeof fn === 'function') {
                            var val = fn.call(user);
                            if (val !== undefined && val !== null) {
                                // 返回值可能是 SteamID 对象（有 GetAccountID 方法）
                                if (typeof val === 'object' && val !== null) {
                                    // 尝试 .GetAccountID() 方法
                                    if (typeof val.GetAccountID === 'function') {
                                        var accId = val.GetAccountID();
                                        if (isValidId3(Number(accId)))
                                            return {id: Number(accId), method: 'SteamClient.User.' + methodsToTry[mi] + '().GetAccountID()'};
                                    }
                                    // 尝试 .IsValid() + 获取属性
                                    var objStr = String(val);
                                    var id3 = sid64toSid3(objStr);
                                    if (id3) return {id: id3, method: 'SteamClient.User.' + methodsToTry[mi] + '() [toString]'};
                                    // 尝试 .GetLow32() 或 .GetLow32BitID() — 某些版本的 SteamID 对象
                                    for (var meth of ['GetLow32', 'GetLow32BitID', 'GetAccountId', 'GetAccountid', 'GetLow']) {
                                        try {
                                            if (typeof val[meth] === 'function') {
                                                var low = val[meth]();
                                                if (isValidId3(Number(low)))
                                                    return {id: Number(low), method: 'SteamClient.User.' + methodsToTry[mi] + '().' + meth + '()'};
                                            }
                                        } catch(e2) {}
                                    }
                                    // 尝试直接属性
                                    for (var prop of ['m_unAccountID', 'accountid', 'low', 'lo', 'accountId', 'nAccountID']) {
                                        try {
                                            var pval = val[prop];
                                            if (pval && isValidId3(Number(pval)))
                                                return {id: Number(pval), method: 'SteamClient.User.' + methodsToTry[mi] + '().' + prop};
                                        } catch(e2) {}
                                    }
                                } else {
                                    // 返回值是字符串或数字
                                    var id3 = sid64toSid3(String(val));
                                    if (id3) return {id: id3, method: 'SteamClient.User.' + methodsToTry[mi] + '()'};
                                    if (isValidId3(Number(val)))
                                        return {id: Number(val), method: 'SteamClient.User.' + methodsToTry[mi] + '()'};
                                }
                            }
                        }
                    } catch(e) {}
                }
            }
        } catch(e) {}

        // ========== 方法3: SteamClient 其他子模块 ==========
        try {
            if (typeof SteamClient !== 'undefined') {
                // Friends、Settings 等模块也可能有 SteamID 信息
                var modules = Object.keys(SteamClient);
                for (var mi = 0; mi < modules.length; mi++) {
                    if (modules[mi] === 'User') continue; // 已在方法2中处理
                    try {
                        var mod = SteamClient[modules[mi]];
                        if (!mod || typeof mod !== 'object') continue;
                        // 尝试直接调用已知 getter（native binding 不可枚举，必须直接访问）
                        var getters = ['GetSteamID', 'GetCurrentSteamID', 'GetAccountID'];
                        for (var gi = 0; gi < getters.length; gi++) {
                            try {
                                var fn = mod[getters[gi]];
                                if (typeof fn === 'function') {
                                    var val = fn.call(mod);
                                    if (val !== undefined && val !== null) {
                                        if (typeof val === 'object' && val !== null && typeof val.GetAccountID === 'function') {
                                            var accId = val.GetAccountID();
                                            if (isValidId3(Number(accId)))
                                                return {id: Number(accId), method: 'SteamClient.' + modules[mi] + '.' + getters[gi] + '().GetAccountID()'};
                                        }
                                        var id3 = sid64toSid3(String(val));
                                        if (id3) return {id: id3, method: 'SteamClient.' + modules[mi] + '.' + getters[gi] + '()'};
                                        if (isValidId3(Number(val)))
                                            return {id: Number(val), method: 'SteamClient.' + modules[mi] + '.' + getters[gi] + '()'};
                                    }
                                }
                            } catch(e) {}
                        }
                        // 也扫描可枚举属性（某些模块可能有普通 JS 属性）
                        var modKeys;
                        try { modKeys = Object.keys(mod); } catch(e) { continue; }
                        for (var ki = 0; ki < modKeys.length; ki++) {
                            var key = modKeys[ki];
                            var keyLow = key.toLowerCase();
                            if (typeof mod[key] === 'function') {
                                if (keyLow === 'getsteamid' || keyLow === 'getcurrentsteamid'
                                    || keyLow === 'getaccountid') {
                                    var val = tryGet(function() { return mod[key](); });
                                    if (val) {
                                        var id3 = sid64toSid3(String(val));
                                        if (id3) return {id: id3, method: 'SteamClient.' + modules[mi] + '.' + key + '()'};
                                        if (isValidId3(Number(val)))
                                            return {id: Number(val), method: 'SteamClient.' + modules[mi] + '.' + key + '()'};
                                    }
                                }
                                continue;
                            }
                            try {
                                var val = mod[key];
                                if (keyLow.indexOf('steamid') !== -1 || keyLow.indexOf('steam_id') !== -1) {
                                    var id3 = sid64toSid3(String(val));
                                    if (id3) return {id: id3, method: 'SteamClient.' + modules[mi] + '.' + key};
                                }
                                if (keyLow.indexOf('accountid') !== -1 || keyLow.indexOf('account_id') !== -1) {
                                    if (isValidId3(Number(val)))
                                        return {id: Number(val), method: 'SteamClient.' + modules[mi] + '.' + key};
                                }
                            } catch(e) {}
                        }
                    } catch(e) {}
                }
            }
        } catch(e) {}

        // ========== 方法4: collectionStore 深度扫描 ==========
        try {
            if (typeof collectionStore !== 'undefined') {
                // m_cloudStorage -> m_mapNamespaces
                var cs = collectionStore.m_cloudStorage;
                if (cs) {
                    var ns = cs.m_mapNamespaces;
                    if (ns && ns.forEach) {
                        var found = null;
                        ns.forEach(function(v, k) {
                            if (!found && v) {
                                var sid = v.m_strSteamId || v.m_strSteamID || v.m_steamid || v.steamid;
                                if (sid) found = {val: String(sid), key: 'm_mapNamespaces.steamid'};
                                var acc = v.m_unAccountID || v.m_AccountID || v.m_accountID || v.accountid;
                                if (!found && acc && isValidId3(Number(acc)))
                                    found = {val: null, id3: Number(acc), key: 'm_mapNamespaces.accountid'};
                            }
                        });
                        if (found) {
                            if (found.id3) return {id: found.id3, method: 'collectionStore.' + found.key};
                            var id3 = sid64toSid3(found.val);
                            if (id3) return {id: id3, method: 'collectionStore.' + found.key};
                        }
                    }
                    // 直接扫描 cloudStorage 的一级属性
                    var csKeys = Object.keys(cs);
                    for (var i = 0; i < csKeys.length; i++) {
                        var k = csKeys[i].toLowerCase();
                        if (k.indexOf('steamid') !== -1 || k.indexOf('steam_id') !== -1) {
                            try {
                                var val = cs[csKeys[i]];
                                var id3 = sid64toSid3(String(val));
                                if (id3) return {id: id3, method: 'collectionStore.m_cloudStorage.' + csKeys[i]};
                            } catch(e) {}
                        }
                        if (k.indexOf('accountid') !== -1 || k.indexOf('account_id') !== -1) {
                            try {
                                var val = cs[csKeys[i]];
                                if (isValidId3(Number(val)))
                                    return {id: Number(val), method: 'collectionStore.m_cloudStorage.' + csKeys[i]};
                            } catch(e) {}
                        }
                    }
                }

                // 扫描所有 collection，从 collection 的数据文件路径中提取 SteamID3
                // 路径中通常包含 /userdata/<SteamID3>/
                try {
                    var collections = collectionStore.userCollections || collectionStore.m_mapCollections;
                    if (collections && collections.forEach) {
                        var foundId = null;
                        collections.forEach(function(col, key) {
                            if (foundId) return;
                            try {
                                var str = JSON.stringify(col);
                                // 匹配 /userdata/数字/ 模式
                                var m = str.match(/userdata[\/\\]+(\d{5,12})[\/\\]/);
                                if (m && isValidId3(Number(m[1]))) foundId = Number(m[1]);
                            } catch(e) {}
                        });
                        if (foundId) return {id: foundId, method: 'collectionStore.collections.path'};
                    }
                } catch(e) {}
            }
        } catch(e) {}

        // ========== 方法5: cloudStorageInternalState ==========
        try {
            var csState = window.cloudStorageInternalState;
            if (csState) {
                var sid = csState.m_strSteamId || csState.m_strSteamID || csState.m_steamid;
                if (sid) {
                    var id3 = sid64toSid3(String(sid));
                    if (id3) return {id: id3, method: 'cloudStorageInternalState'};
                }
            }
        } catch(e) {}

        // ========== 方法6: 已知全局 store 对象 ==========
        // 现代 Steam 可能有 appStore, userStore, settingsStore 等
        try {
            var knownStores = [
                'appStore', 'userStore', 'settingsStore', 'authStore',
                'friendStore', 'friendsStore', 'currentUserStore'
            ];
            for (var si = 0; si < knownStores.length; si++) {
                try {
                    var store = window[knownStores[si]];
                    if (!store || typeof store !== 'object') continue;
                    // 直接字段检查
                    var fields = [
                        'm_steamid', 'm_strSteamID', 'm_strSteamId', 'steamid', 'steamID',
                        'm_AccountID', 'm_nAccountID', 'm_unAccountID', 'accountid', 'accountId'
                    ];
                    for (var fi = 0; fi < fields.length; fi++) {
                        try {
                            var val = store[fields[fi]];
                            if (val !== undefined && val !== null && val !== '') {
                                var id3 = sid64toSid3(String(val));
                                if (id3) return {id: id3, method: knownStores[si] + '.' + fields[fi]};
                                if (isValidId3(Number(val)))
                                    return {id: Number(val), method: knownStores[si] + '.' + fields[fi]};
                            }
                        } catch(e) {}
                    }
                } catch(e) {}
            }
        } catch(e) {}

        // ========== 方法7: 扫描所有 window 全局变量中的 store 对象 ==========
        try {
            var allKeys = Object.keys(window);
            for (var wi = 0; wi < allKeys.length; wi++) {
                var wk = allKeys[wi];
                if (wk.length < 3 || wk.indexOf('Store') === -1 && wk.indexOf('store') === -1
                    && wk.indexOf('Manager') === -1 && wk.indexOf('Context') === -1) continue;
                try {
                    var obj = window[wk];
                    if (!obj || typeof obj !== 'object') continue;
                    var objKeys = Object.keys(obj);
                    for (var oi = 0; oi < objKeys.length && oi < 100; oi++) {
                        var ok = objKeys[oi];
                        var okLow = ok.toLowerCase();
                        if (okLow.indexOf('steamid') !== -1 || okLow.indexOf('steam_id') !== -1) {
                            try {
                                var val = String(obj[ok]);
                                var id3 = sid64toSid3(val);
                                if (id3) return {id: id3, method: wk + '.' + ok};
                            } catch(e) {}
                        }
                        if (okLow.indexOf('accountid') !== -1 || okLow.indexOf('account_id') !== -1) {
                            try {
                                var val = obj[ok];
                                if (isValidId3(Number(val)))
                                    return {id: Number(val), method: wk + '.' + ok};
                            } catch(e) {}
                        }
                    }
                } catch(e) {}
            }
        } catch(e) {}

        // ========== 全部失败，收集详细调试信息 ==========
        var debug = {error: 'unable to determine', debug: {}};
        try {
            // loginStore 详细结构
            if (typeof loginStore !== 'undefined') {
                debug.debug.loginStoreKeys = Object.keys(loginStore).slice(0, 30);
                // m_vecLoginUsers 的详细结构
                if (loginStore.m_vecLoginUsers) {
                    var users = loginStore.m_vecLoginUsers;
                    debug.debug.vecLoginUsersLength = users.length;
                    if (users.length > 0 && users[0]) {
                        var firstKeys = Object.keys(users[0]);
                        debug.debug.vecLoginUsers0_keys = firstKeys;
                        var firstVals = {};
                        for (var i = 0; i < firstKeys.length && i < 20; i++) {
                            try {
                                var v = users[0][firstKeys[i]];
                                firstVals[firstKeys[i]] = (typeof v === 'object' && v !== null)
                                    ? '{obj:' + Object.keys(v).slice(0, 10).join(',') + '}'
                                    : String(v).substring(0, 60);
                            } catch(e) { firstVals[firstKeys[i]] = '(error)'; }
                        }
                        debug.debug.vecLoginUsers0_vals = firstVals;
                    }
                }
            } else {
                debug.debug.loginStore = 'undefined';
            }
            // SteamClient.User 探测
            if (typeof SteamClient !== 'undefined' && SteamClient.User) {
                debug.debug.SteamClientUserType = typeof SteamClient.User;
                debug.debug.SteamClientUserKeys = [];
                try {
                    debug.debug.SteamClientUserKeys = Object.keys(SteamClient.User).slice(0, 30);
                } catch(e) {}
                // 尝试列出 SteamClient.User 的所有属性（含不可枚举）
                try {
                    debug.debug.SteamClientUserOwnProps =
                        Object.getOwnPropertyNames(SteamClient.User).slice(0, 30);
                } catch(e) {}
                // 尝试 Prototype 链
                try {
                    var proto = Object.getPrototypeOf(SteamClient.User);
                    if (proto) {
                        debug.debug.SteamClientUserProtoProps =
                            Object.getOwnPropertyNames(proto).slice(0, 30);
                    }
                } catch(e) {}
            }
            // collectionStore.m_cloudStorageMap 结构
            if (typeof collectionStore !== 'undefined') {
                if (collectionStore.m_cloudStorageMap) {
                    debug.debug.cloudStorageMapKeys =
                        Object.keys(collectionStore.m_cloudStorageMap).slice(0, 30);
                }
                if (collectionStore.m_cloudStorage) {
                    debug.debug.cloudStorageKeys =
                        Object.keys(collectionStore.m_cloudStorage).slice(0, 30);
                }
            }
            // 列出所有包含 Store/store 的全局变量
            var storeGlobals = [];
            var allKeys = Object.keys(window);
            for (var i = 0; i < allKeys.length; i++) {
                if (allKeys[i].indexOf('Store') !== -1 || allKeys[i].indexOf('store') !== -1)
                    storeGlobals.push(allKeys[i]);
            }
            debug.debug.globalStoreNames = storeGlobals.slice(0, 40);
        } catch(e) {
            debug.debug.dumpError = e.message;
        }
        return debug;
    } catch(e) {
        return {error: e.message};
    }
})()
""", timeout=8)
        if isinstance(result, dict):
            if result.get("id"):
                return int(result["id"])
            # 如果拿到 raw SteamID64 字符串
            raw = result.get("raw")
            if raw:
                s = str(raw)
                if s.isdigit():
                    try:
                        sid64 = int(s)
                        sid3 = sid64 - 76561197960265728
                        if 0 < sid3 < 4294967296:
                            return sid3
                    except (ValueError, OverflowError):
                        pass
            # 记录调试信息（如果有）
            debug_info = result.get("debug")
            if debug_info:
                import logging
                logging.getLogger(__name__).warning(
                    f"CEF SteamID 检测失败，调试信息: {debug_info}")
        return None

    def is_steam_fully_loaded(self) -> bool:
        """检测 Steam 是否已完全加载（collectionStore 可用）

        Returns:
            True 表示 Steam 已完全就绪可以操作收藏夹
        """
        result = self._eval_js("""
(function() {
    try {
        if (typeof collectionStore === 'undefined') return {ready: false, reason: 'no collectionStore'};
        if (!collectionStore.m_cloudStorageMap) return {ready: false, reason: 'no cloudStorageMap'};
        return {ready: true};
    } catch(e) {
        return {ready: false, reason: e.message};
    }
})()
""", timeout=5)
        if isinstance(result, dict):
            return result.get("ready", False)
        return False

    def batch_sync_collections(self, operations: list,
                               progress_callback: Callable = None
                               ) -> Tuple[int, int, List[str]]:
        """批量执行收藏夹操作（带进度回调）

        Args:
            operations: 操作列表，每项为 dict:
                {"action": "upsert", "col_id": "uc-xxx", "name": "名称", "added": [...]}
                {"action": "delete", "col_id": "uc-xxx", "name": "名称（仅显示）"}
            progress_callback: fn(current_index, total, current_name, status_text)

        Returns:
            (success_count, fail_count, error_details)
        """
        total = len(operations)
        success = 0
        fail = 0
        errors = []

        for i, op in enumerate(operations):
            name = op.get("name", op.get("col_id", "?"))
            action = op["action"]

            if progress_callback:
                action_text = "正在同步" if action == "upsert" else "正在删除"
                progress_callback(i, total, name, f"☁️ {action_text} ({i+1}/{total})...")

            if action == "upsert":
                ok, err = self.create_or_update_collection(
                    op["col_id"], op["name"],
                    op.get("added", []), op.get("removed", [])
                )
            elif action == "delete":
                ok, err = self.delete_collection(op["col_id"])
            else:
                ok, err = False, f"未知操作: {action}"

            if ok:
                success += 1
            else:
                fail += 1
                errors.append(f"{name}: {err}")

        if progress_callback:
            progress_callback(total, total, "",
                              f"✅ 云同步完成：成功 {success}/{total}" +
                              (f"，失败 {fail}" if fail else ""))

        return success, fail, errors

    # ==================== 收藏夹 & 游戏库 查询 ====================

    def get_all_collections_with_apps(self, timeout: int = 20) -> dict:
        """获取所有收藏夹及其实际游戏列表（通过 CEF 实时查询）

        解决：
        - 动态分类返回 Steam 客户端实时计算的匹配结果
        - 静态分类返回经过 Steam 过滤后的列表（只含实际拥有的游戏）

        Returns:
            {
                "collections": {
                    "<col_id>": {
                        "name": str,
                        "isDynamic": bool,
                        "appIds": [int, ...],
                        "count": int
                    }, ...
                },
                "method": str,   # 调试：使用的属性路径
                "error": str     # 仅在失败时
            }
        """
        js = """
(function() {
    try {
        // 获取所有用户收藏夹
        var uc = collectionStore.userCollections;
        if (!uc) return {error: 'collectionStore.userCollections 不存在'};

        // 转换为数组（可能是 Array 或 Map）
        var colls;
        if (Array.isArray(uc)) {
            colls = uc;
        } else if (uc instanceof Map) {
            colls = Array.from(uc.values());
        } else if (typeof uc === 'object') {
            colls = Object.values(uc);
        } else {
            return {error: 'userCollections 类型不支持: ' + typeof uc};
        }

        var result = {};
        var method = '';

        for (var i = 0; i < colls.length; i++) {
            var col = colls[i];
            if (!col) continue;

            var colId = col.id || col.m_strID || ('idx_' + i);
            var name = col.name || col.m_strName || colId;
            var isDynamic = !!(col.bIsDynamic || col.m_bIsDynamic ||
                              (col.filterSpec && Object.keys(col.filterSpec).length > 0));

            // 尝试多种属性名获取游戏列表
            var apps = null;
            if (col.allApps && (Array.isArray(col.allApps) || col.allApps.length !== undefined)) {
                apps = Array.from(col.allApps);
                method = method || 'allApps';
            } else if (col.visibleApps && (Array.isArray(col.visibleApps) || col.visibleApps.length !== undefined)) {
                apps = Array.from(col.visibleApps);
                method = method || 'visibleApps';
            } else if (col.m_rgApps && (Array.isArray(col.m_rgApps) || col.m_rgApps.length !== undefined)) {
                apps = Array.from(col.m_rgApps);
                method = method || 'm_rgApps';
            } else if (col.apps) {
                if (col.apps instanceof Map) {
                    apps = Array.from(col.apps.keys());
                } else if (Array.isArray(col.apps)) {
                    apps = col.apps;
                } else {
                    apps = Object.keys(col.apps).map(Number);
                }
                method = method || 'apps';
            }

            // 提取 appId（可能是对象或纯数字）
            var appIds = [];
            if (apps) {
                for (var j = 0; j < apps.length; j++) {
                    var a = apps[j];
                    if (typeof a === 'number') {
                        appIds.push(a);
                    } else if (typeof a === 'object' && a !== null) {
                        appIds.push(a.appid || a.appId || a.nAppID || a);
                    } else if (typeof a === 'string') {
                        appIds.push(parseInt(a, 10));
                    }
                }
            }

            result[colId] = {
                name: name,
                isDynamic: isDynamic,
                appIds: appIds,
                count: appIds.length
            };
        }

        return {collections: result, method: method || 'unknown'};
    } catch(e) {
        return {error: e.message || String(e)};
    }
})()
"""
        result = self._eval_js(js, timeout=timeout)
        if isinstance(result, dict):
            return result
        return {"error": f"意外返回值: {result}"}

    def get_all_owned_apps(self, games_only: bool = True, timeout: int = 30) -> dict:
        """获取账号拥有的所有游戏/应用

        解决：获取完整入库列表，包括未安装的、免费领取的游戏。

        Args:
            games_only: True=只返回游戏(type=1), False=返回所有类型

        Returns:
            {
                "apps": [
                    {"appid": int, "name": str, "type": int, "installed": bool},
                    ...
                ],
                "count": int,
                "method": str,
                "error": str    # 仅在失败时
            }
        """
        games_only_js = "true" if games_only else "false"
        js = f"""
(function() {{
    try {{
        var gamesOnly = {games_only_js};
        var allApps = null;
        var method = '';

        // 尝试多种路径获取 allApps
        if (typeof appStore !== 'undefined' && appStore) {{
            if (appStore.allApps) {{
                allApps = appStore.allApps;
                method = 'appStore.allApps';
            }} else if (appStore.m_mapApps) {{
                allApps = Array.from(appStore.m_mapApps.values());
                method = 'appStore.m_mapApps';
            }} else if (appStore.storeItems) {{
                allApps = Array.from(appStore.storeItems.values());
                method = 'appStore.storeItems';
            }}
        }}

        // 回退：尝试 appInfoStore
        if (!allApps && typeof appInfoStore !== 'undefined' && appInfoStore) {{
            if (appInfoStore.allApps) {{
                allApps = appInfoStore.allApps;
                method = 'appInfoStore.allApps';
            }}
        }}

        if (!allApps) {{
            // 尝试从 collectionStore 的 "全部游戏" 收藏中获取
            var uc = collectionStore.userCollections;
            if (uc) {{
                var cols = Array.isArray(uc) ? uc :
                           (uc instanceof Map ? Array.from(uc.values()) :
                           Object.values(uc));
                for (var i = 0; i < cols.length; i++) {{
                    var c = cols[i];
                    var cId = c.id || c.m_strID || '';
                    // "type-games" 或 "all-games" 是 Steam 内置的全部游戏收藏
                    if (cId === 'type-games' || cId === 'all-games' ||
                        cId === 'type-all' || cId === 'all') {{
                        var a = c.allApps || c.visibleApps || c.m_rgApps || c.apps;
                        if (a) {{
                            allApps = Array.isArray(a) ? a :
                                      (a instanceof Map ? Array.from(a.values()) :
                                      Object.values(a));
                            method = 'collectionStore[' + cId + ']';
                            break;
                        }}
                    }}
                }}
            }}
        }}

        if (!allApps) {{
            return {{error: 'allApps 不存在于 appStore / appInfoStore / collectionStore'}};
        }}

        // 转换为数组（如果还不是）
        if (!Array.isArray(allApps)) {{
            try {{ allApps = Array.from(allApps); }} catch(e) {{
                return {{error: '无法转换 allApps 为数组: ' + e.message}};
            }}
        }}

        var apps = [];
        // 限制返回数量，避免 JSON 过大
        var limit = 50000;
        for (var i = 0; i < Math.min(allApps.length, limit); i++) {{
            var app = allApps[i];
            if (!app) continue;

            var appid = 0, name = '', appType = 0, installed = false;

            if (typeof app === 'number') {{
                appid = app;
            }} else if (typeof app === 'object') {{
                appid = app.appid || app.nAppID || app.appId || app.m_nAppID || 0;
                name = app.display_name || app.strDisplayName ||
                       app.name || app.m_strName ||
                       app.sort_as || app.strSortAs || '';
                appType = app.app_type ?? app.nAppType ??
                          app.type ?? app.m_nAppType ?? 0;
                installed = !!(app.installed || app.bInstalled ||
                              app.m_bInstalled || app.rt_last_time_played);
            }}

            if (appid <= 0) continue;
            // 过滤类型：1 = 游戏
            if (gamesOnly && appType !== 0 && appType !== 1) continue;

            var timeAcquired = 0;
            if (typeof app === 'object') {{
                timeAcquired = app.rt_time_acquired || app.rtTimeAcquired || 0;
            }}

            var extra = {{}};
            if (typeof app === 'object') {{
                extra.review_pct = app.review_percentage_without_bombs || 0;
                extra.review_score = app.review_score_without_bombs || 0;
                extra.metacritic = app.metacritic_score || 0;
                extra.rt_release = app.rt_original_release_date || app.rt_steam_release_date || 0;
                extra.rt_purchased = app.rt_purchased_time || 0;
            }}

            apps.push({{
                appid: appid,
                name: name || '',
                type: appType,
                installed: installed,
                rt_time_acquired: timeAcquired,
                review_pct: extra.review_pct || 0,
                review_score: extra.review_score || 0,
                metacritic: extra.metacritic || 0,
                rt_release: extra.rt_release || 0,
                rt_purchased: extra.rt_purchased || 0
            }});
        }}

        return {{apps: apps, count: apps.length, method: method}};
    }} catch(e) {{
        return {{error: e.message || String(e)}};
    }}
}})()
"""
        result = self._eval_js(js, timeout=timeout)
        if isinstance(result, dict):
            return result
        return {"error": f"意外返回值: {result}"}

    def get_app_overviews_batch(self, app_ids: list, timeout: int = 30) -> dict:
        """批量查询 appOverview（用于未入库游戏）

        返回 {app_id_str: {name, type, review_pct, ...}, ...}
        """
        if not app_ids:
            return {}
        ids_json = json.dumps([int(a) for a in app_ids[:5000]])
        js = f"""
(function() {{
    try {{
        var ids = {ids_json};
        var r = {{}};
        for (var i = 0; i < ids.length; i++) {{
            var app = null;
            try {{ app = appStore.GetAppOverviewByAppID(ids[i]); }}
            catch(e) {{ continue; }}
            if (!app) continue;
            var name = app.display_name || app.strDisplayName ||
                       app.name || app.m_strName || '';
            if (!name) continue;
            r[ids[i]] = {{
                name: name,
                type: app.app_type ?? app.nAppType ?? 1,
                review_pct: app.review_percentage_without_bombs || 0,
                review_score: app.review_score_without_bombs || 0,
                metacritic: app.metacritic_score || 0,
                rt_release: app.rt_original_release_date || app.rt_steam_release_date || 0,
                rt_purchased: app.rt_purchased_time || 0
            }};
        }}
        return r;
    }} catch(e) {{
        return {{error: e.message || String(e)}};
    }}
}})()
"""
        result = self._eval_js(js, timeout=timeout)
        if isinstance(result, dict) and "error" not in result:
            return result
        if isinstance(result, dict):
            print(f"[CEF] 批量查询失败: {result.get('error')}")
        return {}

