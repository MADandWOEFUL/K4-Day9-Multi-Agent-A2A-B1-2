"""Workers — pure functions implementing each agent's task.

Each worker:
  - Receives (state, tools, bus)
  - Reads data via ToolLayer (no direct CSV access)
  - Writes its results into state (deterministic keys defined below)
  - Publishes a task.result via bus (supervisor publishes it for us)
  - Returns state

This makes every worker unit-testable in isolation: pass mock tools +
state, assert state mutation, no IO.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .base import cap, round2
from .bus import MessageBus
from .tools import ToolLayer


# ---------- customer ----------

def customer_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    order = tools.get_order(state["claimed_order_id"]).payload
    state["order"] = order
    customer = tools.get_customer(order["customer_id"]).payload if order else None
    state["customer"] = customer

    if customer:
        related = tools.get_orders_for_customer_unique(
            customer["customer_unique_id"]
        ).payload
        related = [oid for oid in related if oid != state["claimed_order_id"]]
    else:
        related = []
    state["related_order_ids"] = cap(related, 5)
    state["customer_unique_id"] = customer["customer_unique_id"] if customer else None
    return state


# ---------- order ----------

def order_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    order_id = state["claimed_order_id"]
    items = tools.get_items(order_id).payload
    state["items"] = items

    seller_ids: List[str] = []
    seller_seen = set()
    for r in items:
        sid = r.get("seller_id")
        if sid and sid not in seller_seen:
            seller_seen.add(sid)
            seller_ids.append(sid)
    state["seller_ids"] = cap(seller_ids, 3)

    # products + categories (English Title Case)
    product_ids: List[str] = []
    product_seen = set()
    category_names: List[str] = []
    category_seen = set()
    for r in items:
        pid = r.get("product_id")
        if pid and pid not in product_seen:
            product_seen.add(pid)
            product_ids.append(pid)
        prod = tools.get_product(pid).payload if pid else None
        if prod:
            pt = (prod.get("product_category_name") or "").strip()
            en = tools.category_english(pt).payload
            if not en or en == pt:
                en = pt
            en = en.replace("_", " ").title() if en else None
            if en and en not in category_seen:
                category_seen.add(en)
                category_names.append(en)

    state["product_ids"] = cap(product_ids, 5)
    state["category_names"] = cap(category_names, 5)
    return state


# ---------- payment ----------

def payment_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    order_id = state["claimed_order_id"]
    order = state.get("order")
    items = state.get("items", [])
    payments = tools.get_payments(order_id).payload
    state["_payments_cache"] = payments

    def to_float(v: Any) -> float:
        try:
            return float(v) if v not in (None, "") else 0.0
        except Exception:
            return 0.0

    payments_sorted = sorted(
        payments, key=lambda r: int(r.get("payment_sequential", "0") or 0)
    )

    if not order:
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
        return state

    if not items:
        # Bug fix: orders with no item rows can still have payments.
        payment_total = sum(to_float(r.get("payment_value")) for r in payments_sorted)
        payment_types = []
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
            "item_total_brl": None,
            "freight_total_brl": None,
            "expected_total_brl": None,
            "payment_total_brl": round2(payment_total),
            "difference_brl": None,
            "reconciled": None,
            "payment_types": payment_types,
            "_payment_ids": cap(payment_ids, 5),
        }
        return state

    item_total = sum(to_float(r.get("price")) for r in items)
    freight_total = sum(to_float(r.get("freight_value")) for r in items)
    payment_total = sum(to_float(r.get("payment_value")) for r in payments_sorted)
    expected_total = item_total + freight_total
    diff = round(payment_total - expected_total, 2)
    if diff == 0:
        diff = 0.0  # normalize -0.0 to 0.0
    reconciled = abs(diff) <= 0.10
    payment_types: List[str] = []
    seen = set()
    for r in payments_sorted:
        t = (r.get("payment_type") or "").strip()
        if t and t not in seen:
            seen.add(t)
            payment_types.append(t)
    payment_ids = [f"{order_id}:{r['payment_sequential']}" for r in payments_sorted]
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
    return state


# ---------- delivery ----------

def delivery_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    order = state.get("order") or {}
    items = state.get("items", [])

    def parse(s: Any) -> Any:
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    delivered_at = parse(order.get("order_delivered_customer_date"))
    estimated_at = parse(order.get("order_estimated_delivery_date"))
    carrier_at = parse(order.get("order_delivered_carrier_date"))

    delivered_after_est = (
        delivered_at is not None
        and estimated_at is not None
        and delivered_at > estimated_at
    )
    delivery_variance_hours: Any = None
    if delivered_at and estimated_at:
        delivery_variance_hours = round(
            (delivered_at - estimated_at).total_seconds() / 3600.0, 2
        )

    seller_handoff_analysis = []
    late_handoff_seller_ids: List[str] = []
    for it in items:
        sid = it.get("seller_id")
        shipping_limit = parse(it.get("shipping_limit_date"))
        handoff_variance_hours: Any = None
        late_handoff = False
        if shipping_limit and carrier_at:
            handoff_variance_hours = round(
                (carrier_at - shipping_limit).total_seconds() / 3600.0, 2
            )
            if handoff_variance_hours > 0:
                late_handoff = True
                if sid and sid not in late_handoff_seller_ids:
                    late_handoff_seller_ids.append(sid)
        seller_handoff_analysis.append({
            "seller_id": sid,
            "shipping_limit_at": it.get("shipping_limit_date") or None,
            "handoff_variance_hours": handoff_variance_hours,
            "late_handoff": late_handoff,
        })

    state["delivery_analysis"] = {
        "delivered_at": order.get("order_delivered_customer_date") or None,
        "estimated_delivery_at": order.get("order_estimated_delivery_date") or None,
        "carrier_handoff_at": order.get("order_delivered_carrier_date") or None,
        "delivery_variance_hours": delivery_variance_hours,
        "seller_handoff_analysis": seller_handoff_analysis,
        "late_handoff_seller_ids": late_handoff_seller_ids,
        "_delivered_after_estimate": delivered_after_est,
    }
    return state


# ---------- policy ----------

PRIMARY_RULES = [
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


SECONDARY_RULES = [
    ("multi_item_order", lambda s: len(s.get("items", [])) >= 2),
    ("multi_seller_order", lambda s: len(s.get("seller_ids", [])) >= 2),
    (
        "split_payment",
        lambda s: len(s.get("payment_reconciliation", {}).get("_payment_ids", [])) >= 2,
    ),
    (
        "repeat_customer",
        lambda s: len([x for x in s.get("related_order_ids", []) if x]) >= 1,
    ),
    (
        "multiple_categories",
        lambda s: len(s.get("category_names", [])) >= 2,
    ),
]


def policy_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    order = state.get("order") or {}
    delivery = state.get("delivery_analysis") or {}
    payment = state.get("payment_reconciliation") or {}

    status = (order.get("order_status") if order else "") or ""
    payment_total = _num(payment.get("payment_total_brl"))
    freight_total = _num(payment.get("freight_total_brl"))
    delivered_after_est = bool(delivery.get("_delivered_after_estimate"))
    any_late_handoff = bool(delivery.get("late_handoff_seller_ids"))
    payment_count = len(payment.get("_payment_ids", []))
    reconciled = bool(payment.get("reconciled"))

    primary = None
    refund_value = 0.0
    primary_action = "reject_late_refund"

    for rule in PRIMARY_RULES:
        ok = False
        if rule["key"] in ("canceled_order_paid", "unavailable_order_paid"):
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

    fallback = False
    if primary is None:
        primary = {
            "key": "unsupported_late_claim",
            "root_cause": "DELIVERY_WITHIN_ESTIMATE",
            "action": "reject_late_refund",
            "responsible": (None, None),
        }
        refund_value = 0.0
        primary_action = "reject_late_refund"
        fallback = True

    primary_issue = primary["key"]
    root_cause_code = primary["root_cause"]

    # secondary
    secondary: List[str] = []
    for key, pred in SECONDARY_RULES:
        try:
            if pred(state):
                secondary.append(key)
        except Exception:
            pass

    # responsible
    responsible: List[Dict[str, Any]] = []
    party_type, _ = primary["responsible"]
    if party_type == "seller":
        for sid in (
            delivery.get("late_handoff_seller_ids") or state.get("seller_ids", [])
        ):
            responsible.append({"party_type": "seller", "party_id": sid})
    elif party_type == "platform":
        responsible.append({"party_type": "platform", "party_id": "OLIST_PLATFORM"})
    elif party_type == "logistics_provider":
        responsible.append(
            {"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}
        )
    responsible = responsible[:3]

    # actions
    actions: List[str] = [primary_action]
    if primary_issue == "late_delivery_seller":
        _add(actions, "review_seller_handoff")
        if (delivery.get("delivery_variance_hours") or 0) > 72:
            _add(actions, "review_carrier_delay")
    elif primary_issue == "late_delivery_logistics":
        _add(actions, "review_carrier_delay")

    if refund_value > 0:
        _add(actions, "verify_refund_completion")

    if len(state.get("seller_ids", [])) >= 2:
        _add(actions, "coordinate_multi_seller_case")

    if primary_issue != "valid_split_payment" and payment_count >= 2:
        _add(actions, "verify_payment_allocation")

    actions = cap(actions, 5)

    case_status = "action_required" if refund_value > 0 else "no_action"

    confidence = _dynamic_confidence(state, primary_issue, fallback)

    # ranked_causes: primary + up to 2 secondary, NEVER duplicate the primary
    ranked: List[Dict[str, Any]] = [{"cause_code": root_cause_code, "rank": 1}]
    seen_codes = {root_cause_code}
    for code in _secondary_root_causes(primary_issue, state):
        if code not in seen_codes:
            seen_codes.add(code)
            ranked.append({"cause_code": code, "rank": len(ranked) + 1})
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
    return state


def _add(actions: List[str], name: str) -> None:
    if name and name not in actions:
        actions.append(name)


def _num(v: Any) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except Exception:
        return 0.0


def _secondary_root_causes(primary: str, state: Dict[str, Any]) -> List[str]:
    """Return 0-2 secondary cause codes that are CONSISTENT with the primary."""
    delivery = state.get("delivery_analysis", {}) or {}
    variance = delivery.get("delivery_variance_hours") or 0
    extras: List[str] = []
    if primary == "late_delivery_seller":
        extras.append("CARRIER_DELIVERED_AFTER_ESTIMATE")
        if isinstance(variance, (int, float)) and variance > 72:
            extras.append("SELLER_HANDOFF_AFTER_LIMIT")
    elif primary == "late_delivery_logistics":
        extras.append("SELLER_HANDOFF_AFTER_LIMIT")
    elif primary == "canceled_order_paid":
        extras.append("DELIVERY_WITHIN_ESTIMATE")
    elif primary == "unavailable_order_paid":
        extras.append("DELIVERY_WITHIN_ESTIMATE")
    elif primary == "valid_split_payment":
        extras.append("DELIVERY_WITHIN_ESTIMATE")
    return extras[:2]


def _dynamic_confidence(state: Dict[str, Any], primary: str, fallback: bool) -> float:
    score = 0.85
    if (
        len(state.get("items", [])) >= 1
        and len(state.get("payment_reconciliation", {}).get("_payment_ids", [])) >= 1
    ):
        score += 0.05
    if state.get("order") is not None:
        score += 0.03
    if fallback:
        score -= 0.10
    bonus = {
        "canceled_order_paid": 0.02,
        "unavailable_order_paid": 0.02,
        "late_delivery_seller": 0.03,
        "late_delivery_logistics": 0.02,
        "valid_split_payment": 0.02,
    }.get(primary, 0.0)
    score += bonus
    return float(max(0.50, min(0.98, round(score, 2))))


# ---------- context enrichment ----------

def context_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    """Per README §6 schema: customer_context has only two fields."""
    customer = state.get("customer") or {}
    state["customer_context"] = {
        "customer_unique_id": customer.get("customer_unique_id"),
        "related_order_ids": cap(state.get("related_order_ids", []), 5),
    }
    state["product_context"] = {
        "product_ids": cap(state.get("product_ids", []), 5),
        "category_names": cap(state.get("category_names", []), 5),
    }
    return state


# ---------- evidence composition ----------

def evidence_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    order_id = state["claimed_order_id"]
    valid: List[str] = []
    if tools.evidence_exists(f"order:{order_id}"):
        valid.append(f"order:{order_id}")
    items = state.get("items", [])
    for r in items[:5]:
        ev = f"item:{order_id}:{r['order_item_id']}"
        if tools.evidence_exists(ev):
            valid.append(ev)
    for pid in state.get("payment_reconciliation", {}).get("_payment_ids", [])[:5]:
        ev = pid if pid.startswith("payment:") else f"payment:{pid}"
        if tools.evidence_exists(ev):
            valid.append(ev)
    delivery = state.get("delivery_analysis") or {}
    seen_sellers = set()
    for sid in (
        delivery.get("late_handoff_seller_ids") or state.get("seller_ids", [])
    ):
        if sid and sid not in seen_sellers and tools.evidence_exists(f"seller:{sid}"):
            seen_sellers.add(sid)
            valid.append(f"seller:{sid}")
        if len(seen_sellers) >= 3:
            break
    seen_codes = set()
    for cause in state.get("root_cause_analysis", {}).get("ranked_causes", []):
        code = cause.get("cause_code")
        if code and code not in seen_codes:
            seen_codes.add(code)
            valid.append(f"policy:{code}")
    seen = set()
    out = []
    for e in valid:
        if e not in seen:
            seen.add(e)
            out.append(e)
    state["evidence_ids"] = out[:20]
    return state


# ---------- verifier ----------

_LIMITS = {
    "order_ids": 5,
    "item_ids": 5,
    "payment_ids": 5,
    "related_order_ids": 5,
    "product_ids": 5,
    "category_names": 5,
    "seller_ids": 3,
    "evidence_ids": 20,
    "resolution_actions": 5,
}


def verifier_worker(
    state: Dict[str, Any], tools: ToolLayer, bus: MessageBus
) -> Dict[str, Any]:
    order_id = state["claimed_order_id"]
    raw_payment_ids = state.get("payment_reconciliation", {}).get("_payment_ids", [])
    payment_ids: List[str] = []
    for pid in raw_payment_ids:
        # _payment_ids are stored as "<order_id>:<seq>" already
        if pid.startswith(f"{order_id}:"):
            payment_ids.append(pid)
        elif ":" in pid:
            # already in correct format
            payment_ids.append(pid)
        else:
            # bare sequential — wrap with order_id
            payment_ids.append(f"{order_id}:{pid}")

    item_ids = [f"{order_id}:{r['order_item_id']}" for r in state.get("items", [])]

    affected = {
        "order_ids": [order_id],
        "item_ids": cap(item_ids, _LIMITS["item_ids"]),
        "seller_ids": cap(
            state.get("seller_ids", []), _LIMITS["seller_ids"]
        ),
        "payment_ids": cap(payment_ids, _LIMITS["payment_ids"]),
    }
    state["affected_entities"] = affected

    # verify all evidence still reconstructable
    valid_ev = []
    for e in state.get("evidence_ids", []):
        if tools.evidence_exists(e):
            valid_ev.append(e)
    state["evidence_ids"] = cap(valid_ev, _LIMITS["evidence_ids"])

    # cap everything
    for k, lim in _LIMITS.items():
        if k in affected:
            continue
        v = state.get(k)
        if isinstance(v, list):
            state[k] = cap(v, lim)

    # rank_causes / parties already ≤3 by construction
    return state