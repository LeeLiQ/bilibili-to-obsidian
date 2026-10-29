"""OpenAI 兼容 chat/completions 的最小客户端（不依赖 openai SDK，任何兼容端点都能用）。"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import requests

from .config import Config


def image_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def chat(cfg: Config, messages: list[dict]) -> str:
    key = cfg.llm_key()
    if not key:
        raise SystemExit(f"环境变量 {cfg.llm_api_key_env} 为空（[llm] api_key_env）")
    url = cfg.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.llm_auth_header.lower() == "authorization":
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers[cfg.llm_auth_header] = key
    body: dict = {"model": cfg.llm_model, "messages": messages}
    if cfg.llm_json_mode:
        body["response_format"] = {"type": "json_object"}
    if cfg.llm_temperature is not None:
        body["temperature"] = cfg.llm_temperature

    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=cfg.llm_timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
                time.sleep(4 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
            return r.json()["choices"][0]["message"]["content"] or ""
        except requests.RequestException as e:
            last = e
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败：{last}")


def parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            return json.loads(m.group(0))
        raise
