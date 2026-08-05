"""Supervisor — LangGraph-style planner that owns the execution plan.

The supervisor keeps a Plan (ordered sequence of worker steps). For each
case it walks the plan, dispatches to workers via the MessageBus, gathers
results, and routes to the next step. On failure it retries with a small
budget; on persistent failure it marks the case as failed and continues.

This is the supervisor-worker pattern from the lesson: the planner
(here, the plan list) is the source of truth; workers do not call each
other directly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .bus import MessageBus, AgentMessage
from .tools import ToolLayer


@dataclass
class PlanStep:
    name: str               # worker name
    description: str        # human-readable purpose
    run: Callable[[Dict[str, Any], ToolLayer, MessageBus], Dict[str, Any]]
    required: bool = True   # if False, failure doesn't abort the case


@dataclass
class Plan:
    steps: List[PlanStep]

    def step_names(self) -> List[str]:
        return [s.name for s in self.steps]


def default_plan(workers: Dict[str, Callable]) -> Plan:
    """Standard EC_POLICY_V2 plan.

    Order matters — each worker reads what previous workers wrote
    into the shared case_state dict. Optional workers (e.g. context
    enrichment) run last and never block.
    """
    return Plan(
        steps=[
            PlanStep("customer_worker", "Resolve customer identity + history",
                     workers["customer"], required=True),
            PlanStep("order_worker", "Load order + items + sellers + products",
                     workers["order"], required=True),
            PlanStep("payment_worker", "Aggregate payments + reconcile",
                     workers["payment"], required=True),
            PlanStep("delivery_worker", "Compute delivery + handoff variance",
                     workers["delivery"], required=True),
            PlanStep("policy_worker", "Apply EC_POLICY_V2 → assessment + refund",
                     workers["policy"], required=True),
            PlanStep("context_worker", "Enrich customer/product/delivery context",
                     workers.get("context", lambda s, t, b: s),
                     required=False),
            PlanStep("evidence_worker", "Compose evidence_ids (reconstructable only)",
                     workers.get("evidence", lambda s, t, b: s),
                     required=True),
            PlanStep("verifier_worker", "Validate schema + limits + evidence",
                     workers["verifier"], required=True),
        ]
    )


@dataclass
class Supervisor:
    tools: ToolLayer
    bus: MessageBus
    plan: Plan

    # stats
    case_count: int = 0
    success_count: int = 0
    failure_count: int = 0

    def run_case(
        self,
        case: Dict[str, Any],
        *,
        max_retries: int = 1,
    ) -> Dict[str, Any]:
        case_id = case.get("case_id") or case.get("customer_request", {}).get(
            "claimed_order_id", "unknown"
        )
        # Top-level case span
        case_span = self.bus.publish(
            kind="plan.update",
            sender="supervisor",
            receiver="self",
            payload={"event": "case.start", "case_id": case_id,
                     "plan": self.plan.step_names()},
        ).span_id

        state: Dict[str, Any] = {
            "case_id": case_id,
            "claimed_order_id": case["customer_request"]["claimed_order_id"],
            "policy_version": case.get("policy_version"),
            "investigation_scope": case.get("investigation_scope", {}),
        }

        for step in self.plan.steps:
            step_span = self.bus.publish(
                kind="task.assign",
                sender="supervisor",
                receiver=step.name,
                parent_span_id=case_span,
                payload={"description": step.description},
            ).span_id

            attempt = 0
            ok = False
            last_err: Optional[str] = None
            while attempt <= max_retries:
                try:
                    out = step.run(state, self.tools, self.bus)
                    if out is None:
                        out = state
                    state = out
                    ok = True
                    self.bus.publish(
                        kind="task.result",
                        sender=step.name,
                        receiver="supervisor",
                        parent_span_id=step_span,
                        payload={"ok": True, "attempt": attempt},
                    )
                    break
                except Exception as e:
                    last_err = repr(e)
                    attempt += 1
                    if attempt > max_retries:
                        self.bus.publish(
                            kind="task.result",
                            sender=step.name,
                            receiver="supervisor",
                            parent_span_id=step_span,
                            payload={"ok": False, "error": last_err},
                        )

            if not ok and step.required:
                # Mark case as failed but continue so we still emit a JSON file.
                state["_failed_step"] = step.name
                state["_error"] = last_err
                self.failure_count += 1
                break

        self.bus.publish(
            kind="case.end",
            sender="supervisor",
            receiver="log",
            parent_span_id=case_span,
            payload={
                "case_id": case_id,
                "primary_issue": state.get("case_assessment", {}).get("primary_issue"),
                "refund_brl": state.get("financial_resolution", {}).get(
                    "recommended_refund_brl"
                ),
                "failed": "_failed_step" in state,
            },
        )

        self.case_count += 1
        if "_failed_step" not in state:
            self.success_count += 1
        return state