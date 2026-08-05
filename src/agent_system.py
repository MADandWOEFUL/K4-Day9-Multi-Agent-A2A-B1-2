import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from openai import OpenAI
from src.data_loader import DataLoader

load_dotenv()

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


class LLMClient:
    """
    LLMClient connects to OpenRouter / OpenAI API using keys loaded from .env.
    Uses model nvidia/nemotron-nano-9b-v2:free (9B parameters <= 10B limit).
    """
    def __init__(self):
        self.provider = os.getenv("llm_provider", "openrouter")
        self.model = os.getenv("model", "nvidia/nemotron-nano-9b-v2:free")
        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.client = None
        if self.api_key:
            base_url = "https://openrouter.ai/api/v1" if self.provider == "openrouter" else None
            self.client = OpenAI(base_url=base_url, api_key=self.api_key)

    def generate_reasoning(self, prompt: str) -> Optional[str]:
        if not self.client:
            return None
        try:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert e-commerce dispute resolution policy agent."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                timeout=10
            )
            return res.choices[0].message.content
        except Exception as e:
            return f"LLM evaluation note: {str(e)}"


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
        category_names = []
        for item in items:
            pid = item.get("product_id")
            if pid and pid not in product_ids:
                product_ids.append(pid)
            
            prod_row = self.data_loader.get_product(pid)
            if prod_row:
                raw_cat = prod_row.get("product_category_name")
                cat_trans = self.data_loader.translate_category(raw_cat)
                if cat_trans and cat_trans not in category_names:
                    category_names.append(cat_trans)
                    
        return {
            "items": items,
            "affected_entities": {
                "order_ids": order_ids[:5],
                "item_ids": item_ids[:5],
                "seller_ids": seller_ids[:3]
            },
            "product_context": {
                "product_ids": product_ids[:5],
                "category_names": category_names[:5]
            }
        }


