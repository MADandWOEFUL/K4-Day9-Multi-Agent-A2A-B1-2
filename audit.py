"""Audit all 50 outputs against raw CSV data."""
from __future__ import annotations
import csv, json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "output"

def parse_dt(s):
    if not s or not isinstance(s, str): return None
    s = s.strip()
    if not s: return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try: return datetime.strptime(s, fmt)
        except ValueError: continue
    return None

orders = {}
with open(DATA / "olist_orders_dataset.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        orders[row["order_id"]] = row

items_by_order = defaultdict(list)
with open(DATA / "olist_order_items_dataset.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        items_by_order[row["order_id"]].append(row)

payments_by_order = defaultdict(list)
with open(DATA / "olist_order_payments_dataset.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        payments_by_order[row["order_id"]].append(row)

customers_by_id = {}
with open(DATA / "olist_customers_dataset.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        customers_by_id[row["customer_id"]] = row

products = {}
with open(DATA / "olist_products_dataset.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        products[row["product_id"]] = row

cat_trans = {}
with open(DATA / "product_category_name_translation.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        pt = (row.get("product_category_name") or "").strip()
        en = (row.get("product_category_name_english") or "").strip()
        if pt and en:
            cat_trans[pt] = en

customer_unique_orders = defaultdict(set)
for oid, orow in orders.items():
    cid = orow["customer_id"]
    cust = customers_by_id.get(cid)
    if cust:
        customer_unique_orders[cust["customer_unique_id"]].add(oid)

def get_true_values(oid):
    o = orders.get(oid)
    if not o:
        return None
    cust = customers_by_id.get(o["customer_id"])
    cuid = cust["customer_unique_id"] if cust else None
    related = sorted(customer_unique_orders.get(cuid, set()) - {oid})[:5]

    items = items_by_order.get(oid, [])
    items_sorted = sorted(items, key=lambda r: int(r.get("order_item_id", "0") or 0))
    item_ids = [f"{oid}:{r['order_item_id']}" for r in items_sorted]

    sellers_seen = []
    products_seen = []
    cats_seen = []
    for r in items_sorted:
        sid = r.get("seller_id")
        pid = r.get("product_id")
        if sid and sid not in sellers_seen:
            sellers_seen.append(sid)
        if pid and pid not in products_seen:
            products_seen.append(pid)
        prod = products.get(pid) if pid else None
        pt = prod.get("product_category_name") if prod else None
        en = cat_trans.get(pt) if pt else None
        if en and en not in cats_seen:
            cats_seen.append(en)

    pays = payments_by_order.get(oid, [])
    pays_sorted = sorted(pays, key=lambda r: int(r.get("payment_sequential", "0") or 0))
    payment_ids = [f"{oid}:{r['payment_sequential']}" for r in pays_sorted]

    delivered_at = parse_dt(o.get("order_delivered_customer_date"))
    estimated = parse_dt(o.get("order_estimated_delivery_date"))
    carrier = parse_dt(o.get("order_delivered_carrier_date"))
    da_after_est = delivered_at is not None and estimated is not None and delivered_at > estimated

    per_seller = {}
    for r in items_sorted:
        sid = r.get("seller_id")
        limit = parse_dt(r.get("shipping_limit_date"))
        if limit is not None:
            if sid not in per_seller or limit < per_seller[sid]:
                per_seller[sid] = limit
    late_handoff_sellers = []
    for sid in sorted(per_seller.keys()):
        limit = per_seller[sid]
        late = carrier is not None and limit is not None and carrier > limit
        if late:
            late_handoff_sellers.append(sid)

    item_total = sum(float(r.get("price", 0) or 0) for r in items_sorted)
    freight_total = sum(float(r.get("freight_value", 0) or 0) for r in items_sorted)
    payment_total = sum(float(r.get("payment_value", 0) or 0) for r in pays_sorted)
    expected = item_total + freight_total
    diff = payment_total - expected
    reconciled = abs(diff) <= 0.10

    return {
        "order_status": o.get("order_status"),
        "customer_unique_id": cuid,
        "related_order_ids": related,
        "item_count": len(items_sorted),
        "seller_ids": sellers_seen[:3],
        "product_ids": products_seen[:5],
        "category_names": cats_seen[:5],
        "payment_count": len(pays_sorted),
        "payment_ids": payment_ids[:5],
        "reconciled": reconciled,
        "diff": round(diff, 2),
        "item_total": round(item_total, 2),
        "freight_total": round(freight_total, 2),
        "payment_total": round(payment_total, 2),
        "expected_total": round(expected, 2),
        "delivered_after_estimate": da_after_est,
        "late_handoff_seller_ids": late_handoff_sellers[:3],
    }
