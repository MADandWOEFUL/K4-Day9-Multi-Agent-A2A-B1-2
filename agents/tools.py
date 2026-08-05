"""ToolLayer — MCP-style tool interface wrapping data access.

Tools are stateless, JSON-serializable, and emit a ToolResult envelope.
Agents call tools, never raw CSVs. This makes the boundary between
"compute" (deterministic, in agents) and "data" (deterministic, here)
explicit and unit-testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .data_loader import DataIndex


@dataclass
class ToolResult:
    """Standard envelope every tool returns."""

    ok: bool
    name: str
    payload: Any = None
    error: Optional[str] = None
    span_id: str = ""
    started_at: str = ""
    ended_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "name": self.name,
            "payload": self.payload,
            "error": self.error,
            "span_id": self.span_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


@dataclass
class ToolLayer:
    """All tool entry-points an agent may call."""

    index: DataIndex
    trace_log: List[Dict[str, Any]] = field(default_factory=list)

    # ---------- order / customer / seller tools ----------

    def get_order(self, order_id: str) -> ToolResult:
        o = self.index.order(order_id)
        return ToolResult(ok=o is not None, name="get_order", payload=o)

    def get_items(self, order_id: str) -> ToolResult:
        items = self.index.items(order_id)
        # stable order by order_item_id
        items_sorted = sorted(items, key=lambda r: int(r.get("order_item_id", "0") or 0))
        return ToolResult(ok=True, name="get_items", payload=items_sorted)

    def get_payments(self, order_id: str) -> ToolResult:
        payments = self.index.payments(order_id)
        payments_sorted = sorted(
            payments, key=lambda r: int(r.get("payment_sequential", "0") or 0)
        )
        return ToolResult(ok=True, name="get_payments", payload=payments_sorted)

    def get_customer(self, customer_id: str) -> ToolResult:
        c = self.index.customer(customer_id)
        return ToolResult(ok=c is not None, name="get_customer", payload=c)

    def get_orders_for_customer_unique(self, customer_unique_id: str) -> ToolResult:
        ids = sorted(self.index.orders_for_customer_unique(customer_unique_id))
        return ToolResult(
            ok=True, name="get_orders_for_customer_unique", payload=ids
        )

    def get_product(self, product_id: str) -> ToolResult:
        p = self.index.product(product_id)
        return ToolResult(ok=p is not None, name="get_product", payload=p)

    def get_seller(self, seller_id: str) -> ToolResult:
        s = self.index.seller(seller_id)
        return ToolResult(ok=s is not None, name="get_seller", payload=s)

    def category_english(self, pt_name: Optional[str]) -> ToolResult:
        if not pt_name:
            return ToolResult(ok=True, name="category_english", payload=None)
        return ToolResult(
            ok=True,
            name="category_english",
            payload=self.index.category_english(pt_name),
        )

    # ---------- evidence-existence check ----------

    def evidence_exists(self, evidence_id: str) -> bool:
        """Reconstruct an evidence_id from the data; True if it exists."""
        try:
            kind, _, rest = evidence_id.partition(":")
            if kind == "order":
                return self.index.order(rest) is not None
            if kind == "seller":
                return self.index.seller(rest) is not None
            if kind in ("item", "payment"):
                parts = rest.split(":")
                if len(parts) != 2:
                    return False
                oid, seq = parts
                if kind == "item":
                    items = self.index.items(oid)
                    return any(i.get("order_item_id") == seq for i in items)
                payments = self.index.payments(oid)
                return any(
                    p.get("payment_sequential") == seq for p in payments
                )
            if kind == "policy":
                # policy codes are an enum, not data — accept any non-empty string
                return bool(rest)
        except Exception:
            return False
        return False

    # ---------- time / money helpers (deterministic) ----------

    @staticmethod
    def parse_dt(s: Any) -> Optional[datetime]:
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def hours_between(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
        if a is None or b is None:
            return None
        return round((b - a).total_seconds() / 3600.0, 2)

    @staticmethod
    def fmt_dt(dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def to_float(v: Any) -> float:
        try:
            return float(v) if v not in (None, "") else 0.0
        except Exception:
            return 0.0

    def call(self, name: str, *args: Any, **kwargs: Any) -> ToolResult:
        """Generic tool dispatcher (used by tests / dynamic router)."""
        fn = getattr(self, name, None)
        if not fn or not callable(fn):
            return ToolResult(ok=False, name=name, error=f"unknown tool {name}")
        started = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        try:
            res = fn(*args, **kwargs)
            res.started_at = started
            res.ended_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            self.trace_log.append(res.to_dict())
            return res
        except Exception as e:
            res = ToolResult(ok=False, name=name, error=repr(e), started_at=started)
            res.ended_at = datetime.utcnow().isoformat("seconds") + "Z"
            self.trace_log.append(res.to_dict())
            return res