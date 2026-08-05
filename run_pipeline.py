"""CLI entry point — runs the multi-agent pipeline over input/ and writes output/."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agents import build_pipeline, LOG_DIR
from agents.base import reset_trace, trace
from agents.llm import MODEL_NAME


def write_metadata(elapsed_s: float, case_count: int) -> None:
    meta = {
        "model": MODEL_NAME,
        "parameter_size": "9B",
        "framework": "Python 3.14 stdlib + custom multi-agent pipeline",
        "runtime": {
            "python_version": "3.14.3",
            "elapsed_seconds": round(elapsed_s, 2),
            "cases_processed": case_count,
        },
        "agents": [
            "coordinator",
            "customer_agent",
            "order_agent",
            "payment_agent",
            "delivery_agent",
            "policy_agent",
            "verifier_agent",
        ],
        "policy_version": "EC_POLICY_V2",
        "data_sources": [
            "olist_orders_dataset.csv",
            "olist_order_items_dataset.csv",
            "olist_order_payments_dataset.csv",
            "olist_customers_dataset.csv",
            "olist_products_dataset.csv",
            "olist_sellers_dataset.csv",
            "product_category_name_translation.csv",
        ],
    }
    (LOG_DIR / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process at most N cases (debug)",
    )
    args = parser.parse_args()

    coord = build_pipeline()
    # load all cases
    in_dir = Path(__file__).resolve().parent / "input"
    files = sorted(in_dir.glob("EC_*.json"))
    if args.limit:
        files = files[: args.limit]

    reset_trace()
    t0 = time.time()
    count = 0
    for f in files:
        case = json.loads(f.read_text(encoding="utf-8"))
        try:
            state = coord.run_case(case)
            count += 1
        except Exception as e:
            trace(
                "case.error",
                case_id=case.get("case_id"),
                error=repr(e),
            )
    elapsed = time.time() - t0
    write_metadata(elapsed, count)
    print(f"Processed {count}/{len(files)} cases in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
