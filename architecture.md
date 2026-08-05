# Architecture — Day 9 Multi-Agent Dispute Resolution

## 1. Goal & design philosophy

Resolve 50 customer dispute cases over the Olist e-commerce dataset using a
true **multi-agent pipeline** where each agent owns a single domain, hands a
typed evidence package to the next agent, and is independently verifiable.
The system is policy-driven (`EC_POLICY_V2`); all numeric and array decisions
are deterministic and derived from the CSVs; the LLM is only used to (a)
classify customer/case metadata that requires natural-language reasoning and
(b) assemble the final structured case assessment under a strict JSON schema.

Two non-negotiable rules from the brief:

- Every numeric decision (refund, variance hours, reconciliation) is computed
  in deterministic Python against the CSVs.
- Every evidence ID is reconstructable directly from the CSVs in the exact
  `order:<id>`, `item:<oid>:<item_id>`, `payment:<oid>:<seq>`, `seller:<id>`,
  `policy:<code>` format.

## 2. Agent roster (7 agents, all used end-to-end)

| # | Agent           | Role                                                                                | Inputs (handoff)                                       | Outputs (handoff)                                                   |
| - | --------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------- |
| 1 | Coordinator     | Orchestrator. Loads case, owns the shared state, enforces pipeline order.           | Input JSON `EC_xxx.json`                               | `CaseState` (typed dict) shared across agents                       |
| 2 | CustomerAgent   | Resolves customer identity & full order history.                                    | `claimed_order_id`                                     | `customer_unique_id`, `related_order_ids`                           |
| 3 | OrderAgent      | Loads order row, items, sellers, products, categories.                              | `claimed_order_id`, `customer_unique_id`               | order row, `item_ids`, `seller_ids`, `product_ids`, `category_names` |
| 4 | PaymentAgent    | Aggregates payment rows, computes expected/payment totals.                          | order row, item rows                                   | payment reconciliation object                                       |
| 5 | DeliveryAgent   | Computes delivery variance and per-seller handoff variance.                         | order row, item rows                                   | delivery analysis object + `late_handoff_seller_ids`                |
| 6 | PolicyAgent     | Applies `EC_POLICY_V2` priority table → primary + secondary issues, refund, actions. | everything above                                       | case assessment, financial resolution, resolution actions           |
| 7 | VerifierAgent   | Independent re-check of every field, array limits, schema, evidence existence.      | full assembled case                                    | OK / hard-gate list; final write to `output/` and trace to `logging/` |

The **Coordinator** owns the orchestration. Each domain agent is a Python
class with one public method `run(state) -> state` and writes its slice into
the shared `CaseState`. Domain logic that is purely deterministic (joins,
sums, time math, policy rules) lives inside the agent as plain Python; the
LLM (`nvidia/nemotron-nano-9b-v2:free`) is invoked only by `PolicyAgent`
and `Coordinator` for the assembly that requires narrative reasoning, and
its output is **always validated** against the deterministic facts computed
by `OrderAgent`, `PaymentAgent`, and `DeliveryAgent` — the LLM is overruled
when it disagrees.

## 3. Data access (read-only, in-memory CSV caches)

`agents/data_loader.py` opens the 9 Olist CSVs once at startup and indexes
them in three in-memory dictionaries:

- `orders_by_id`: `{order_id -> order_row}`
- `items_by_order`: `{order_id -> [item_rows]}`
- `payments_by_order`: `{order_id -> [payment_rows]}`

Plus secondary maps:

- `customer_unique_index`: `{customer_unique_id -> [order_id, ...]}`
- `product_index`: `{product_id -> product_row}`
- `category_translation`: `{pt_category -> en_category}`

These indexes give O(1) lookups so each case runs in milliseconds except
for LLM calls. `Seller` rows are pulled only when needed for evidence IDs.

## 4. Hand-off protocol

Each agent receives and returns a single `CaseState` (a `dict[str, Any]`)
under this contract:

```text
CaseState keys (in order agents populate them):
  case_id, claimed_order_id
  customer_unique_id, related_order_ids          (CustomerAgent)
  order, items, sellers, products, categories     (OrderAgent)
  payment_reconciliation                          (PaymentAgent)
  delivery_analysis                               (DeliveryAgent)
  case_assessment, financial_resolution, actions  (PolicyAgent)
  evidence_ids, root_cause_analysis               (PolicyAgent)
  verified, verifier_report                       (VerifierAgent)
```

This contract is the **only** way state crosses agent boundaries, so the
hand-off is auditable from `logging/trace.jsonl`. Every agent logs its
in/out snapshot to trace.

## 5. LLM usage policy

Model: `nvidia/nemotron-nano-9b-v2:free` (≤ 10B parameters) via OpenRouter.

The LLM is used in exactly two places:

