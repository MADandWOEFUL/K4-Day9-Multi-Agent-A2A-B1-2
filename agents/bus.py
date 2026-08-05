"""MessageBus — A2A-style typed message passing between agents.

Every interaction in the supervisor-worker pipeline is a typed
AgentMessage with: kind, sender, receiver, span_id, timestamp, payload.
The bus also emits a span tree (parent → child) so trace.jsonl becomes
replayable for debugging.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import LOG_DIR


VALID_KINDS = {
    "task.assign",      # supervisor -> worker
    "task.result",      # worker -> supervisor
    "tool.call",        # worker -> tools
    "tool.result",      # tools -> worker
    "plan.update",      # supervisor internal
    "case.end",         # supervisor -> log
    "verification",     # verifier -> supervisor
}


@dataclass
class AgentMessage:
    kind: str
    sender: str
    receiver: str
    span_id: str
    parent_span_id: str = ""
    payload: Any = None
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "sender": self.sender,
            "receiver": self.receiver,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "timestamp": self.timestamp,
            "payload": _jsonable(self.payload),
        }


def _jsonable(o: Any) -> Any:
    """Convert arbitrary object to a JSON-serializable form."""
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (datetime,)):
        return o.isoformat()
    if hasattr(o, "to_dict"):
        return _jsonable(o.to_dict())
    return repr(o)


class MessageBus:
    """Thread-safe in-process bus + append-only trace log."""

    def __init__(self, log_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._messages: List[AgentMessage] = []
        self._log_path = log_path or (LOG_DIR / "trace.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate the trace file at bus startup so each pipeline run is
        # independent and replayable from a single file.
        self._log_path.write_text("", encoding="utf-8")

    def publish(
        self,
        kind: str,
        sender: str,
        receiver: str,
        payload: Any = None,
        parent_span_id: str = "",
        span_id: Optional[str] = None,
    ) -> AgentMessage:
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown kind: {kind}")
        msg = AgentMessage(
            kind=kind,
            sender=sender,
            receiver=receiver,
            span_id=span_id or uuid.uuid4().hex[:12],
            parent_span_id=parent_span_id,
            payload=payload,
            timestamp=datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
        )
        with self._lock:
            self._messages.append(msg)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
        return msg

    @property
    def messages(self) -> List[AgentMessage]:
        return list(self._messages)

    def span_tree(self) -> Dict[str, List[str]]:
        """Return a map of parent_span_id -> [child_span_ids]."""
        tree: Dict[str, List[str]] = {}
        for m in self._messages:
            if m.parent_span_id:
                tree.setdefault(m.parent_span_id, []).append(m.span_id)
        return tree

    def messages_for_span(self, span_id: str) -> List[AgentMessage]:
        return [m for m in self._messages if m.span_id == span_id or m.parent_span_id == span_id]
