"""Base types: Agent, CaseState, trace helpers, money/time helpers."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logging"
OUT_DIR = ROOT / "output"
IN_DIR = ROOT / "input"
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

_trace_lock = threading.Lock()
_TRACE_PATH = LOG_DIR / "trace.jsonl"


def _trace_append(line: Dict[str, Any]) -> None:
    with _trace_lock:
        with _TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def trace(event: str, **fields: Any) -> None:
    """Append a structured trace line. Lightweight, safe across agents."""
    record = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "event": event,
        **fields,
    }
    _trace_append(record)


def reset_trace() -> None:
    """Truncate trace.jsonl — called once at the start of a pipeline run."""
    with _trace_lock:
        _TRACE_PATH.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def round2(x: Optional[float]) -> Optional[float]:
    """Round to 2 decimals or pass through None."""
    if x is None:
        return None
    return round(float(x), 2)


def parse_dt(s: Optional[str]) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD HH:MM:SS' or None → datetime; tolerate trailing/leading ws."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    # Some CSVs use only date; pad to midnight
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def format_dt(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime → 'YYYY-MM-DD HH:MM:SS' or None."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def hours_between(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    """(b - a) in hours, rounded to 2 decimals; None if either side missing."""
    if a is None or b is None:
        return None
    return round2((b - a).total_seconds() / 3600.0)


def cap(seq: Iterable[Any], n: int) -> List[Any]:
    out = list(seq)
    return out[:n]


# ---------------------------------------------------------------------------
# Agent base
# ---------------------------------------------------------------------------


class Agent:
    name: str = "Agent"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    # helpers
    @staticmethod
    def _emit(state: Dict[str, Any], **fields: Any) -> None:
        state.update(fields)
