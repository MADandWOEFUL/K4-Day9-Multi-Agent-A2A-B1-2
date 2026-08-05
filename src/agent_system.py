import json
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from openai import OpenAI
from src.data_loader import DataLoader

load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_dt(dt_str: Any) -> Optional[datetime]:
    if not dt_str or not isinstance(dt_str, str) or dt_str.lower() in ("nan", "null", "none"):
        return None
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def format_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    """
    LLMClient connects to OpenRouter / OpenAI API using keys loaded from .env.
    Uses model qwen/qwen3-8b (8B parameters <= 10B limit).
    """
    MODEL_NAME = "qwen/qwen3-8b"

    def __init__(self):
        self.provider = os.getenv("llm_provider", "openrouter")
        self.model = self.MODEL_NAME          # Always hardcoded; not read from .env
        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.client = None
        if self.api_key:
            base_url = "https://openrouter.ai/api/v1" if self.provider == "openrouter" else None
            self.client = OpenAI(base_url=base_url, api_key=self.api_key)

    def _chat(self, system: str, user: str, max_tokens: int = 600, timeout: int = 20) -> Optional[str]:
        if not self.client:
            return None
        try:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return res.choices[0].message.content
        except Exception as e:
            return None

    def generate_policy_decision(self, facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Ask the LLM to apply EC_POLICY_V2 and produce a structured JSON decision.
        Returns parsed dict on success, None on failure.
        """
        system_prompt = (
            "You are an expert e-commerce dispute resolution AI applying the EC_POLICY_V2 policy.\n"
            "You will receive verified facts extracted from the Olist database and must reason\n"
            "step-by-step to produce a JSON decision. Output ONLY a JSON object — no markdown fences,\n"
            "no extra text before or after.\n\n"
            "=== EC_POLICY_V2 PRIMARY ISSUE HIERARCHY (apply in order, first match wins) ===\n"
            "1. canceled_order_paid     : order_status=canceled AND payment_total > 0\n"
            "   → case_status: action_required | cause_code: ORDER_CANCELED_AFTER_PAYMENT\n"
            "   → responsible_parties: [{party_type:platform, party_id:OLIST_PLATFORM}]\n"
            "   → refund_amount: payment_total | primary_action: issue_full_refund\n\n"
            "2. unavailable_order_paid  : order_status=unavailable AND payment_total > 0\n"
            "   → case_status: action_required | cause_code: ORDER_UNAVAILABLE_AFTER_PAYMENT\n"
            "   → responsible_parties: [{party_type:platform, party_id:OLIST_PLATFORM}]\n"
            "   → refund_amount: payment_total | primary_action: issue_full_refund\n\n"
            "3. late_delivery_seller    : delivery_variance_hours > 0 AND late_handoff_seller_ids is non-empty\n"
            "   → case_status: action_required | cause_code: SELLER_HANDOFF_AFTER_LIMIT\n"
            "   → responsible_parties: each late seller as {party_type:seller, party_id:<seller_id>}\n"
            "   → refund_amount: freight_total | primary_action: refund_freight\n\n"
            "4. late_delivery_logistics : delivery_variance_hours > 0 AND late_handoff_seller_ids is empty\n"
            "   → case_status: action_required | cause_code: CARRIER_DELIVERED_AFTER_ESTIMATE\n"
            "   → responsible_parties: [{party_type:logistics_provider, party_id:LOGISTICS_PROVIDER}]\n"
            "   → refund_amount: freight_total | primary_action: refund_freight\n\n"
            "5. valid_split_payment     : payment_count >= 2 AND reconciled = true\n"
            "   → case_status: no_action | cause_code: MULTIPLE_PAYMENTS_RECONCILED\n"
            "   → responsible_parties: [] | refund_amount: 0 | primary_action: explain_valid_split_payment\n\n"
            "6. unsupported_late_claim  : none of the above match\n"
            "   → case_status: no_action | cause_code: DELIVERY_WITHIN_ESTIMATE\n"
            "   → responsible_parties: [] | refund_amount: 0 | primary_action: reject_late_refund\n\n"
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

        raw = self._chat(system_prompt, user_prompt, max_tokens=3000, timeout=40)
        if not raw:
            return None
        return self._parse_json(raw)

    def _parse_json(self, raw: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON from LLM output (handles markdown code fences)."""
        # Strip markdown fences if present
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        # Try to find the first JSON object
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Last resort: try raw
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


# ---------------------------------------------------------------------------
# Domain Agents (deterministic data extraction)
# ---------------------------------------------------------------------------

class CustomerAgent:
    """
    CustomerAgent looks up customer identity and order history.
    """
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "CustomerAgent"

    def process(self, order: Dict[str, Any], claimed_order_id: str) -> Dict[str, Any]:
        customer_id = order.get("customer_id", "")
        cust_row = self.data_loader.get_customer(customer_id)
        if cust_row:
            customer_unique_id = cust_row["customer_unique_id"]
        else:
            customer_unique_id = "unknown"

        related_orders = self.data_loader.get_customer_related_orders(customer_unique_id, claimed_order_id)
        return {
            "customer_unique_id": customer_unique_id,
            "related_order_ids": related_orders[:5]
        }


class OrderProductAgent:
    """
    OrderProductAgent checks order items, sellers, product details, and categories.
    """
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "OrderProductAgent"

    def process(self, claimed_order_id: str) -> Dict[str, Any]:
        items = self.data_loader.get_items(claimed_order_id)

        order_ids = [claimed_order_id]
        item_ids = [f"{claimed_order_id}:{item['order_item_id']}" for item in items[:5]]

        seller_ids = []
        for item in items:
            sid = item.get("seller_id")
            if sid and sid not in seller_ids:
                seller_ids.append(sid)

        product_ids = []
        for item in items:
            pid = item.get("product_id")
            if pid and pid not in product_ids:
                product_ids.append(pid)

        category_names = []
        for item in items:
            pid = item.get("product_id")
            if pid:
                prod = self.data_loader.get_product(pid)
                if prod:
                    # Keep the original Portuguese category name as-is (sanitize against NaN)
                    cat_pt = prod.get("product_category_name")
                    if isinstance(cat_pt, str) and cat_pt.strip() and cat_pt.strip().lower() != "nan":
                        clean_cat = cat_pt.strip()
                        if clean_cat not in category_names:
                            category_names.append(clean_cat)

        return {
            "items": items,
            "affected_entities": {
                "order_ids": order_ids[:5],
                "item_ids": item_ids[:5],
                "seller_ids": seller_ids[:3],
            },
            "product_context": {
                "product_ids": product_ids[:5],
                "category_names": category_names[:5],
            }
        }


class PaymentAgent:
    """
    PaymentAgent reconciles payment rows against item + freight totals.
    """
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "PaymentAgent"

    def process(self, claimed_order_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        payments = self.data_loader.get_payments(claimed_order_id)

        payment_ids = [f"{claimed_order_id}:{p['payment_sequential']}" for p in payments[:5]]
        payment_total = round(sum(float(p.get("payment_value", 0)) for p in payments), 2)
        payment_types = list(dict.fromkeys(p.get("payment_type", "") for p in payments))

        if not items:
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
                }
            }

        item_total = round(sum(float(i.get("price", 0)) for i in items), 2)
        freight_total = round(sum(float(i.get("freight_value", 0)) for i in items), 2)
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
            }
        }


