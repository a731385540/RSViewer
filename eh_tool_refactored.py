import requests
from typing import Optional
from requests import Response
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, urlparse, parse_qs, parse_qsl, urlencode, urlunparse
# 全局变量
proxies = {}

# Runtime credentials come from RSViewer's ignored local configuration.
ehCookies = ""

ehSource = {
    "e-hentai": "https://e-hentai.org/",
    "exhentai": "https://exhentai.org/"
}
proxy = False


class EhData:
    """
    面向上层页面使用的数据仓库。

    - EhBase: 负责请求、翻页定位、HTML 解析，只保存当前页状态
    - EhData: 负责把多次请求得到的 Gallery 按 gid 合并缓存
    """

    DISPLAY_MODES = {
        "m": "m",
        "minimal": "m",
        "p": "p",
        "minimal+": "p",
        "l": "l",
        "compact": "l",
        "card": "l",
        "e": "e",
        "extended": "e",
        "t": "t",
        "thumbnail": "t",
    }

    def __init__(
        self,
        cookies: str,
        source: str = "exhentai",
        auto_load: bool = False,
        proxies: Optional[dict] = None,
        use_proxy: bool = False,
        timeout: int = 20,
        trust_env: bool = True,
    ):
        global ehSource

        self.source = ehSource
        self.Data = {}

        self.nextGid = None
        self.prevGid = None
        self.nextUrl = None
        self.prevUrl = None
        self.currentUrl = None
        self.lastResult = None

        self.req = Srequests(
            proxies=proxies,
            proxy=use_proxy,
            timeout=timeout,
            trust_env=trust_env,
        )
        self.req.setCookie(cookies)

        if source not in self.source:
            raise ValueError(f"未知 source: {source}")

        self.base = EhBase(self.source[source])

        if auto_load:
            self.getMain()

    def clear(self):
        """只清空 Gallery 缓存，不改变当前翻页位置。"""
        self.Data.clear()

    def reset(self):
        """清空缓存并回到首页状态。"""
        self.clear()
        self.nextGid = None
        self.prevGid = None
        self.nextUrl = None
        self.prevUrl = None
        self.currentUrl = None
        self.lastResult = None
        self.base.resetPageState()

    def extend(self, data: list):
        """按 gid 做 upsert，并返回缓存变化统计。"""
        added = 0
        updated = 0

        for item in data:
            gid = item.get("gid")
            if gid is None:
                continue

            if gid in self.Data:
                updated += 1
            else:
                added += 1

            self.Data[gid] = item

        return {
            "added": added,
            "updated": updated,
            "cached": len(self.Data),
        }

    def update(self, data: dict):
        gid = data.get("gid")
        if gid is None:
            return False
        self.Data[gid] = data
        return True

    def get(self, gid: int):
        """从本地缓存取一个 Gallery。"""
        return self.Data.get(int(gid))

    def values(self, newest_first: bool = True):
        """返回缓存中的 Gallery 列表。"""
        values = list(self.Data.values())
        return sorted(
            values,
            key=lambda x: x.get("gid") or 0,
            reverse=newest_first,
        )

    # ----------------------------------------------------------
    # 页面层主要调用接口
    # ----------------------------------------------------------

    def getMain(self, gid=None, time=None, direction="next", search=None):
        """
        获取列表页。

        无参数:
            获取首页/当前 source 的第一页。

        gid=123456:
            使用 EH Jump 定位到指定 GID 附近。

        time="2026-08-14":
            使用 EH Seek 定位到指定日期。

        direction:
            "next" -> 较旧方向（Next > / Seek >）
            "prev" -> 较新方向（< Prev / < Seek）
        """
        result = self.base.getMain(
            self.req,
            gid=gid,
            time=time,
            direction=direction,
            search=search,
        )
        return self._cachePage(result)

    def getUrl(self, url: str):
        """Load a pagination URL produced by the same EH list page."""
        result = self.base.getMain(self.req, url=url)
        return self._cachePage(result)

    def setDisplayMode(self, mode: str):
        """Use the list page's own inline setting to update its default mode."""
        key = str(mode or "").strip().casefold()
        value = self.DISPLAY_MODES.get(key)
        if value is None:
            return {"error": f"未知的页面显示模式: {mode}"}

        url = urljoin(self.base.source, f"?inline_set=dm_{value}")
        response = self.req.get(url)
        if response is None or not getattr(response, "ok", False):
            return {"error": "更新页面显示模式失败"}
        return {
            "mode": value,
            "url": getattr(response, "url", url),
        }

    def getNext(self):
        """加载当前页的下一页（更旧）。"""
        if not self.nextUrl:
            return {"error": "没有下一页"}

        result = self.base.getMain(
            self.req,
            url=self.nextUrl,
        )
        return self._cachePage(result)

    def getPrev(self):
        """加载当前页的上一页（更新）。"""
        if not self.prevUrl:
            return {"error": "没有上一页"}

        result = self.base.getMain(
            self.req,
            url=self.prevUrl,
        )
        return self._cachePage(result)

    def seekDate(self, value, direction="next"):
        return self.getMain(
            time=value,
            direction=direction,
        )

    def seekGid(self, gid: int, direction="next"):
        return self.getMain(
            gid=gid,
            direction=direction,
        )

    def jump(self, value, direction="next"):
        """
        相对跳转：7=7天、2w=2周、3m=3月、1y=1年。
        """
        result = self.base.getMain(
            self.req,
            jump=value,
            direction=direction,
        )
        return self._cachePage(result)

    def _cachePage(self, result):
        if not isinstance(result, dict):
            return {"error": "EhBase 返回了未知数据"}

        if "error" in result:
            self.lastResult = result
            return result

        cache_info = self.extend(result.get("data", []))

        self.nextGid = result.get("next_gid")
        self.prevGid = result.get("prev_gid")
        self.nextUrl = result.get("next_url")
        self.prevUrl = result.get("prev_url")
        self.currentUrl = result.get("current_url")

        result["cache"] = cache_info
        self.lastResult = result
        return result


