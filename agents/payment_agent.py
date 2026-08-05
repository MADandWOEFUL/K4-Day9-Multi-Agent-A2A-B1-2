"""PaymentAgent — aggregates payment rows, reconciles against item_total + freight."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import Agent, cap, round2, trace


class PaymentAgent(Agent):
    name = "payment_agent"

    @staticmethod
    def _to_float(v: Any) -> float:
        try:
            return float(v) if v not in (None, "") else 0.0
        except Exception:
            return 0.0

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        order = state.get("order")
        items = state.get("items", [])

        if order is None:
            state["payment_reconciliation"] = {
                "currency": "BRL",
                "item_total_brl": None,
                "freight_total_brl": None,
                "expected_total_brl": None,
                "payment_total_brl": None,
                "difference_brl": None,
                "reconciled": None,
                "payment_types": [],
                "_payment_ids": [],
            }
            trace(self.name, case_id=state["case_id"], status="no_order")
            return state

        order_id = order["order_id"]
        payments = list(state.get("_payments_cache", []))  # coordinator may preload
        if not payments:
            # fallback: re-read from index via items path
            from .data_loader import DataIndex  # local import to avoid cycle

            idx: DataIndex = state["_index"]
            payments = idx.payments(order_id)

        if not items:
            state["payment_reconciliation"] = {
                "currency": "BRL",
                "item_total_brl": None,
                "freight_total_brl": None,
                "expected_total_brl": None,
                "payment_total_brl": None,
                "difference_brl": None,
                "reconciled": None,
                "payment_types": [],
                "_payment_ids": [],
            }
            trace(self.name, case_id=state["case_id"], status="no_items")
            return state

        # payments sorted by payment_sequential int
        payments_sorted = sorted(
            payments, key=lambda r: int(r.get("payment_sequential", "0") or 0)
        )

        item_total = sum(self._to_float(r.get("price")) for r in items)
        freight_total = sum(self._to_float(r.get("freight_value")) for r in items)
        payment_total = sum(self._to_float(r.get("payment_value")) for r in payments_sorted)
        expected_total = item_total + freight_total
        diff = payment_total - expected_total
        reconciled = abs(diff) <= 0.10
        payment_types: List[str] = []
        seen = set()
        for r in payments_sorted:
            t = (r.get("payment_type") or "").strip()
            if t and t not in seen:
                seen.add(t)
                payment_types.append(t)
        payment_ids = [
            f"{order_id}:{r['payment_sequential']}" for r in payments_sorted
        ]

        state["payment_reconciliation"] = {
            "currency": "BRL",
            "item_total_brl": round2(item_total),
            "freight_total_brl": round2(freight_total),
            "expected_total_brl": round2(expected_total),
            "payment_total_brl": round2(payment_total),
            "difference_brl": round2(diff),
            "reconciled": reconciled,
            "payment_types": payment_types,
            "_payment_ids": cap(payment_ids, 5),
        }
        trace(
            self.name,
            case_id=state["case_id"],
            payment_count=len(payments_sorted),
            reconciled=reconciled,
            diff=round2(diff),
        )
        return state
