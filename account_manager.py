"""Steam 账号发现与管理 — 统一账号模型 + 自动扫描

合并自:
- 软件A: SteamAccount 类 + SteamDiscovery 扫描器
- 软件B: SteamAccountScanner 扫描器

统一后的 SteamAccount 同时包含收藏夹和笔记所需的全部属性。
"""

import json
import os
import platform
import re
from typing import Optional

try:
    import vdf as _vdf_lib
    _HAS_VDF_LIB = True
except ImportError:
    _vdf_lib = None
    _HAS_VDF_LIB = False

try:
    import urllib.request
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

from core_notes import NOTES_APPID
from utils import urlopen as _urlopen, steam_sort_key


class SteamAccount:
    """统一 Steam 账号信息类

    同时包含收藏夹管理和笔记管理所需的全部属性。

    Attributes:
        friend_code (str): Steam ID3 / Friend Code（字符串形式）
        userdata_path (str): userdata/<id>/ 目录完整路径
        steam_path (str): Steam 安装根目录
        persona_name (str): 用户昵称
        storage_path (str|None): cloud-storage-namespace-1.json 路径（收藏夹用，可能不存在）
        notes_dir (str): 笔记目录路径（即使不存在也记录预期路径）
        notes_count (int): 已有笔记数量
    """

    def __init__(self, friend_code: str, userdata_path: str, steam_path: str,
                 persona_name: str, storage_path: Optional[str] = None,
                 notes_dir: str = "", notes_count: int = 0):
        self.friend_code = friend_code
        self.userdata_path = userdata_path
        self.steam_path = steam_path
        self.persona_name = persona_name
        self.storage_path = storage_path  # 可为 None
        self.notes_dir = notes_dir
        self.notes_count = notes_count

    def __hash__(self):
        return hash(self.friend_code)

    def __eq__(self, other):
        if isinstance(other, SteamAccount):
            return self.friend_code == other.friend_code
        return NotImplemented

    def __repr__(self):
        return f"<SteamAccount {self.persona_name} ({self.friend_code})>"

    # ── 向后兼容：支持 dict 风格访问（过渡期使用）──

    def get(self, key, default=None):
        """兼容 dict.get() 调用"""
        return getattr(self, key, default)

    def __getitem__(self, key):
        """兼容 dict[key] 调用"""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key):
        """兼容 'key' in account 调用"""
        return hasattr(self, key)

    @staticmethod
    def _get_persona_name(userdata_path: str, friend_code: str) -> str:
        """尝试从配置文件获取用户昵称

        优先使用 vdf 库解析（更可靠），回退到正则匹配。
        """
        localconfig_path = os.path.join(userdata_path, "config", "localconfig.vdf")
        if not os.path.exists(localconfig_path):
            return f"Steam 用户 {friend_code}"

        # 方案 A: 使用 vdf 库解析（来自软件A，更可靠）
        if _HAS_VDF_LIB:
            try:
                with open(localconfig_path, 'r', encoding='utf-8', errors='ignore') as f:
                    localconfig = _vdf_lib.load(f)
                persona_name = (localconfig
                                .get('UserLocalConfigStore', {})
                                .get('friends', {})
                                .get('PersonaName', ''))
                if persona_name:
                    return persona_name
            except (OSError, Exception):
                pass

        # 方案 B: 正则匹配回退（来自软件B）
        try:
            with open(localconfig_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            match = re.search(r'"PersonaName"\s+"([^"]+)"', content)
            if match:
                return match.group(1)
        except (IOError, OSError):
            pass

        return f"Steam 用户 {friend_code}"


class SteamAccountScanner:
    """Steam 账号扫描器：自动发现系统中所有 Steam 账号

    合并自软件A的 SteamDiscovery 和软件B的 SteamAccountScanner。
    返回统一的 SteamAccount 实例列表。
    """

    @staticmethod
    def get_steam_paths():
        """获取所有可能的 Steam 安装路径"""
        system = platform.system()
        paths = []

        # 检测 WSL
        is_wsl = False
        if system == "Linux":
            try:
                with open("/proc/version", "r") as f:
                    if "microsoft" in f.read().lower():
                        is_wsl = True
            except (IOError, OSError):
                pass

        if system == "Windows":
            possible = [
                os.path.expandvars(r"%ProgramFiles(x86)%\Steam"),
                os.path.expandvars(r"%ProgramFiles%\Steam"),
                r"C:\Steam", r"D:\Steam", r"E:\Steam",
                r"D:\Program Files (x86)\Steam",
                r"D:\Program Files\Steam",
                r"E:\Program Files (x86)\Steam",
                r"E:\Program Files\Steam",
            ]
            # 尝试注册表
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                    r"SOFTWARE\WOW6432Node\Valve\Steam")
                install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                winreg.CloseKey(key)
                if install_path and install_path not in possible:
                    paths.append(install_path)
            except (OSError, ImportError):
                pass
            paths.extend(possible)

        elif system == "Darwin":
            home = os.path.expanduser("~")
            paths = [
                os.path.join(home, "Library/Application Support/Steam"),
            ]

        elif system == "Linux":
            home = os.path.expanduser("~")
            paths = [
                os.path.join(home, ".steam/steam"),
                os.path.join(home, ".local/share/Steam"),
                os.path.join(home, ".steam"),
            ]
            if is_wsl:
                for drive in "cdefgh":
                    paths.extend([
                        f"/mnt/{drive}/Program Files (x86)/Steam",
                        f"/mnt/{drive}/Program Files/Steam",
                        f"/mnt/{drive}/Steam",
                    ])

        return [p for p in paths if os.path.exists(p)]

    @staticmethod
    def scan_accounts():
        """扫描所有 Steam 账号，返回 SteamAccount 实例列表

        同时填充收藏夹和笔记所需的属性。
        """
        accounts = []
        seen_ids = set()
        steam_paths = SteamAccountScanner.get_steam_paths()

        for steam_path in steam_paths:
            userdata_path = os.path.join(steam_path, "userdata")
            if not os.path.exists(userdata_path):
                continue

            try:
                for entry in os.listdir(userdata_path):
                    entry_path = os.path.join(userdata_path, entry)
                    if not os.path.isdir(entry_path) or not entry.isdigit():
                        continue

                    friend_code = entry
                    if friend_code in seen_ids:
                        continue
                    seen_ids.add(friend_code)

                    # 笔记相关
                    notes_dir = os.path.join(entry_path, NOTES_APPID, "remote")
                    notes_count = 0
                    if os.path.exists(notes_dir):
                        notes_count = len([
                            f for f in os.listdir(notes_dir)
                            if f.startswith("notes_") and os.path.isfile(
                                os.path.join(notes_dir, f))
                        ])

                    # 收藏夹相关
                    storage_path = os.path.join(
                        entry_path, "config", "cloudstorage",
                        "cloud-storage-namespace-1.json")

                    persona_name = SteamAccount._get_persona_name(
                        entry_path, friend_code)

                    accounts.append(SteamAccount(
                        friend_code=friend_code,
                        userdata_path=entry_path,
                        steam_path=steam_path,
                        persona_name=persona_name,
                        storage_path=storage_path,
                        notes_dir=notes_dir,
                        notes_count=notes_count,
                    ))
            except PermissionError:
                continue

        return accounts

    @staticmethod
    def scan_library(steam_path: str) -> list:
        """扫描本地 Steam 库中所有已安装的游戏 (通过 appmanifest 文件)

        仅包含本地已安装游戏，作为在线扫描的 fallback。
        Returns: [{app_id: str, name: str}, ...] 按游戏名排序
        """
        games = {}
        steamapps_dirs = []
        primary_steamapps = os.path.join(steam_path, "steamapps")
        if os.path.isdir(primary_steamapps):
            steamapps_dirs.append(primary_steamapps)
        primary_lower = os.path.join(steam_path, "SteamApps")
        if os.path.isdir(primary_lower) and primary_lower != primary_steamapps:
            steamapps_dirs.append(primary_lower)

        for sa_dir in list(steamapps_dirs):
            lf_path = os.path.join(sa_dir, "libraryfolders.vdf")
            if os.path.exists(lf_path):
                try:
                    with open(lf_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    for m in re.finditer(r'"path"\s+"([^"]+)"', content):
                        extra_path = m.group(1).replace("\\\\", "\\")
                        extra_sa = os.path.join(extra_path, "steamapps")
                        if os.path.isdir(extra_sa) and extra_sa not in steamapps_dirs:
                            steamapps_dirs.append(extra_sa)
                except Exception:
                    pass

        for sa_dir in steamapps_dirs:
            try:
                for fname in os.listdir(sa_dir):
                    if not fname.startswith("appmanifest_") or not fname.endswith(".acf"):
                        continue
                    fpath = os.path.join(sa_dir, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        aid_m = re.search(r'"appid"\s+"(\d+)"', content)
                        name_m = re.search(r'"name"\s+"([^"]+)"', content)
                        if aid_m:
                            aid = aid_m.group(1)
                            name = name_m.group(1) if name_m else f"AppID {aid}"
                            if aid not in games and aid not in (
                                "228980", "250820", "1007",
                            ):
                                games[aid] = {'app_id': aid, 'name': name}
                    except Exception:
                        continue
            except PermissionError:
                continue

        result = sorted(games.values(), key=lambda x: x['name'].lower())
        return result

    @staticmethod
    def scan_owned_games(friend_code: str, steam_api_key: str = "",
                          debug: bool = False):
        """通过 Steam Web API 获取用户拥有的所有游戏"""
        if not _HAS_URLLIB:
            return ([], "urllib 不可用") if debug else []

        steam_id64 = int(friend_code) + 76561197960265728
        debug_lines = []

        url = (f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
               f"?steamid={steam_id64}&include_appinfo=1&include_played_free_games=1"
               f"&include_free_sub=1&skip_unvetted_apps=0&include_extended_appinfo=1")
        if steam_api_key:
            url += f"&key={steam_api_key}"

        debug_lines.append(f"[请求] Steam ID64 = {steam_id64}")
        masked_url = url.replace(steam_api_key, steam_api_key[:6] + "..." + steam_api_key[-4:]) if steam_api_key else url
        debug_lines.append(f"[请求] URL = {masked_url}")

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "SteamToolkit/1.0"
            })
            with _urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            debug_lines.append(f"[错误] 请求失败: {e}")
            return ([], "\n".join(debug_lines)) if debug else []

        response_obj = data.get("response", {})
        debug_lines.append(f"[解析] response 键列表 = {list(response_obj.keys())}")
        debug_lines.append(f"[解析] response.game_count = {response_obj.get('game_count', '(不存在)')}")

        games_data = response_obj.get("games", [])
        debug_lines.append(f"[解析] games 数组长度 = {len(games_data)}")

        if not games_data:
            debug_lines.append(f"[注意] games 为空！完整 response 内容 = {json.dumps(response_obj, ensure_ascii=False)[:500]}")

        if games_data:
            for i, g in enumerate(games_data[:3]):
                debug_lines.append(f"[样本] games[{i}] = {g}")

        result = []
        for g in games_data:
            aid = str(g.get("appid", ""))
            name = g.get("name", f"AppID {aid}")
            if aid:
                result.append({'app_id': aid, 'name': name})

        result.sort(key=lambda x: steam_sort_key(x['name']))
        debug_lines.append(f"[结果] 最终返回 {len(result)} 款游戏")

        return (result, "\n".join(debug_lines)) if debug else result

    @staticmethod
    def get_collections(userdata_path: str) -> list:
        """从 cloud-storage-namespace-1.json 获取用户的 Steam 收藏夹列表"""
        json_path = os.path.join(userdata_path, "config", "cloudstorage",
                                 "cloud-storage-namespace-1.json")
        if not os.path.exists(json_path):
            return []
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return []

        collections = []
        for entry in data:
            key = entry[0] if isinstance(entry, list) else ""
            meta = entry[1] if isinstance(entry, list) and len(entry) > 1 else {}
            if not key.startswith("user-collections."):
                continue
            if meta.get("is_deleted") is True or "value" not in meta:
                continue
            try:
                val_obj = json.loads(meta['value'])
                is_dynamic = "filterSpec" in val_obj
                app_ids = [int(x) for x in val_obj.get("added", []) if str(x).isdigit()]
                # 从 key "user-collections.uc-xxxx" 提取 collection ID
                col_id = key.replace("user-collections.", "")
                coll_info = {
                    "id": col_id,
                    "name": val_obj.get("name", "未命名"),
                    "app_ids": app_ids,
                    "is_dynamic": is_dynamic,
                }
                # 动态收藏夹：保存筛选规则供 UI 显示
                if is_dynamic:
                    coll_info["filterSpec"] = val_obj.get("filterSpec", {})
                collections.append(coll_info)
            except Exception:
                continue
        collections.sort(key=lambda c: steam_sort_key(c['name']))
        return collections

    @staticmethod
    def fetch_all_steam_app_names(api_key: str = "", progress_callback=None,
                                   estimated_total: int = 0) -> dict:
        """获取 Steam 全量应用名称列表"""
        if api_key:
            try:
                result = {}
                last_appid = 0
                max_results = 50000
                page = 0
                while True:
                    page += 1
                    url = (f"https://api.steampowered.com/IStoreService/GetAppList/v1/"
                           f"?key={api_key}&max_results={max_results}"
                           f"&last_appid={last_appid}&include_games=1"
                           f"&include_dlc=0&include_software=1&include_videos=0&include_hardware=0")
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "SteamToolkit/1.0"
                    })
                    with _urlopen(req, timeout=60) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    apps = data.get("response", {}).get("apps", [])
                    if not apps:
                        break
                    for app in apps:
                        aid = str(app.get("appid", ""))
                        name = app.get("name", "")
                        if aid and name:
                            result[aid] = name
                    has_more = data.get("response", {}).get("have_more_results", False)
                    if has_more and estimated_total < len(result):
                        avg_per_page = len(result) / page
                        estimated_total = max(estimated_total,
                                              int(len(result) + avg_per_page * 1.5))
                    if not has_more:
                        estimated_total = len(result)
                    if progress_callback:
                        try:
                            progress_callback(len(result), page, not has_more,
                                              estimated_total)
                        except Exception:
                            pass
                    if not has_more:
                        break
                    last_appid = apps[-1].get("appid", 0)
                    if page > 10:
                        break
                if result:
                    print(f"[游戏名称] IStoreService 获取成功: {len(result)} 条 ({page} 页)")
                    return result
            except Exception as e:
                print(f"[游戏名称] IStoreService 获取失败: {e}，尝试回退方案...")

        url = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
        try:
            if progress_callback:
                try:
                    progress_callback(0, 0, False, estimated_total)
                except Exception:
                    pass
            req = urllib.request.Request(url, headers={
                "User-Agent": "SteamToolkit/1.0"
            })
            with _urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            apps = data.get("applist", {}).get("apps", [])
            result = {}
            for app in apps:
                aid = str(app.get("appid", ""))
                name = app.get("name", "")
                if aid and name:
                    result[aid] = name
            if result:
                print(f"[游戏名称] ISteamApps (v2) 获取成功: {len(result)} 条")
            if progress_callback:
                try:
                    progress_callback(len(result), 1, True, len(result))
                except Exception:
                    pass
            return result
        except Exception as e:
            print(f"[游戏名称] 全量列表获取失败: {e}")
            return {}

    # ────────────────────── 在线状态检测 ──────────────────────

    _STEAM_ID64_BASE = 76561197960265728

    @staticmethod
    def check_player_in_game(friend_code: str, steam_api_key: str) -> dict:
        """调用 GetPlayerSummaries 检测账号是否正在游戏中。

        Returns:
            {'in_game': bool, 'game_name': str, 'error': str}
        """
        if not steam_api_key or not _HAS_URLLIB:
            return {'in_game': False, 'game_name': '', 'error': ''}
        steam_id64 = int(friend_code) + SteamAccountScanner._STEAM_ID64_BASE
        url = (f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
               f"?key={steam_api_key}&steamids={steam_id64}")
        try:
            req = urllib.request.Request(url,
                                         headers={"User-Agent": "SteamToolkit/1.0"})
            with _urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            players = data.get("response", {}).get("players", [])
            if not players:
                return {'in_game': False, 'game_name': '', 'error': ''}
            p = players[0]
            game_name = p.get("gameextrainfo", "")
            return {'in_game': bool(p.get("gameid")),
                    'game_name': game_name, 'error': ''}
        except Exception as e:
            return {'in_game': False, 'game_name': '', 'error': str(e)}

    # ────────────────────── 自动登录账号切换 ──────────────────────

    @staticmethod
    def launch_steam(steam_path: str) -> tuple:
        """启动 Steam 客户端

        Returns:
            (success: bool, message: str)
        """
        import subprocess
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.Popen(["open", "-a", "Steam"])
            elif system == "Windows":
                exe = os.path.join(steam_path, "steam.exe")
                if os.path.exists(exe):
                    subprocess.Popen([exe])
                else:
                    subprocess.Popen(["start", "steam://"], shell=True)
            else:
                subprocess.Popen(["steam"])
            return True, "OK"
        except Exception as e:
            return False, str(e)
