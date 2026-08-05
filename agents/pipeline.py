"""Pipeline runner — supervisor-worker with message bus + tool layer.

This is the new architecture:
  - Supervisor owns the plan and retry budget.
  - Workers are pure functions (state, tools, bus) -> state.
  - ToolLayer wraps data access (MCP-style).
  - MessageBus logs every interaction (A2A-style, span tree).
  - LLM is NOT in the hot path; it's a side annotation only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .bus import MessageBus
from .data_loader import DataIndex
from .supervisor import Supervisor, default_plan
from .tools import ToolLayer
from .workers import (
    customer_worker,
    order_worker,
    payment_worker,
    delivery_worker,
    policy_worker,
    context_worker,
    evidence_worker,
    verifier_worker,
)
from .llm import MODEL_NAME


def _public_delivery(d: dict) -> dict:
    """Strip internal keys from delivery_analysis per README §6 schema."""
    return {
        "delivered_at": d.get("delivered_at"),
        "estimated_delivery_at": d.get("estimated_delivery_at"),
        "carrier_handoff_at": d.get("carrier_handoff_at"),
        "delivery_variance_hours": d.get("delivery_variance_hours"),
        "seller_handoff_analysis": d.get("seller_handoff_analysis", []),
        "late_handoff_seller_ids": d.get("late_handoff_seller_ids", []),
    }


def _public_payment(p: dict) -> dict:
    """Strip internal keys from payment_reconciliation per README §6 schema."""
    return {
        "currency": p.get("currency", "BRL"),
        "item_total_brl": p.get("item_total_brl"),
        "freight_total_brl": p.get("freight_total_brl"),
        "expected_total_brl": p.get("expected_total_brl"),
        "payment_total_brl": p.get("payment_total_brl"),
        "difference_brl": p.get("difference_brl"),
        "reconciled": p.get("reconciled"),
        "payment_types": p.get("payment_types", []),
    }


def run_pipeline(
    input_dir: Path = Path("input"),
    output_dir: Path = Path("output"),
    log_dir: Path = Path("logging"),
    limit: int | None = None,
) -> Dict[str, Any]:
    started = time.time()
    log_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    index = DataIndex()
    bus = MessageBus(log_path=log_dir / "trace.jsonl")
    tools = ToolLayer(index=index)

    workers = {
        "customer": customer_worker,
        "order": order_worker,
        "payment": payment_worker,
        "delivery": delivery_worker,
        "policy": policy_worker,
        "context": context_worker,
        "evidence": evidence_worker,
        "verifier": verifier_worker,
    }
    supervisor = Supervisor(tools=tools, bus=bus, plan=default_plan(workers))

    inputs = sorted(input_dir.glob("EC_*.json"))
    if limit:
        inputs = inputs[:limit]

    cases_processed = 0
    cases_failed = 0

    for path in inputs:
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[skip] bad input {path}: {e}", file=sys.stderr)
            continue

        final = supervisor.run_case(case)
        out_path = output_dir / f"{final['case_id']}.json"
        # Strict output schema per README §6: only the documented top-level keys.
        clean = {
            "case_id": final["case_id"],
            "case_assessment": final["case_assessment"],
            "affected_entities": final["affected_entities"],
            "customer_context": final["customer_context"],
            "product_context": final["product_context"],
            "delivery_analysis": _public_delivery(final["delivery_analysis"]),
            "payment_reconciliation": _public_payment(
                final["payment_reconciliation"]
            ),
            "root_cause_analysis": final["root_cause_analysis"],
            "evidence_ids": final["evidence_ids"],
            "financial_resolution": final["financial_resolution"],
            "resolution_actions": final["resolution_actions"],
        }
        out_path.write_text(
            json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        cases_processed += 1
        if "_failed_step" in final:
            cases_failed += 1

    elapsed = round(time.time() - started, 2)

    metadata = {
        "model": MODEL_NAME,
        "parameter_size": "9B",
        "framework": "supervisor-worker + message bus + tool layer (custom)",
        "cases_processed": cases_processed,
        "cases_failed": cases_failed,
        "elapsed_seconds": elapsed,
        "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "supervisor_stats": {
            "case_count": supervisor.case_count,
            "success_count": supervisor.success_count,
            "failure_count": supervisor.failure_count,
        },
        "tool_calls": len(tools.trace_log),
        "messages": len(bus.messages),
    }
    (log_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Processed {cases_processed}/{len(inputs)} cases in {elapsed}s "
        f"(failed={cases_failed})"
    )
    return metadata


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--input", type=Path, default=Path("input"))
    ap.add_argument("--output", type=Path, default=Path("output"))
    ap.add_argument("--log", type=Path, default=Path("logging"))
    args = ap.parse_args()
    run_pipeline(
        input_dir=args.input,
        output_dir=args.output,
        log_dir=args.log,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())