class Srequests:
    def __init__(
        self,
        proxies=None,
        proxy=False,
        timeout=20,
        trust_env=True,
    ):
        self.proxies = dict(proxies or {})
        self.session = requests.Session()
        self.session.trust_env = bool(trust_env)
        self.proxy = proxy
        self.timeout = max(3, int(timeout))

    def setCookie(self, cookie: str):
        if not cookie:
            return

        for part in cookie.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.session.cookies.set(k, v, path="/")

    def get(self, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        if self.proxy:
            return self.session.get(url, proxies=self.proxies, **kwargs)
        return self.session.get(url, **kwargs)

    def post(self, url, data, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        if self.proxy:
            return self.session.post(url, proxies=self.proxies, data=data, **kwargs)
        return self.session.post(url, data=data, **kwargs)


class EhBase:

    GALLERY_RE = re.compile(
        r"/g/(\d+)/([0-9a-zA-Z]+)/?"
    )

    PAGE_RE = re.compile(
        r"([\d,]+)\s+pages?",
        re.IGNORECASE
    )

    DATE_RE = re.compile(
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}"
    )

    SCORE_RE = re.compile(
        r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)",
        re.IGNORECASE
    )

    SCORE_POSITION_RE = re.compile(
        r"background-position\s*:\s*(-?\d+)px\s+(-?\d+)px",
        re.IGNORECASE,
    )

    SEEK_RE = re.compile(
        r"^(?:\d{4}|\d{2,4}-\d{2}|\d{2,4}-\d{2}-\d{2})$"
    )

    JUMP_RE = re.compile(
        r"^\d+(?:[wmyg])?$",
        re.IGNORECASE
    )

    JS_NEXT_RE = re.compile(
        r'var\s+nexturl\s*=\s*"([^"]*)"\s*;',
        re.IGNORECASE | re.DOTALL,
    )

    JS_PREV_RE = re.compile(
        r'var\s+prevurl\s*=\s*"([^"]*)"\s*;',
        re.IGNORECASE | re.DOTALL,
    )

    CATEGORY_NAMES = {
        "Doujinshi",
        "Manga",
        "Artist CG",
        "Game CG",
        "Western",
        "Non-H",
        "Image Set",
        "Cosplay",
        "Asian Porn",
        "Misc",
    }

    def __init__(self, source: str):
        self.source = source
        self.response: Optional[Response] = None

        # EhBase 只保存当前页，不承担长期缓存。
        self.Data = []

        self.NextGid = None
        self.PrevGid = None
        self.NextUrl = None
        self.PrevUrl = None
        self.CurrentUrl = None

        # Jump/Seek 使用的底层导航 URL。
        self._nextBaseUrl = None
        self._prevBaseUrl = None

    def setSource(self, source):
        self.source = source
        self.resetPageState()

    def clearData(self):
        self.Data.clear()

    def resetPageState(self):
        self.response = None
        self.Data = []
        self.NextGid = None
        self.PrevGid = None
        self.NextUrl = None
        self.PrevUrl = None
        self.CurrentUrl = None
        self._nextBaseUrl = None
        self._prevBaseUrl = None

    # ==========================================================
    # 请求列表页 / Jump / Seek
    # ==========================================================

    def getMain(
        self,
        req,
        gid=None,
        time=None,
        direction="next",
        url=None,
        jump=None,
        search=None,
    ):
        """
        获取一个 EH 列表页。

        优先级: url > gid > time > jump > 首页
        direction: next=较旧方向，prev=较新方向
        """
        if direction not in ("next", "prev"):
            return {"error": "direction 只能是 next 或 prev"}

        locator_count = sum(
            value is not None
            for value in (gid, time, jump)
        )
        if locator_count > 1:
            return {"error": "gid、time、jump 一次只能指定一个"}

        try:
            target_url = url

            # Jump/Seek 依赖页面源码提供的 nexturl/prevurl。
            if target_url is None and locator_count:
                context_error = self._ensureNavigation(req)
                if context_error:
                    return context_error

                if gid is not None:
                    try:
                        gid = int(gid)
                    except (TypeError, ValueError):
                        return {"error": "gid 必须是整数"}

                    if gid <= 0:
                        return {"error": "gid 必须大于 0"}

                    target_url = self._buildLocateUrl(
                        "jump",
                        f"{gid}g",
                        direction,
                    )

                elif time is not None:
                    seek_value = self._normalizeSeek(time)
                    if seek_value is None:
                        return {
                            "error": "time 格式应为 YYYY、YYYY-MM 或 YYYY-MM-DD"
                        }

                    target_url = self._buildLocateUrl(
                        "seek",
                        seek_value,
                        direction,
                    )

                elif jump is not None:
                    jump_value = str(jump).strip().lower()
                    if not self.JUMP_RE.fullmatch(jump_value):
                        return {
                            "error": "jump 格式应类似 7、2w、3m、1y 或 123456g"
                        }

                    target_url = self._buildLocateUrl(
                        "jump",
                        jump_value,
                        direction,
                    )

            if target_url is None:
                target_url = self.source
                if search:
                    target_url = self._setQueryParam(
                        target_url,
                        "f_search",
                        str(search).strip(),
                    )

            return self._requestPage(req, target_url)

        except Exception as e:
            return {"error": f"请求失败: {e}"}

    def _requestPage(self, req, url):
        self.response = req.get(url)

        if self.response is None:
            return {"error": "No Response"}

        if not self.response.text:
            return {"error": "No Html"}

        self.CurrentUrl = getattr(
            self.response,
            "url",
            None,
        ) or url

        soup = BeautifulSoup(
            self.response.text,
            "lxml"
        )

        self._updateNavigation(
            soup,
            self.response.text,
        )

        self.NextUrl = self._get_page_url(
            soup,
            "unext",
        ) or self._get_page_url(
            soup,
            "dnext",
        )

        self.PrevUrl = self._get_page_url(
            soup,
            "uprev",
        ) or self._get_page_url(
            soup,
            "dprev",
        )

        self.NextGid = self._get_cursor(
            self.NextUrl,
            "next",
        )
        self.PrevGid = self._get_cursor(
            self.PrevUrl,
            "prev",
        )

        result = self.extra(soup)

        if isinstance(result, dict) and "error" in result:
            return result

        # EhBase.Data 永远只代表当前页。
        self.Data = list(result)

        return {
            "data": self.Data,
            "next_gid": self.NextGid,
            "prev_gid": self.PrevGid,
            "next_url": self.NextUrl,
            "prev_url": self.PrevUrl,
            "current_url": self.CurrentUrl,
        }

    def _ensureNavigation(self, req):
        """首次直接定位时，先读取 source 获得站点生成的 nexturl/prevurl。"""
        if self._nextBaseUrl or self._prevBaseUrl:
            return None

        response = req.get(self.source)
        if response is None or not response.text:
            return {"error": "无法获取 Jump/Seek 导航上下文"}

        soup = BeautifulSoup(
            response.text,
            "lxml",
        )
        self._updateNavigation(
            soup,
            response.text,
        )

        if not self._nextBaseUrl and not self._prevBaseUrl:
            return {"error": "页面中没有找到 Jump/Seek 导航 URL"}

        return None

    def _updateNavigation(self, soup, html):
        """优先读取 var nexturl/prevurl，以可点击导航链接兜底。"""
        next_match = self.JS_NEXT_RE.search(html or "")
        prev_match = self.JS_PREV_RE.search(html or "")

        if next_match:
            value = next_match.group(1).strip()
            if value:
                self._nextBaseUrl = urljoin(
                    self.CurrentUrl or self.source,
                    value,
                )

        if prev_match:
            value = prev_match.group(1).strip()
            if value:
                self._prevBaseUrl = urljoin(
                    self.CurrentUrl or self.source,
                    value,
                )

        if not self._nextBaseUrl:
            self._nextBaseUrl = (
                self._get_page_url(soup, "unext")
                or self._get_page_url(soup, "dnext")
            )

        if not self._prevBaseUrl:
            self._prevBaseUrl = (
                self._get_page_url(soup, "uprev")
                or self._get_page_url(soup, "dprev")
            )

    def _buildLocateUrl(self, key, value, direction):
        base_url = (
            self._nextBaseUrl
            if direction == "next"
            else self._prevBaseUrl
        )

        # 边界页面某一方向可能为空，另一端可作为定位 URL 的兜底。
        if not base_url:
            base_url = self._nextBaseUrl or self._prevBaseUrl

        if not base_url:
            return None

        return self._setQueryParam(
            base_url,
            key,
            value,
        )

    @staticmethod
    def _setQueryParam(url, key, value):
        parsed = urlparse(url)
        query = dict(parse_qsl(
            parsed.query,
            keep_blank_values=True,
        ))

        query.pop("seek", None)
        query.pop("jump", None)
        query[key] = value

        return urlunparse(
            parsed._replace(
                query=urlencode(query)
            )
        )

    def _normalizeSeek(self, value):
        if hasattr(value, "strftime"):
            try:
                return value.strftime("%Y-%m-%d")
            except Exception:
                return None

        value = str(value).strip()
        if not self.SEEK_RE.fullmatch(value):
            return None

        return value

    # ==========================================================
    # 页面内容解析
    # ==========================================================

    def extra(self, soup):

        page_mode_node = soup.find(
            "option",
            selected="selected"
        )

        # 有时 selected 属性可能不是 selected="selected"
        if not page_mode_node:
            page_mode_node = soup.select_one(
                "option[selected]"
            )

        if not page_mode_node:
            return {
                "error": "无法判断页面显示模式"
            }

        page_mode = page_mode_node.get_text(
            strip=True
        )

        valid_modes = {
            "Minimal",
            "Minimal+",
            "Compact",
            "Extended",
            "Thumbnail",
        }

        if page_mode not in valid_modes:
            return {
                "error":
                    f"未知的页面类型: {page_mode}"
            }

        page_nodes = self._get_gallery_nodes(
            soup,
            page_mode
        )

        current_page_data = []

        for node in page_nodes:

            try:
                data = self._parse_gallery(
                    node,
                    page_mode
                )

            except Exception as e:

                # 某一条炸了，不要让整个页面一起炸
                print(
                    f"[EH] 解析 Gallery 失败: {e}"
                )

                continue

            if data is None:
                continue

            current_page_data.append(data)

        return current_page_data

    # ==========================================================
    # 查找 Gallery 根节点
    # ==========================================================

    def _get_gallery_nodes(
        self,
        soup,
        page_mode
    ):

        result = []

        # Thumbnail 是 div
        if page_mode == "Thumbnail":

            nodes = soup.select(
                "div.gl1t"
            )

            for node in nodes:

                if self._find_gallery_link(node):
                    result.append(node)

            return result

        # 另外四种基本都是 table -> tr
        for tr in soup.find_all("tr"):

            if self._find_gallery_link(tr):
                result.append(tr)

        return result

    # ==========================================================
    # 解析单个 Gallery
    # ==========================================================

    def _parse_gallery(
        self,
        node,
        page_mode
    ):

        # 注意：
        # Data 必须在循环内部创建
        # 每一个 gallery 都是一个新的 dict
        data = {
            "gid": None,
            "token": None,
            "gallery_url": None,

            "type": None,

            "thumb_url": None,
            "alt": None,

            "title": None,

            "upload": None,

            "score": None,
            "score_style": None,

            "page_num": None,

            "uploader": None,

            "label": {},

            "page_mode": page_mode,
        }

        # ======================================================
        # Gallery 链接
        # ======================================================

        gallery_link = self._find_gallery_link(
            node
        )

        if not gallery_link:
            return None

        href = gallery_link.get("href")

        if not href:
            return None

        gallery_url = urljoin(
            self.source,
            href
        )

        data["gallery_url"] = (
            gallery_url
        )

        match = self.GALLERY_RE.search(
            gallery_url
        )

        if match:

            data["gid"] = int(
                match.group(1)
            )

            data["token"] = (
                match.group(2)
            )

        # ======================================================
        # 标题
        # ======================================================

        title_node = node.select_one(
            ".glink"
        )

        if title_node:

            data["title"] = (
                title_node.get_text(
                    " ",
                    strip=True
                )
            )

        # ======================================================
        # 缩略图
        # ======================================================

        img_tag = node.find("img")

        if img_tag:

            thumb_url = (
                img_tag.get("data-src")
                or img_tag.get("src")
            )

            if thumb_url:

                # EH lazyload 可能出现 base64 占位图
                if not thumb_url.startswith(
                    "data:image"
                ):
                    data["thumb_url"] = (
                        urljoin(
                            self.source,
                            thumb_url
                        )
                    )

            data["alt"] = (
                img_tag.get("alt")
            )

            # 标题没找到时图片属性兜底
            if not data["title"]:

                data["title"] = (
                    img_tag.get("title")
                    or img_tag.get("alt")
                )

        # ======================================================
        # 分类
        # ======================================================

        data["type"] = (
            self._extract_category(
                node
            )
        )

        # ======================================================
        # Rating
        # ======================================================

        rating_node = node.select_one(
            ".ir"
        )

        if rating_node:

            style = rating_node.get(
                "style"
            )

            title = rating_node.get(
                "title",
                ""
            )

            data["score_style"] = style

            data["score"] = self._extract_score(
                title,
                style,
            )

        # ======================================================
        # 整个节点文本
        # ======================================================

        node_text = node.get_text(
            " ",
            strip=True
        )

        # ======================================================
        # Pages
        # ======================================================

        page_match = (
            self.PAGE_RE.search(
                node_text
            )
        )

        if page_match:

            try:
                data["page_num"] = int(
                    page_match
                    .group(1)
                    .replace(",", "")
                )

            except ValueError:
                pass

        # ======================================================
        # 上传时间
        # ======================================================

        date_match = (
            self.DATE_RE.search(
                node_text
            )
        )

        if date_match:

            data["upload"] = (
                date_match.group(0)
            )

        # ======================================================
        # uploader
        # ======================================================

        data["uploader"] = (
            self._extract_uploader(
                node
            )
        )

        # ======================================================
        # tags / labels
        # ======================================================

        data["label"] = (
            self._extract_labels(
                node
            )
        )

        return data

    @classmethod
    def _extract_score(cls, title, style):
        """Read a 0-5 rating from text or EH's star sprite position."""
        score_match = cls.SCORE_RE.search(title or "")
        if score_match:
            try:
                score = float(score_match.group(1))
            except ValueError:
                score = None
            if score is not None and 0 <= score <= 5:
                return score

        position_match = cls.SCORE_POSITION_RE.search(style or "")
        if not position_match:
            return None
        x = int(position_match.group(1))
        y = int(position_match.group(2))
        if (x + 80) % 16:
            return None
        star_step = (x + 80) // 16
        if not 0 <= star_step <= 5:
            return None
        if y == -1:
            half_stars = star_step * 2
        elif y == -21:
            half_stars = star_step * 2 - 1
        else:
            return None
        if not 0 <= half_stars <= 10:
            return None
        return half_stars / 2

    # ==========================================================
    # Gallery URL
    # ==========================================================

    def _find_gallery_link(
        self,
        node
    ):

        for a in node.find_all(
            "a",
            href=True
        ):

            href = a.get("href")

            if not href:
                continue

            if self.GALLERY_RE.search(
                href
            ):
                return a

        return None

    # ==========================================================
    # 分类
    # ==========================================================

    def _extract_category(
        self,
        node
    ):

        # Minimal / Minimal+ / Compact
        category_node = (
            node.select_one(
                ".glcat"
            )
        )

        if category_node:

            text = (
                category_node.get_text(
                    " ",
                    strip=True
                )
            )

            if text:
                return text

        # Extended / Thumbnail fallback
        #
        # 不强依赖具体 class，
        # 直接寻找标准分类文字
        for element in node.find_all(
            ["td", "div"]
        ):

            text = element.get_text(
                " ",
                strip=True
            )

            if text in self.CATEGORY_NAMES:
                return text

        return None

    # ==========================================================
    # uploader
    # ==========================================================

    def _extract_uploader(
        self,
        node
    ):

        for a in node.find_all(
            "a",
            href=True
        ):

            href = a.get(
                "href",
                ""
            )

            # 支持不同形式
            if (
                "/uploader/" in href
                or "f_uploader=" in href
            ):

                text = a.get_text(
                    " ",
                    strip=True
                )

                if text:
                    return text

        # Minimal+ 原来的结构做 fallback
        uploader_td = (
            node.select_one(
                ".gl5m"
            )
        )

        if uploader_td:

            a = uploader_td.find("a")

            if a:
                return a.get_text(
                    " ",
                    strip=True
                )

        return None

    # ==========================================================
    # 标签
    # ==========================================================

    def _extract_labels(
        self,
        node
    ):

        labels = {}

        # EH tag 常见 class
        tag_nodes = node.select(
            ".gt, .gtl, .gtw"
        )

        for tag in tag_nodes:

            raw = (
                tag.get("title")
                or tag.get_text(
                    " ",
                    strip=True
                )
            )

            if not raw:
                continue

            raw = raw.strip()

            # 标准 EH 标签：
            #
            # female:xxx
            # male:xxx
            # language:english
            # artist:xxx
            #
            if ":" in raw:

                namespace, tag_name = (
                    raw.split(":", 1)
                )

                namespace = (
                    namespace.strip()
                )

                tag_name = (
                    tag_name.strip()
                )

            else:

                namespace = "misc"
                tag_name = raw

            if not tag_name:
                continue

            labels.setdefault(
                namespace,
                []
            )

            # 防止重复
            if (
                tag_name
                not in labels[namespace]
            ):

                labels[namespace].append(
                    tag_name
                )

        return labels

    # ==========================================================
    # 翻页 URL
    # ==========================================================

    def _get_page_url(
        self,
        soup,
        element_id
    ):

        node = soup.find(
            "a",
            id=element_id
        )

        if not node:
            return None

        href = node.get(
            "href"
        )

        if not href or href == "#":
            return None

        return urljoin(
            self.CurrentUrl or self.source,
            href
        )

    # ==========================================================
    # Next / Prev GID
    # ==========================================================

    def _get_cursor(
        self,
        url,
        key
    ):

        if not url:
            return None

        try:

            query = parse_qs(
                urlparse(url).query
            )

            value = query.get(key)

            if not value:
                return None

            return int(
                value[0]
            )

        except (
            ValueError,
            TypeError
        ):
            return None



if __name__ == '__main__':
    if not ehCookies:
        raise SystemExit("请从 RSViewer 设置页提供 Cookie 后运行")
    e = EhData(ehCookies)
    result = e.getMain()
    print(result)
    print(f"cached: {len(e.Data)}")