class DeliveryAgent:
    """
    DeliveryAgent computes delivery variance and per-seller handoff variance.
    """
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "DeliveryAgent"

    def process(self, order: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
        delivered_at  = parse_dt(order.get("order_delivered_customer_date"))
        estimated_at  = parse_dt(order.get("order_estimated_delivery_date"))
        carrier_at    = parse_dt(order.get("order_delivered_carrier_date"))

        if delivered_at and estimated_at:
            delivery_variance_hours = round(
                (delivered_at - estimated_at).total_seconds() / 3600, 2
            )
        else:
            delivery_variance_hours = None

        seller_handoff_analysis = []
        late_handoff_seller_ids = []

        if carrier_at and items:
            seller_earliest: Dict[str, datetime] = {}
            for item in items:
                sid = item.get("seller_id", "")
                limit_dt = parse_dt(item.get("shipping_limit_date"))
                if sid and limit_dt:
                    if sid not in seller_earliest or limit_dt < seller_earliest[sid]:
                        seller_earliest[sid] = limit_dt

            for sid, limit_dt in seller_earliest.items():
                variance = round((carrier_at - limit_dt).total_seconds() / 3600, 2)
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


# ---------------------------------------------------------------------------
# PolicyAgent — LLM-based reasoning with deterministic fallback
# ---------------------------------------------------------------------------

class PolicyAgent:
    """
    PolicyAgent applies EC_POLICY_V2 by sending structured facts to the LLM
    and asking it to reason step-by-step before producing a JSON decision.
    Falls back to deterministic rule evaluation if the LLM fails or returns
    malformed output.
    """

    VALID_PRIMARY_ISSUES = {
        "canceled_order_paid",
        "unavailable_order_paid",
        "late_delivery_seller",
        "late_delivery_logistics",
        "valid_split_payment",
        "unsupported_late_claim",
    }
    VALID_CAUSE_CODES = {
        "ORDER_CANCELED_AFTER_PAYMENT",
        "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "SELLER_HANDOFF_AFTER_LIMIT",
        "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "MULTIPLE_PAYMENTS_RECONCILED",
        "DELIVERY_WITHIN_ESTIMATE",
    }

    def __init__(self, llm_client: LLMClient):
        self.name = "PolicyAgent"
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process(
        self,
        order: Dict[str, Any],
        claimed_order_id: str,
        customer_ctx: Dict[str, Any],
        order_product_ctx: Dict[str, Any],
        payment_ctx: Dict[str, Any],
        delivery_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:

        pay_recon   = payment_ctx["payment_reconciliation"]
        del_analysis = delivery_ctx["delivery_analysis"]
        items       = order_product_ctx["items"]
        payments    = payment_ctx["payments"]

        # Structured facts passed to LLM
        facts = {
            "order_status":              order.get("order_status", ""),
            "payment_total":             pay_recon["payment_total_brl"],
            "freight_total":             pay_recon["freight_total_brl"],
            "reconciled":                pay_recon["reconciled"],
            "payment_count":             len(payments),
            "item_count":                len(items),
            "seller_count":              len(order_product_ctx["affected_entities"]["seller_ids"]),
            "category_count":            len(order_product_ctx["product_context"]["category_names"]),
            "has_other_orders":          len(customer_ctx["related_order_ids"]) > 0,
            "delivery_variance_hours":   del_analysis["delivery_variance_hours"],
            "late_handoff_seller_ids":   del_analysis["late_handoff_seller_ids"],
        }

        # --- Try LLM reasoning ---
        llm_result  = self.llm_client.generate_policy_decision(facts)
        llm_decision = self._validate_llm_decision(llm_result, facts)
        used_llm    = llm_decision is not None
        reasoning   = llm_decision.get("reasoning", "") if used_llm else "LLM unavailable — deterministic fallback."

        # --- Deterministic fallback if LLM failed ---
        if not used_llm:
            llm_decision = self._deterministic_decision(facts)

        primary_issue       = llm_decision["primary_issue"]
        secondary_issues    = llm_decision["secondary_issues"]
        cause_code          = llm_decision["cause_code"]
        case_status         = llm_decision["case_status"]
        refund_amount       = round(float(llm_decision["refund_amount"]), 2)
        responsible_parties = llm_decision["responsible_parties"][:3]
        resolution_actions  = llm_decision["resolution_actions"][:5]

        # --- Safety net: resolution_actions must never be empty ---
        if not resolution_actions:
            resolution_actions = self._build_actions(
                primary_issue, case_status, secondary_issues,
                refund_amount, len(order_product_ctx["affected_entities"]["seller_ids"]), len(payments)
            )

        # Ranked causes: primary root cause at rank 1
        ranked = [{"cause_code": cause_code, "rank": 1}]

        # --- Evidence IDs — deterministic and strictly traceable to real data (§5) ---
        evidence_ids = [f"order:{claimed_order_id}"]
        for item in items[:5]:
            evidence_ids.append(f"item:{claimed_order_id}:{item['order_item_id']}")
        for pay in payments[:5]:
            evidence_ids.append(f"payment:{claimed_order_id}:{pay['payment_sequential']}")
        # Seller evidence: ONLY include seller if seller is a responsible party (§5)
        seen_sellers = set()
        for party in responsible_parties:
            if party.get("party_type") == "seller":
                sid = party.get("party_id")
                if sid and sid not in seen_sellers:
                    seen_sellers.add(sid)
                    evidence_ids.append(f"seller:{sid}")
        # Policy evidence: policy root cause code
        evidence_ids.append(f"policy:{cause_code}")

        # Compute dynamic confidence based on data completeness & reconciliation
        conf = 0.88
        if len(items) > 0 and len(payments) > 0:
            conf += 0.04
        if del_analysis.get("delivered_at") and del_analysis.get("estimated_delivery_at"):
            conf += 0.03
        if len(items) > 0:
            conf += 0.02
        if used_llm:
            conf += 0.02
        # Per-rule modifier
        bonus = {
            "canceled_order_paid": 0.01,
            "unavailable_order_paid": 0.01,
            "late_delivery_seller": 0.01,
            "late_delivery_logistics": 0.01,
            "valid_split_payment": 0.01,
            "unsupported_late_claim": 0.0,
        }.get(primary_issue, 0.0)
        conf += bonus
        confidence_val = round(max(0.30, min(0.98, conf)), 2)

        return {
            "case_assessment": {
                "primary_issue":    primary_issue,
                "secondary_issues": secondary_issues,
                "case_status":      case_status,
                "confidence":       confidence_val,
            },
            "root_cause_analysis": {
                "ranked_causes":       ranked,
                "responsible_parties": responsible_parties,
            },
            "evidence_ids":       evidence_ids[:20],
            "financial_resolution": {
                "currency":              "BRL",
                "recommended_refund_brl": refund_amount,
            },
            "resolution_actions": resolution_actions,
            "llm_used":           used_llm,
            "llm_reasoning":      reasoning,
        }

    # ------------------------------------------------------------------
    # LLM output validation
    # ------------------------------------------------------------------

    def _validate_llm_decision(
        self, llm_result: Optional[Dict], facts: Dict
    ) -> Optional[Dict[str, Any]]:
        if not llm_result or not isinstance(llm_result, dict):
            return None

        primary = llm_result.get("primary_issue", "")
        cause   = llm_result.get("cause_code", "")

        CAUSE_TO_PRIMARY = {
            "ORDER_CANCELED_AFTER_PAYMENT": "canceled_order_paid",
            "ORDER_UNAVAILABLE_AFTER_PAYMENT": "unavailable_order_paid",
            "SELLER_HANDOFF_AFTER_LIMIT": "late_delivery_seller",
            "CARRIER_DELIVERED_AFTER_ESTIMATE": "late_delivery_logistics",
            "MULTIPLE_PAYMENTS_RECONCILED": "valid_split_payment",
            "DELIVERY_WITHIN_ESTIMATE": "unsupported_late_claim",
        }
        PRIMARY_TO_CAUSE = {v: k for k, v in CAUSE_TO_PRIMARY.items()}

        # Harmonize primary_issue and cause_code if there is a mismatch
        if cause in CAUSE_TO_PRIMARY and primary != CAUSE_TO_PRIMARY[cause]:
            if facts.get("order_status") in ("canceled", "unavailable"):
                if facts.get("order_status") == "canceled":
                    primary = "canceled_order_paid"
                else:
                    primary = "unavailable_order_paid"
                cause = PRIMARY_TO_CAUSE[primary]
            elif primary in PRIMARY_TO_CAUSE:
                cause = PRIMARY_TO_CAUSE[primary]
            else:
                primary = CAUSE_TO_PRIMARY[cause]
        elif primary in PRIMARY_TO_CAUSE and cause != PRIMARY_TO_CAUSE[primary]:
            cause = PRIMARY_TO_CAUSE[primary]

        if primary not in self.VALID_PRIMARY_ISSUES:
            return None
        if cause not in self.VALID_CAUSE_CODES:
            return None

        # Build secondary_issues strictly from ground truth facts in fixed order (§4)
        secondary = []
        if facts.get("item_count", 0) >= 2:      secondary.append("multi_item_order")
        if facts.get("seller_count", 0) >= 2:    secondary.append("multi_seller_order")
        if facts.get("payment_count", 0) >= 2:   secondary.append("split_payment")
        if facts.get("has_other_orders", False): secondary.append("repeat_customer")
        if facts.get("category_count", 0) >= 2:  secondary.append("multiple_categories")

        # Enforce case_status, refund_amount, and responsible_parties consistency with primary_issue
        payment_total = facts.get("payment_total") or 0.0
        freight_total = facts.get("freight_total") or 0.0

        if primary in ("canceled_order_paid", "unavailable_order_paid"):
            case_status = "action_required"
            refund = round(float(payment_total), 2)
            responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
        elif primary == "late_delivery_seller":
            case_status = "action_required"
            refund = round(float(freight_total), 2)
            late_sellers = facts.get("late_handoff_seller_ids", [])
            responsible = [{"party_type": "seller", "party_id": sid} for sid in late_sellers[:3]]
        elif primary == "late_delivery_logistics":
            case_status = "action_required"
            refund = round(float(freight_total), 2)
            responsible = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
        else: # valid_split_payment, unsupported_late_claim
            case_status = "no_action"
            refund = 0.0
            responsible = []

        # case_status follows refund (per EC_POLICY_V2)
        case_status = "action_required" if refund > 0 else "no_action"

        # Build resolution_actions strictly adhering to §4
        actions = self._build_actions(
            primary, case_status, secondary, refund,
            facts.get("seller_count", 0), facts.get("payment_count", 0)
        )

        return {
            "reasoning":           llm_result.get("reasoning", ""),
            "primary_issue":       primary,
            "secondary_issues":    secondary,
            "cause_code":          cause,
            "case_status":         case_status,
            "refund_amount":       refund,
            "responsible_parties": responsible,
            "resolution_actions":  actions,
        }

    # ------------------------------------------------------------------
    # Deterministic fallback
    # ------------------------------------------------------------------

    def _deterministic_decision(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        order_status    = facts["order_status"]
        payment_total   = facts["payment_total"] or 0.0
        freight_total   = facts["freight_total"] or 0.0
        reconciled      = facts["reconciled"]
        payment_count   = facts["payment_count"]
        item_count      = facts["item_count"]
        seller_count    = facts["seller_count"]
        category_count  = facts["category_count"]
        has_other_orders = facts["has_other_orders"]
        del_variance    = facts["delivery_variance_hours"]
        late_sellers    = facts["late_handoff_seller_ids"]

        # EC_POLICY_V2 Priority Hierarchy (§4)
        # 1. canceled_order_paid
        if order_status == "canceled" and payment_total > 0:
            primary, cause = "canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT"
            case_status, refund = "action_required", payment_total
            responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]

        # 2. unavailable_order_paid
        elif order_status == "unavailable" and payment_total > 0:
            primary, cause = "unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT"
            case_status, refund = "action_required", payment_total
            responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]

        # 3. late_delivery_seller
        elif del_variance is not None and del_variance > 0 and len(late_sellers) > 0:
            primary, cause = "late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT"
            case_status, refund = "action_required", freight_total
            responsible = [{"party_type": "seller", "party_id": sid} for sid in late_sellers[:3]]

        # 4. late_delivery_logistics
        elif del_variance is not None and del_variance > 0 and len(late_sellers) == 0:
            primary, cause = "late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE"
            case_status, refund = "action_required", freight_total
            responsible = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]

        # 5. valid_split_payment
        elif payment_count >= 2 and reconciled is True:
            primary, cause = "valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED"
            case_status, refund = "no_action", 0.0
            responsible = []

        # 6. unsupported_late_claim
        else:
            primary, cause = "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE"
            case_status, refund = "no_action", 0.0
            responsible = []

        # Secondary issues in exact fixed order (§4)
        secondary = []
        if item_count >= 2:      secondary.append("multi_item_order")
        if seller_count >= 2:    secondary.append("multi_seller_order")
        if payment_count >= 2:   secondary.append("split_payment")
        if has_other_orders:     secondary.append("repeat_customer")
        if category_count >= 2:  secondary.append("multiple_categories")

        actions = self._build_actions(primary, case_status, secondary, refund, seller_count, payment_count)

        return {
            "reasoning":           "Deterministic fallback (LLM unavailable).",
            "primary_issue":       primary,
            "secondary_issues":    secondary,
            "cause_code":          cause,
            "case_status":         case_status,
            "refund_amount":       refund,
            "responsible_parties": responsible,
            "resolution_actions":  actions,
        }

    def _build_actions(self, primary_issue: str, case_status: str, secondary_issues: List[str],
                       refund: float = 0.0, seller_count: int = 0, payment_count: int = 0) -> List[str]:
        """Deterministically build resolution_actions following strict §4 ordering:
        Primary action -> review_seller_handoff / review_carrier_delay ->
        verify_refund_completion -> coordinate_multi_seller_case -> verify_payment_allocation."""
        action_map = {
            "canceled_order_paid":     "issue_full_refund",
            "unavailable_order_paid":  "issue_full_refund",
            "late_delivery_seller":    "refund_freight",
            "late_delivery_logistics": "refund_freight",
            "valid_split_payment":     "explain_valid_split_payment",
            "unsupported_late_claim":  "reject_late_refund",
        }
        primary_action = action_map.get(primary_issue, "reject_late_refund")
        actions = [primary_action]

        # Step 1: review_seller_handoff OR review_carrier_delay
        if primary_issue == "late_delivery_seller":
            actions.append("review_seller_handoff")
        elif primary_issue == "late_delivery_logistics":
            actions.append("review_carrier_delay")

        # Step 2: verify_refund_completion (for full refund on canceled/unavailable)
        if primary_issue in ("canceled_order_paid", "unavailable_order_paid"):
            actions.append("verify_refund_completion")

        # Step 3: coordinate_multi_seller_case (if multi_seller_order)
        if seller_count >= 2 or "multi_seller_order" in secondary_issues:
            actions.append("coordinate_multi_seller_case")

        # Step 4: verify_payment_allocation (if split_payment AND not valid_split_payment)
        if (payment_count >= 2 or "split_payment" in secondary_issues) and primary_issue != "valid_split_payment":
            actions.append("verify_payment_allocation")

        return actions[:5]


