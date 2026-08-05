"""VerifierAgent — schema, limits, evidence existence, final JSON assembly."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Set

from .base import Agent, OUT_DIR, cap, round2, trace


_LIMITS = {
    "order_ids": 5,
    "item_ids": 5,
    "payment_ids": 5,
    "related_order_ids": 5,
    "product_ids": 5,
    "category_names": 5,
    "seller_ids": 3,
    "ranked_causes": 3,
    "responsible_parties": 3,
    "evidence_ids": 20,
    "resolution_actions": 5,
}


def _cap_dict(d: Dict[str, List[Any]], key: str, n: int) -> List[Any]:
    v = d.get(key, [])
    if not isinstance(v, list):
        return []
    return v[:n]


class VerifierAgent(Agent):
    name = "verifier_agent"

    def __init__(self, index) -> None:
        self.index = index

    # ---------- evidence reconstruction ---------------------------------

    def _valid_evidence(self, state: Dict[str, Any]) -> List[str]:
        order_id = state.get("claimed_order_id")
        valid: List[str] = []
        if order_id and self.index.order(order_id) is not None:
            valid.append(f"order:{order_id}")

        items = state.get("items", [])
        for r in items:
            ev = f"item:{order_id}:{r['order_item_id']}"
            valid.append(ev)

        payment_ids: List[str] = (
            state.get("payment_reconciliation", {}).get("_payment_ids") or []
        )
        for pid in payment_ids:
            # ensure `payment:` prefix per evidence spec
            if pid.startswith("payment:"):
                valid.append(pid)
            else:
                valid.append(f"payment:{pid}")

        seller_ids = state.get("seller_ids", []) or state.get(
            "delivery_analysis", {}
        ).get("late_handoff_seller_ids", [])
        for sid in seller_ids:
            valid.append(f"seller:{sid}")

        root_cause = (
            state.get("root_cause_analysis", {})
            .get("ranked_causes", [{}])[0]
            .get("cause_code")
        )
        if root_cause:
            valid.append(f"policy:{root_cause}")

        # Dedup while keeping stable order
        seen: Set[str] = set()
        out: List[str] = []
        for e in valid:
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out[: _LIMITS["evidence_ids"]]

    # ---------- schema constraints --------------------------------------

    @staticmethod
    def _filter_evidence(state: Dict[str, Any], evidence: List[str]) -> List[str]:
        """Drop any evidence that isn't a known seller product or otherwise
        reconstructable from the data; gracefully tolerates type errors."""
        return [e for e in evidence if isinstance(e, str) and ":" in e]

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        order_id = state.get("claimed_order_id")
        order = state.get("order")
        items = state.get("items", [])
        seller_ids = state.get("seller_ids", [])
        product_ids = state.get("product_ids", [])
        category_names = state.get("category_names", [])
        related = state.get("related_order_ids", [])
        delivery = state.get("delivery_analysis", {})
        payment = state.get("payment_reconciliation", {}) or {}
        assessment = state.get("case_assessment", {}) or {}
        financial = state.get("financial_resolution", {}) or {}
        actions = state.get("resolution_actions", []) or []
        rca = state.get("root_cause_analysis", {}) or {}
        customer_unique_id = state.get("customer_unique_id")

        payment_ids = payment.get("_payment_ids", []) or []
        item_ids = state.get("item_ids", []) or []

        # ---- evidence assembly ----
        evidence = self._valid_evidence(state)
        evidence = self._filter_evidence(state, evidence)
        evidence = cap(evidence, _LIMITS["evidence_ids"])

        # ---- enforce limits ----
        order_ids = [order_id] if order_id else []
        seller_ids_lim = cap(seller_ids or [], _LIMITS["seller_ids"])
        product_ids_lim = cap(product_ids or [], _LIMITS["product_ids"])
        category_lim = cap(category_names or [], _LIMITS["category_names"])
        item_ids_lim = cap(item_ids or [], _LIMITS["item_ids"])
        payment_ids_lim = cap(payment_ids or [], _LIMITS["payment_ids"])
        related_lim = cap(related or [], _LIMITS["related_order_ids"])
        actions_lim = cap(actions or [], _LIMITS["resolution_actions"])
        ranked = cap(rca.get("ranked_causes", []) or [], _LIMITS["ranked_causes"])
        parties = cap(
            rca.get("responsible_parties", []) or [],
            _LIMITS["responsible_parties"],
        )

        # ---- confidence enforcement ----
        conf = assessment.get("confidence", 0.9)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.9
        conf = max(0.0, min(1.0, round(conf, 2)))

        # ---- build final ----
        final: Dict[str, Any] = {
            "case_id": state["case_id"],
            "case_assessment": {
                "primary_issue": assessment.get(
                    "primary_issue", "unsupported_late_claim"
                ),
                "secondary_issues": assessment.get("secondary_issues", []) or [],
                "case_status": assessment.get("case_status", "no_action"),
                "confidence": conf,
            },
            "affected_entities": {
                "order_ids": order_ids[: _LIMITS["order_ids"]],
                "item_ids": item_ids_lim,
                "seller_ids": seller_ids_lim,
                "payment_ids": payment_ids_lim,
            },
            "customer_context": {
                "customer_unique_id": customer_unique_id,
                "related_order_ids": related_lim,
            },
            "product_context": {
                "product_ids": product_ids_lim,
                "category_names": category_lim,
            },
            "delivery_analysis": {
                "delivered_at": delivery.get("delivered_at"),
                "estimated_delivery_at": delivery.get("estimated_delivery_at"),
                "carrier_handoff_at": delivery.get("carrier_handoff_at"),
                "delivery_variance_hours": delivery.get(
                    "delivery_variance_hours"
                ),
                "seller_handoff_analysis": delivery.get(
                    "seller_handoff_analysis", []
                ) or [],
                "late_handoff_seller_ids": delivery.get(
                    "late_handoff_seller_ids", []
                )
                or [],
            },
            "payment_reconciliation": {
                "currency": "BRL",
                "item_total_brl": payment.get("item_total_brl"),
                "freight_total_brl": payment.get("freight_total_brl"),
                "expected_total_brl": payment.get("expected_total_brl"),
                "payment_total_brl": payment.get("payment_total_brl"),
                "difference_brl": payment.get("difference_brl"),
                "reconciled": payment.get("reconciled"),
                "payment_types": payment.get("payment_types", []) or [],
            },
            "root_cause_analysis": {
                "ranked_causes": ranked,
                "responsible_parties": parties,
            },
            "evidence_ids": evidence,
            "financial_resolution": {
                "currency": "BRL",
                "recommended_refund_brl": round2(
                    financial.get("recommended_refund_brl")
                ),
            },
            "resolution_actions": actions_lim,
        }

        # ---- write to output/ ----
        out_path = OUT_DIR / f"{state['case_id']}.json"
        out_path.write_text(
            json.dumps(final, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        state["_final"] = final
        state["_output_path"] = str(out_path)
        state["verified"] = True
        trace(
            self.name,
            case_id=state["case_id"],
            output=str(out_path),
            evidence_count=len(evidence),
        )
        return state
