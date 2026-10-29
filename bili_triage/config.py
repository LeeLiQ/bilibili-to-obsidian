"""读取 config.toml。密钥只从环境变量取，不进配置文件。

默认值按设计说明 §6：主力跑在 Mac（个人设备，登录 B 站、mlx-whisper），代码跨平台。
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # py>=3.11
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _p(s: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(s)))


@dataclass
class Config:
    vault: Path
    # §6 Mac vault 结构
    inbox: str = "00-收件箱"
    saved: str = "20-资料"
    rejected: str = "90-弃"
    rejected_log: str = "分诊拒绝记录.md"
    attachments: str = "attachments"
    attach_prefix: str = "分诊-"          # attachments/分诊-<bvid>/
    worklog: str = ""                      # vault 内台账，留空不写

    cache_dir: Path = field(default_factory=lambda: Path.home() / ".bili-triage" / "cache")
    cache_days: int = 30

    cookies_from_browser: str = ""         # safari / chrome / firefox …（只在个人设备上配）
    cookies_file: Path | None = None
    max_height: int = 1080

    asr_backend: str = "auto"              # auto / mlx / local / api
    asr_mlx_model: str = "mlx-community/whisper-large-v3-turbo"
    asr_model: str = "large-v3-turbo"      # faster-whisper
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_beam_size: int = 1
    asr_cpu_threads: int = 0
    asr_api_base: str = "https://api.groq.com/openai/v1"
    asr_api_model: str = "whisper-large-v3-turbo"
    asr_api_key_env: str = "GROQ_API_KEY"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key_env: str = "OPENAI_API_KEY"
    llm_auth_header: str = "Authorization"
    llm_json_mode: bool = True
    llm_temperature: float | None = None
    llm_images_per_bucket: int = 2
    llm_max_images: int = 24
    llm_timeout: int = 240

    bucket_sec: int = 120

    fav_media_ids: list[int] = field(default_factory=list)
    fav_max_per_run: int = 5

    export_dir: Path = field(default_factory=lambda: Path.home() / "bili-to-pc")
    state_path: Path = field(default_factory=lambda: Path.home() / ".bili-triage" / "state.json")

    # ---- 派生 ----
    @property
    def inbox_dir(self) -> Path:
        return self.vault / self.inbox

    @property
    def saved_dir(self) -> Path:
        return self.vault / self.saved

    @property
    def rejected_path(self) -> Path:
        return self.vault / self.rejected / self.rejected_log

    @property
    def attach_root(self) -> Path:
        return self.vault / self.attachments

    def attach_dir(self, key: str) -> Path:
        return self.attach_root / f"{self.attach_prefix}{key}"

    @property
    def worklog_path(self) -> Path | None:
        return self.vault / self.worklog if self.worklog else None

    @property
    def asr_resolved(self) -> str:
        if self.asr_backend != "auto":
            return self.asr_backend
        return "mlx" if is_apple_silicon() else "local"

    def llm_key(self) -> str:
        return os.environ.get(self.llm_api_key_env, "")

    def asr_key(self) -> str:
        return os.environ.get(self.asr_api_key_env, "")


def find_config(explicit: str | None) -> Path:
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("BILI_TRIAGE_CONFIG"):
        cands.append(Path(os.environ["BILI_TRIAGE_CONFIG"]))
    cands += [Path.cwd() / "config.toml", PROJECT_ROOT / "config.toml", Path.home() / ".bili-triage" / "config.toml"]
    for c in cands:
        if c.is_file():
            return c
    raise SystemExit(
        "找不到 config.toml。复制 config.example.toml 为 config.toml 并修改，"
        "或用 --config / 环境变量 BILI_TRIAGE_CONFIG 指定。"
    )


def load_config(explicit: str | None = None) -> Config:
    path = find_config(explicit)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    v, c, b = raw.get("vault", {}), raw.get("cache", {}), raw.get("bilibili", {})
    a, l, t = raw.get("asr", {}), raw.get("llm", {}), raw.get("triage", {})
    fav, ex = raw.get("favorites", {}), raw.get("export", {})

    if not v.get("path"):
        raise SystemExit("config.toml 缺 [vault] path")
    cfg = Config(vault=_p(v["path"]))
    for k in ("inbox", "saved", "rejected", "rejected_log", "attachments", "attach_prefix", "worklog"):
        if k in v:
            setattr(cfg, k, v[k])

    if c.get("dir"):
        cfg.cache_dir = _p(c["dir"])
    cfg.cache_days = int(c.get("days", cfg.cache_days))

    cfg.cookies_from_browser = b.get("cookies_from_browser", cfg.cookies_from_browser)
    if b.get("cookies_file"):
        cfg.cookies_file = _p(b["cookies_file"])
    cfg.max_height = int(b.get("max_height", cfg.max_height))

    cfg.asr_backend = a.get("backend", cfg.asr_backend)
    cfg.asr_mlx_model = a.get("mlx_model", cfg.asr_mlx_model)
    cfg.asr_model = a.get("model", cfg.asr_model)
    cfg.asr_device = a.get("device", cfg.asr_device)
    cfg.asr_compute_type = a.get("compute_type", cfg.asr_compute_type)
    cfg.asr_beam_size = int(a.get("beam_size", cfg.asr_beam_size))
    cfg.asr_cpu_threads = int(a.get("cpu_threads", cfg.asr_cpu_threads))
    cfg.asr_api_base = a.get("api_base", cfg.asr_api_base)
    cfg.asr_api_model = a.get("api_model", cfg.asr_api_model)
    cfg.asr_api_key_env = a.get("api_key_env", cfg.asr_api_key_env)

    cfg.llm_base_url = l.get("base_url", cfg.llm_base_url)
    cfg.llm_model = l.get("model", cfg.llm_model)
    cfg.llm_api_key_env = l.get("api_key_env", cfg.llm_api_key_env)
    cfg.llm_auth_header = l.get("auth_header", cfg.llm_auth_header)
    cfg.llm_json_mode = bool(l.get("json_mode", cfg.llm_json_mode))
    cfg.llm_temperature = l.get("temperature", cfg.llm_temperature)
    cfg.llm_images_per_bucket = int(l.get("images_per_bucket", cfg.llm_images_per_bucket))
    cfg.llm_max_images = int(l.get("max_images", cfg.llm_max_images))
    cfg.llm_timeout = int(l.get("timeout", cfg.llm_timeout))

    cfg.bucket_sec = int(t.get("bucket_sec", cfg.bucket_sec))

    cfg.fav_media_ids = [int(x) for x in fav.get("media_ids", [])]
    cfg.fav_max_per_run = int(fav.get("max_per_run", cfg.fav_max_per_run))

    if ex.get("dir"):
        cfg.export_dir = _p(ex["dir"])
    return cfg
