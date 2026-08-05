"""
Multi-Agent Dispute Resolution System (EC_POLICY_V2 Compliant)
==============================================================

This module implements the complete multi-agent workflow for e-commerce
order dispute resolution on the Brazilian E-Commerce (Olist) dataset.

Architecture Overview:
----------------------
1. CoordinatorAgent: Orchestrates the pipeline, passes state, logs traces,
   and constructs candidate dispute assessments.
2. CustomerAgent: Resolves customer profile and historical order relations.
3. OrderProductAgent: Identifies order items, distinct sellers, products, and categories.
4. PaymentAgent: Reconciles payments against expected item and freight totals.
5. DeliveryAgent: Computes customer delivery and seller carrier handoff variances.
6. PolicyAgent: Applies EC_POLICY_V2 via LLM reasoning with strict deterministic fallback.
7. VerifierAgent: Enforces schema bounds, array limits, confidence clamps, and cleans internal state.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from src.data_loader import DataLoader

load_dotenv()

# ===========================================================================
# Domain Constants & Policy Specification (EC_POLICY_V2)
# ===========================================================================

MODEL_NAME = "qwen/qwen3.5-9b"

PRIMARY_TO_CAUSE: Dict[str, str] = {
    "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
    "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
    "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
    "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
}

CAUSE_TO_PRIMARY: Dict[str, str] = {v: k for k, v in PRIMARY_TO_CAUSE.items()}

PRIMARY_TO_ACTION: Dict[str, str] = {
    "canceled_order_paid": "issue_full_refund",
    "unavailable_order_paid": "issue_full_refund",
    "late_delivery_seller": "refund_freight",
    "late_delivery_logistics": "refund_freight",
    "valid_split_payment": "explain_valid_split_payment",
    "unsupported_late_claim": "reject_late_refund",
}

VALID_PRIMARY_ISSUES = set(PRIMARY_TO_CAUSE.keys())
VALID_CAUSE_CODES = set(PRIMARY_TO_CAUSE.values())

VALID_SECONDARY_ISSUES = {
    "multi_item_order",
    "multi_seller_order",
    "split_payment",
    "repeat_customer",
    "multiple_categories",
}

VALID_ACTIONS = {
    "issue_full_refund",
    "refund_freight",
    "explain_valid_split_payment",
    "reject_late_refund",
    "review_seller_handoff",
    "review_carrier_delay",
    "verify_refund_completion",
    "coordinate_multi_seller_case",
    "verify_payment_allocation",
}

# Specification Limits (§6)
MAX_ORDER_IDS = 5
MAX_ITEM_IDS = 5
MAX_SELLER_IDS = 3
MAX_PAYMENT_IDS = 5
MAX_RELATED_ORDERS = 5
MAX_PRODUCT_IDS = 5
MAX_CATEGORIES = 5
MAX_CAUSES = 3
MAX_RESPONSIBLE_PARTIES = 3
MAX_EVIDENCES = 20
MAX_ACTIONS = 5


# ===========================================================================
# Datetime Utilities
# ===========================================================================

def parse_dt(dt_str: Any) -> Optional[datetime]:
    """Parse ISO / SQL datetime string safely into a datetime object."""
    if not dt_str or not isinstance(dt_str, str) or dt_str.lower() in ("nan", "null", "none"):
        return None
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_dt(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime object into standardized '%Y-%m-%d %H:%M:%S' format."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# LLM Client
# ===========================================================================

class LLMClient:
    """
    Client for interacting with OpenRouter / OpenAI compatible LLM API.
    Supports <= 10B parameter models (e.g. qwen/qwen3.5-9b, qwen/qwen3-8b).
    """

    def __init__(self, model_name: Optional[str] = None):
        self.provider = os.getenv("llm_provider", "openrouter")
        self.model = model_name or os.getenv("LLM_MODEL") or MODEL_NAME
        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.client: Optional[OpenAI] = None

        if self.api_key:
            base_url = "https://openrouter.ai/api/v1" if self.provider == "openrouter" else None
            self.client = OpenAI(base_url=base_url, api_key=self.api_key)

    def _chat(self, system: str, user: str, max_tokens: int = 3500, timeout: int = 45) -> Optional[str]:
        """Execute chat completion with timeout and error protection."""
        if not self.client:
            return None
        try:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                timeout=timeout,
            )
            msg = res.choices[0].message
            content = msg.content
            if not content and hasattr(msg, "reasoning") and msg.reasoning:
                content = msg.reasoning
            return content
        except Exception:
            return None

    def generate_policy_decision(self, facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Prompt the LLM to apply EC_POLICY_V2 rules on extracted case facts.
        Returns parsed JSON dictionary or None if LLM is unreachable or invalid.
        """
        system_prompt = (
            "You are an expert e-commerce dispute resolution AI applying the EC_POLICY_V2 policy.\n"
            "You will receive verified facts extracted from the Olist database and must reason\n"
            "step-by-step to produce a JSON decision. Output ONLY a JSON object — no markdown fences,\n"
            "no extra text before or after.\n\n"
            "=== EC_POLICY_V2 PRIMARY ISSUE HIERARCHY (apply in order, first match wins) ===\n"
            "1. canceled_order_paid     : order_status=canceled AND payment_total > 0\n"
            "   -> case_status: action_required | cause_code: ORDER_CANCELED_AFTER_PAYMENT\n"
            "   -> responsible_parties: [{party_type:platform, party_id:OLIST_PLATFORM}]\n"
            "   -> refund_amount: payment_total | primary_action: issue_full_refund\n\n"
            "2. unavailable_order_paid  : order_status=unavailable AND payment_total > 0\n"
            "   -> case_status: action_required | cause_code: ORDER_UNAVAILABLE_AFTER_PAYMENT\n"
            "   -> responsible_parties: [{party_type:platform, party_id:OLIST_PLATFORM}]\n"
            "   -> refund_amount: payment_total | primary_action: issue_full_refund\n\n"
            "3. late_delivery_seller    : delivery_variance_hours > 0 AND late_handoff_seller_ids is non-empty\n"
            "   -> case_status: action_required | cause_code: SELLER_HANDOFF_AFTER_LIMIT\n"
            "   -> responsible_parties: each late seller as {party_type:seller, party_id:<seller_id>}\n"
            "   -> refund_amount: freight_total | primary_action: refund_freight\n\n"
            "4. late_delivery_logistics : delivery_variance_hours > 0 AND late_handoff_seller_ids is empty\n"
            "   -> case_status: action_required | cause_code: CARRIER_DELIVERED_AFTER_ESTIMATE\n"
            "   -> responsible_parties: [{party_type:logistics_provider, party_id:LOGISTICS_PROVIDER}]\n"
            "   -> refund_amount: freight_total | primary_action: refund_freight\n\n"
            "5. valid_split_payment     : payment_count >= 2 AND reconciled = true\n"
            "   -> case_status: no_action | cause_code: MULTIPLE_PAYMENTS_RECONCILED\n"
            "   -> responsible_parties: [] | refund_amount: 0 | primary_action: explain_valid_split_payment\n\n"
            "6. unsupported_late_claim  : none of the above match\n"
            "   -> case_status: no_action | cause_code: DELIVERY_WITHIN_ESTIMATE\n"
            "   -> responsible_parties: [] | refund_amount: 0 | primary_action: reject_late_refund\n\n"
            "=== SECONDARY ISSUES (add in this fixed order if condition met) ===\n"
            "- multi_item_order        : item_count >= 2\n"
            "- multi_seller_order      : seller_count >= 2\n"
            "- split_payment           : payment_count >= 2\n"
            "- repeat_customer         : has_other_orders = true\n"
            "- multiple_categories     : category_count >= 2\n\n"
            "=== ADDITIONAL ACTIONS (append after primary_action in order if applicable) ===\n"
            "- review_seller_handoff   : if primary_issue = late_delivery_seller\n"
            "- review_carrier_delay    : if primary_issue = late_delivery_logistics\n"
            "- verify_refund_completion: if primary_issue in (canceled_order_paid, unavailable_order_paid)\n"
            "- coordinate_multi_seller_case: if multi_seller_order in secondary_issues\n"
            "- verify_payment_allocation: if split_payment in secondary_issues AND primary_issue != valid_split_payment\n\n"
            "=== OUTPUT FORMAT (strict JSON, no markdown) ===\n"
            "{\n"
            '  "reasoning": "<chain-of-thought: which rule matched and why>",\n'
            '  "primary_issue": "<one of the 6 issue codes>",\n'
            '  "secondary_issues": ["<code>", ...],\n'
            '  "cause_code": "<root cause code>",\n'
            '  "case_status": "<action_required|no_action>",\n'
            '  "refund_amount": <float, rounded 2 decimals>,\n'
            '  "responsible_parties": [{"party_type": "...", "party_id": "..."}],\n'
            '  "resolution_actions": ["<action>", ...]\n'
            "}"
        )

        user_prompt = (
            "Apply EC_POLICY_V2 to the following verified facts and produce the JSON decision.\n\n"
            f"FACTS:\n{json.dumps(facts, indent=2, ensure_ascii=False)}"
        )

        raw = self._chat(system_prompt, user_prompt)
        if not raw:
            return None
        return self._parse_json(raw)

    def _parse_json(self, raw: str) -> Optional[Dict[str, Any]]:
        """Safely extract JSON object from raw response string."""
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


