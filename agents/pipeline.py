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
        # write output
        out_path = output_dir / f"{final['case_id']}.json"
        # remove private keys
        clean = {
            k: v
            for k, v in final.items()
            if not (k.startswith("_") or k == "order" or k == "customer")
        }
        clean.setdefault("case_id", final["case_id"])
        out_path.write_text(
            json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        cases_processed += 1
        if "_failed_step" in final:
            cases_failed += 1

    elapsed = round(time.time() - started, 2)

    metadata = {
        "model": "nvidia/nemotron-nano-9b-v2:free",
        "parameter_size": "9B",
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