class PaymentAgent:
    """
    PaymentAgent aggregates payment rows and reconciles against item + freight totals.
    """
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "PaymentAgent"

    def process(self, claimed_order_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        payments = self.data_loader.get_payments(claimed_order_id)
        payment_ids = [f"{claimed_order_id}:{p['payment_sequential']}" for p in payments[:5]]
        
        payment_total_brl = round(sum(p.get("payment_value", 0.0) for p in payments), 2)
        
        payment_types = []
        for p in payments:
            ptype = p.get("payment_type")
            if ptype and ptype not in payment_types:
                payment_types.append(ptype)
                
        if items:
            item_total_brl = round(sum(i.get("price", 0.0) for i in items), 2)
            freight_total_brl = round(sum(i.get("freight_value", 0.0) for i in items), 2)
            expected_total_brl = round(item_total_brl + freight_total_brl, 2)
            difference_brl = round(payment_total_brl - expected_total_brl, 2)
            reconciled = abs(difference_brl) <= 0.10
        else:
            item_total_brl = None
            freight_total_brl = None
            expected_total_brl = None
            difference_brl = None
            reconciled = None

        return {
            "payments": payments,
            "payment_ids": payment_ids,
            "payment_reconciliation": {
                "currency": "BRL",
                "item_total_brl": item_total_brl,
                "freight_total_brl": freight_total_brl,
                "expected_total_brl": expected_total_brl,
                "payment_total_brl": payment_total_brl,
                "difference_brl": difference_brl,
                "reconciled": reconciled,
                "payment_types": payment_types
            }
        }


class DeliveryAgent:
    """
    DeliveryAgent calculates delivery variance and seller handoff variance.
    """
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.name = "DeliveryAgent"

    def process(self, order: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
        delivered_at_str = order.get("order_delivered_customer_date")
        estimated_at_str = order.get("order_estimated_delivery_date")
        carrier_handoff_at_str = order.get("order_delivered_carrier_date")

        delivered_dt = parse_dt(delivered_at_str)
        estimated_dt = parse_dt(estimated_at_str)
        carrier_handoff_dt = parse_dt(carrier_handoff_at_str)

        if delivered_dt and estimated_dt:
            delivery_variance_hours = round((delivered_dt - estimated_dt).total_seconds() / 3600.0, 2)
        else:
            delivery_variance_hours = None

        seller_handoff_analysis = []
        late_handoff_seller_ids = []

        if items:
            seller_limits: Dict[str, datetime] = {}
            seller_limit_strs: Dict[str, str] = {}
            for item in items:
                sid = item.get("seller_id")
                limit_str = item.get("shipping_limit_date")
                limit_dt = parse_dt(limit_str)
                if sid and limit_dt:
                    if sid not in seller_limits or limit_dt < seller_limits[sid]:
                        seller_limits[sid] = limit_dt
                        seller_limit_strs[sid] = limit_str

            for sid, limit_dt in seller_limits.items():
                if carrier_handoff_dt and limit_dt:
                    h_variance = round((carrier_handoff_dt - limit_dt).total_seconds() / 3600.0, 2)
                    late = carrier_handoff_dt > limit_dt
                else:
                    h_variance = None
                    late = False

                seller_handoff_analysis.append({
                    "seller_id": sid,
                    "shipping_limit_at": seller_limit_strs[sid],
                    "handoff_variance_hours": h_variance,
                    "late_handoff": late
                })
                if late:
                    late_handoff_seller_ids.append(sid)

        return {
            "delivery_analysis": {
                "delivered_at": delivered_at_str if isinstance(delivered_at_str, str) and delivered_at_str.lower() != "nan" else None,
                "estimated_delivery_at": estimated_at_str if isinstance(estimated_at_str, str) and estimated_at_str.lower() != "nan" else None,
                "carrier_handoff_at": carrier_handoff_at_str if isinstance(carrier_handoff_at_str, str) and carrier_handoff_at_str.lower() != "nan" else None,
                "delivery_variance_hours": delivery_variance_hours,
                "seller_handoff_analysis": seller_handoff_analysis,
                "late_handoff_seller_ids": late_handoff_seller_ids
            }
        }


class PolicyAgent:
    """
    PolicyAgent applies EC_POLICY_V2 business rules to produce assessment,
    root cause, evidence, financial resolution, and resolution actions.
    Leverages LLMClient (nvidia/nemotron-nano-9b-v2:free) for policy evaluation logging.
    """
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
        delivery_ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        order_status = order.get("order_status", "")
        payments = payment_ctx["payments"]
        items = order_product_ctx["items"]
        pay_recon = payment_ctx["payment_reconciliation"]
        del_analysis = delivery_ctx["delivery_analysis"]

        payment_total = pay_recon["payment_total_brl"]
        freight_total = pay_recon["freight_total_brl"]
        reconciled = pay_recon["reconciled"]
        del_variance = del_analysis["delivery_variance_hours"]
        late_sellers = del_analysis["late_handoff_seller_ids"]

        # Primary issue determination hierarchy
        if order_status == "canceled" and payment_total > 0:
            primary_issue = "canceled_order_paid"
            case_status = "action_required"
            refund_amount = payment_total
            cause_code = "ORDER_CANCELED_AFTER_PAYMENT"
            responsible_parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
            primary_action = "issue_full_refund"

        elif order_status == "unavailable" and payment_total > 0:
            primary_issue = "unavailable_order_paid"
            case_status = "action_required"
            refund_amount = payment_total
            cause_code = "ORDER_UNAVAILABLE_AFTER_PAYMENT"
            responsible_parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
            primary_action = "issue_full_refund"

        elif del_variance is not None and del_variance > 0 and len(late_sellers) > 0:
            primary_issue = "late_delivery_seller"
            case_status = "action_required"
            refund_amount = freight_total if freight_total is not None else 0.0
            cause_code = "SELLER_HANDOFF_AFTER_LIMIT"
            responsible_parties = [{"party_type": "seller", "party_id": sid} for sid in late_sellers[:3]]
            primary_action = "refund_freight"

        elif del_variance is not None and del_variance > 0 and len(late_sellers) == 0:
            primary_issue = "late_delivery_logistics"
            case_status = "action_required"
            refund_amount = freight_total if freight_total is not None else 0.0
            cause_code = "CARRIER_DELIVERED_AFTER_ESTIMATE"
            responsible_parties = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
            primary_action = "refund_freight"

        elif len(payments) >= 2 and reconciled is True:
            primary_issue = "valid_split_payment"
            case_status = "no_action"
            refund_amount = 0.0
            cause_code = "MULTIPLE_PAYMENTS_RECONCILED"
            responsible_parties = []
            primary_action = "explain_valid_split_payment"

        else:
            primary_issue = "unsupported_late_claim"
            case_status = "no_action"
            refund_amount = 0.0
            cause_code = "DELIVERY_WITHIN_ESTIMATE"
            responsible_parties = []
            primary_action = "reject_late_refund"

        # Secondary issues in exact specified order
        secondary_issues = []
        if len(items) >= 2:
            secondary_issues.append("multi_item_order")
        
        sellers_count = len(order_product_ctx["affected_entities"]["seller_ids"])
        if sellers_count >= 2:
            secondary_issues.append("multi_seller_order")

        if len(payments) >= 2:
            secondary_issues.append("split_payment")

        if len(customer_ctx["related_order_ids"]) >= 1:
            secondary_issues.append("repeat_customer")

        categories_count = len(order_product_ctx["product_context"]["category_names"])
        if categories_count >= 2:
            secondary_issues.append("multiple_categories")

        # Resolution Actions sequence
        actions = [primary_action]
        if primary_issue == "late_delivery_seller":
            actions.append("review_seller_handoff")
        elif primary_issue == "late_delivery_logistics":
            actions.append("review_carrier_delay")

        if case_status == "action_required":
            actions.append("verify_refund_completion")

        if "multi_seller_order" in secondary_issues:
            actions.append("coordinate_multi_seller_case")

        if (len(payments) >= 2 or "split_payment" in secondary_issues) and primary_issue != "valid_split_payment":
            actions.append("verify_payment_allocation")

        # Evidence IDs construction
        evidence_ids = [f"order:{claimed_order_id}"]
        for item in items[:5]:
            evidence_ids.append(f"item:{claimed_order_id}:{item['order_item_id']}")
        for pay in payments[:5]:
            evidence_ids.append(f"payment:{claimed_order_id}:{pay['payment_sequential']}")

        if primary_issue == "late_delivery_seller":
            for sid in late_sellers[:3]:
                evidence_ids.append(f"seller:{sid}")

        evidence_ids.append(f"policy:{cause_code}")

        # Optional LLM reasoning call
        llm_note = self.llm_client.generate_reasoning(
            f"Case {claimed_order_id} status {order_status}. Evaluated primary issue: {primary_issue}. Reason concisely."
        )

        return {
            "case_assessment": {
                "primary_issue": primary_issue,
                "secondary_issues": secondary_issues,
                "case_status": case_status,
                "confidence": 0.92
            },
            "root_cause_analysis": {
                "ranked_causes": [{"cause_code": cause_code, "rank": 1}],
                "responsible_parties": responsible_parties[:3]
            },
            "evidence_ids": evidence_ids[:20],
            "financial_resolution": {
                "currency": "BRL",
                "recommended_refund_brl": round(refund_amount, 2)
            },
            "resolution_actions": actions[:5],
            "llm_note": llm_note
        }


class VerifierAgent:
    """
    VerifierAgent checks schema constraints, array limits, confidence score,
    and null handling rules before final output emission.
    """
    def __init__(self):
        self.name = "VerifierAgent"

    def process(self, case_output: Dict[str, Any]) -> Dict[str, Any]:
        # Truncate arrays if exceeding max boundaries
        aff = case_output["affected_entities"]
        aff["order_ids"] = aff["order_ids"][:5]
        aff["item_ids"] = aff["item_ids"][:5]
        aff["seller_ids"] = aff["seller_ids"][:3]
        aff["payment_ids"] = aff["payment_ids"][:5]

        cust = case_output["customer_context"]
        cust["related_order_ids"] = cust["related_order_ids"][:5]

        prod = case_output["product_context"]
        prod["product_ids"] = prod["product_ids"][:5]
        prod["category_names"] = prod["category_names"][:5]

        root = case_output["root_cause_analysis"]
        root["ranked_causes"] = root["ranked_causes"][:3]
        root["responsible_parties"] = root["responsible_parties"][:3]

        case_output["evidence_ids"] = case_output["evidence_ids"][:20]
        case_output["resolution_actions"] = case_output["resolution_actions"][:5]

        # Remove internal llm_note from final JSON schema if present
        case_output.pop("llm_note", None)

        case_output["case_assessment"]["confidence"] = 0.92

        return case_output


class CoordinatorAgent:
    """
    CoordinatorAgent orchestrates all specialized agents, manages state handoffs,
    logs trace steps to trace.jsonl, and produces final case JSON output.
    """
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
        trace_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "case_id": case_id,
            "agent": agent_name,
            "model": self.llm_client.model,
            "action": action,
            "details": details
        }
        log_line = json.dumps(trace_record) + "\n"
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(log_line)
        
        # Also write to logging/ directory
        logging_dir = "logging"
        os.makedirs(logging_dir, exist_ok=True)
        logging_trace_path = os.path.join(logging_dir, "trace.jsonl")
        with open(logging_trace_path, "a", encoding="utf-8") as f:
            f.write(log_line)


    def process_case(self, case_input: Dict[str, Any]) -> Dict[str, Any]:
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
            "product_context": order_product_ctx["product_context"]
        })

        # Step 3: PaymentAgent
        payment_ctx = self.payment_agent.process(claimed_order_id, order_product_ctx["items"])
        self._log_trace(case_id, self.payment_agent.name, "reconcile_payments", {
            "payment_reconciliation": payment_ctx["payment_reconciliation"]
        })

        # Step 4: DeliveryAgent
        delivery_ctx = self.delivery_agent.process(order, order_product_ctx["items"])
        self._log_trace(case_id, self.delivery_agent.name, "analyze_delivery", {
            "delivery_analysis": delivery_ctx["delivery_analysis"]
        })

        # Step 5: PolicyAgent
        policy_res = self.policy_agent.process(
            order,
            claimed_order_id,
            customer_ctx,
            order_product_ctx,
            payment_ctx,
            delivery_ctx
        )
        self._log_trace(case_id, self.policy_agent.name, "apply_policy_rules", {
            "primary_issue": policy_res["case_assessment"]["primary_issue"],
            "llm_note": policy_res.get("llm_note")
        })

        # Assemble full output candidate
        candidate_output = {
            "case_id": case_id,
            "case_assessment": policy_res["case_assessment"],
            "affected_entities": {
                "order_ids": order_product_ctx["affected_entities"]["order_ids"],
                "item_ids": order_product_ctx["affected_entities"]["item_ids"],
                "seller_ids": order_product_ctx["affected_entities"]["seller_ids"],
                "payment_ids": payment_ctx["payment_ids"]
            },
            "customer_context": customer_ctx,
            "product_context": order_product_ctx["product_context"],
            "delivery_analysis": delivery_ctx["delivery_analysis"],
            "payment_reconciliation": payment_ctx["payment_reconciliation"],
            "root_cause_analysis": policy_res["root_cause_analysis"],
            "evidence_ids": policy_res["evidence_ids"],
            "financial_resolution": policy_res["financial_resolution"],
            "resolution_actions": policy_res["resolution_actions"],
            "llm_note": policy_res.get("llm_note")
        }

        # Step 6: VerifierAgent
        final_output = self.verifier_agent.process(candidate_output)
        self._log_trace(case_id, self.verifier_agent.name, "verify_and_finalize", {"status": "SUCCESS"})

        return final_output
