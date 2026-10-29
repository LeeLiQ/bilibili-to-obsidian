"""L1 组卡：一次多模态 LLM 调用 + 代码校验。

分工：LLM 只做「分类」和「挑出事实」；数字由代码算；每条事实由代码回指核对，核不上就删（不软化）。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from . import llm
from .asr import as_lines
from .config import Config
from .frames import grab_many
from .util import fmt_ts, log, parse_ts

TYPES = ["口播", "演示", "代码", "闲聊"]
BANNED = re.compile(
    r"值得|干货|精彩|优质|硬核|必看|强烈|宝藏|良心|注水|拖沓|啰嗦|浅显|深入浅出|非常好|很棒|很好|出色|实用|"
    r"推荐(?!系统|算法|模型|位|引擎)"
)

SYSTEM = """你在为一条 B 站技术视频生成「证据面板」的事实部分。观众会自己判断看不看；你只负责把可核对的事实摆出来。

硬规则（违反任何一条，输出作废）：
1. 不给结论、不评分、不用评价词（值得、干货、精彩、推荐、硬核、实用、注水、拖沓、浅显……）。只描述「做了什么、出现了什么」。
2. 每条事实带 mm:ss 时间戳，必须能指回转写里的某一行，或指回某张附图。
3. 不许编造视频里没出现的工具名、版本号、数字。转写里的英文术语可能被语音识别写错（如「克劳德」=Claude），可以依据画面或常识纠正拼写，但不能新增。
4. 没有就给空数组，不要凑数。

你会收到：元数据、简介、UP 主章节（可能为空）、分桶统计表、带时间戳的转写、若干张画面截图（每张标了时间戳和所属桶）。

只输出一个 JSON 对象（不要 markdown 代码块）：
{
  "one_liner": "≤40 字，描述这条视频在做什么，不含评价",
  "timeline": [{"bucket": 0, "type": "口播|演示|代码|闲聊", "what": "≤20 字，这一段在做什么"}],
  "reproducible": [{"t": "mm:ss", "item": "具体细节", "evidence": "口述|画面", "quote": "原文"}],
  "style_quotes": [{"t": "mm:ss", "text": "转写原文"}]
}

字段要求：
- timeline：每个桶恰好一条，覆盖全部桶序号。类型判据——演示：画面在操作软件/终端/浏览器；代码：画面主体是代码或配置文件且在讲解；口播：以讲解为主、画面基本静止（人像/PPT/图片）；闲聊：与主题无关的个人经历、寒暄、广告、卖课引流。结合统计表的画面变化（change/cuts）和截图判断。
- reproducible：观众能照着复现的具体细节——工具名+版本、模型名、命令、配置项与取值、prompt 原文、代码片段、具体数字阈值。泛泛的概念（「用了 RAG」）不算。
  · evidence=口述：quote 必须逐字复制转写里该时间点附近的原文片段（10–40 字）。
  · evidence=画面：t 必须是你看到它的那张截图的时间戳；quote 写截图里看到的原文字（可为空）。
  · 同一细节只列一次，按时间排序。
- style_quotes：恰好 3 条，分别取自全片前 1/3、中 1/3、后 1/3，逐字复制转写原文（15–60 字），用来让观众判断口播风格。选最能代表该段说话方式的句子，不要只挑说得最好的。"""


# ---------------- 输入构建 ----------------

def pick_image_times(buckets: list[tuple[float, float]], per_bucket: int, max_images: int) -> list[tuple[int, float]]:
    per = per_bucket
    if len(buckets) * per > max_images:
        per = 1
    picks = []
    for i, (a, b) in enumerate(buckets):
        for k in range(per):
            picks.append((i, a + (b - a) * (k + 1) / (per + 1)))
    if len(picks) > max_images:  # 超长视频：均匀下采样
        step = len(picks) / max_images
        picks = [picks[int(j * step)] for j in range(max_images)]
    return picks


def _stats_table(bstats: list[dict]) -> str:
    head = "| 桶 | 时间 | 字数 | 有声占比 | 语速 | 英文词 | 版本号 | 命令词 | 画面变化 | 切换 |\n|---|---|---|---|---|---|---|---|---|---|"
    rows = [
        f"| {r['i']} | {fmt_ts(r['from'])}–{fmt_ts(r['to'])} | {r['units']} | {r['speech_ratio']} | {r['rate']} | "
        f"{r['en_words']} | {r['versions']} | {r['cmd_words']} | {r.get('change')} | {r.get('cuts')} |"
        for r in bstats
    ]
    return head + "\n" + "\n".join(rows)


def build_messages(ctx: dict, images: list[tuple[int, float, Path]]) -> list[dict]:
    m = ctx["meta"]
    chap = "\n".join(f"- {fmt_ts(c['from'])} {c['content']}" for c in ctx["chapters"]) or "（无）"
    text = f"""## 元数据