# ---------------------------------------------------------------------------
# VerifierAgent
# ---------------------------------------------------------------------------

class VerifierAgent:
    """
    VerifierAgent checks schema constraints, array limits, confidence score,
    and null handling rules before final output emission.
    """
    def __init__(self):
        self.name = "VerifierAgent"

    def process(self, case_output: Dict[str, Any]) -> Dict[str, Any]:
        aff = case_output["affected_entities"]
        aff["order_ids"]   = aff["order_ids"][:5]
        aff["item_ids"]    = aff["item_ids"][:5]
        aff["seller_ids"]  = aff["seller_ids"][:3]
        aff["payment_ids"] = aff["payment_ids"][:5]

        cust = case_output["customer_context"]
        cust["related_order_ids"] = cust["related_order_ids"][:5]

        prod = case_output["product_context"]
        prod["product_ids"]    = prod["product_ids"][:5]
        prod["category_names"] = prod["category_names"][:5]

        root = case_output["root_cause_analysis"]
        root["ranked_causes"]       = root["ranked_causes"][:3]
        root["responsible_parties"] = root["responsible_parties"][:3]

        case_output["evidence_ids"]        = case_output["evidence_ids"][:20]
        case_output["resolution_actions"]  = case_output["resolution_actions"][:5]

        # Clamp confidence to [0, 1] — value comes from PolicyAgent (dynamic)
        conf = case_output["case_assessment"].get("confidence", 0.90)
        try:
            conf = float(conf)
        except (ValueError, TypeError):
            conf = 0.90
        case_output["case_assessment"]["confidence"] = max(0.0, min(1.0, round(conf, 2)))

        # Strip internal fields not in output schema
        case_output.pop("llm_used", None)
        case_output.pop("llm_reasoning", None)

        return case_output


