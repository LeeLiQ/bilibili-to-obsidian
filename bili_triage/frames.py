"""抽帧三件事：联系表（扫）、关键帧扫描（时间轴的画面变化信号）、区间/变化点单帧（学）。

全部走本地缓存的视频文件，不重新下载。单帧命名 <bvid>-t<秒>.png：
带 bvid 前缀，避免不同视频的 t-0492.png 在 vault 里重名导致 ![[...]] 解析歧义。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .util import fmt_ts

SCAN_W, SCAN_H = 64, 36


def _ff(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", "-y", *args], capture_output=True)


def _ff_vfr(args_before_out: list[str], out: list[str], vfr_mode: str = "passthrough") -> subprocess.CompletedProcess:
    """ffmpeg ≥5.1 用 -fps_mode，旧版用 -vsync；自动回退。"""
    r = _ff([*args_before_out, "-fps_mode", vfr_mode, *out])
    if r.returncode != 0 and b"fps_mode" in r.stderr:
        legacy = {"passthrough": "0", "vfr": "vfr"}[vfr_mode]
        r = _ff([*args_before_out, "-vsync", legacy, *out])
    return r


def probe_duration(video: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def grab(video: Path, t: float, out: Path, width: int | None = None) -> Path:
    args = ["-loglevel", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(video), "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={width}:-2"]
    if out.suffix.lower() in (".jpg", ".jpeg"):
        args += ["-q:v", "3"]
    out.parent.mkdir(parents=True, exist_ok=True)
    r = _ff([*args, str(out)])
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"抽帧失败 t={t:.1f}: {r.stderr.decode(errors='ignore')[-300:]}")
    return out


def grab_many(video: Path, jobs: list[tuple[float, Path, int | None]], workers: int = 4) -> list[Path]:
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda j: grab(video, *j), jobs))


def _font(size: int):
    for name in ("msyh.ttc", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def contact_sheet(video: Path, duration: float, out: Path, cols: int = 4, rows: int = 4, tile_w: int = 360) -> Path:
    """均匀取 cols*rows 帧拼图，每格左上角标时间戳。"""
    n = cols * rows
    ts = [(i + 0.5) * duration / n for i in range(n)]
    with tempfile.TemporaryDirectory() as td:
        paths = grab_many(video, [(t, Path(td) / f"{i:02d}.jpg", tile_w) for i, t in enumerate(ts)])
        tiles = [Image.open(p).convert("RGB") for p in paths]
        th = tiles[0].height
        sheet = Image.new("RGB", (cols * tile_w, rows * th), "black")
        draw = ImageDraw.Draw(sheet)
        font = _font(18)
        for i, (im, t) in enumerate(zip(tiles, ts)):
            x, y = (i % cols) * tile_w, (i // cols) * th
            sheet.paste(im.resize((tile_w, th)), (x, y))
            label = fmt_ts(t)
            draw.rectangle([x, y, x + 62, y + 24], fill="black")
            draw.text((x + 5, y + 2), label, fill="white", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=85)
    return out


_PTS = re.compile(rb"Parsed_showinfo.*?pts_time:\s*([\d.]+)")


def keyframe_scan(video: Path) -> list[tuple[float, np.ndarray]]:
    """只解码关键帧（-skip_frame nokey，很快），缩到 64x36 灰度，返回 [(t, 帧)]。"""
    r = _ff_vfr(
        ["-skip_frame", "nokey", "-i", str(video), "-an", "-vf", f"scale={SCAN_W}:{SCAN_H},format=gray,showinfo"],
        ["-f", "rawvideo", "pipe:1"],
    )
    if r.returncode != 0:
        raise RuntimeError("关键帧扫描失败：" + r.stderr.decode(errors="ignore")[-300:])
    pts = [float(m) for m in _PTS.findall(r.stderr)]
    size = SCAN_W * SCAN_H
    n = min(len(pts), len(r.stdout) // size)
    buf = np.frombuffer(r.stdout[: n * size], dtype=np.uint8).reshape(n, SCAN_H, SCAN_W)
    return [(pts[i], buf[i].astype(np.int16)) for i in range(n)]


def bucket_visual(scan: list[tuple[float, np.ndarray]], buckets: list[tuple[float, float]], cut_thr: float = 0.06) -> list[dict]:
    """每桶：相邻关键帧平均差异（0–1，越大画面越活跃）+ 明显切换次数。"""
    diffs = [
        (scan[i][0], float(np.abs(scan[i][1] - scan[i - 1][1]).mean()) / 255.0)
        for i in range(1, len(scan))
    ]
    out = []
    for a, b in buckets:
        ds = [d for t, d in diffs if a <= t < b]
        out.append({
            "change": round(sum(ds) / len(ds), 3) if ds else None,
            "cuts": sum(1 for d in ds if d > cut_thr),
            "keyframes": len(ds),
        })
    return out


def frame_name(prefix: str, t: float) -> str:
    return f"{prefix}-t{int(t):04d}.png"


def extract_range(
    video: Path, start: float, end: float, out_dir: Path, prefix: str,
    mode: str = "interval", every: float = 5.0, threshold: float = 0.25,
) -> list[tuple[float, Path]]:
    """L2：区间抽单帧原图。interval = 固定间隔（录屏更稳）；scene = 画面变化点（PPT 更好）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    if mode == "interval":
        ts, t = [], start
        while t <= end + 1e-6:
            ts.append(t)
            t += every
        jobs, seen = [], set()
        for t in ts:
            name = frame_name(prefix, t)
            if name not in seen:
                seen.add(name)
                jobs.append((t, out_dir / name, None))
        paths = grab_many(video, jobs)
        return [(j[0], p) for j, p in zip(jobs, paths)]

    if mode != "scene":
        raise ValueError("mode 只能是 interval 或 scene")
    with tempfile.TemporaryDirectory() as td:
        r = _ff_vfr(
            ["-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(video), "-an",
             "-vf", f"select='gt(scene,{threshold})',showinfo"],
            [str(Path(td) / "f_%04d.png")],
            vfr_mode="vfr",
        )
        if r.returncode != 0:
            raise RuntimeError("变化点抽帧失败：" + r.stderr.decode(errors="ignore")[-300:])
        pts = [float(m) for m in _PTS.findall(r.stderr)]
        files = sorted(Path(td).glob("f_*.png"))
        results = [(start, grab(video, start, out_dir / frame_name(prefix, start)))]  # 起点总要一张
        seen = {results[0][1].name}
        for rel, f in zip(pts, files):
            t = start + rel
            name = frame_name(prefix, t)
            if name in seen:
                continue
            seen.add(name)
            dst = out_dir / name
            shutil.move(str(f), str(dst))  # 跨盘安全
            results.append((t, dst))
    return results
