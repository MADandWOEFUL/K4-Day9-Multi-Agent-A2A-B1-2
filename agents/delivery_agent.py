"""DeliveryAgent — delivery variance and per-seller handoff variance."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Agent, format_dt, hours_between, parse_dt, round2, trace


class DeliveryAgent(Agent):
    name = "delivery_agent"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        order = state.get("order")
        items = state.get("items", [])

        if order is None:
            state["delivery_analysis"] = {
                "delivered_at": None,
                "estimated_delivery_at": None,
                "carrier_handoff_at": None,
                "delivery_variance_hours": None,
                "seller_handoff_analysis": [],
                "late_handoff_seller_ids": [],
            }
            trace(self.name, case_id=state["case_id"], status="no_order")
            return state

        delivered_at = parse_dt(order.get("order_delivered_customer_date"))
        estimated_at = parse_dt(order.get("order_estimated_delivery_date"))
        carrier_at = parse_dt(order.get("order_delivered_carrier_date"))

        delivered_after_estimate = (
            delivered_at is not None
            and estimated_at is not None
            and delivered_at > estimated_at
        )

        delivery_variance = hours_between(estimated_at, delivered_at)

        # per-seller handoff analysis: bucket items by seller_id, take min
        # shipping_limit_date, compare to carrier_handoff.
        per_seller: Dict[str, Dict[str, Any]] = {}
        for r in items:
            sid = r.get("seller_id")
            if not sid:
                continue
            limit_dt = parse_dt(r.get("shipping_limit_date"))
            entry = per_seller.setdefault(
                sid,
                {"shipping_limit_at": None, "min_limit_dt": None},
            )
            if limit_dt is not None and (
                entry["min_limit_dt"] is None or limit_dt < entry["min_limit_dt"]
            ):
                entry["min_limit_dt"] = limit_dt
                entry["shipping_limit_at"] = limit_dt

        seller_handoff_analysis: List[Dict[str, Any]] = []
        late_handoff_seller_ids: List[str] = []
        for sid in sorted(per_seller.keys()):
            entry = per_seller[sid]
            limit_at = entry["shipping_limit_at"]
            variance = hours_between(limit_at, carrier_at)
            late = bool(
                carrier_at is not None
                and limit_at is not None
                and carrier_at > limit_at
            )
            seller_handoff_analysis.append(
                {
                    "seller_id": sid,
                    "shipping_limit_at": format_dt(limit_at),
                    "handoff_variance_hours": variance,
                    "late_handoff": late,
                }
            )
            if late:
                late_handoff_seller_ids.append(sid)

        state["delivery_analysis"] = {
            "delivered_at": format_dt(delivered_at),
            "estimated_delivery_at": format_dt(estimated_at),
            "carrier_handoff_at": format_dt(carrier_at),
            "delivery_variance_hours": delivery_variance,
            "seller_handoff_analysis": seller_handoff_analysis[:3],
            "late_handoff_seller_ids": late_handoff_seller_ids[:3],
            "_delivered_after_estimate": delivered_after_estimate,
        }
        trace(
            self.name,
            case_id=state["case_id"],
            delivered_after_estimate=delivered_after_estimate,
            late_handoff_count=len(late_handoff_seller_ids),
        )
        return state