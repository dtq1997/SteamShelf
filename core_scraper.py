"""
core_scraper.py — Steam 页面抓取 Mixin

包含：JSON 读写、收藏夹解析、HTML AppID 提取、鉴赏家 API 抓取、
通用列表抓取、SteamDB 解析、增量/替换更新、导入/导出、Steam250。
"""

import json
import os
import re
import secrets
import time

from utils import steam_sort_key
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from json import JSONDecodeError


class ScraperMixin:
    """Steam 页面抓取与收藏夹操作（Mixin，self 指向 SteamToolboxCore 实例）"""

    def load_json(self):
        if not self.current_account.storage_path or not os.path.exists(self.current_account.storage_path):
            print("[CollectionsCore] 错误: 读取文件失败，请确保已选择有效的 Steam 账号。")
            return None
        try:
            with open(self.current_account.storage_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[CollectionsCore] 读取错误: 解析失败: {e}")
            return None

    def save_json(self, data, create_backup=True, backup_description=""):
        """保存 JSON 数据到原文件

        Args:
            data: 要保存的数据
            create_backup: 是否在保存前创建备份
            backup_description: 备份描述

        Returns:
            (bool, str): (是否成功, 信息文本)
        """
        if not self.current_account.storage_path:
            return False, "未选择账号，无法保存。"

        # 创建备份
        if create_backup and self.backup_manager:
            backup_path = self.backup_manager.create_backup(description=backup_description)
            if backup_path:
                backup_info = f"\n已自动备份至: {os.path.basename(backup_path)}"
            else:
                backup_info = "\n⚠️ 备份创建失败"
        else:
            backup_info = ""

        # 写入原文件（使用原子写入）
        tmp_path = self.current_account.storage_path + ".tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

            if os.path.exists(self.current_account.storage_path):
                os.replace(tmp_path, self.current_account.storage_path)
            else:
                os.rename(tmp_path, self.current_account.storage_path)

            msg = f"文件已保存：{os.path.basename(self.current_account.storage_path)}{backup_info}"
            return True, msg
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            return False, f"无法写入文件: {e}"
            return False


    def get_static_collections(self, data):
        """获取所有收藏夹（含动态）及其 entry 引用，按字母排序"""
        return self.get_all_collections_with_refs(data)

    @staticmethod
    def get_all_collections_with_refs(data):
        """获取所有收藏夹（含动态收藏夹）及其 entry 引用，按字母排序"""
        collections = []
        for entry in data:
            key = entry[0]
            meta = entry[1]
            if key.startswith("user-collections."):
                if meta.get("is_deleted") is True or "value" not in meta:
                    continue
                try:
                    val_obj = json.loads(meta['value'])
                    is_dynamic = "filterSpec" in val_obj
                    icon = "🔍" if is_dynamic else "📁"
                    collections.append({
                        "entry_ref": entry,
                        "id": val_obj.get("id"),
                        "name": val_obj.get("name"),
                        "added": val_obj.get("added", []),
                        "is_dynamic": is_dynamic,
                        "display_name": f"{icon} {val_obj.get('name', '未命名')}"
                    })
                except Exception:
                    continue
        collections.sort(key=lambda c: steam_sort_key(c.get('name') or ''))
        return collections

    @staticmethod
    def get_all_collections_ordered(data):
        """获取所有收藏夹（按字母顺序排序，与 Steam 客户端一致）"""
        collections = []
        for entry in data:
            key = entry[0]
            meta = entry[1]
            if key.startswith("user-collections."):
                if meta.get("is_deleted") is True or "value" not in meta:
                    continue
                try:
                    val_obj = json.loads(meta['value'])
                    is_dynamic = "filterSpec" in val_obj
                    col_info = {
                        "id": val_obj.get("id"),
                        "name": val_obj.get("name", "未命名"),
                        "added": val_obj.get("added", []),
                        "removed": val_obj.get("removed", []),
                        "is_dynamic": is_dynamic
                    }
                    if is_dynamic:
                        col_info["filterSpec"] = val_obj.get("filterSpec")
                    collections.append(col_info)
                except Exception:
                    continue
        collections.sort(key=lambda c: steam_sort_key(c['name']))
        return collections

    @staticmethod
    def extract_ids_from_html(html_text):
        """核心提取逻辑：从 HTML 中提取 AppID"""
        search_area = html_text
        list_start = html_text.find('id="RecommendationsRows"')
        if list_start == -1:
            list_start = html_text.find('class="creator_grid_ctn"')

        if list_start != -1:
            footer_start = html_text.find('id="footer"', list_start)
            search_area = html_text[list_start: (footer_start if footer_start != -1 else len(html_text))]

        raw_matches = re.findall(r'data-ds-appid="([\d,]+)"', search_area)
        all_ids = []
        for m in raw_matches:
            if ',' in m:
                all_ids.extend(m.split(','))
            else:
                all_ids.append(m)

        # 如果 data-ds-appid 未找到，回退到从 store.steampowered.com/app/ URL 中提取
        if not all_ids:
            app_url_matches = re.findall(r'store\.steampowered\.com/app/(\d+)', search_area)
            all_ids = app_url_matches

        return list(dict.fromkeys([int(aid) for aid in all_ids if aid.isdigit()]))

    def extract_page_name_from_html(self, html_text, url_hint=""):
        """从 HTML 中智能提取页面名称（带类型前缀）"""
        type_name_cn = "列表"
        if url_hint:
            page_type, _ = self.extract_steam_list_info(url_hint)
            type_names = {
                "curator": "鉴赏家",
                "publisher": "发行商",
                "developer": "开发商",
                "franchise": "系列",
                "genre": "类型",
                "category": "分类",
            }
            type_name_cn = type_names.get(page_type, "列表")

        if "curator" in html_text.lower() or "鉴赏家" in html_text:
            type_name_cn = "鉴赏家"
        elif "publisher" in html_text.lower():
            type_name_cn = "发行商"
        elif "developer" in html_text.lower():
            type_name_cn = "开发商"

        name = None
        match = re.search(r'class="curator_name".*?><a.*?>(.*?)</a>', html_text, re.S)
        if match:
            name = match.group(1).strip()

        if not name:
            match = re.search(r'<title>(.*?)</title>', html_text, re.I)
            if match:
                title = match.group(1)
                title = re.sub(r'\s*[-–—]\s*Steam.*$', '', title, flags=re.I)
                title = re.sub(r'\s*on Steam.*$', '', title, flags=re.I)
                title = re.sub(r'^Steam 鉴赏家：', '', title)
                title = re.sub(r'^Steam Curator:\s*', '', title, flags=re.I)
                name = title.strip()

        if name:
            return f"{type_name_cn}：{name}"
        return f"{type_name_cn}：未知"

    @staticmethod
    def extract_steam_list_info(url_or_id):
        """从 URL 或直接输入中提取 Steam 列表页面信息"""
        text = url_or_id.strip()

        if text.isdigit():
            return "curator", text

        patterns = [
            (r'/curator/(\d+)', "curator"),
            (r'/publisher/([^/?#]+)', "publisher"),
            (r'/developer/([^/?#]+)', "developer"),
            (r'/franchise/([^/?#]+)', "franchise"),
            (r'/genre/([^/?#]+)', "genre"),
            (r'/category/([^/?#]+)', "category"),
        ]

        for pattern, page_type in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return page_type, match.group(1)

        return None, None

    def fetch_steam_list(self, page_type, identifier, progress_callback=None, login_cookies=None):
        """通过 Steam API 自动获取列表页面的所有游戏"""
        type_names = {
            "curator": "鉴赏家",
            "publisher": "发行商",
            "developer": "开发商",
            "franchise": "系列",
            "genre": "类型",
            "category": "分类",
        }
        type_name_cn = type_names.get(page_type, "列表")

        has_login = login_cookies is not None and len(login_cookies.strip()) > 0

        if has_login:
            cookies = f"{login_cookies}; {self._BASE_COOKIES}"
        else:
            cookies = self._BASE_COOKIES

        if page_type in ("curator", "publisher", "developer"):
            return self.fetch_curator_style_api(page_type, identifier, type_name_cn, cookies, has_login,
                                                 progress_callback)
        else:
            return self.fetch_generic_list(page_type, identifier, type_name_cn, cookies, has_login, progress_callback)

    _BASE_COOKIES = "birthtime=283993201; wants_mature_content=1; mature_content=1; lastagecheckage=1-0-1979; steamCountry=US%7C0"

    def fetch_curator_style_api(self, page_type, identifier, type_name_cn, cookies, has_login, progress_callback=None):
        """统一的 ajaxgetfilteredrecommendations API 抓取"""
        from urllib.parse import unquote

        page_url = f"https://store.steampowered.com/{page_type}/{identifier}/"
        # HTML 页面获取只用基础 cookie（年龄验证），不带登录 cookie
        # Steam 对 developer/publisher 页面在带 steamLoginSecure 时会产生重定向循环
        headers_html = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cookie': self._BASE_COOKIES,
        }

        # 阶段1：解析 curator_id 和页面名称
        resolved = self._resolve_curator_info(
            page_type, identifier, page_url, headers_html, progress_callback)
        if resolved is None:
            return self.fetch_generic_list(
                page_type, identifier, type_name_cn, cookies, has_login, progress_callback)
        curator_id, page_name = resolved

        # 阶段2：多语言分页 API 抓取
        all_unique_ids, page_name = self._paginate_curator_api(
            curator_id, page_url, cookies, page_name, progress_callback)

        # 阶段3：组装结果
        if not all_unique_ids:
            return [], None, f"该{type_name_cn}没有任何游戏，或标识符无效。\n请检查 URL 是否正确。", has_login

        if page_name:
            display_name = f"{type_name_cn}：{page_name}"
        else:
            display_name = f"{type_name_cn}：{unquote(identifier)}"
        return list(all_unique_ids), display_name, None, has_login

    def _resolve_curator_info(self, page_type, identifier, page_url, headers_html, progress_callback):
        """解析鉴赏家/发行商/开发商页面，提取 curator_id 和页面名称。

        Returns:
            (curator_id, page_name) 或 None（表示需要回退到通用抓取）
        """
        curator_id = None
        page_name = None

        if page_type == "curator":
            curator_id = identifier
            if progress_callback:
                progress_callback(0, 0, "正在验证鉴赏家页面...", "正在连接 Steam 商店...")
            try:
                req = urllib.request.Request(page_url, headers=headers_html)
                with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as resp:
                    html_content = resp.read().decode('utf-8')
                page_name = self._extract_name_from_html(html_content, [
                    r'class="curator_name"[^>]*>.*?<a[^>]*>(.*?)</a>',
                    r'<title>Steam 鉴赏家：([^<]+?)</title>',
                    r'<title>([^<]+?)(?:\s*[-–—]\s*Steam)?</title>',
                ])
            except Exception:
                pass
        else:
            if progress_callback:
                progress_callback(0, 0, "正在获取页面信息...", f"正在访问 {page_type}/{identifier} ...")
            try:
                req = urllib.request.Request(page_url, headers=headers_html)
                with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as resp:
                    html_content = resp.read().decode('utf-8')
            except Exception:
                return None

            clanid_patterns = [
                r'curator_clanid[=:][\s"\']*(\d+)',
                r'IgnoreCurator\(\s*(\d+)',
                r'newshub/group/(\d+)',
                r'data-clanid=["\']?(\d+)',
                r'"clanAccountID"\s*:\s*(\d+)',
            ]
            for pattern in clanid_patterns:
                clanid_match = re.search(pattern, html_content)
                if clanid_match:
                    curator_id = clanid_match.group(1)
                    break

            page_name = self._extract_name_from_html(html_content, [
                r'class="curator_name"[^>]*>.*?<a[^>]*>(.*?)</a>',
                r'<title>(?:Steam (?:Publisher|Developer):\s*)?([^<]+?)(?:\s*[-–—]\s*Steam)?</title>',
            ])

        return (curator_id, page_name) if curator_id else None

    @staticmethod
    def _extract_name_from_html(html_content, patterns):
        """从 HTML 中按优先级匹配名称，返回第一个有效匹配或 None"""
        for pattern in patterns:
            match = re.search(pattern, html_content, re.S | re.I)
            if match:
                extracted = re.sub(r'<[^>]+>', '', match.group(1)).strip()
                extracted = extracted.replace('&amp;', '&').replace('&quot;', '"')
                if extracted and len(extracted) < 100:
                    return extracted
        return None

    def _proxy_adult_scan(self, curator_id, cookies, progress_callback):
        """通过系统代理 + 购物车换区抓取成人游戏。

        Steam 按 IP 过滤成人内容，且匿名用户即使非受限 IP 也被过滤。
        需要：系统代理（非受限出口 IP）+ 登录 cookie + setcountry 换区。

        Returns:
            set[int]: 发现的 app ID 集合，失败返回空 set
        """
        import http.cookiejar
        from http.cookiejar import Cookie

        proxies = urllib.request.getproxies()
        if not proxies.get('https') and not proxies.get('http'):
            return set()
        slc_match = re.search(r'steamLoginSecure=([^;]+)', cookies)
        if not slc_match:
            return set()

        if progress_callback:
            progress_callback(0, 0, "检测到系统代理", "🔞 正在通过代理扫描成人内容...")

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies),
            urllib.request.HTTPSHandler(context=self.ssl_context),
            urllib.request.HTTPCookieProcessor(jar),
        )
        # 注入 steamLoginSecure
        jar.set_cookie(Cookie(
            0, 'steamLoginSecure', slc_match.group(1), None, False,
            'store.steampowered.com', False, True, '/', True, True,
            int(time.time()) + 86400, False, None, None, {}))

        ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'
        try:
            # 建立 session
            opener.open(urllib.request.Request(
                'https://store.steampowered.com/', headers={'User-Agent': ua}
            ), timeout=15).read()
        except Exception:
            return set()

        sid = next((c.value for c in jar if c.name == 'sessionid'), None)
        if not sid:
            return set()

        orig_cc = next((c.value[:2] for c in jar if c.name == 'steamCountry'), 'CN')
        all_ids = set()
        scan_regions = ['US', 'JP']
        try:
            for cc in scan_regions:
                try:
                    body = urllib.parse.urlencode({'sessionid': sid, 'cc': cc}).encode()
                    opener.open(urllib.request.Request(
                        'https://store.steampowered.com/country/setcountry', data=body,
                        headers={'User-Agent': ua, 'Content-Type': 'application/x-www-form-urlencoded',
                                 'Referer': 'https://store.steampowered.com/cart/'},
                    ), timeout=15)
                except Exception:
                    continue
                region_ids = self._proxy_paginate(opener, curator_id, ua)
                new = region_ids - all_ids
                all_ids.update(region_ids)
                if progress_callback:
                    progress_callback(0, 0, "检测到系统代理",
                                      f"🔞 {cc} 区扫描完成（+{len(new)} 新）")
                if not new and cc != scan_regions[0]:
                    break  # 无新增，跳过剩余区域
        finally:
            try:
                body = urllib.parse.urlencode({'sessionid': sid, 'cc': orig_cc}).encode()
                opener.open(urllib.request.Request(
                    'https://store.steampowered.com/country/setcountry', data=body,
                    headers={'User-Agent': ua, 'Content-Type': 'application/x-www-form-urlencoded',
                             'Referer': 'https://store.steampowered.com/cart/'},
                ), timeout=15)
            except Exception:
                pass
        return all_ids

    def _proxy_paginate(self, opener, curator_id, ua):
        """通过 opener 分页抓取 curator API 的全部 appid。"""
        base = f"https://store.steampowered.com/curator/{curator_id}/ajaxgetfilteredrecommendations/"
        all_ids = set()
        start, count = 0, 100
        total = None
        while True:
            req = urllib.request.Request(f"{base}?start={start}&count={count}", headers={
                'User-Agent': ua, 'Accept': 'application/json, */*',
                'X-Requested-With': 'XMLHttpRequest',
            })
            resp = opener.open(req, timeout=30)
            data = json.loads(resp.read().decode('utf-8'))
            if not data.get('success'):
                break
            if total is None:
                total = int(data.get('total_count', 0))
                if total == 0:
                    break
            html = data.get('results_html', '')
            ids = {int(x) for x in re.findall(r'data-ds-appid="(\d+)"', html)}
            new = ids - all_ids
            all_ids.update(ids)
            start += count
            if not new or start >= total:
                break
            time.sleep(0.05)
        return all_ids

    _LANG_CONFIGS = [
        ("schinese", "zh-CN,zh;q=0.9,en;q=0.8", "简体中文", "CN"),
        ("english", "en-US,en;q=0.9", "English", "US"),
        ("japanese", "ja,en;q=0.8", "日本語", "JP"),
        ("tchinese", "zh-TW,zh;q=0.9,en;q=0.8", "繁體中文", "TW"),
        ("koreana", "ko,en;q=0.8", "한국어", "KR"),
    ]

    def _paginate_curator_api(self, curator_id, page_url, cookies, page_name, progress_callback):
        """多语言并行分页抓取 ajaxgetfilteredrecommendations API。

        使用线程池并行扫描所有语言，大幅提升速度。

        Returns:
            (all_unique_ids: set, page_name: str|None)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        base_url = f"https://store.steampowered.com/curator/{curator_id}/ajaxgetfilteredrecommendations/"

        # 提前检测代理和登录状态
        proxies = urllib.request.getproxies()
        has_proxy = bool(proxies.get('https') or proxies.get('http'))
        has_login = bool(re.search(r'steamLoginSecure=', cookies))
        can_adult_scan = has_proxy and has_login
        if progress_callback:
            if can_adult_scan:
                progress_callback(0, 0, "准备扫描", "🔞 检测到代理+登录态，将额外扫描成人游戏")
            elif not has_proxy:
                progress_callback(0, 0, "准备扫描", "💡 未检测到代理，成人游戏可能不完整")

        all_unique_ids = set()
        _lock = threading.Lock()
        # 共享进度状态（供 progress_callback 汇总显示）
        _lang_status = {}  # lang_display -> "状态文字"
        _max_total = [0]

        def _report_progress():
            """汇总所有语言的进度并回调"""
            if not progress_callback:
                return
            with _lock:
                total = len(all_unique_ids)
                mt = _max_total[0]
                lines = [f"  {s}" for s in _lang_status.values() if s]
            detail = "\n".join(lines) if lines else ""
            progress_callback(total, mt, f"已获取 {total} 个", detail)

        def _fetch_lang(lang_idx, lang_code, accept_lang, lang_display, country_code="US"):
            """单个语言+区域的完整分页抓取（在线程中运行）"""
            import re as _re
            # 不发送 steamLoginSecure，否则服务器会用账号注册区域覆盖 cc 参数
            base_cookies = _re.sub(r'steamLoginSecure=[^;]*;?\s*', '', cookies)
            region_cookies = base_cookies.replace("steamCountry=US%7C0", f"steamCountry={country_code}%7C0")
            headers_api = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': accept_lang,
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': page_url,
                'Cookie': region_cookies,
            }

            local_ids = set()
            local_name = None
            start = 0
            count = 100
            total_count = None
            lang_page = 0

            with _lock:
                _lang_status[lang_display] = f"🌐 {lang_display} 正在连接..."
            _report_progress()

            while True:
                url = f"{base_url}?start={start}&count={count}&l={lang_code}&cc={country_code}"
                lang_page += 1

                try:
                    req = urllib.request.Request(url, headers=headers_api)
                    with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as resp:
                        data = json.loads(resp.read().decode('utf-8'))

                    if not data.get('success'):
                        break

                    if total_count is None:
                        total_count = int(data.get('total_count', 0))
                        if total_count == 0:
                            break
                        with _lock:
                            if total_count > _max_total[0]:
                                _max_total[0] = total_count

                    html_chunk = data.get('results_html', '')
                    new_in_page = 0
                    if html_chunk:
                        chunk_ids = re.findall(r'data-ds-appid="(\d+)"', html_chunk)
                        for aid in chunk_ids:
                            aid_int = int(aid)
                            if aid_int not in local_ids:
                                new_in_page += 1
                            local_ids.add(aid_int)

                        if local_name is None:
                            name_match = re.search(r'class="curator_name"[^>]*>.*?<a[^>]*>(.*?)</a>', html_chunk, re.S)
                            if name_match:
                                local_name = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()

                    total_pages = (total_count + count - 1) // count if total_count else "?"
                    with _lock:
                        all_unique_ids.update(local_ids)
                        _lang_status[lang_display] = (
                            f"🌐 {lang_display} 第{lang_page}/{total_pages}页"
                            f"（+{new_in_page}）")
                    _report_progress()

                    start += count
                    if start >= total_count or not html_chunk:
                        break

                    time.sleep(0.05)

                except Exception:
                    break

            # 最终合并
            with _lock:
                all_unique_ids.update(local_ids)
                _lang_status[lang_display] = f"✅ {lang_display} 完成（{len(local_ids)} 个）"
            _report_progress()

            return local_ids, local_name

        # 并行启动所有语言 + 代理扫描
        workers = len(self._LANG_CONFIGS) + (1 if can_adult_scan else 0)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_lang, idx, lc, al, ld, cc): ld
                for idx, (lc, al, ld, cc) in enumerate(self._LANG_CONFIGS)
            }
            proxy_future = None
            if can_adult_scan:
                proxy_future = pool.submit(self._proxy_adult_scan, curator_id, cookies, progress_callback)

            for future in as_completed(futures):
                try:
                    local_ids, local_name = future.result()
                    if local_name and page_name is None:
                        page_name = local_name
                except Exception:
                    pass

            if proxy_future:
                try:
                    proxy_ids = proxy_future.result()
                    if proxy_ids:
                        adult_new = len(proxy_ids - all_unique_ids)
                        all_unique_ids.update(proxy_ids)
                        if adult_new > 0 and progress_callback:
                            progress_callback(
                                len(all_unique_ids), len(all_unique_ids),
                                f"已获取 {len(all_unique_ids)} 个",
                                f"🔞 代理扫描发现 {adult_new} 个新游戏")
                except Exception:
                    pass

        if progress_callback:
            progress_callback(
                len(all_unique_ids),
                _max_total[0] if _max_total[0] else len(all_unique_ids),
                f"已获取 {len(all_unique_ids)} 个",
                f"✅ 全部语言扫描完成 — 共 {len(all_unique_ids)} 个唯一游戏"
            )

        return all_unique_ids, page_name

    def fetch_generic_list(self, page_type, identifier, type_name_cn, cookies, has_login, progress_callback=None):
        """通过通用方式抓取发行商/开发商/系列等页面的游戏列表"""
        from urllib.parse import unquote

        base_url = f"https://store.steampowered.com/{page_type}/{identifier}"

        # HTML 页面获取只用基础 cookie，避免登录 cookie 导致重定向循环
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Cookie': self._BASE_COOKIES,
        }

        all_unique_ids = set()
        page_name = None

        if progress_callback:
            progress_callback(0, 0, "正在获取页面...", f"正在连接 {page_type}/{identifier} ...")

        try:
            req = urllib.request.Request(base_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as resp:
                html_content = resp.read().decode('utf-8')

            name_patterns = [
                r'<div class="curator_name"[^>]*>.*?<a[^>]*>(.*?)</a>',
                r'<div class="page_title_area[^"]*"[^>]*>.*?<span[^>]*>(.*?)</span>',
                r'<h2 class="pageheader">(.*?)</h2>',
                r'<title>([^<]+?)(?:\s*[-–—]\s*Steam|\s*on Steam)?</title>',
            ]

            for pattern in name_patterns:
                match = re.search(pattern, html_content, re.S | re.I)
                if match:
                    extracted_name = match.group(1).strip()
                    extracted_name = re.sub(r'<[^>]+>', '', extracted_name)
                    extracted_name = extracted_name.replace('&amp;', '&').replace('&quot;', '"')
                    if extracted_name and len(extracted_name) < 100:
                        page_name = extracted_name
                        break

            if not page_name:
                page_name = unquote(identifier).replace('%20', ' ').replace('+', ' ')

            ids = self.extract_ids_from_html(html_content)
            for aid in ids:
                all_unique_ids.add(aid)

            if progress_callback:
                progress_callback(len(all_unique_ids), len(all_unique_ids), "已获取主页面",
                                  f"📄 主页面提取了 {len(ids)} 个游戏，正在检查分页...")

            page = 2
            while True:
                ajax_url = f"{base_url}?page={page}"
                try:
                    if progress_callback:
                        progress_callback(len(all_unique_ids), len(all_unique_ids), f"正在获取第 {page} 页",
                                          f"📄 正在加载第 {page} 页...")

                    req_page = urllib.request.Request(ajax_url, headers=headers)
                    with urllib.request.urlopen(req_page, timeout=15, context=self.ssl_context) as resp_page:
                        page_html = resp_page.read().decode('utf-8')

                    page_ids = self.extract_ids_from_html(page_html)
                    if not page_ids or all(aid in all_unique_ids for aid in page_ids):
                        break

                    new_count = sum(1 for aid in page_ids if aid not in all_unique_ids)
                    for aid in page_ids:
                        all_unique_ids.add(aid)

                    if progress_callback:
                        progress_callback(len(all_unique_ids), len(all_unique_ids), f"已获取第 {page} 页",
                                          f"📄 第 {page} 页新增 {new_count} 个游戏，当前共 {len(all_unique_ids)} 个")

                    page += 1
                    time.sleep(0.3)

                    if page > 50:
                        break

                except Exception:
                    break

        except urllib.error.HTTPError as e:
            return [], None, f"HTTP 错误 {e.code}：无法访问该页面。", has_login
        except Exception as e:
            return [], None, f"获取失败：{str(e)}", has_login

        if not all_unique_ids:
            return [], None, f"该{type_name_cn}页面没有找到任何游戏。", has_login

        unique_ids = list(all_unique_ids)
        display_name = f"{type_name_cn}：{page_name}"

        return unique_ids, display_name, None, has_login

    @staticmethod
    def extract_ids_from_steamdb_html(html_text):
        """从 SteamDB 页面源代码中提取 AppID"""
        tbody_match = re.search(r'<tbody.*?>(.*?)</tbody>', html_text, re.DOTALL)
        if not tbody_match:
            return []
        return [int(aid) for aid in re.findall(r'data-appid="(\d+)"', tbody_match.group(1))]

    def perform_incremental_update(self, data, target_entry, new_ids_from_src, raw_name, create_aux=True):
        """核心增量更新逻辑：主收藏夹追加 + 可选生成两个差异辅助收藏夹

        Args:
            create_aux: 是否创建"比旧版多的"/"比旧版少的"辅助收藏夹

        Returns:
            (added_count, removed_count, total_count, is_updated)
            如果没有新增任何游戏，is_updated 为 False，此时不会做任何修改
        """
        val_obj = json.loads(target_entry[1]['value'])
        old_ids = val_obj.get("added", [])

        old_set = set(old_ids)
        src_set = set(new_ids_from_src)

        added_list = [aid for aid in new_ids_from_src if aid not in old_set]
        removed_list = [aid for aid in old_ids if aid not in src_set]

        # 如果没有新增任何游戏，不做任何操作
        if not added_list:
            return 0, len(removed_list), len(old_ids), False

        # 有新增，执行更新
        val_obj['added'] = old_ids + added_list
        clean_name = raw_name.replace(self.induce_suffix, "").strip()
        suffix = "" if self._cef_active else self.induce_suffix
        val_obj['name'] = f"{clean_name}{suffix}"
        target_entry[1]['value'] = json.dumps(val_obj, ensure_ascii=False, separators=(',', ':'))
        target_entry[1]['timestamp'] = int(time.time())
        target_entry[1]['version'] = self.next_version(data)
        target_entry[1].setdefault('conflictResolutionMethod', 'custom')
        target_entry[1].setdefault('strMethodId', 'union-collections')

        # 主收藏夹 CEF 同步
        col_id = val_obj.get("id", "")
        if col_id:
            self.queue_cef_upsert(col_id, val_obj['name'], val_obj['added'], val_obj.get('removed', []))

        # 创建辅助收藏夹（add_static_collection 内部已自带 queue）
        if create_aux:
            self.add_static_collection(data, f"{clean_name} - 比旧版多的", added_list)
            if removed_list:
                self.add_static_collection(data, f"{clean_name} - 比旧版少的", removed_list)

        return len(added_list), len(removed_list), len(val_obj['added']), True

    def perform_replace_update(self, data, target_entry, new_ids):
        """替换式更新：直接用新 ID 列表替换目标收藏夹的内容

        Returns:
            (old_count, new_count)
        """
        val_obj = json.loads(target_entry[1]['value'])
        old_count = len(val_obj.get("added", []))

        val_obj['added'] = new_ids
        clean_name = val_obj.get('name', '').replace(self.induce_suffix, "").strip()
        suffix = "" if self._cef_active else self.induce_suffix
        val_obj['name'] = f"{clean_name}{suffix}"
        target_entry[1]['value'] = json.dumps(val_obj, ensure_ascii=False, separators=(',', ':'))
        target_entry[1]['timestamp'] = int(time.time())
        target_entry[1]['version'] = self.next_version(data)
        target_entry[1].setdefault('conflictResolutionMethod', 'custom')
        target_entry[1].setdefault('strMethodId', 'union-collections')

        # CEF 同步队列
        col_id = val_obj.get("id", "")
        if col_id:
            self.queue_cef_upsert(col_id, val_obj['name'], new_ids, val_obj.get('removed', []))

        return old_count, len(new_ids)

    # --- 收藏夹导出/导入（两种格式） ---

    @staticmethod
    def export_collections_appid_list(collections):
        """格式一：导出选中收藏夹的去重 AppID 列表（一行一个）
        动态收藏夹只导出其 added 列表。"""
        seen = set()
        unique_ids = []
        for col in collections:
            for aid in col.get('added', []):
                if aid not in seen:
                    seen.add(aid)
                    unique_ids.append(aid)
        return unique_ids

    @staticmethod
    def export_collections_structured(collections):
        """格式二：导出选中收藏夹的完整结构化 JSON
        包含名称、类型、appid、动态逻辑等。"""
        export_data = {
            "format": "steam_collections_structured",
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "collections": []
        }
        for col in collections:
            entry = {
                "name": col.get("name", "未命名"),
                "is_dynamic": col.get("is_dynamic", False),
                "added": col.get("added", []),
                "removed": col.get("removed", []),
            }
            if col.get("is_dynamic") and col.get("filterSpec"):
                entry["filterSpec"] = col["filterSpec"]
            export_data["collections"].append(entry)
        return export_data

    def import_collections_appid_list(self, file_path, data):
        """格式一：导入一行一个 AppID 的列表文件，创建一个新收藏夹"""
        file_title = os.path.splitext(os.path.basename(file_path))[0]
        with open(file_path, 'r', encoding='utf-8') as f:
            app_ids = [int(line.strip()) for line in f if line.strip().isdigit()]
        if not app_ids:
            return None, "文件中没有有效的 AppID。"
        self.add_static_collection(data, file_title, app_ids)
        return len(app_ids), None

    def import_collections_structured(self, file_path, data):
        """格式二：导入结构化 JSON 文件，还原多个收藏夹（含动态逻辑）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                import_data = json.load(f)
        except JSONDecodeError:
            return None, "文件不是有效的 JSON 格式。"

        if import_data.get("format") != "steam_collections_structured":
            return None, "文件格式不匹配：缺少 format 标识。"

        imported_cols = import_data.get("collections", [])
        if not imported_cols:
            return None, "文件中没有收藏夹数据。"

        count = 0
        for col in imported_cols:
            name = col.get("name", "导入的收藏夹")
            is_dynamic = col.get("is_dynamic", False)
            added = col.get("added", [])
            removed = col.get("removed", [])

            if is_dynamic and "filterSpec" in col:
                # 还原动态收藏夹
                col_id = f"uc-{secrets.token_hex(4)}"
                storage_key = f"user-collections.{col_id}"
                actual_name = name if self._cef_active else name + self.induce_suffix
                val_obj = {
                    "id": col_id,
                    "name": actual_name,
                    "added": added,
                    "removed": removed,
                    "filterSpec": col["filterSpec"]
                }
                new_entry = [storage_key, {
                    "key": storage_key,
                    "timestamp": int(time.time()),
                    "value": json.dumps(val_obj, ensure_ascii=False, separators=(',', ':')),
                    "version": self.next_version(data),
                    "conflictResolutionMethod": "custom",
                    "strMethodId": "union-collections"
                }]
                data.append(new_entry)
                self.queue_cef_upsert(col_id, actual_name, added, removed)
            else:
                # 静态收藏夹
                self.add_static_collection(data, name.replace(self.induce_suffix, "").strip(), added)
            count += 1

        return count, None

    def add_dynamic_collection(self, data, name, friend_code):
        col_id = f"uc-{secrets.token_hex(4)}"
        storage_key = f"user-collections.{col_id}"
        actual_name = name if self._cef_active else name + self.induce_suffix
        filter_groups = [{"rgOptions": [], "bAcceptUnion": False} for _ in range(9)]
        filter_groups[0]["bAcceptUnion"] = True
        filter_groups[6]["rgOptions"] = [int(friend_code)]
        val_obj = {"id": col_id, "name": actual_name, "added": [], "removed": [],
                   "filterSpec": {"nFormatVersion": 2, "strSearchText": "", "filterGroups": filter_groups,
                                  "setSuggestions": {}}}
        new_entry = [storage_key, {"key": storage_key, "timestamp": int(time.time()),
                                   "value": json.dumps(val_obj, ensure_ascii=False, separators=(',', ':')),
                                   "version": self.next_version(data),
                                   "conflictResolutionMethod": "custom", "strMethodId": "union-collections"}]
        data.append(new_entry)
        self.queue_cef_upsert(col_id, actual_name, [])

    def fetch_steam250_ids(self, url, progress_callback=None):
        """从 Steam250 页面提取 AppID 列表"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        }

        if progress_callback:
            progress_callback(0, 0, "正在连接 Steam250...", "")

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20, context=self.ssl_context) as resp:
                html_content = resp.read().decode('utf-8')

            if progress_callback:
                progress_callback(0, 0, "正在解析页面...", "")

            raw_ids = re.findall(r'store\.steampowered\.com/app/(\d+)', html_content)

            unique_ids = []
            for aid in raw_ids:
                if aid not in unique_ids:
                    unique_ids.append(aid)

            app_ids = [int(aid) for aid in unique_ids[:250]]

            if not app_ids:
                return [], "未能从页面提取到任何 AppID。页面结构可能已变化。"

            return app_ids, None

        except urllib.error.HTTPError as e:
            return [], f"HTTP 错误 {e.code}：无法访问 Steam250。"
        except urllib.error.URLError as e:
            return [], f"网络错误：{str(e.reason)}"
        except Exception as e:
            return [], f"提取失败：{str(e)}"



