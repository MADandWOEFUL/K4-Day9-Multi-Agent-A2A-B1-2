"""CustomerAgent — resolves customer identity and full order history."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import Agent, cap, trace
from .data_loader import DataIndex


class CustomerAgent(Agent):
    name = "customer_agent"

    def __init__(self, index: DataIndex) -> None:
        self.index = index

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        order_id: str = state["claimed_order_id"]
        order = self.index.order(order_id)
        if order is None:
            trace(self.name, case_id=state["case_id"], status="order_not_found")
            state["customer_unique_id"] = None
            state["related_order_ids"] = []
            state["_customer_status"] = "missing"
            return state

        cust = self.index.customer(order["customer_id"])
        cuid = cust["customer_unique_id"] if cust else None
        related: List[str] = []
        if cuid:
            for oid in self.index.orders_for_customer_unique(cuid):
                if oid != order_id:
                    related.append(oid)
        # stable order: sort
        related = sorted(related)

        state["customer_unique_id"] = cuid
        state["related_order_ids"] = cap(related, 5)
        state["_customer_status"] = "ok"
        trace(
            self.name,
            case_id=state["case_id"],
            customer_unique_id=cuid,
            related_count=len(related),
        )
        return state
