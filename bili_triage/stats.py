"""确定性统计：全部由代码算，数字可复现，不经 LLM。"""
from __future__ import annotations

import re

# whisper 会吞掉"嗯/啊/呃"这类填充音，所以只数能被转写保留下来的口头禅
FILLERS = ["就是说", "然后", "就是", "那个", "对吧", "对不对", "怎么说呢", "其实呢"]
_FILLER_RE = re.compile("|".join(map(re.escape, FILLERS)))
_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-\.+#]*")
_VERSION = re.compile(r"(?<![\w.])v?\d+\.\d+(?:\.\d+)?(?![\w.])", re.I)
_CMD = re.compile(r"\b(pip|npm|npx|pnpm|uv|git|curl|docker|brew|winget|cd|python|node|claude|cursor)\b", re.I)


def units(text: str) -> int:
    """'字数'：汉字逐个算，英文单词各算 1。"""
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


def make_buckets(duration: float, size: int) -> list[tuple[float, float]]:
    out, a = [], 0.0
    while a < duration - 1e-6:
        b = min(a + size, duration)
        out.append((a, b))
        a = b
    # 末桶太短（<30s）并入前一桶
    if len(out) >= 2 and out[-1][1] - out[-1][0] < 30:
        out[-2] = (out[-2][0], out[-1][1])
        out.pop()
    return out


def _segs_in(segs: list[dict], a: float, b: float) -> list[dict]:
    return [s for s in segs if a <= (s["start"] + s["end"]) / 2 < b]


def bucket_stats(segs: list[dict], buckets: list[tuple[float, float]], visual: list[dict] | None) -> list[dict]:
    out = []
    for i, (a, b) in enumerate(buckets):
        ss = _segs_in(segs, a, b)
        text = "".join(s["text"] for s in ss)
        speech_sec = sum(min(s["end"], b) - max(s["start"], a) for s in ss if s["end"] > a)
        u = units(text)
        row = {
            "i": i,
            "from": a,
            "to": b,
            "units": u,
            "speech_ratio": round(min(speech_sec / (b - a), 1.0), 2) if b > a else 0,
            "rate": round(u / (speech_sec / 60)) if speech_sec > 5 else 0,
            "fillers": len(_FILLER_RE.findall(text)),
            "en_words": len(_WORD.findall(text)),
            "versions": len(_VERSION.findall(text)),
            "cmd_words": len(_CMD.findall(text)),
        }
        if visual:
            row.update({"change": visual[i]["change"], "cuts": visual[i]["cuts"]})
        out.append(row)
    return out


def overall(segs: list[dict], duration: float) -> dict:
    text = "".join(s["text"] for s in segs)
    speech_sec = sum(s["end"] - s["start"] for s in segs)
    speech_min = speech_sec / 60 if speech_sec > 0 else 0
    u = units(text)
    return {
        "speech_rate": round(u / speech_min) if speech_min else 0,          # 字/分（按有声时长）
        "filler_per_min": round(len(_FILLER_RE.findall(text)) / speech_min, 1) if speech_min else 0,
        "speech_ratio": round(speech_sec / duration, 2) if duration else 0,  # 有声占比
        "version_refs": len(_VERSION.findall(text)),
        "total_units": u,
    }


def label_ratios(timeline: list[dict], duration: float) -> dict:
    """演示/代码/闲聊占比：用 LLM 给的桶标签 × 桶时长，由代码计算。"""
    agg: dict[str, float] = {}
    for r in timeline:
        agg[r["type"]] = agg.get(r["type"], 0) + (r["to"] - r["from"])
    d = duration or 1
    return {
        "demo_ratio": round((agg.get("演示", 0) + agg.get("代码", 0)) / d, 2),
        "chat_ratio": round(agg.get("闲聊", 0) / d, 2),
    }
