"""PolicyAgent — applies EC_POLICY_V2 priority table and proposes case_assessment.

Two-stage:
  1. deterministic_decide(): pick the policy-correct primary issue,
     secondary issues, refund, actions, ranked causes, responsible parties.
  2. llm_review(): ask the LLM to confirm/annotate with a confidence score.
     When the LLM is unavailable or disagrees with the deterministic pick,
     the deterministic pick wins.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Agent, cap, round2, trace
from .llm import chat_json


PRIMARY_RULES: List[Dict[str, Any]] = [
    {
        "key": "canceled_order_paid",
        "root_cause": "ORDER_CANCELED_AFTER_PAYMENT",
        "action": "issue_full_refund",
        "refund": "payment_total_brl",
        "responsible": ("platform", "OLIST_PLATFORM"),
        "needs_status": "canceled",
    },
    {
        "key": "unavailable_order_paid",
        "root_cause": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "action": "issue_full_refund",
        "refund": "payment_total_brl",
        "responsible": ("platform", "OLIST_PLATFORM"),
        "needs_status": "unavailable",
    },
    {
        "key": "late_delivery_seller",
        "root_cause": "SELLER_HANDOFF_AFTER_LIMIT",
        "action": "refund_freight",
        "refund": "freight_total_brl",
        "responsible": ("seller", None),  # seller_id from late handoff list
        "needs": "delivered_after_estimate and any_late_handoff",
    },
    {
        "key": "late_delivery_logistics",
        "root_cause": "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "action": "refund_freight",
        "refund": "freight_total_brl",
        "responsible": ("logistics_provider", "LOGISTICS_PROVIDER"),
        "needs": "delivered_after_estimate and not any_late_handoff",
    },
    {
        "key": "valid_split_payment",
        "root_cause": "MULTIPLE_PAYMENTS_RECONCILED",
        "action": "explain_valid_split_payment",
        "refund": 0.0,
        "responsible": (None, None),
        "needs": "multiple_payments and reconciled",
    },
    {
        "key": "unsupported_late_claim",
        "root_cause": "DELIVERY_WITHIN_ESTIMATE",
        "action": "reject_late_refund",
        "refund": 0.0,
        "responsible": (None, None),
        "needs": "not delivered_after_estimate and reconciled",
    },
]


SECONDARY_RULES: List[Dict[str, Any]] = [
    ("multi_item_order", lambda s: len(s.get("items", [])) >= 2),
    ("multi_seller_order", lambda s: len(s.get("seller_ids", [])) >= 2),
    (
        "split_payment",
        lambda s: len(
            s.get("payment_reconciliation", {}).get("_payment_ids", [])
        )
        >= 2,
    ),
    (
        "repeat_customer",
        lambda s: len([x for x in s.get("related_order_ids", []) if x])
        >= 1,
    ),
    (
        "multiple_categories",
        lambda s: len(s.get("category_names", [])) >= 2,
    ),
]


def _payment_total_brl(state: Dict[str, Any]) -> float:
    pr = state.get("payment_reconciliation") or {}
    v = pr.get("payment_total_brl")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _freight_total_brl(state: Dict[str, Any]) -> float:
    pr = state.get("payment_reconciliation") or {}
    v = pr.get("freight_total_brl")
    return float(v) if isinstance(v, (int, float)) else 0.0


class PolicyAgent(Agent):
    name = "policy_agent"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        order = state.get("order")
        delivery = state.get("delivery_analysis") or {}
        payment = state.get("payment_reconciliation") or {}

        # ---------- primary issue pick ----------
        status = (order.get("order_status") if order else "") or ""
        payment_total = _payment_total_brl(state)
        freight_total = _freight_total_brl(state)
        delivered_after_est = bool(delivery.get("_delivered_after_estimate"))
        any_late_handoff = bool(delivery.get("late_handoff_seller_ids"))
        payment_count = len(payment.get("_payment_ids", []))
        reconciled = bool(payment.get("reconciled"))

        primary: Optional[Dict[str, Any]] = None
        refund_value: float = 0.0
        primary_action: str = "reject_late_refund"

        for rule in PRIMARY_RULES:
            ok = False
            if rule["key"] in ("canceled_order_paid", "unavailable_order_paid"):
                ok = status == rule["needs_status"] and payment_total > 0
            elif rule["key"] == "late_delivery_seller":
                ok = delivered_after_est and any_late_handoff
            elif rule["key"] == "late_delivery_logistics":
                ok = delivered_after_est and not any_late_handoff
            elif rule["key"] == "valid_split_payment":
                ok = payment_count >= 2 and reconciled
            elif rule["key"] == "unsupported_late_claim":
                ok = (not delivered_after_est) and reconciled
            if ok:
                primary = rule
                # compute refund value
                if rule["refund"] == "payment_total_brl":
                    refund_value = payment_total
                elif rule["refund"] == "freight_total_brl":
                    refund_value = freight_total
                else:
                    refund_value = float(rule["refund"])
                primary_action = rule["action"]
                break

        # Defensive default if nothing matched (e.g. order missing)
        if primary is None:
            primary = {
                "key": "unsupported_late_claim",
                "root_cause": "DELIVERY_WITHIN_ESTIMATE",
                "action": "reject_late_refund",
                "responsible": (None, None),
            }
            refund_value = 0.0
            primary_action = "reject_late_refund"

        primary_issue = primary["key"]
        root_cause_code = primary["root_cause"]

        # ---------- secondary issues ----------
        secondary: List[str] = []
        for key, pred in SECONDARY_RULES:
            try:
                if pred(state):
                    secondary.append(key)
            except Exception:
                pass

        # ---------- responsible parties ----------
        responsible: List[Dict[str, Any]] = []
        party_type, party_id_override = primary["responsible"]
        if party_type == "seller":
            for sid in delivery.get("late_handoff_seller_ids", []) or state.get(
                "seller_ids", []
            ):
                responsible.append({"party_type": "seller", "party_id": sid})
        elif party_type == "platform":
            responsible.append({"party_type": "platform", "party_id": "OLIST_PLATFORM"})
        elif party_type == "logistics_provider":
            responsible.append(
                {"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}
            )
        # cap to 3
        responsible = responsible[:3]

        # ---------- actions ----------
        actions: List[str] = [primary_action]
        if primary_issue in ("late_delivery_seller",):
            if "review_seller_handoff" not in actions:
                actions.append("review_seller_handoff")
        if primary_issue == "late_delivery_logistics":
            if "review_carrier_delay" not in actions:
                actions.append("review_carrier_delay")
        if refund_value > 0 and "verify_refund_completion" not in actions:
            actions.append("verify_refund_completion")
        if (
            len(state.get("seller_ids", [])) >= 2
            and "coordinate_multi_seller_case" not in actions
        ):
            actions.append("coordinate_multi_seller_case")
        if (
            primary_issue != "valid_split_payment"
            and payment_count >= 2
            and "verify_payment_allocation" not in actions
        ):
            actions.append("verify_payment_allocation")
        actions = cap(actions, 5)

        # ---------- case_status ----------
        case_status = "action_required" if refund_value > 0 else "no_action"

        # ---------- confidence via LLM (deterministic fallback 0.90) ----------
        confidence = 0.90
        llm_payload = self._llm_review(state, primary_issue, secondary, refund_value)
        if llm_payload and isinstance(llm_payload.get("confidence"), (int, float)):
            confidence = max(0.0, min(1.0, round(float(llm_payload["confidence"]), 2)))

        # ---------- ranked_causes ----------
        ranked = [{"cause_code": root_cause_code, "rank": 1}]
        # add any secondary root-cause hints as ranks 2 and 3 (optional)
        # using the same delivery-cause for non-topping ranks keeps the list
        # policy-aligned.
        if primary_issue == "late_delivery_seller":
            ranked.append({"cause_code": "DELIVERY_WITHIN_ESTIMATE", "rank": 2})
        elif primary_issue == "late_delivery_logistics":
            ranked.append({"cause_code": "DELIVERY_WITHIN_ESTIMATE", "rank": 2})

        state["case_assessment"] = {
            "primary_issue": primary_issue,
            "secondary_issues": secondary,
            "case_status": case_status,
            "confidence": confidence,
        }
        state["financial_resolution"] = {
            "currency": "BRL",
            "recommended_refund_brl": round2(refund_value),
        }
        state["resolution_actions"] = actions
        state["root_cause_analysis"] = {
            "ranked_causes": ranked[:3],
            "responsible_parties": responsible,
        }
        trace(
            self.name,
            case_id=state["case_id"],
            primary_issue=primary_issue,
            refund=round2(refund_value),
            secondary=secondary,
            llm_used=bool(llm_payload),
        )
        return state

    # --- helpers ---------------------------------------------------------

    def _llm_review(
        self,
        state: Dict[str, Any],
        primary: str,
        secondary: List[str],
        refund: float,
    ) -> Optional[Dict[str, Any]]:
        sys_prompt = (
            "Bạn là một reviewer chính sách e-commerce. Nhiệm vụ duy nhất là "
            "trả về JSON {confidence: number 0..1, notes: string}. Không thay "
            "đổi primary_issue hoặc refund — agent quyết định quyết định đã "
            "xong rồi. Confidence cao khi facts khớp primary_issue đã chọn."
        )
        user_prompt = (
            f"case_id: {state['case_id']}\n"
            f"primary_issue (đã chọn): {primary}\n"
            f"secondary_issues: {secondary}\n"
            f"refund_brl: {refund}\n"
            f"facts: payment_total={state.get('payment_reconciliation', {}).get('payment_total_brl')}, "
            f"freight_total={state.get('payment_reconciliation', {}).get('freight_total_brl')}, "
            f"reconciled={state.get('payment_reconciliation', {}).get('reconciled')}, "
            f"delivered_after_estimate={state.get('delivery_analysis', {}).get('_delivered_after_estimate')}, "
            f"late_handoff_sellers={state.get('delivery_analysis', {}).get('late_handoff_seller_ids')}, "
            f"order_status={state.get('order', {}).get('order_status')}\n"
            "Trả JSON: {\"confidence\": 0.xx, \"notes\": \"...\"}"
        )
        return chat_json(sys_prompt, user_prompt)
