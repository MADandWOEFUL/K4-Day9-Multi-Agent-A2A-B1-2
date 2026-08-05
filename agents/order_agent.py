"""OrderAgent — loads order row, items, sellers, products, categories."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import Agent, cap, trace
from .data_loader import DataIndex


class OrderAgent(Agent):
    name = "order_agent"

    def __init__(self, index: DataIndex) -> None:
        self.index = index

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        order_id: str = state["claimed_order_id"]
        order = self.index.order(order_id)

        if order is None:
            state["order"] = None
            state["items"] = []
            state["item_ids"] = []
            state["seller_ids"] = []
            state["product_ids"] = []
            state["category_names"] = []
            state["_order_status"] = "missing"
            trace(self.name, case_id=state["case_id"], status="missing")
            return state

        items = self.index.items(order_id)
        # Order rows are already stable by their CSV order; sort by order_item_id int.
        items_sorted = sorted(
            items, key=lambda r: int(r.get("order_item_id", "0") or 0)
        )

        item_ids: List[str] = [f"{order_id}:{r['order_item_id']}" for r in items_sorted]
        seller_ids: List[str] = []
        product_ids: List[str] = []
        category_names: List[str] = []
        seller_ids_seen = set()
        product_ids_seen = set()
        category_seen = set()

        for r in items_sorted:
            sid = r.get("seller_id")
            pid = r.get("product_id")
            if sid and sid not in seller_ids_seen:
                seller_ids_seen.add(sid)
                seller_ids.append(sid)
            if pid and pid not in product_ids_seen:
                product_ids_seen.add(pid)
                product_ids.append(pid)
            prod = self.index.product(pid) if pid else None
            if prod:
                pt = (prod.get("product_category_name") or "").strip()
                en = self.index.category_english(pt)
                # Normalize to English Title Case: translate PT -> EN if available,
                # then convert underscore_format to Title Case.
                # Examples: beleza_saude -> Health Beauty
                #           health_beauty -> Health Beauty
                #           furniture_decoracao -> Furniture Decoracao
                if not en or en == pt:
                    en = pt
                en = en.replace("_", " ").title() if en else None
                if en and en not in category_seen:
                    category_seen.add(en)
                    category_names.append(en)

        state["order"] = order
        state["items"] = items_sorted
        state["item_ids"] = cap(item_ids, 5)
        state["seller_ids"] = cap(seller_ids, 3)
        state["product_ids"] = cap(product_ids, 5)
        state["category_names"] = cap(category_names, 5)
        state["_order_status"] = "ok"
        trace(
            self.name,
            case_id=state["case_id"],
            item_count=len(items_sorted),
            seller_count=len(seller_ids),
        )
        return state