标题：{m['title']}
UP 主：{m['up']}
时长：{fmt_ts(ctx['duration'])}
简介：{m.get('desc') or '（无）'}

## UP 主章节
{chap}

## 分桶统计（每桶 {ctx['bucket_sec']} 秒；画面变化 0–1，越大越活跃；切换＝明显换画面次数）
{_stats_table(ctx['bstats'])}

## 转写（[mm:ss] 为该句开始时间）
{as_lines(ctx['segments'])}

## 画面截图（共 {len(images)} 张，见下）"""
    parts: list[dict] = [{"type": "text", "text": text}]
    for i, t, p in images:
        parts.append({"type": "text", "text": f"截图 · 桶 {i} · {fmt_ts(t)}"})
        parts.append(llm.image_part(p))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": parts}]


# ---------------- 校验 ----------------

_PUNCT = re.compile(r"[\s\W_]+", re.U)


def _norm(s: str) -> str:
    return _PUNCT.sub("", s or "").lower()


def match_ratio(quote: str, window: str) -> float:
    """quote 有多大比例的字符按顺序出现在 window 里（0–1）。"""
    q, w = _norm(quote), _norm(window)
    if not q:
        return 0.0
    blocks = SequenceMatcher(None, q, w, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks) / len(q)


def window_text(segs: list[dict], t: float, radius: float) -> str:
    return "".join(s["text"] for s in segs if s["end"] >= t - radius and s["start"] <= t + radius)


def _structural_violations(d: dict, n_buckets: int) -> list[str]:
    v = []
    if not isinstance(d.get("one_liner"), str) or not d["one_liner"].strip():
        v.append("one_liner 缺失")
    elif len(d["one_liner"]) > 50:
        v.append(f"one_liner 超过 40 字（{len(d['one_liner'])}）")
    tl = d.get("timeline") or []
    got = {r.get("bucket") for r in tl if isinstance(r, dict)}
    missing = [i for i in range(n_buckets) if i not in got]
    if missing:
        v.append(f"timeline 缺桶 {missing}")
    bad_types = [r.get("type") for r in tl if isinstance(r, dict) and r.get("type") not in TYPES]
    if bad_types:
        v.append(f"timeline 类型不合法 {bad_types}，只能是 {TYPES}")
    texts = [d.get("one_liner", "")] + [r.get("what", "") for r in tl if isinstance(r, dict)] + [
        r.get("item", "") for r in (d.get("reproducible") or []) if isinstance(r, dict)
    ]
    hits = sorted({m.group(0) for t in texts for m in BANNED.finditer(t or "")})
    if hits:
        v.append(f"出现评价词 {hits}，改为纯描述")
    if len(d.get("style_quotes") or []) != 3:
        v.append("style_quotes 必须恰好 3 条（前/中/后 1/3 各一）")
    return v


def _strip_banned(s: str) -> str:
    """重试后仍含评价词：整句子句删掉（只删词会留下「满满」这种残渣）。"""
    if not s or not BANNED.search(s):
        return (s or "").strip()
    parts = re.split(r"([，,；;。])", s)  # [子句, 标点, 子句, 标点, ...]
    kept = []
    for i in range(0, len(parts), 2):
        clause = parts[i]
        punct = parts[i + 1] if i + 1 < len(parts) else ""
        if clause.strip() and not BANNED.search(clause):
            kept.append(clause + punct)
    return "".join(kept).strip("，,；;。 ").strip()


