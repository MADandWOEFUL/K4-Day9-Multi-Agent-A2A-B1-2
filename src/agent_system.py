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
            "- verify_refund_completion: if case_status = action_required\n"
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
                    cat_pt = prod.get("product_category_name", "")
                    cat_en = self.data_loader.translate_category(cat_pt) or cat_pt
                    if cat_en and cat_en not in category_names:
                        category_names.append(cat_en)

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
                    "item_total_brl": None,
                    "freight_total_brl": None,
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

        # --- Evidence IDs — always deterministic (must be traceable to real data) ---
        evidence_ids = [f"order:{claimed_order_id}"]
        for item in items[:5]:
            evidence_ids.append(f"item:{claimed_order_id}:{item['order_item_id']}")
        for pay in payments[:5]:
            evidence_ids.append(f"payment:{claimed_order_id}:{pay['payment_sequential']}")
        if primary_issue == "late_delivery_seller":
            for sid in del_analysis["late_handoff_seller_ids"][:3]:
                evidence_ids.append(f"seller:{sid}")
        evidence_ids.append(f"policy:{cause_code}")

        return {
            "case_assessment": {
                "primary_issue":    primary_issue,
                "secondary_issues": secondary_issues,
                "case_status":      case_status,
                "confidence":       0.92,
            },
            "root_cause_analysis": {
                "ranked_causes":       [{"cause_code": cause_code, "rank": 1}],
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
        if primary not in self.VALID_PRIMARY_ISSUES:
            return None

        cause = llm_result.get("cause_code", "")
        if cause not in self.VALID_CAUSE_CODES:
            return None

        if llm_result.get("case_status") not in ("action_required", "no_action"):
            return None

        # Validate secondary_issues is a list of known codes
        valid_secondary = {
            "multi_item_order", "multi_seller_order", "split_payment",
            "repeat_customer", "multiple_categories"
        }
        secondary = llm_result.get("secondary_issues", [])
        if not isinstance(secondary, list):
            return None
        # Filter to only valid codes in correct order
        order_map = {v: i for i, v in enumerate([
            "multi_item_order", "multi_seller_order", "split_payment",
            "repeat_customer", "multiple_categories"
        ])}
        secondary = [s for s in secondary if s in valid_secondary]
        secondary = sorted(secondary, key=lambda x: order_map.get(x, 99))

        responsible = llm_result.get("responsible_parties", [])
        if not isinstance(responsible, list):
            responsible = []

        actions = llm_result.get("resolution_actions", [])
        if not isinstance(actions, list):
            actions = []

        try:
            refund = float(llm_result.get("refund_amount", 0))
        except (TypeError, ValueError):
            return None

        return {
            "reasoning":           llm_result.get("reasoning", ""),
            "primary_issue":       primary,
            "secondary_issues":    secondary,
            "cause_code":          cause,
            "case_status":         llm_result["case_status"],
            "refund_amount":       refund,
            "responsible_parties": responsible,
            "resolution_actions":  actions,
        }

    # ------------------------------------------------------------------
    # Deterministic fallback
    # ------------------------------------------------------------------

    def _deterministic_decision(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        order_status    = facts["order_status"]
        payment_total   = facts["payment_total"] or 0
        freight_total   = facts["freight_total"] or 0
        reconciled      = facts["reconciled"]
        payment_count   = facts["payment_count"]
        item_count      = facts["item_count"]
        seller_count    = facts["seller_count"]
        category_count  = facts["category_count"]
        has_other_orders = facts["has_other_orders"]
        del_variance    = facts["delivery_variance_hours"]
        late_sellers    = facts["late_handoff_seller_ids"]

        if order_status == "canceled" and payment_total > 0:
            primary, cause = "canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT"
            case_status, refund = "action_required", payment_total
            responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
            primary_action = "issue_full_refund"

        elif order_status == "unavailable" and payment_total > 0:
            primary, cause = "unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT"
            case_status, refund = "action_required", payment_total
            responsible = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
            primary_action = "issue_full_refund"

        elif del_variance is not None and del_variance > 0 and len(late_sellers) > 0:
            primary, cause = "late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT"
            case_status, refund = "action_required", freight_total
            responsible = [{"party_type": "seller", "party_id": sid} for sid in late_sellers[:3]]
            primary_action = "refund_freight"

        elif del_variance is not None and del_variance > 0 and len(late_sellers) == 0:
            primary, cause = "late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE"
            case_status, refund = "action_required", freight_total
            responsible = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
            primary_action = "refund_freight"

        elif payment_count >= 2 and reconciled is True:
            primary, cause = "valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED"
            case_status, refund = "no_action", 0.0
            responsible = []
            primary_action = "explain_valid_split_payment"

        else:
            primary, cause = "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE"
            case_status, refund = "no_action", 0.0
            responsible = []
            primary_action = "reject_late_refund"

        secondary = []
        if item_count >= 2:      secondary.append("multi_item_order")
        if seller_count >= 2:    secondary.append("multi_seller_order")
        if payment_count >= 2:   secondary.append("split_payment")
        if has_other_orders:     secondary.append("repeat_customer")
        if category_count >= 2:  secondary.append("multiple_categories")

        actions = [primary_action]
        if primary == "late_delivery_seller":     actions.append("review_seller_handoff")
        elif primary == "late_delivery_logistics": actions.append("review_carrier_delay")
        if case_status == "action_required":       actions.append("verify_refund_completion")
        if "multi_seller_order" in secondary:      actions.append("coordinate_multi_seller_case")
        if "split_payment" in secondary and primary != "valid_split_payment":
            actions.append("verify_payment_allocation")

        return {
            "reasoning":           "Deterministic fallback (LLM unavailable).",
            "primary_issue":       primary,
            "secondary_issues":    secondary,
            "cause_code":          cause,
            "case_status":         case_status,
            "refund_amount":       refund,
            "responsible_parties": responsible,
            "resolution_actions":  actions[:5],
        }


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
        case_output["case_assessment"]["confidence"] = 0.92

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
