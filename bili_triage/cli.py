"""bili-triage 命令行。

  bili-triage run <URL|BV> [...]         生成证据面板（L0+L1）
  bili-triage fav                        拉收藏夹里没处理过的（定时任务用）
  bili-triage frames <BV> 08:12-09:40    L2：区间抽单帧，追加到面板
  bili-triage route                      执行你在面板里填的出口，并统计 D2 够用率
  bili-triage export                     打包要去 PC 合成的内容（手动搬运）
  bili-triage gc [--orphans]             清过期缓存（可选：清无人引用的附件目录）
  bili-triage doctor                     检查环境
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

from .config import load_config
from .util import fmt_ts, log, parse_range


def cmd_frames(cfg, a) -> None:
    from . import frames
    from .bili_api import Bili
    from .download import download_video
    from .vault import find_panel

    bili = Bili.from_cfg(cfg)
    ref = bili.resolve(a.ref)
    start, end = parse_range(a.range)
    cache = cfg.cache_dir / ref.key
    cache.mkdir(parents=True, exist_ok=True)
    video, _ = download_video(ref.url(), cache, cfg)  # 有缓存直接复用
    out_dir = cfg.attach_dir(ref.key)
    out = frames.extract_range(video, start, end, out_dir, ref.key, a.mode, a.every, a.threshold)
    log(f"抽出 {len(out)} 帧 → {out_dir}")
    embeds = [f"![[{p.name}]]" for _, p in out]
    for e in embeds:
        print(e)
    panel = find_panel(cfg, ref.key)
    if panel and not a.no_append:
        block = f"\n## 单帧 {fmt_ts(start)}–{fmt_ts(end)}（{a.mode}）\n\n" + "\n".join(embeds) + "\n"
        with open(panel, "a", encoding="utf-8") as f:
            f.write(block)
        log(f"已追加到面板：{panel.name}")


def cmd_doctor(cfg) -> None:
    ok = True

    def check(name, cond, hint=""):
        nonlocal ok
        print(f"  [{'OK' if cond else '!!'}] {name}" + ("" if cond else f"  → {hint}"))
        ok &= bool(cond)

    print("bili-triage doctor")
    check("ffmpeg", shutil.which("ffmpeg"), "winget install Gyan.FFmpeg")
    check("ffprobe", shutil.which("ffprobe"), "随 ffmpeg 一起装")
    check("vault 路径", cfg.vault.is_dir(), f"[vault] path 不存在：{cfg.vault}")
    has_cookie = bool(cfg.cookies_from_browser) or bool(cfg.cookies_file and cfg.cookies_file.is_file())
    check("B 站登录态", has_cookie, "不登录只有约 480p，画面文字读不清（只在个人设备上配）")
    check("LLM 模型名", bool(cfg.llm_model), "[llm] model 还是空的")
    check(f"LLM key（{cfg.llm_api_key_env}）", bool(cfg.llm_key()), "设置环境变量")
    backend = cfg.asr_resolved
    if backend in ("mlx", "local"):
        mod, extra = ("mlx_whisper", "mac") if backend == "mlx" else ("faster_whisper", "local-asr")
        try:
            __import__(mod)
            check(mod, True)
        except ImportError:
            check(mod, False, f"pip install -e '.[{extra}]'")
    else:
        check(f"ASR key（{cfg.asr_api_key_env}）", bool(cfg.asr_key()), "设置环境变量")
    try:
        import opencc  # noqa: F401
        check("opencc（繁转简）", True)
    except ImportError:
        check("opencc（繁转简）", False, "pip install opencc-python-reimplemented")
    print(f"  缓存目录：{cfg.cache_dir}")
    print(f"  LLM：{cfg.llm_base_url} · {cfg.llm_model}")
    print(f"  ASR：{backend}（配置 {cfg.asr_backend}）")
    print(f"  vault：{cfg.vault}")
    print(f"  导出包：{cfg.export_dir}")
    sys.exit(0 if ok else 1)


def main(argv: list[str] | None = None) -> None:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="bili-triage", description="B 站技术视频分诊 → Obsidian 证据面板")
    p.add_argument("--config", help="config.toml 路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="URL/BV → 证据面板")
    r.add_argument("refs", nargs="+")
    r.add_argument("--force", action="store_true", help="忽略已处理状态和元数据缓存，重做")

    f = sub.add_parser("fav", help="处理收藏夹里没处理过的视频")
    f.add_argument("--media-id", type=int, action="append")
    f.add_argument("--max", type=int)

    fr = sub.add_parser("frames", help="L2：区间抽单帧")
    fr.add_argument("ref")
    fr.add_argument("range", help="mm:ss-mm:ss")
    fr.add_argument("--mode", choices=["interval", "scene"], default="interval")
    fr.add_argument("--every", type=float, default=5.0, help="interval 模式间隔秒数")
    fr.add_argument("--threshold", type=float, default=0.25, help="scene 模式变化阈值")
    fr.add_argument("--no-append", action="store_true", help="不追加到面板，只打印嵌入语法")

    sub.add_parser("route", help="执行你在面板里填的出口 + D2 统计")
    e = sub.add_parser("export", help="把 20-资料 和标了 to_pc 的笔记打包，手动搬到 PC")
    e.add_argument("--to", help="导出目录（默认 [export] dir）")
    g = sub.add_parser("gc", help="清理过期缓存")
    g.add_argument("--days", type=int)
    g.add_argument("--orphans", action="store_true", help="同时删除全库无人引用的附件目录")
    sub.add_parser("doctor", help="检查环境")

    a = p.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "run":
        from .pipeline import run_one
        from .vault import gc

        gc(cfg)  # 顺手清过期缓存
        failed = 0
        for ref in a.refs:
            try:
                run_one(cfg, ref, force=a.force)
            except Exception:
                failed += 1
        sys.exit(1 if failed else 0)
    elif a.cmd == "fav":
        from .pipeline import run_favorites
        from .vault import gc

        gc(cfg)
        run_favorites(cfg, a.media_id, a.max)
    elif a.cmd == "frames":
        cmd_frames(cfg, a)
    elif a.cmd == "route":
        from .vault import route

        route(cfg)
    elif a.cmd == "export":
        from pathlib import Path

        from .vault import export

        export(cfg, Path(a.to).expanduser() if a.to else None)
    elif a.cmd == "gc":
        from .vault import gc

        gc(cfg, a.days, a.orphans)
    elif a.cmd == "doctor":
        cmd_doctor(cfg)


if __name__ == "__main__":
    main()
