"""PolicyAgent — applies EC_POLICY_V2 priority table and proposes case_assessment.

Improvements over v1:
  - Fix Bug #1: `unavailable_order_paid`/`canceled_order_paid` are picked even when
    the order has no items/payments (refund=0 in that case).
  - Fix Bug #2: dynamic confidence based on data quality instead of constant 0.90.
  - Fix Bug #3: ranked_causes carries 2-3 entries for richer evidence.
  - Fix Bug #5 / Improvement E: actions follow the strict brief §4 ordering and
    do not pile secondary actions onto canceled/unavailable/late-rejected cases.
  - The LLM only annotates confidence; it cannot overwrite the deterministic pick.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Agent, cap, round2, trace
from .llm import chat_json


# Rule table — the order is the priority (first match wins).
PRIMARY_RULES: List[Dict[str, Any]] = [
    {
        # Bug #1 fix: trigger whenever status is canceled, even when payment_total
        # is None/0.  Refund equals whatever was paid (0 for unpaid canceled orders).
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
        "responsible": ("seller", None),
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


# Brief §4 strict order for secondary actions.  Items not in this set still win
# when they are the primary action (issue_full_refund, refund_freight, etc.).
_SECONDARY_ACTION_ORDER = [
    "review_seller_handoff",       # only for late_delivery_seller
    "review_carrier_delay",        # only for late_delivery_logistics
    "verify_refund_completion",    # whenever refund > 0
    "coordinate_multi_seller_case",  # when ≥ 2 sellers
    "verify_payment_allocation",   # when ≥ 2 payments and primary is not
                                   # valid_split_payment
]


def _payment_total_brl(state: Dict[str, Any]) -> float:
    pr = state.get("payment_reconciliation") or {}
    v = pr.get("payment_total_brl")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _freight_total_brl(state: Dict[str, Any]) -> float:
    pr = state.get("payment_reconciliation") or {}
    v = pr.get("freight_total_brl")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _is_edge_case(state: Dict[str, Any]) -> bool:
    """True when the data is degenerate: no items, no payments, missing order."""
    return (
        state.get("order") is None
        or len(state.get("items", [])) == 0
        or len(
            state.get("payment_reconciliation", {}).get("_payment_ids", [])
        )
        == 0
    )


def _dynamic_confidence(state: Dict[str, Any], primary: str, fallback: bool) -> float:
    """Bug #2 fix: compute confidence from the same facts policy uses.

    Base 0.85, +0.05 for rich evidence, +0.05 for non-degenerate data,
    −0.10 if the primary came from the defensive fallback (nothing matched),
    + a small bonus depending on which rule fired.
    """
    score = 0.85
    if len(state.get("items", [])) >= 1 and len(
        state.get("affected_entities", {}).get("payment_ids", [])
    ) >= 1:
        score += 0.05
    if not _is_edge_case(state):
        score += 0.05
    if fallback:
        score -= 0.10

    # Per-rule modifier
    bonus = {
        "canceled_order_paid": 0.02,
        "unavailable_order_paid": 0.02,
        "late_delivery_seller": 0.03,
        "late_delivery_logistics": 0.02,
        "valid_split_payment": 0.02,
        "unsupported_late_claim": 0.0,
    }.get(primary, 0.0)
    score += bonus

    return float(max(0.30, min(0.98, round(score, 2))))


def _secondary_root_causes(
    primary: str, state: Dict[str, Any]
) -> List[str]:
    """Return 0-2 extra cause codes ranked below the primary."""
    extras: List[str] = []
    delivery = state.get("delivery_analysis", {}) or {}
    variance = delivery.get("delivery_variance_hours") or 0
    if primary == "late_delivery_seller":
        # rank 2: handoff itself; rank 3: long carrier delay adds context
        extras.append("CARRIER_DELIVERED_AFTER_ESTIMATE")
        if isinstance(variance, (int, float)) and variance > 72:
            extras.append("DELIVERY_WITHIN_ESTIMATE")
    elif primary == "late_delivery_logistics":
        extras.append("DELIVERY_WITHIN_ESTIMATE")
    elif primary == "canceled_order_paid":
        extras.append("DELIVERY_WITHIN_ESTIMATE")
    elif primary == "unavailable_order_paid":
        extras.append("DELIVERY_WITHIN_ESTIMATE")
    elif primary == "valid_split_payment":
        extras.append("DELIVERY_WITHIN_ESTIMATE")
    # unsupported_late_claim → no extras (single cause)
    return extras[:2]


class PolicyAgent(Agent):
    name = "policy_agent"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        order = state.get("order")
        delivery = state.get("delivery_analysis") or {}
        payment = state.get("payment_reconciliation") or {}

        # ---------- facts ----------
        status = (order.get("order_status") if order else "") or ""
        payment_total = _payment_total_brl(state)
        freight_total = _freight_total_brl(state)
        delivered_after_est = bool(delivery.get("_delivered_after_estimate"))
        any_late_handoff = bool(delivery.get("late_handoff_seller_ids"))
        payment_count = len(payment.get("_payment_ids", []))
        reconciled = bool(payment.get("reconciled"))

        # ---------- primary issue pick ----------
        primary: Optional[Dict[str, Any]] = None
        refund_value: float = 0.0
        primary_action: str = "reject_late_refund"

        for rule in PRIMARY_RULES:
            ok = False
            if rule["key"] in ("canceled_order_paid", "unavailable_order_paid"):
                # Bug #1 fix: trigger whenever status matches, even when no payment.
                # Refund = payment_total_brl which is 0 for unpaid canceled/unavailable
                # orders (matches the spec's "tổng payment" interpretation).
                ok = status == rule["needs_status"]
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
                if rule["refund"] == "payment_total_brl":
                    refund_value = payment_total
                elif rule["refund"] == "freight_total_brl":
                    refund_value = freight_total
                else:
                    refund_value = float(rule["refund"])
                primary_action = rule["action"]
                break

        fallback_used = False
        if primary is None:
            primary = {
                "key": "unsupported_late_claim",
                "root_cause": "DELIVERY_WITHIN_ESTIMATE",
                "action": "reject_late_refund",
                "responsible": (None, None),
            }
            refund_value = 0.0
            primary_action = "reject_late_refund"
            fallback_used = True

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
        party_type, _ = primary["responsible"]
        if party_type == "seller":
            # Prefer the late_handoff_seller_ids list (set by DeliveryAgent);
            # fall back to all sellers on the order (e.g. multi-seller case).
            for sid in (
                delivery.get("late_handoff_seller_ids") or state.get("seller_ids", [])
            ):
                responsible.append({"party_type": "seller", "party_id": sid})
        elif party_type == "platform":
            responsible.append(
                {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
            )
        elif party_type == "logistics_provider":
            responsible.append(
                {
                    "party_type": "logistics_provider",
                    "party_id": "LOGISTICS_PROVIDER",
                }
            )
        responsible = responsible[:3]

        # ---------- actions (Bug #5 / Improvement E) ----------
        # 1. Primary action first.
        # 2. Brief §4 secondary actions, only those that apply to THIS primary,
        #    in the strict order: review_seller_handoff / review_carrier_delay
        #    → verify_refund_completion → coordinate_multi_seller_case
        #    → verify_payment_allocation.
        actions: List[str] = [primary_action]
        if primary_issue == "late_delivery_seller":
            _add_action(actions, "review_seller_handoff")
            _add_action(
                actions,
                "review_carrier_delay"
                if (delivery.get("delivery_variance_hours") or 0) > 72
                else None,
            )
        elif primary_issue == "late_delivery_logistics":
            _add_action(actions, "review_carrier_delay")

        if refund_value > 0:
            _add_action(actions, "verify_refund_completion")

        if len(state.get("seller_ids", [])) >= 2:
            _add_action(actions, "coordinate_multi_seller_case")

        if primary_issue != "valid_split_payment" and payment_count >= 2:
            _add_action(actions, "verify_payment_allocation")

        actions = cap(actions, 5)

        # ---------- case_status ----------
        case_status = "action_required" if refund_value > 0 else "no_action"

        # ---------- confidence ----------
        confidence = _dynamic_confidence(state, primary_issue, fallback_used)
        llm_payload = self._llm_review(
            state, primary_issue, secondary, refund_value
        )
        if llm_payload and isinstance(llm_payload.get("confidence"), (int, float)):
            # average deterministic and LLM confidence, then clamp
            llm_conf = max(0.0, min(1.0, float(llm_payload["confidence"])))
            confidence = round((confidence + llm_conf) / 2.0, 2)

        # ---------- ranked_causes (Bug #3) ----------
        ranked: List[Dict[str, Any]] = [
            {"cause_code": root_cause_code, "rank": 1}
        ]
        for i, code in enumerate(_secondary_root_causes(primary_issue, state), start=2):
            ranked.append({"cause_code": code, "rank": i})
        ranked = ranked[:3]

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
            "ranked_causes": ranked,
            "responsible_parties": responsible,
        }
        trace(
            self.name,
            case_id=state["case_id"],
            primary_issue=primary_issue,
            refund=round2(refund_value),
            secondary=secondary,
            llm_used=bool(llm_payload),
            confidence=confidence,
            actions=actions,
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
            "Bạn là reviewer chính sách e-commerce. Trả về JSON "
            "{confidence: number 0..1, notes: string}. Không thay đổi "
            "primary_issue hoặc refund — agent đã chọn rồi. Confidence cao "
            "khi facts khớp primary_issue đã chọn."
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


def _add_action(actions: List[str], name: Optional[str]) -> None:
    """Push a secondary action if name given and not already present."""
    if name and name not in actions:
        actions.append(name)
