# Rule: E-commerce Dispute Resolution Multi-Agent Principles

## 1. Zero-Trust Verification Principle
- **Customer Request Is an Unverified Claim**: Treat `customer_request.message` solely as a case trigger / lookup identifier (`claimed_order_id`). Never use text sentiment or claimed amounts for refund calculations.
- **Relational Ground Truth**: Always derive entity state by joining source tables (`orders`, `order_items`, `order_payments`, `customers`, `products`).

## 2. Decision Tree & Responsibility Rules (`EC_POLICY_V2`)
- **Hierarchy of Issues**:
  1. `canceled_order_paid` -> Refund full payment (`platform`)
  2. `unavailable_order_paid` -> Refund full payment (`platform`)
  3. `late_delivery_seller` (variance > 0 AND late seller handoff) -> Refund freight (`seller`)
  4. `late_delivery_logistics` (variance > 0 AND on-time seller handoff) -> Refund freight (`logistics_provider`)
  5. `valid_split_payment` (split payments reconciled within 0.10 BRL) -> No refund (`no_action`)
  6. `unsupported_late_claim` (delivered on/before estimate) -> No refund (`no_action`)

## 3. LLM Reasoning Model Best Practices
- **Token Budgeting**: Allocate at least 3500 max tokens when using reasoning models (e.g. Qwen 3.5 9B) to allow room for internal reasoning tokens.
- **Fallback & Resilience**: Implement deterministic policy evaluation fallback in case of API timeouts or rate limits.