# ---------------------------------------------------------------------------
# CoordinatorAgent
# ---------------------------------------------------------------------------

class CoordinatorAgent:
    """
    CoordinatorAgent orchestrates all specialized agents, manages state handoffs,
    logs trace steps (including LLM reasoning) to trace.jsonl, and produces
    the final case JSON output.
    """
    def __init__(self, data_loader: DataLoader, trace_path: str = "trace.jsonl"):
        self.data_loader    = data_loader
        self.trace_path     = trace_path
        self.llm_client     = LLMClient()
        self.customer_agent      = CustomerAgent(data_loader)
        self.order_product_agent = OrderProductAgent(data_loader)
        self.payment_agent       = PaymentAgent(data_loader)
        self.delivery_agent      = DeliveryAgent(data_loader)
        self.policy_agent        = PolicyAgent(self.llm_client)
        self.verifier_agent      = VerifierAgent()

    def _log_trace(self, case_id: str, agent_name: str, action: str, details: Dict[str, Any]):
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "case_id":   case_id,
            "agent":     agent_name,
            "model":     self.llm_client.model,
            "action":    action,
            "details":   details,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(line)
        # Mirror to logging/
        logging_trace = os.path.join("logging", "trace.jsonl")
        os.makedirs("logging", exist_ok=True)
        with open(logging_trace, "a", encoding="utf-8") as f:
            f.write(line)

    def process_case(self, case_input: Dict[str, Any]) -> Dict[str, Any]:
        case_id          = case_input["case_id"]
        claimed_order_id = case_input["customer_request"]["claimed_order_id"]

        self._log_trace(case_id, "CoordinatorAgent", "receive_case",
                        {"claimed_order_id": claimed_order_id})

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
            "product_context":   order_product_ctx["product_context"],
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

        # Step 5: PolicyAgent — LLM reasons over facts from Steps 1-4
        policy_res = self.policy_agent.process(
            order,
            claimed_order_id,
            customer_ctx,
            order_product_ctx,
            payment_ctx,
            delivery_ctx,
        )
        self._log_trace(case_id, self.policy_agent.name, "apply_policy_EC_POLICY_V2", {
            "llm_used":      policy_res.get("llm_used"),
            "llm_reasoning": policy_res.get("llm_reasoning"),
            "primary_issue": policy_res["case_assessment"]["primary_issue"],
            "secondary_issues": policy_res["case_assessment"]["secondary_issues"],
            "case_status":   policy_res["case_assessment"]["case_status"],
        })

        # Assemble candidate output
        candidate_output = {
            "case_id":        case_id,
            "case_assessment": policy_res["case_assessment"],
            "affected_entities": {
                "order_ids":   order_product_ctx["affected_entities"]["order_ids"],
                "item_ids":    order_product_ctx["affected_entities"]["item_ids"],
                "seller_ids":  order_product_ctx["affected_entities"]["seller_ids"],
                "payment_ids": payment_ctx["payment_ids"],
            },
            "customer_context":      customer_ctx,
            "product_context":       order_product_ctx["product_context"],
            "delivery_analysis":     delivery_ctx["delivery_analysis"],
            "payment_reconciliation": payment_ctx["payment_reconciliation"],
            "root_cause_analysis":   policy_res["root_cause_analysis"],
            "evidence_ids":          policy_res["evidence_ids"],
            "financial_resolution":  policy_res["financial_resolution"],
            "resolution_actions":    policy_res["resolution_actions"],
            # Internal fields — removed by VerifierAgent
            "llm_used":     policy_res.get("llm_used"),
            "llm_reasoning": policy_res.get("llm_reasoning"),
        }

        # Step 6: VerifierAgent
        final_output = self.verifier_agent.process(candidate_output)
        self._log_trace(case_id, self.verifier_agent.name, "verify_and_finalize",
                        {"status": "SUCCESS"})

        return final_output
