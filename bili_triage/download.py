"""yt-dlp 下载：音频和视频（仅画面流）分开拉，音频先到先转写。已有缓存直接复用。"""
from __future__ import annotations

from pathlib import Path

from .util import log


def _find(cache: Path, stem: str) -> Path | None:
    for p in sorted(cache.glob(stem + ".*")):
        if p.suffix not in (".part", ".ytdl", ".tmp") and p.stat().st_size > 0:
            return p
    return None


def _opts(cfg) -> dict:
    o = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "http_headers": {"Referer": "https://www.bilibili.com/"},
    }
    if cfg.cookies_file and Path(cfg.cookies_file).is_file():
        o["cookiefile"] = str(cfg.cookies_file)
    elif cfg.cookies_from_browser:
        o["cookiesfrombrowser"] = (cfg.cookies_from_browser,)
    return o


def download_audio(url: str, cache: Path, cfg) -> Path:
    hit = _find(cache, "audio")
    if hit:
        return hit
    import yt_dlp

    o = _opts(cfg) | {"format": "bestaudio/best", "outtmpl": str(cache / "audio.%(ext)s")}
    with yt_dlp.YoutubeDL(o) as y:
        y.download([url])
    hit = _find(cache, "audio")
    if not hit:
        raise RuntimeError("音频下载失败")
    return hit


def download_video(url: str, cache: Path, cfg) -> tuple[Path, int | None]:
    """返回 (路径, 实际高度)。优先 avc1：ffmpeg 抽帧解码比 hevc/av1 快得多。"""
    hit = _find(cache, "video")
    if hit:
        return hit, None
    import yt_dlp

    h = cfg.max_height
    fmt = f"bestvideo[height<={h}][vcodec^=avc1]/bestvideo[height<={h}]/best[height<={h}]/best"
    o = _opts(cfg) | {"format": fmt, "outtmpl": str(cache / "video.%(ext)s")}
    with yt_dlp.YoutubeDL(o) as y:
        info = y.extract_info(url, download=True)
    hit = _find(cache, "video")
    if not hit:
        raise RuntimeError("视频下载失败")
    height = (info or {}).get("height")
    if height and height < 720:
        log(f"注意：视频只有 {height}p，多半是没登录或登录态过期，画面文字可能读不清")
    return hit, height