# ===========================================================================
# Specialized Domain Agents
# ===========================================================================

class CustomerAgent:
    """Agent responsible for customer identity lookup and history resolution."""

    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "CustomerAgent"

    def process(self, order: Dict[str, Any], claimed_order_id: str) -> Dict[str, Any]:
        customer_id = order.get("customer_id", "")
        cust_row = self.data_loader.get_customer(customer_id)
        customer_unique_id = cust_row["customer_unique_id"] if cust_row else "unknown"
        related_orders = self.data_loader.get_customer_related_orders(customer_unique_id, claimed_order_id)

        return {
            "customer_unique_id": customer_unique_id,
            "related_order_ids": related_orders[:MAX_RELATED_ORDERS],
        }


class OrderProductAgent:
    """Agent responsible for order items, sellers, products, and categories."""

    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "OrderProductAgent"

    def process(self, claimed_order_id: str) -> Dict[str, Any]:
        items = self.data_loader.get_items(claimed_order_id)

        order_ids = [claimed_order_id]
        item_ids = [f"{claimed_order_id}:{item['order_item_id']}" for item in items[:MAX_ITEM_IDS]]

        seller_ids: List[str] = []
        product_ids: List[str] = []
        category_names: List[str] = []

        for item in items:
            sid = item.get("seller_id")
            if sid and sid not in seller_ids:
                seller_ids.append(sid)

            pid = item.get("product_id")
            if pid and pid not in product_ids:
                product_ids.append(pid)

            if pid:
                prod = self.data_loader.get_product(pid)
                if prod:
                    cat_pt = prod.get("product_category_name")
                    if isinstance(cat_pt, str) and cat_pt.strip() and cat_pt.strip().lower() != "nan":
                        clean_cat = cat_pt.strip()
                        if clean_cat not in category_names:
                            category_names.append(clean_cat)

        return {
            "items": items,
            "affected_entities": {
                "order_ids": order_ids[:MAX_ORDER_IDS],
                "item_ids": item_ids[:MAX_ITEM_IDS],
                "seller_ids": seller_ids[:MAX_SELLER_IDS],
            },
            "product_context": {
                "product_ids": product_ids[:MAX_PRODUCT_IDS],
                "category_names": category_names[:MAX_CATEGORIES],
            },
        }


