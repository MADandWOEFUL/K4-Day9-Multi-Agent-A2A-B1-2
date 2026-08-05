"""Coordinator — orchestrator agent that drives the case pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .base import Agent, IN_DIR, OUT_DIR, reset_trace, trace
from .customer_agent import CustomerAgent
from .data_loader import DataIndex
from .delivery_agent import DeliveryAgent
from .order_agent import OrderAgent
from .payment_agent import PaymentAgent
from .policy_agent import PolicyAgent
from .verifier_agent import VerifierAgent


class Coordinator(Agent):
    name = "coordinator"

    def __init__(self, data_index: DataIndex) -> None:
        self.index = data_index
        self.customer_agent = CustomerAgent(data_index)
        self.order_agent = OrderAgent(data_index)
        self.payment_agent = PaymentAgent()
        self.delivery_agent = DeliveryAgent()
        self.policy_agent = PolicyAgent()
        self.verifier_agent = VerifierAgent(data_index)

    # ---- single-case -----------------------------------------------------

    def run_case(self, case: Dict[str, Any]) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            "case_id": case["case_id"],
            "claimed_order_id": case["customer_request"]["claimed_order_id"],
            "policy_version": case.get("policy_version"),
            "investigation_scope": case.get("investigation_scope", {}),
            "_index": self.index,
        }
        trace("pipeline.start", case_id=state["case_id"], order_id=state["claimed_order_id"])

        # Preload payments cache so payment_agent doesn't need index.
        state["_payments_cache"] = self.index.payments(state["claimed_order_id"])

        # Pipeline order
        for agent in (
            self.customer_agent,
            self.order_agent,
            self.payment_agent,
            self.delivery_agent,
            self.policy_agent,
            self.verifier_agent,
        ):
            state = agent.run(state)

        trace("pipeline.end", case_id=state["case_id"])
        return state

    # ---- batch ------------------------------------------------------------

    def run_all(self, in_dir: Path = IN_DIR) -> List[Dict[str, Any]]:
        reset_trace()
        results: List[Dict[str, Any]] = []
        files = sorted(in_dir.glob("EC_*.json"))
        for f in files:
            case = json.loads(f.read_text(encoding="utf-8"))
            try:
                state = self.run_case(case)
                results.append(state)
            except Exception as e:
                trace("pipeline.error", case_id=case.get("case_id"), error=repr(e))
        return results
