"""B 站公开接口：元数据、章节、置顶评论、收藏夹。全部 best-effort，失败不阻塞主流程（view 除外）。"""
from __future__ import annotations

import http.cookiejar
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
URL_RE = re.compile(r"https?://[^\s　-〿＀-￯<>\"'()（）]+")


class BiliError(RuntimeError):
    pass


@dataclass
class VideoRef:
    bvid: str
    page: int = 1  # 分 P，从 1 开始

    @property
    def key(self) -> str:
        return self.bvid if self.page == 1 else f"{self.bvid}_p{self.page}"

    def url(self, t: float | None = None) -> str:
        q = []
        if self.page > 1:
            q.append(f"p={self.page}")
        if t is not None:
            q.append(f"t={int(t)}")
        return f"https://www.bilibili.com/video/{self.bvid}" + ("?" + "&".join(q) if q else "")


class Bili:
    def __init__(self, cookies_file: Path | None = None, cookies_from_browser: str = ""):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        if cookies_file and Path(cookies_file).is_file():
            jar = http.cookiejar.MozillaCookieJar(str(cookies_file))
            jar.load(ignore_discard=True, ignore_expires=True)
            self.s.cookies.update(jar)
        elif cookies_from_browser:
            try:  # 与 yt-dlp 读同一份浏览器登录态
                from yt_dlp.cookies import extract_cookies_from_browser

                jar = extract_cookies_from_browser(cookies_from_browser)
                for c in jar:
                    if "bilibili.com" in (c.domain or ""):
                        self.s.cookies.set_cookie(c)
            except Exception as e:  # 读不到就匿名访问，不阻塞
                print(f"读取 {cookies_from_browser} 的 cookie 失败，按未登录处理：{e}")

    @classmethod
    def from_cfg(cls, cfg) -> "Bili":
        return cls(cfg.cookies_file, cfg.cookies_from_browser)

    def _get(self, url: str, **params):
        r = self.s.get(url, params=params, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("code") != 0:
            raise BiliError(f"{url} code={j.get('code')} msg={j.get('message')}")
        return j.get("data") or {}

    # ---------- 解析输入 ----------
    def resolve(self, s: str) -> VideoRef:
        s = s.strip()
        if "b23.tv" in s:
            s = self.s.get(s, allow_redirects=True, timeout=15).url
        m = BV_RE.search(s)
        if not m:
            raise BiliError(f"没找到 BV 号：{s}")
        page = 1
        if s.startswith("http"):
            qs = parse_qs(urlparse(s).query)
            if qs.get("p", [""])[0].isdigit():
                page = int(qs["p"][0])
        return VideoRef(m.group(0), page)

    # ---------- 元数据 ----------
    def view(self, bvid: str) -> dict:
        return self._get("https://api.bilibili.com/x/web-interface/view", bvid=bvid)

    def chapters(self, bvid: str, cid: int) -> list[dict]:
        """UP 主手动/自动打的分段章节。需要登录时会失败，返回 []。"""
        try:
            d = self._get("https://api.bilibili.com/x/player/v2", bvid=bvid, cid=cid)
            return [
                {"from": int(v.get("from", 0)), "to": int(v.get("to", 0)), "content": v.get("content", "")}
                for v in (d.get("view_points") or [])
            ]
        except Exception:
            return []

    def pinned_reply(self, aid: int) -> str:
        """UP 主置顶评论正文（常放 repo / 文档链接）。"""
        try:
            d = self._get("https://api.bilibili.com/x/v2/reply", type=1, oid=aid, sort=1, ps=1, pn=1)
            top = (d.get("upper") or {}).get("top") or {}
            return ((top.get("content") or {}).get("message")) or ""
        except Exception:
            return ""

    def fav_bvids(self, media_id: int, ps: int = 20) -> list[str]:
        d = self._get(
            "https://api.bilibili.com/x/v3/fav/resource/list",
            media_id=media_id, pn=1, ps=ps, platform="web",
        )
        out = []
        for m in d.get("medias") or []:
            # type 2 = 视频；attr != 0 通常是失效视频
            if m.get("type") == 2 and m.get("bvid") and not m.get("attr"):
                out.append(m["bvid"])
        return out


def extract_links(*texts: str) -> list[str]:
    seen, out = set(), []
    for t in texts:
        for u in URL_RE.findall(t or ""):
            u = u.rstrip(".,;:!?，。；：！？")
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out
