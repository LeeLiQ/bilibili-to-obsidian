"""转写：faster-whisper（本地）或 OpenAI 兼容 /audio/transcriptions（云端）。

输出统一为 segments: [{"start": float, "end": float, "text": str}]，简体中文。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import requests

from .config import Config
from .util import fmt_ts, log, read_json, write_json

# whisper 在静音/音乐段的经典幻觉（中文训练语料里的字幕组水印）
HALLUCINATIONS = [
    r"请不吝点赞.{0,6}订阅.{0,6}转发.{0,6}打赏",
    r"字幕由.{0,20}提供",
    r"明镜与点点栏目",
    r"^(谢谢|感谢)(大家)?(的)?(收看|观看)[。！!]*$",
    r"^(中文字幕|字幕)[:：]?.{0,12}$",
    r"Amara\.org",
]
_HALLU_RE = re.compile("|".join(HALLUCINATIONS))


def _prompt(title: str) -> str:
    # 用简体+标点的提示句引导输出简体并带标点；标题里的工具名帮助拼写
    return f"以下是一段普通话技术视频的简体中文转写，带标点符号。视频标题：{title}。"


def _t2s():
    try:
        import opencc

        cc = opencc.OpenCC("t2s")
        return cc.convert
    except Exception:
        return lambda s: s


def _clean(segs: list[dict]) -> list[dict]:
    conv = _t2s()
    out, prev = [], None
    for s in segs:
        t = conv((s["text"] or "").strip())
        if not t or _HALLU_RE.search(t):
            continue
        if t == prev:  # 循环复读
            continue
        prev = t
        out.append({"start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2), "text": t})
    return out


def _mlx(cfg: Config, audio: Path, title: str) -> list[dict]:
    """Apple Silicon：mlx-whisper，20 分钟音频约 1 分钟。mlx 没有 VAD，靠 _clean 过滤幻觉句。"""
    try:
        import mlx_whisper
    except ImportError:
        raise SystemExit("未安装 mlx-whisper：pip install -e '.[mac]'，或把 [asr] backend 改为 local / api")
    r = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=cfg.asr_mlx_model,
        language="zh",
        initial_prompt=_prompt(title),
        condition_on_previous_text=False,
        verbose=None,
    )
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in r.get("segments", [])]


def _local(cfg: Config, audio: Path, title: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("未安装 faster-whisper：pip install -e .[local-asr]，或把 [asr] backend 改为 api")
    model = WhisperModel(
        cfg.asr_model, device=cfg.asr_device, compute_type=cfg.asr_compute_type, cpu_threads=cfg.asr_cpu_threads
    )
    segs, info = model.transcribe(
        str(audio),
        language="zh",
        beam_size=cfg.asr_beam_size,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=_prompt(title),
    )
    out = []
    for s in segs:  # 生成器，迭代时才真正解码
        out.append({"start": s.start, "end": s.end, "text": s.text})
    return out


def _api(cfg: Config, audio: Path, title: str, cache: Path) -> list[dict]:
    key = cfg.asr_key()
    if not key:
        raise SystemExit(f"[asr] backend=api 但环境变量 {cfg.asr_api_key_env} 为空")
    # 压到单声道 16k 48kbps mp3，40 分钟约 14MB，低于常见 25MB 上限
    small = cache / "audio_asr.mp3"
    if not small.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", "16000", "-b:a", "48k", str(small)],
            check=True,
        )
    with open(small, "rb") as f:
        r = requests.post(
            cfg.asr_api_base.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (small.name, f, "audio/mpeg")},
            data={
                "model": cfg.asr_api_model,
                "language": "zh",
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
                "prompt": _prompt(title),
                "temperature": "0",
            },
            timeout=600,
        )
    r.raise_for_status()
    j = r.json()
    if not j.get("segments"):
        raise RuntimeError("ASR 接口没返回 segments（该模型可能不支持 verbose_json 段级时间戳）")
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in j["segments"]]


def transcribe(cfg: Config, audio: Path, title: str, cache: Path) -> list[dict]:
    cached = cache / "transcript.json"
    hit = read_json(cached)
    if hit:
        return hit
    backend = cfg.asr_resolved
    model = {"mlx": cfg.asr_mlx_model, "local": cfg.asr_model, "api": cfg.asr_api_model}.get(backend, "?")
    log(f"转写中（{backend} / {model}）…")
    if backend == "mlx":
        raw = _mlx(cfg, audio, title)
    elif backend == "local":
        raw = _local(cfg, audio, title)
    elif backend == "api":
        raw = _api(cfg, audio, title, cache)
    else:
        raise SystemExit(f"[asr] backend 不认识：{backend}（auto / mlx / local / api）")
    segs = _clean(raw)
    write_json(cached, segs)
    write_srt(segs, cache / "transcript.srt")
    return segs


def write_srt(segs: list[dict], path: Path) -> None:
    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, s in enumerate(segs, 1):
        lines += [str(i), f"{ts(s['start'])} --> {ts(s['end'])}", s["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def as_lines(segs: list[dict]) -> str:
    """喂给 LLM 的转写：每段一行 [mm:ss] 文本。"""
    return "\n".join(f"[{fmt_ts(s['start'])}] {s['text']}" for s in segs)