1. `PolicyAgent.assess(...)` — given deterministic facts, returns the final
   `case_assessment` JSON with `primary_issue`, `secondary_issues`,
   `case_status`, `confidence`, `responsible_parties`, `ranked_causes`. The
   schema is enforced via the OpenRouter `response_format={"type":"json_object"}`
   parameter and post-validated against the deterministic policy rules.
2. `Coordinator.summarize_evidence(...)` — chooses the minimum evidence set
   to attach to the case, prioritizing `order`, then items, then payments,
   then sellers (in the responsible-party order), then policy codes.

The Verifier re-runs all the policy rules deterministically; if the LLM's
`primary_issue` disagrees with the deterministic pick, the deterministic
pick wins and a warning is appended to `trace.jsonl`.

## 6. Deterministic policy table (`EC_POLICY_V2`)

Priority order (first match wins):

1. `canceled_order_paid`            — `order_status == "canceled"` and `payment_total > 0`
2. `unavailable_order_paid`         — `order_status == "unavailable"` and `payment_total > 0`
3. `late_delivery_seller`           — delivered after estimate AND any seller handed off after its `shipping_limit_date`
4. `late_delivery_logistics`        — delivered after estimate AND no seller was late
5. `valid_split_payment`            — ≥ 2 payment rows AND `|payment_total - expected_total| ≤ 0.10`
6. `unsupported_late_claim`         — delivered within estimate AND payment reconciled

Secondary issues (appended in fixed order when applicable):

1. `multi_item_order`     — ≥ 2 item rows
2. `multi_seller_order`   — ≥ 2 distinct sellers
3. `split_payment`        — ≥ 2 payment rows
4. `repeat_customer`      — `len(related_order_ids) ≥ 1`
5. `multiple_categories`  — ≥ 2 distinct categories

Actions are appended in this fixed order after the primary action:

- primary action from the table
- if late delivery seller → `review_seller_handoff`
- if late delivery logistics → `review_carrier_delay`
- if primary is not `valid_split_payment` AND ≥ 2 payments → `verify_payment_allocation`
- `verify_refund_completion` only when refund > 0
- `coordinate_multi_seller_case` only when ≥ 2 sellers

## 7. Limits & null-handling (hard rules)

Hard limits in `VerifierAgent`:

- max 5 `order_ids` / `item_ids` / `payment_ids` / `related_order_ids` / `product_ids` / `category_names`
- max 3 `seller_ids` / `ranked_causes` / `responsible_parties`
- max 20 `evidence_ids`, max 5 `resolution_actions`
- `confidence ∈ [0, 1]`, rounded to 2 decimals
- `delivery_variance_hours`, `handoff_variance_hours`, all BRL values rounded to 2 decimals
- missing order → `null` timestamps; no item rows → `expected_total_brl`,
  `difference_brl`, `reconciled` set to `null` and `items`, `sellers`,
  `products`, `categories`, `seller_handoff_analysis` to `[]`
- evidence IDs not reconstructable from CSVs are dropped (counted as
  false positives)

Any violation is a **hard gate** — the case is still emitted but flagged
in `trace.jsonl` and the affected field is replaced by its safe default.

## 8. File layout

```text
agents/
  __init__.py
  base.py           # Agent base + CaseState dataclass + trace logging
  data_loader.py    # CSV index loader
  llm.py            # OpenRouter client for nemotron-nano-9b-v2:free
  customer_agent.py
  order_agent.py
  payment_agent.py
  delivery_agent.py
  policy_agent.py
  verifier_agent.py
  coordinator.py    # pipeline driver
run_pipeline.py     # CLI entry: read input/, write output/, append trace
.env                # OPENROUTER_API_KEY=... (not committed)
.gitignore          # includes .env
logging/
  metadata.json     # model, params, framework, runtime
  trace.jsonl       # one JSON line per agent invocation per case
output/             # 50 JSON files EC_001.json ... EC_050.json
architecture.md     # this file
individual_5SoCuoiMHV_HoVaTen.md
```

## 9. End-to-end sequence per case

```text
Coordinator.load(case) ──► CustomerAgent ──► OrderAgent ──► PaymentAgent
                                                            └► DeliveryAgent
                                                  PolicyAgent ──► VerifierAgent
                                                            └► write output/EC_xxx.json
                                                            └► append trace.jsonl
```

The Coordinator retries each agent at most once on transient errors
(network, JSON parse). It never retries `VerifierAgent`; verification
failures are reported, not patched.

## 10. Determinism & reproducibility

- CSV data is loaded once and frozen at start.
- Time math uses `datetime` parsing, no timezone conversion (per brief).
- Rounding: `round(value, 2)` for hours and BRL; rounding only at the
  boundary that leaves the agent.
- All randomness (none in domain logic) is isolated to LLM sampling
  temperature = 0.
- Trace records the LLM raw response per agent so a re-run is replayable.

This satisfies the brief's hard requirement that "every agent has a real
hand-off" and that "verifiable evidence wins over narrative reasoning."