def validate(d: dict, ctx: dict, image_times: list[float]) -> dict:
    """代码侧校验：回指核对、补齐、清理。返回渲染用的 panel。"""
    segs, dur, buckets = ctx["segments"], ctx["duration"], ctx["buckets"]
    dropped: list[str] = []

    # timeline：按桶号对齐，缺的标"?"
    by_i = {r.get("bucket"): r for r in (d.get("timeline") or []) if isinstance(r, dict)}
    timeline = []
    for i, (a, b) in enumerate(buckets):
        r = by_i.get(i) or {}
        typ = r.get("type") if r.get("type") in TYPES else "?"
        timeline.append({
            "from": a, "to": b, "type": typ, "what": _strip_banned(r.get("what", "")) or "（缺）",
            "cuts": ctx["bstats"][i].get("cuts") or 0,
        })

    # reproducible：口述→原文须在 ±45s 转写窗口里；画面→t 须贴近某张实际给过的截图
    repro = []
    for r in d.get("reproducible") or []:
        if not isinstance(r, dict):
            continue
        t = parse_ts(r.get("t", ""))
        item = _strip_banned(str(r.get("item", "")))
        if t is None or not (0 <= t <= dur) or not item:
            dropped.append(f"可复现·时间戳无效：{r.get('t')} {item}")
            continue
        ev = r.get("evidence")
        if ev == "画面":
            if not image_times or min(abs(t - it) for it in image_times) > 3:
                dropped.append(f"可复现·画面条目对不上任何截图：[{fmt_ts(t)}] {item}")
                continue
        else:
            ev = "口述"
            if match_ratio(str(r.get("quote", "")), window_text(segs, t, 45)) < 0.6:
                dropped.append(f"可复现·口述原文在转写里找不到：[{fmt_ts(t)}] {item}")
                continue
        repro.append({"t": t, "item": item, "evidence": ev})
    repro.sort(key=lambda x: x["t"])

    # style_quotes：须逐字可回指；按前/中/后三段各取一，缺的由代码从转写里补
    thirds = [(0, dur / 3), (dur / 3, 2 * dur / 3), (2 * dur / 3, dur + 1)]
    llm_q = []
    for q in d.get("style_quotes") or []:
        if not isinstance(q, dict):
            continue
        t = parse_ts(q.get("t", ""))
        if t is None or match_ratio(str(q.get("text", "")), window_text(segs, t, 30)) < 0.8:
            dropped.append(f"引述·转写里找不到：{q.get('t')} {str(q.get('text', ''))[:20]}…")
            continue
        llm_q.append({"t": t, "text": str(q["text"]).strip(), "by": "llm"})
    quotes = []
    for a, b in thirds:
        hit = next((q for q in llm_q if a <= q["t"] < b), None)
        if not hit:
            hit = _fallback_quote(segs, a, b)
        if hit:
            quotes.append(hit)

    return {
        "one_liner": _strip_banned(d.get("one_liner", "")) or "（缺）",
        "timeline": timeline,
        "reproducible": repro,
        "quotes": quotes,
        "dropped": dropped,
    }


def _fallback_quote(segs: list[dict], a: float, b: float) -> dict | None:
    """该段中点附近、长度 ≥15 字的第一句原文。"""
    mid = (a + b) / 2
    cands = [s for s in segs if a <= s["start"] < b and len(_norm(s["text"])) >= 15]
    if not cands:
        return None
    s = min(cands, key=lambda s: abs(s["start"] - mid))
    return {"t": s["start"], "text": s["text"], "by": "code"}


# ---------------- 主入口 ----------------

def build_panel(cfg: Config, ctx: dict, cache: Path, video: Path) -> dict:
    picks = pick_image_times(ctx["buckets"], cfg.llm_images_per_bucket, cfg.llm_max_images)
    fdir = cache / "llm_frames"
    jobs = [(t, fdir / f"b{i:02d}_{int(t):05d}.jpg", 1024) for i, t in picks]
    todo = [j for j in jobs if not j[1].exists()]
    if todo:
        grab_many(video, todo)
    images = [(i, t, j[1]) for (i, t), j in zip(picks, jobs)]

    messages = build_messages(ctx, images)
    data, raw, violations = None, "", []
    for attempt in range(2):
        raw = llm.chat(cfg, messages)
        try:
            data = llm.parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            violations = ["输出不是合法 JSON"]
            data = None
        else:
            violations = _structural_violations(data, len(ctx["buckets"]))
        if not violations:
            break
        log(f"LLM 输出违规（第 {attempt + 1} 次）：{violations}")
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "以下问题必须修正后重新输出完整 JSON：\n- " + "\n- ".join(violations)},
        ]
    (cache / "llm_raw.json").write_text(raw, encoding="utf-8")
    if data is None:
        raise RuntimeError("LLM 两次都没给出合法 JSON，原始输出见缓存 llm_raw.json")

    panel = validate(data, ctx, [t for _, t, _ in images])
    panel["violations_left"] = violations
    if panel["dropped"]:
        log(f"校验器删除 {len(panel['dropped'])} 条无法回指的条目")
    return panel
