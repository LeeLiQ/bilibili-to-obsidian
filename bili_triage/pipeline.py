"""L0 摄取 → L1 组卡 → 写入 vault。全程无人值守。"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import frames
from .asr import transcribe
from .bili_api import Bili, VideoRef, extract_links
from .config import Config
from .download import download_audio, download_video
from .panel import build_panel
from .render import render
from .stats import bucket_stats, label_ratios, make_buckets, overall
from .util import fmt_ts, log, read_json, timed, write_json
from .vault import State, worklog, write_panel

BUDGET_SEC = 180  # 硬约束：粘贴 URL → 面板落 vault ≤ 3 分钟


def fetch_meta(bili: Bili, ref: VideoRef, cache: Path, force: bool) -> dict:
    meta = None if force else read_json(cache / "meta.json")
    if meta:
        return meta
    v = bili.view(ref.bvid)
    pages = v.get("pages") or []
    if ref.page > len(pages):
        raise RuntimeError(f"{ref.bvid} 只有 {len(pages)} P，没有 P{ref.page}")
    pg = pages[ref.page - 1]
    title = v["title"]
    if len(pages) > 1:
        title += f" · P{ref.page} {pg.get('part', '')}".rstrip()
    meta = {
        "title": title,
        "up": (v.get("owner") or {}).get("name", ""),
        "desc": v.get("desc", ""),
        "aid": v["aid"],
        "cid": pg["cid"],
        "duration": pg.get("duration") or v.get("duration"),
        "pubdate": v.get("pubdate"),
        "n_pages": len(pages),
    }
    meta["chapters"] = bili.chapters(ref.bvid, meta["cid"])
    meta["pinned"] = bili.pinned_reply(meta["aid"])
    write_json(cache / "meta.json", meta)
    return meta


def run_one(cfg: Config, s: str, force: bool = False) -> Path | None:
    t_start = time.perf_counter()
    bili = Bili.from_cfg(cfg)
    ref = bili.resolve(s)
    state = State(cfg)
    if state.done(ref.key) and not force:
        log(f"{ref.key} 已处理过（{state.get(ref.key).get('panel')}），跳过。重做加 --force")
        return None

    cache = cfg.cache_dir / ref.key
    cache.mkdir(parents=True, exist_ok=True)
    timings: dict = {}
    try:
        with timed("元数据", timings):
            meta = fetch_meta(bili, ref, cache, force)
        log(f"《{meta['title']}》 {meta['up']} · {fmt_ts(meta['duration'] or 0)}")
        links = extract_links(meta.get("desc", ""), meta.get("pinned", ""))
        url = ref.url()

        # 视频在后台下载；音频先到先转写
        with ThreadPoolExecutor(1) as ex:
            vfut = ex.submit(download_video, url, cache, cfg)
            with timed("下载音频", timings):
                audio = download_audio(url, cache, cfg)
            with timed("转写", timings):
                segs = transcribe(cfg, audio, meta["title"], cache)
            with timed("等待视频", timings):
                video, height = vfut.result()
        if height:
            meta["video_height"] = height
            write_json(cache / "meta.json", meta)

        duration = frames.probe_duration(video) or float(meta["duration"])
        buckets = make_buckets(duration, cfg.bucket_sec)
        attach_dir = cfg.attach_dir(ref.key)
        sheet_name = f"sheet-{ref.key}.jpg"
        with timed("抽帧", timings):
            frames.contact_sheet(video, duration, attach_dir / sheet_name)
            visual = frames.bucket_visual(frames.keyframe_scan(video), buckets)

        bstats = bucket_stats(segs, buckets, visual)
        ctx = {
            "meta": meta, "duration": duration, "buckets": buckets, "bucket_sec": cfg.bucket_sec,
            "bstats": bstats, "segments": segs, "chapters": meta.get("chapters") or [],
            "overall": overall(segs, duration),
        }
        with timed("组卡 LLM", timings):
            panel = build_panel(cfg, ctx, cache, video)
        ratios = label_ratios(panel["timeline"], duration)

        md = render(ref, meta, ctx, panel, ratios, links, sheet_name, timings)
        path = write_panel(cfg, ref.key, meta["title"], md)
        total = round(time.perf_counter() - t_start, 1)
        state.mark(ref.key, status="done", panel=str(path), seconds=total, timings=timings)
        worklog(cfg, f"[[{path.stem}]] · bili-triage · 面板生成 · {len(panel['reproducible'])} 件可复现 · 出口待定")
        flag = "" if total <= BUDGET_SEC else f"（超过 {BUDGET_SEC}s 预算）"
        log(f"完成 {total}s{flag} → {path}")
        return path
    except Exception as e:
        state.mark(ref.key, status="failed", error=f"{type(e).__name__}: {e}", failures=state.failures(ref.key) + 1)
        log(f"失败：{ref.key} {type(e).__name__}: {e}")
        traceback.print_exc()
        raise


def run_favorites(cfg: Config, media_ids: list[int] | None = None, max_n: int | None = None) -> None:
    ids = media_ids or cfg.fav_media_ids
    if not ids:
        raise SystemExit("没配置收藏夹：config.toml [favorites] media_ids，或 --media-id")
    bili, state = Bili.from_cfg(cfg), State(cfg)
    budget = max_n or cfg.fav_max_per_run
    todo = []
    for mid in ids:
        for bv in bili.fav_bvids(mid):
            st = state.get(bv)
            if st.get("status") == "done" or st.get("failures", 0) >= 3:
                continue
            if bv not in todo:
                todo.append(bv)
    log(f"收藏夹待分诊 {len(todo)} 条，本轮处理 {min(len(todo), budget)} 条")
    for bv in todo[:budget]:
        try:
            run_one(cfg, bv)
        except Exception:
            continue  # 已记入 state，下一轮重试（最多 3 次）
