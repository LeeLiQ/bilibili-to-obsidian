"""证据面板 Markdown 渲染。frontmatter 字段与 vault 三条 digest 管道同一套词汇。"""
from __future__ import annotations

import json
import time

from .bili_api import VideoRef
from .stats import units
from .util import fmt_ts, log


def _q(s) -> str:
    return json.dumps("" if s is None else str(s), ensure_ascii=False)


def _merge_timeline(tl: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in tl:
        if out and out[-1]["type"] == r["type"]:
            o = out[-1]
            o["to"] = r["to"]
            o["cuts"] += r["cuts"]
            if r["what"] not in o["whats"]:
                o["whats"].append(r["what"])
        else:
            out.append({**r, "whats": [r["what"]]})
    return out


def render(ref: VideoRef, meta: dict, ctx: dict, panel: dict, ratios: dict, links: list[str],
           sheet_name: str, timings: dict) -> str:
    ov = ctx["overall"]
    dur = ctx["duration"]
    link = ref.url
    pub = time.strftime("%Y-%m-%d", time.localtime(meta["pubdate"])) if meta.get("pubdate") else ""
    repro = panel["reproducible"]
    n_screen = sum(1 for r in repro if r["evidence"] == "画面")

    # 字段按设计说明 §5.3 模板，另加分诊出口与 D2 字段
    fm = [
        "---",
        "type: 分诊",
        f"source: {_q(ref.url())}",
        f"bvid: {ref.key}",
        f"cid: {meta['cid']}",
        f"title: {_q(meta['title'])}",
        f"up: {_q(meta['up'])}",
        f"duration: {_q(fmt_ts(dur))}",
        f"published: {pub}",
        f"captured: {time.strftime('%Y-%m-%d')}",
        f"speech_rate: {ov['speech_rate']}",
        f"filler_per_min: {ov['filler_per_min']}",
        f"demo_ratio: {ratios['demo_ratio']}",
        f"chat_ratio: {ratios['chat_ratio']}",
        f"reproducible_count: {len(repro)}",
        f"video_height: {meta.get('video_height') or ''}",
        "decidable: ",  # D2：看完这张卡能不能决定看不看 → 能 / 不能
        "exit: ",       # 你的出口：跳过 / 存着 / 看片段 / 精读；填完跑 bili-triage route
        "reason: ",     # 跳过时可选：原因关键词，写进 90-弃 那一行
        "topic: ",      # 进 20-资料 时可选：主题子目录
        "---",
    ]

    body = [f"**一句话**：{panel['one_liner']}", "", "## 时间轴", "",
            "| 时间 | 类型 | 画面切换 | 内容 |", "|---|---|---|---|"]
    for r in _merge_timeline(panel["timeline"]):
        span = f"[{fmt_ts(r['from'])}–{fmt_ts(r['to'])}]({link(r['from'])})"
        body.append(f"| {span} | {r['type']} | {r['cuts']} | {'；'.join(r['whats'])} |")

    body += ["", f"## 可复现清单（{len(repro)} 件 · 画面 {n_screen} / 口述 {len(repro) - n_screen}）", ""]
    if repro:
        for r in repro:
            item = r["item"].replace("`", "'")
            body.append(f"- [{fmt_ts(r['t'])}]({link(r['t'])}) `{item}`（{r['evidence']}）")
    else:
        body.append("- 无：未检出工具名+版本 / 命令 / 配置 / prompt / 代码")

    body += ["", "## 原文引述", ""]
    for q in panel["quotes"]:
        body.append(f"- [{fmt_ts(q['t'])}]({link(q['t'])})「{q['text']}」")

    body += [
        "", "## 客观指标", "",
        f"语速 {ov['speech_rate']} 字/分 · 口头禅 {ov['filler_per_min']} 次/分 · 有声占比 {int(ov['speech_ratio'] * 100)}% · "
        f"演示+代码占比 {int(ratios['demo_ratio'] * 100)}% · 闲聊占比 {int(ratios['chat_ratio'] * 100)}% · "
        f"转写中版本号 {ov['version_refs']} 处 · 清晰度 {meta.get('video_height') or '?'}p",
        "",
        "> 口头禅只计「然后/就是/那个/对吧」等（转写会吞掉「嗯/啊」）；占比＝时间轴标签 × 时长，由代码算。",
        "", "## 视觉", "", f"![[{sheet_name}]]", "",
        f"单帧：`bili-triage frames {ref.key} 08:12-09:40`（录屏用默认的固定间隔；PPT 类加 `--mode scene`）",
    ]
    if links:
        body += ["", "## 简介链接", ""] + [f"- {u}" for u in links]

    l1 = timings.get("组卡 LLM", 0)
    l0 = round(sum(v for k, v in timings.items() if k != "组卡 LLM"), 1)
    body += ["", f"<small>bili-triage · L0 {l0}s · L1 {l1}s</small>"]
    if panel["dropped"]:
        body += ["", f"> [!quote]- 校验器删除 {len(panel['dropped'])} 条无法回指的条目"]
        body += [f"> - {d}" for d in panel["dropped"]]

    # 验收 #2：正文 ≤ 400 字（不含时间轴表格和图片）
    prose = panel["one_liner"] + "".join(r["item"] for r in repro) + "".join(q["text"] for q in panel["quotes"])
    n = units(prose)
    if n > 400:
        log(f"注意：面板正文约 {n} 字，超过 400 字目标")
    return "\n".join(fm) + "\n\n" + "\n".join(body) + "\n"
