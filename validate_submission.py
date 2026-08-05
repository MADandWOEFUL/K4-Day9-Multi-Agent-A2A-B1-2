import os
import glob
import json
import sys

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

VALID_SECONDARY_ISSUES = {
    "multi_item_order",
    "multi_seller_order",
    "split_payment",
    "repeat_customer",
    "multiple_categories",
}

VALID_ACTIONS = {
    "issue_full_refund",
    "refund_freight",
    "explain_valid_split_payment",
    "reject_late_refund",
    "review_seller_handoff",
    "review_carrier_delay",
    "verify_refund_completion",
    "coordinate_multi_seller_case",
    "verify_payment_allocation",
}

PRIMARY_TO_CAUSE = {
    "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
    "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
    "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
    "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
}

REQUIRED_TOP_KEYS = {
    "case_id",
    "case_assessment",
    "affected_entities",
    "customer_context",
    "product_context",
    "delivery_analysis",
    "payment_reconciliation",
    "root_cause_analysis",
    "evidence_ids",
    "financial_resolution",
    "resolution_actions",
}

def validate_file(path: str) -> list[str]:
    errors = []
    fname = os.path.basename(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return [f"JSON decode error: {e}"]

    # 1. Check top-level keys
    keys = set(data.keys())
    missing_keys = REQUIRED_TOP_KEYS - keys
    if missing_keys:
        errors.append(f"Missing top-level keys: {missing_keys}")
    extra_keys = keys - REQUIRED_TOP_KEYS
    if extra_keys:
        errors.append(f"Unexpected extra top-level keys: {extra_keys}")

    case_id = data.get("case_id", "")
    expected_case_id = os.path.splitext(fname)[0]
    if case_id != expected_case_id:
        errors.append(f"case_id mismatch: expected '{expected_case_id}', got '{case_id}'")

    # 2. Case assessment
    ass = data.get("case_assessment", {})
    primary = ass.get("primary_issue")
    if primary not in VALID_PRIMARY_ISSUES:
        errors.append(f"Invalid primary_issue: '{primary}'")

    secondaries = ass.get("secondary_issues", [])
    if not isinstance(secondaries, list):
        errors.append("secondary_issues must be a list")
    else:
        for s in secondaries:
            if s not in VALID_SECONDARY_ISSUES:
                errors.append(f"Invalid secondary_issue: '{s}'")

    case_status = ass.get("case_status")
    if case_status not in ("action_required", "no_action"):
        errors.append(f"Invalid case_status: '{case_status}'")

    conf = ass.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
        errors.append(f"Invalid confidence: '{conf}' (must be float in [0.0, 1.0])")

    # 3. Affected entities bounds
    aff = data.get("affected_entities", {})
    if len(aff.get("order_ids", [])) > 5:
        errors.append(f"order_ids exceeds limit of 5: {len(aff.get('order_ids', []))}")
    if len(aff.get("item_ids", [])) > 5:
        errors.append(f"item_ids exceeds limit of 5: {len(aff.get('item_ids', []))}")
    if len(aff.get("seller_ids", [])) > 3:
        errors.append(f"seller_ids exceeds limit of 3: {len(aff.get('seller_ids', []))}")
    if len(aff.get("payment_ids", [])) > 5:
        errors.append(f"payment_ids exceeds limit of 5: {len(aff.get('payment_ids', []))}")

    # 4. Customer context
    cust = data.get("customer_context", {})
    if len(cust.get("related_order_ids", [])) > 5:
        errors.append(f"related_order_ids exceeds limit of 5: {len(cust.get('related_order_ids', []))}")

    # 5. Product context
    prod = data.get("product_context", {})
    if len(prod.get("product_ids", [])) > 5:
        errors.append(f"product_ids exceeds limit of 5: {len(prod.get('product_ids', []))}")
    if len(prod.get("category_names", [])) > 5:
        errors.append(f"category_names exceeds limit of 5: {len(prod.get('category_names', []))}")

    # 6. Root cause analysis
    rca = data.get("root_cause_analysis", {})
    ranked = rca.get("ranked_causes", [])
    if not isinstance(ranked, list) or len(ranked) == 0:
        errors.append("ranked_causes must be a non-empty list")
    elif len(ranked) > 3:
        errors.append(f"ranked_causes exceeds limit of 3: {len(ranked)}")
    else:
        for r in ranked:
            c = r.get("cause_code")
            if c not in VALID_CAUSE_CODES:
                errors.append(f"Invalid cause_code in ranked_causes: '{c}'")
        # Check rank 1 cause code matches primary issue
        if primary in PRIMARY_TO_CAUSE and ranked[0].get("cause_code") != PRIMARY_TO_CAUSE[primary]:
            errors.append(f"Rank 1 cause code '{ranked[0].get('cause_code')}' does not match primary issue '{primary}' (expected '{PRIMARY_TO_CAUSE[primary]}')")

    resp_parties = rca.get("responsible_parties", [])
    if len(resp_parties) > 3:
        errors.append(f"responsible_parties exceeds limit of 3: {len(resp_parties)}")

    # 7. Financial resolution
    fin = data.get("financial_resolution", {})
    if fin.get("currency") != "BRL":
        errors.append(f"Invalid currency: '{fin.get('currency')}' (must be 'BRL')")
    refund = fin.get("recommended_refund_brl")
    if not isinstance(refund, (int, float)) or refund < 0:
        errors.append(f"Invalid recommended_refund_brl: '{refund}'")
    else:
        # Case status consistency
        if refund > 0 and case_status != "action_required":
            errors.append(f"refund is {refund} > 0 but case_status is '{case_status}'")
        elif refund == 0 and case_status != "no_action":
            errors.append(f"refund is 0 but case_status is '{case_status}'")

    # 8. Resolution actions
    actions = data.get("resolution_actions", [])
    if not isinstance(actions, list) or len(actions) == 0:
        errors.append("resolution_actions must be a non-empty list")
    elif len(actions) > 5:
        errors.append(f"resolution_actions exceeds limit of 5: {len(actions)}")
    else:
        for a in actions:
            if a not in VALID_ACTIONS:
                errors.append(f"Invalid resolution_action: '{a}'")

    # 9. Evidence IDs
    evidences = data.get("evidence_ids", [])
    if not isinstance(evidences, list):
        errors.append("evidence_ids must be a list")
    elif len(evidences) > 20:
        errors.append(f"evidence_ids exceeds limit of 20: {len(evidences)}")
    else:
        for ev in evidences:
            prefix = ev.split(":")[0] if ":" in ev else ""
            if prefix not in ("order", "item", "payment", "seller", "policy"):
                errors.append(f"Invalid evidence_id format: '{ev}'")

    return errors

def main(output_dir: str = "output"):
    print(f"[*] Validating output files in '{output_dir}'...")
    files = sorted(glob.glob(os.path.join(output_dir, "EC_*.json")))
    if len(files) != 50:
        print(f"❌ Error: Expected 50 files, but found {len(files)}")
        sys.exit(1)

    all_passed = True
    for f in files:
        errs = validate_file(f)
        if errs:
            all_passed = False
            print(f"❌ {os.path.basename(f)} failed with {len(errs)} errors:")
            for e in errs:
                print(f"   - {e}")

    if all_passed:
        print("✅ ALL 50 FILES PASSED 100% SCHEMA AND BUSINESS RULE CHECKS!")
    else:
        print("❌ SOME FILES FAILED VALIDATION!")
        sys.exit(1)

if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "output"
    main(out_dir)
