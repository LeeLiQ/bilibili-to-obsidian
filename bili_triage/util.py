from __future__ import annotations

import json
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def fmt_ts(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_ts(s: str | int | float) -> float | None:
    """'mm:ss' / 'h:mm:ss' / 秒数 → 秒。不合法返回 None。"""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if not 2 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        return None
    vals = [float(p) for p in parts]
    if len(vals) == 2:
        return vals[0] * 60 + vals[1]
    return vals[0] * 3600 + vals[1] * 60 + vals[2]


def parse_range(s: str) -> tuple[float, float]:
    m = re.fullmatch(r"\s*([\d:.]+)\s*[-~–到]\s*([\d:.]+)\s*", s)
    if not m:
        raise ValueError(f"区间格式应为 mm:ss-mm:ss，收到：{s}")
    a, b = parse_ts(m.group(1)), parse_ts(m.group(2))
    if a is None or b is None or b <= a:
        raise ValueError(f"区间不合法：{s}")
    return a, b


_BAD_FS = re.compile(r'[\\/:*?"<>|#^\[\]\r\n\t]')


def safe_filename(title: str, maxlen: int = 80) -> str:
    s = _BAD_FS.sub(" ", title)
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s[:maxlen].rstrip() or "untitled"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


@contextmanager
def timed(label: str, sink: dict):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        sink[label] = round(time.perf_counter() - t0, 1)
        log(f"{label} 用时 {sink[label]}s")


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def die(msg: str, code: int = 1):
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)