class PaymentAgent:
    """Agent responsible for payment reconciliation and difference calculation."""

    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "PaymentAgent"

    def process(self, claimed_order_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        payments = self.data_loader.get_payments(claimed_order_id)
        payment_ids = [f"{claimed_order_id}:{p['payment_sequential']}" for p in payments[:MAX_PAYMENT_IDS]]
        payment_total = round(sum(float(p.get("payment_value", 0.0)) for p in payments), 2)
        payment_types = list(dict.fromkeys(p.get("payment_type", "") for p in payments))

        if not items:
            # Specification §4: zero-item orders have null totals, difference, reconciled
            return {
                "payments": payments,
                "payment_ids": payment_ids,
                "payment_reconciliation": {
                    "currency": "BRL",
                    "item_total_brl": 0.0,
                    "freight_total_brl": 0.0,
                    "expected_total_brl": None,
                    "payment_total_brl": payment_total,
                    "difference_brl": None,
                    "reconciled": None,
                    "payment_types": payment_types,
                },
            }

        item_total = round(sum(float(i.get("price", 0.0)) for i in items), 2)
        freight_total = round(sum(float(i.get("freight_value", 0.0)) for i in items), 2)
        expected_total = round(item_total + freight_total, 2)
        difference = round(payment_total - expected_total, 2)
        if abs(difference) < 1e-9:
            difference = 0.0
        reconciled = abs(difference) <= 0.10

        return {
            "payments": payments,
            "payment_ids": payment_ids,
            "payment_reconciliation": {
                "currency": "BRL",
                "item_total_brl": item_total,
                "freight_total_brl": freight_total,
                "expected_total_brl": expected_total,
                "payment_total_brl": payment_total,
                "difference_brl": difference,
                "reconciled": reconciled,
                "payment_types": payment_types,
            },
        }


class DeliveryAgent:
    """Agent responsible for computing delivery timeline and seller handoff variances."""

    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "DeliveryAgent"

    def process(self, order: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
        delivered_at = parse_dt(order.get("order_delivered_customer_date"))
        estimated_at = parse_dt(order.get("order_estimated_delivery_date"))
        carrier_at = parse_dt(order.get("order_delivered_carrier_date"))

        delivery_variance_hours = None
        if delivered_at and estimated_at:
            delivery_variance_hours = round((delivered_at - estimated_at).total_seconds() / 3600.0, 2)

        seller_handoff_analysis: List[Dict[str, Any]] = []
        late_handoff_seller_ids: List[str] = []

        if carrier_at and items:
            seller_earliest: Dict[str, datetime] = {}
            for item in items:
                sid = item.get("seller_id", "")
                limit_dt = parse_dt(item.get("shipping_limit_date"))
                if sid and limit_dt:
                    if sid not in seller_earliest or limit_dt < seller_earliest[sid]:
                        seller_earliest[sid] = limit_dt

            for sid, limit_dt in seller_earliest.items():
                variance = round((carrier_at - limit_dt).total_seconds() / 3600.0, 2)
                late = variance > 0
                seller_handoff_analysis.append({
                    "seller_id": sid,
                    "shipping_limit_at": format_dt(limit_dt),
                    "handoff_variance_hours": variance,
                    "late_handoff": late,
                })
                if late:
                    late_handoff_seller_ids.append(sid)

        return {
            "delivery_analysis": {
                "delivered_at": format_dt(delivered_at),
                "estimated_delivery_at": format_dt(estimated_at),
                "carrier_handoff_at": format_dt(carrier_at),
                "delivery_variance_hours": delivery_variance_hours,
                "seller_handoff_analysis": seller_handoff_analysis,
                "late_handoff_seller_ids": late_handoff_seller_ids,
            }
        }


# ===========================================================================
# Policy Engine & Policy Agent
# ===========================================================================

def build_secondary_issues(facts: Dict[str, Any]) -> List[str]:
    """Compute secondary issues according to strict EC_POLICY_V2 fixed ordering."""
    secondary = []
    if facts.get("item_count", 0) >= 2:
        secondary.append("multi_item_order")
    if facts.get("seller_count", 0) >= 2:
        secondary.append("multi_seller_order")
    if facts.get("payment_count", 0) >= 2:
        secondary.append("split_payment")
    if facts.get("has_other_orders", False):
        secondary.append("repeat_customer")
    if facts.get("category_count", 0) >= 2:
        secondary.append("multiple_categories")
    return secondary


def build_resolution_actions(
    primary_issue: str,
    secondary_issues: List[str],
    seller_count: int = 0,
    payment_count: int = 0,
) -> List[str]:
    """
    Assemble resolution_actions according to strict EC_POLICY_V2 §4 ordering:
    1. Primary action
    2. review_seller_handoff OR review_carrier_delay
    3. verify_refund_completion (for full refunds on canceled / unavailable)
    4. coordinate_multi_seller_case (if multi-seller)
    5. verify_payment_allocation (if split payment AND not valid_split_payment)
    """
    primary_action = PRIMARY_TO_ACTION.get(primary_issue, "reject_late_refund")
    actions = [primary_action]

    # Step 1: Review delay
    if primary_issue == "late_delivery_seller":
        actions.append("review_seller_handoff")
    elif primary_issue == "late_delivery_logistics":
        actions.append("review_carrier_delay")

    # Step 2: Verify full refund completion
    if primary_issue in ("canceled_order_paid", "unavailable_order_paid"):
        actions.append("verify_refund_completion")

    # Step 3: Multi-seller coordination
    if seller_count >= 2 or "multi_seller_order" in secondary_issues:
        actions.append("coordinate_multi_seller_case")

    # Step 4: Split payment allocation verification
    if (payment_count >= 2 or "split_payment" in secondary_issues) and primary_issue != "valid_split_payment":
        actions.append("verify_payment_allocation")

    return actions[:MAX_ACTIONS]


def evaluate_policy_rules(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic rule engine implementing EC_POLICY_V2 Priority Hierarchy (§4).
    1. canceled_order_paid
    2. unavailable_order_paid
    3. late_delivery_seller
    4. late_delivery_logistics
    5. valid_split_payment
    6. unsupported_late_claim
    """
    order_status = facts["order_status"]
    payment_total = facts["payment_total"] or 0.0
    freight_total = facts["freight_total"] or 0.0
    reconciled = facts["reconciled"]
    payment_count = facts["payment_count"]
    seller_count = facts["seller_count"]
    del_variance = facts["delivery_variance_hours"]
    late_sellers = facts["late_handoff_seller_ids"]

    # 1. Canceled order with payment
    if order_status == "canceled" and payment_total > 0:
        primary = "canceled_order_paid"
        case_status = "action_required"
        refund = payment_total
        responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]

    # 2. Unavailable order with payment
    elif order_status == "unavailable" and payment_total > 0:
        primary = "unavailable_order_paid"
        case_status = "action_required"
        refund = payment_total
        responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]

    # 3. Delivered late due to seller handoff delay
    elif del_variance is not None and del_variance > 0 and len(late_sellers) > 0:
        primary = "late_delivery_seller"
        case_status = "action_required"
        refund = freight_total
        responsible = [{"party_type": "seller", "party_id": sid} for sid in late_sellers[:MAX_SELLER_IDS]]

    # 4. Delivered late due to carrier logistics delay
    elif del_variance is not None and del_variance > 0 and len(late_sellers) == 0:
        primary = "late_delivery_logistics"
        case_status = "action_required"
        refund = freight_total
        responsible = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]

    # 5. Split payment successfully reconciled
    elif payment_count >= 2 and reconciled is True:
        primary = "valid_split_payment"
        case_status = "no_action"
        refund = 0.0
        responsible = []

    # 6. Unsupported late claim / normal delivery
    else:
        primary = "unsupported_late_claim"
        case_status = "no_action"
        refund = 0.0
        responsible = []

    cause_code = PRIMARY_TO_CAUSE[primary]
    secondary_issues = build_secondary_issues(facts)
    actions = build_resolution_actions(primary, secondary_issues, seller_count, payment_count)

    return {
        "primary_issue": primary,
        "secondary_issues": secondary_issues,
        "cause_code": cause_code,
        "case_status": case_status,
        "refund_amount": round(float(refund), 2),
        "responsible_parties": responsible,
        "resolution_actions": actions,
    }


def build_evidence_ids(
    claimed_order_id: str,
    items: List[Dict[str, Any]],
    payments: List[Dict[str, Any]],
    responsible_parties: List[Dict[str, Any]],
    cause_code: str,
) -> List[str]:
    """Construct verifiable evidence IDs directly from data rows and policy code (§5)."""
    evidences = [f"order:{claimed_order_id}"]

    for item in items[:MAX_ITEM_IDS]:
        evidences.append(f"item:{claimed_order_id}:{item['order_item_id']}")

    for pay in payments[:MAX_PAYMENT_IDS]:
        evidences.append(f"payment:{claimed_order_id}:{pay['payment_sequential']}")

    seen_sellers = set()
    for party in responsible_parties:
        if party.get("party_type") == "seller":
            sid = party.get("party_id")
            if sid and sid not in seen_sellers:
                seen_sellers.add(sid)
                evidences.append(f"seller:{sid}")

    evidences.append(f"policy:{cause_code}")
    return evidences[:MAX_EVIDENCES]


def calculate_confidence(
    items: List[Dict[str, Any]],
    payments: List[Dict[str, Any]],
    del_analysis: Dict[str, Any],
    primary_issue: str,
    used_llm: bool,
) -> float:
    """Compute confidence score based on data completeness, timestamps, and LLM verification."""
    conf = 0.88
    if len(items) > 0 and len(payments) > 0:
        conf += 0.04
    if del_analysis.get("delivered_at") and del_analysis.get("estimated_delivery_at"):
        conf += 0.03
    if len(items) > 0:
        conf += 0.02
    if used_llm:
        conf += 0.02

    rule_modifier = {
        "canceled_order_paid": 0.01,
        "unavailable_order_paid": 0.01,
        "late_delivery_seller": 0.01,
        "late_delivery_logistics": 0.01,
        "valid_split_payment": 0.01,
        "unsupported_late_claim": 0.0,
    }.get(primary_issue, 0.0)

    conf += rule_modifier
    return round(max(0.30, min(0.98, conf)), 2)


class PolicyAgent:
    """Agent that synthesizes domain context into dispute resolution via LLM and rules."""

    def __init__(self, llm_client: LLMClient):
        self.name = "PolicyAgent"
        self.llm_client = llm_client

    def process(
        self,
        order: Dict[str, Any],
        claimed_order_id: str,
        customer_ctx: Dict[str, Any],
        order_product_ctx: Dict[str, Any],
        payment_ctx: Dict[str, Any],
        delivery_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        pay_recon = payment_ctx["payment_reconciliation"]
        del_analysis = delivery_ctx["delivery_analysis"]
        items = order_product_ctx["items"]
        payments = payment_ctx["payments"]

        facts = {
            "order_status": order.get("order_status", ""),
            "payment_total": pay_recon["payment_total_brl"],
            "freight_total": pay_recon["freight_total_brl"],
            "reconciled": pay_recon["reconciled"],
            "payment_count": len(payments),
            "item_count": len(items),
            "seller_count": len(order_product_ctx["affected_entities"]["seller_ids"]),
            "category_count": len(order_product_ctx["product_context"]["category_names"]),
            "has_other_orders": len(customer_ctx["related_order_ids"]) > 0,
            "delivery_variance_hours": del_analysis["delivery_variance_hours"],
            "late_handoff_seller_ids": del_analysis["late_handoff_seller_ids"],
        }

        # Step A: Query LLM for reasoning
        llm_raw = self.llm_client.generate_policy_decision(facts)
        llm_decision = self._validate_and_sanitize_llm(llm_raw, facts)
        used_llm = llm_decision is not None
        reasoning = (
            llm_decision.get("reasoning", "") if used_llm else "LLM unavailable — deterministic fallback."
        )

        # Step B: Fallback if LLM unavailable
        if not used_llm:
            llm_decision = evaluate_policy_rules(facts)

        primary_issue = llm_decision["primary_issue"]
        secondary_issues = llm_decision["secondary_issues"]
        cause_code = llm_decision["cause_code"]
        case_status = llm_decision["case_status"]
        refund_amount = round(float(llm_decision["refund_amount"]), 2)
        responsible_parties = llm_decision["responsible_parties"][:MAX_RESPONSIBLE_PARTIES]
        resolution_actions = llm_decision["resolution_actions"][:MAX_ACTIONS]

        # Step C: Generate evidence and confidence
        ranked_causes = [{"cause_code": cause_code, "rank": 1}]
        evidence_ids = build_evidence_ids(claimed_order_id, items, payments, responsible_parties, cause_code)
        confidence_val = calculate_confidence(items, payments, del_analysis, primary_issue, used_llm)

        return {
            "case_assessment": {
                "primary_issue": primary_issue,
                "secondary_issues": secondary_issues,
                "case_status": case_status,
                "confidence": confidence_val,
            },
            "root_cause_analysis": {
                "ranked_causes": ranked_causes,
                "responsible_parties": responsible_parties,
            },
            "evidence_ids": evidence_ids,
            "financial_resolution": {
                "currency": "BRL",
                "recommended_refund_brl": refund_amount,
            },
            "resolution_actions": resolution_actions,
            "llm_used": used_llm,
            "llm_reasoning": reasoning,
        }

    def _validate_and_sanitize_llm(
        self, llm_result: Optional[Dict[str, Any]], facts: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Validate LLM output against policy constraints, repairing small discrepancies."""
        if not llm_result or not isinstance(llm_result, dict):
            return None

        primary = llm_result.get("primary_issue", "")
        cause = llm_result.get("cause_code", "")

        # Harmonize primary issue and cause code
        if cause in CAUSE_TO_PRIMARY and primary != CAUSE_TO_PRIMARY[cause]:
            if facts.get("order_status") in ("canceled", "unavailable"):
                primary = "canceled_order_paid" if facts.get("order_status") == "canceled" else "unavailable_order_paid"
                cause = PRIMARY_TO_CAUSE[primary]
            elif primary in PRIMARY_TO_CAUSE:
                cause = PRIMARY_TO_CAUSE[primary]
            else:
                primary = CAUSE_TO_PRIMARY[cause]
        elif primary in PRIMARY_TO_CAUSE and cause != PRIMARY_TO_CAUSE[primary]:
            cause = PRIMARY_TO_CAUSE[primary]

        if primary not in VALID_PRIMARY_ISSUES or cause not in VALID_CAUSE_CODES:
            return None

        # Build ground-truth secondary issues and actions
        secondary = build_secondary_issues(facts)
        payment_total = facts.get("payment_total") or 0.0
        freight_total = facts.get("freight_total") or 0.0

        if primary in ("canceled_order_paid", "unavailable_order_paid"):
            refund = round(float(payment_total), 2)
            responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
        elif primary == "late_delivery_seller":
            refund = round(float(freight_total), 2)
            late_sellers = facts.get("late_handoff_seller_ids", [])
            responsible = [{"party_type": "seller", "party_id": sid} for sid in late_sellers[:MAX_SELLER_IDS]]
        elif primary == "late_delivery_logistics":
            refund = round(float(freight_total), 2)
            responsible = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
        else:
            refund = 0.0
            responsible = []

        case_status = "action_required" if refund > 0 else "no_action"
        actions = build_resolution_actions(
            primary, secondary, facts.get("seller_count", 0), facts.get("payment_count", 0)
        )

        return {
            "reasoning": llm_result.get("reasoning", ""),
            "primary_issue": primary,
            "secondary_issues": secondary,
            "cause_code": cause,
            "case_status": case_status,
            "refund_amount": refund,
            "responsible_parties": responsible,
            "resolution_actions": actions,
        }


# ===========================================================================
# Verifier Agent
# ===========================================================================

class VerifierAgent:
    """Agent that strictly checks schema boundaries, array lengths, and data types."""

    def __init__(self):
        self.name = "VerifierAgent"

    def process(self, case_output: Dict[str, Any]) -> Dict[str, Any]:
        aff = case_output["affected_entities"]
        aff["order_ids"] = aff["order_ids"][:MAX_ORDER_IDS]
        aff["item_ids"] = aff["item_ids"][:MAX_ITEM_IDS]
        aff["seller_ids"] = aff["seller_ids"][:MAX_SELLER_IDS]
        aff["payment_ids"] = aff["payment_ids"][:MAX_PAYMENT_IDS]

        cust = case_output["customer_context"]
        cust["related_order_ids"] = cust["related_order_ids"][:MAX_RELATED_ORDERS]

        prod = case_output["product_context"]
        prod["product_ids"] = prod["product_ids"][:MAX_PRODUCT_IDS]
        prod["category_names"] = prod["category_names"][:MAX_CATEGORIES]

        root = case_output["root_cause_analysis"]
        root["ranked_causes"] = root["ranked_causes"][:MAX_CAUSES]
        root["responsible_parties"] = root["responsible_parties"][:MAX_RESPONSIBLE_PARTIES]

        case_output["evidence_ids"] = case_output["evidence_ids"][:MAX_EVIDENCES]
        case_output["resolution_actions"] = case_output["resolution_actions"][:MAX_ACTIONS]

        # Clamp confidence to [0.0, 1.0]
        conf = case_output["case_assessment"].get("confidence", 0.90)
        try:
            conf = float(conf)
        except (ValueError, TypeError):
            conf = 0.90
        case_output["case_assessment"]["confidence"] = max(0.0, min(1.0, round(conf, 2)))

        # Clean internal state fields before emission
        case_output.pop("llm_used", None)
        case_output.pop("llm_reasoning", None)

        return case_output


# ===========================================================================
# Coordinator Agent (Pipeline Orchestrator)
# ===========================================================================

class CoordinatorAgent:
    """Main orchestrator coordinating data extraction, policy reasoning, and verification."""

    def __init__(self, data_loader: DataLoader, trace_path: str = "trace.jsonl"):
        self.data_loader = data_loader
        self.trace_path = trace_path
        self.llm_client = LLMClient()

        self.customer_agent = CustomerAgent(data_loader)
        self.order_product_agent = OrderProductAgent(data_loader)
        self.payment_agent = PaymentAgent(data_loader)
        self.delivery_agent = DeliveryAgent(data_loader)
        self.policy_agent = PolicyAgent(self.llm_client)
        self.verifier_agent = VerifierAgent()

    def _log_trace(self, case_id: str, agent_name: str, action: str, details: Dict[str, Any]):
        """Append step trace to trace.jsonl and logging/trace.jsonl."""
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "case_id": case_id,
            "agent": agent_name,
            "model": self.llm_client.model,
            "action": action,
            "details": details,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"

        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(line)

        logging_trace = os.path.join("logging", "trace.jsonl")
        os.makedirs("logging", exist_ok=True)
        with open(logging_trace, "a", encoding="utf-8") as f:
            f.write(line)

    def process_case(self, case_input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute end-to-end multi-agent resolution for a single dispute case."""
        case_id = case_input["case_id"]
        claimed_order_id = case_input["customer_request"]["claimed_order_id"]

        self._log_trace(case_id, "CoordinatorAgent", "receive_case", {"claimed_order_id": claimed_order_id})

        order = self.data_loader.get_order(claimed_order_id)
        if not order:
            raise ValueError(f"Order ID {claimed_order_id} not found in dataset!")

        # Step 1: CustomerAgent
        customer_ctx = self.customer_agent.process(order, claimed_order_id)
        self._log_trace(case_id, self.customer_agent.name, "resolve_customer_context", customer_ctx)

        # Step 2: OrderProductAgent
        order_product_ctx = self.order_product_agent.process(claimed_order_id)
        self._log_trace(case_id, self.order_product_agent.name, "resolve_order_product_context", {
            "affected_entities": order_product_ctx["affected_entities"],
            "product_context": order_product_ctx["product_context"],
        })

        # Step 3: PaymentAgent
        payment_ctx = self.payment_agent.process(claimed_order_id, order_product_ctx["items"])
        self._log_trace(case_id, self.payment_agent.name, "reconcile_payments", {
            "payment_reconciliation": payment_ctx["payment_reconciliation"],
        })

        # Step 4: DeliveryAgent
        delivery_ctx = self.delivery_agent.process(order, order_product_ctx["items"])
        self._log_trace(case_id, self.delivery_agent.name, "analyze_delivery", {
            "delivery_analysis": delivery_ctx["delivery_analysis"],
        })

        # Step 5: PolicyAgent
        policy_res = self.policy_agent.process(
            order,
            claimed_order_id,
            customer_ctx,
            order_product_ctx,
            payment_ctx,
            delivery_ctx,
        )
        self._log_trace(case_id, self.policy_agent.name, "apply_policy_EC_POLICY_V2", {
            "llm_used": policy_res.get("llm_used"),
            "llm_reasoning": policy_res.get("llm_reasoning"),
            "primary_issue": policy_res["case_assessment"]["primary_issue"],
            "secondary_issues": policy_res["case_assessment"]["secondary_issues"],
            "case_status": policy_res["case_assessment"]["case_status"],
        })

        # Assemble candidate payload
        candidate_output = {
            "case_id": case_id,
            "case_assessment": policy_res["case_assessment"],
            "affected_entities": {
                "order_ids": order_product_ctx["affected_entities"]["order_ids"],
                "item_ids": order_product_ctx["affected_entities"]["item_ids"],
                "seller_ids": order_product_ctx["affected_entities"]["seller_ids"],
                "payment_ids": payment_ctx["payment_ids"],
            },
            "customer_context": customer_ctx,
            "product_context": order_product_ctx["product_context"],
            "delivery_analysis": delivery_ctx["delivery_analysis"],
            "payment_reconciliation": payment_ctx["payment_reconciliation"],
            "root_cause_analysis": policy_res["root_cause_analysis"],
            "evidence_ids": policy_res["evidence_ids"],
            "financial_resolution": policy_res["financial_resolution"],
            "resolution_actions": policy_res["resolution_actions"],
            "llm_used": policy_res.get("llm_used"),
            "llm_reasoning": policy_res.get("llm_reasoning"),
        }

        # Step 6: VerifierAgent
        final_output = self.verifier_agent.process(candidate_output)
        self._log_trace(case_id, self.verifier_agent.name, "verify_and_finalize", {"status": "SUCCESS"})

        return final_output
