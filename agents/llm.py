"""Thin OpenRouter client for nvidia/nemotron-nano-9b-v2:free.

Reads OPENROUTER_API_KEY from environment (.env loaded by coordinator).
Falls back to a deterministic local stub when the key is missing or the
call fails so the pipeline can still produce policy-correct outputs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - requests should be installed
    requests = None  # type: ignore

from .base import trace

ROOT = Path(__file__).resolve().parent.parent

MODEL_NAME = "nvidia/nemotron-nano-9b-v2:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _load_key() -> Optional[str]:
    """Read OPENROUTER_API_KEY from env, also try .env file."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and key != "YOUR_OPENROUTER_KEY_HERE":
        return key
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "OPENROUTER_API_KEY":
                v = v.strip().strip('"').strip("'")
                if v and v != "YOUR_OPENROUTER_KEY_HERE":
                    return v
    return None


def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    max_retries: int = 2,
    timeout: float = 60.0,
) -> Optional[Dict[str, Any]]:
    """Call the LLM and parse the reply as JSON.

    Returns None on any failure (logged). The caller must always have a
    deterministic fallback.
    """
    key = _load_key()
    if key is None or requests is None:
        trace("llm.skip", reason="no_api_key_or_requests")
        return None

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://k4-day9-multi-agent.local",
        "X-Title": "K4 Day9 Multi-Agent A2A",
    }
    payload: Dict[str, Any] = {
        "model": MODEL_NAME,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    last_err: Optional[str] = None
    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            resp = requests.post(
                OPENROUTER_URL, headers=headers, json=payload, timeout=timeout
            )
            elapsed = time.time() - t0
            if resp.status_code >= 400:
                last_err = f"http_{resp.status_code}: {resp.text[:300]}"
                trace(
                    "llm.http_error",
                    attempt=attempt,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                continue
            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            trace(
                "llm.ok",
                model=MODEL_NAME,
                elapsed=round(elapsed, 2),
                tokens=data.get("usage", {}),
            )
            try:
                return json.loads(content)
            except Exception as e:
                last_err = f"json_parse: {e}"
                trace("llm.parse_error", content_preview=content[:300])
        except Exception as e:
            last_err = repr(e)
            trace("llm.exception", attempt=attempt, error=repr(e))
        time.sleep(0.5 * (attempt + 1))
    trace("llm.failed", last_err=last_err)
    return None