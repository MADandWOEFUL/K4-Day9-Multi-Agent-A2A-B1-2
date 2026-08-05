"""Compact auditor for all 50 cases."""
import csv, json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

def pd(s):
    if not s or not isinstance(s, str): return None
    s=s.strip()
    if not s: return None
    for f in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
        try: return datetime.strptime(s,f)
        except: continue
    return None

# Load CSVs
ords={}
with open(ROOT/"data/olist_orders_dataset.csv",encoding="utf-8") as f:
    for r in csv.DictReader(f): ords[r["order_id"]]=r
items=defaultdict(list)
with open(ROOT/"data/olist_order_items_dataset.csv",encoding="utf-8") as f:
    for r in csv.DictReader(f): items[r["order_id"]].append(r)
pays=defaultdict(list)
with open(ROOT/"data/olist_order_payments_dataset.csv",encoding="utf-8") as f:
    for r in csv.DictReader(f): pays[r["order_id"]].append(r)
custs={}
with open(ROOT/"data/olist_customers_dataset.csv",encoding="utf-8") as f:
    for r in csv.DictReader(f): custs[r["customer_id"]]=r
prods={}
with open(ROOT/"data/olist_products_dataset.csv",encoding="utf-8") as f:
    for r in csv.DictReader(f): prods[r["product_id"]]=r
cats={}
with open(ROOT/"data/product_category_name_translation.csv",encoding="utf-8") as f:
    for r in csv.DictReader(f):
        pt=(r.get("product_category_name") or "").strip()
        en=(r.get("product_category_name_english") or "").strip()
        if pt and en: cats[pt]=en

cuord=defaultdict(set)
for oid,o in ords.items():
    c=custs.get(o["customer_id"])
    if c: cuord[c["customer_unique_id"]].add(oid)

errs = []
for i in range(1,51):
    cid=f"EC_{i:03d}"
    inp=json.loads((ROOT/f"input/{cid}.json").read_text(encoding="utf-8"))
    out=json.loads((ROOT/f"output/{cid}.json").read_text(encoding="utf-8"))
    oid=inp["customer_request"]["claimed_order_id"]
    o=ords.get(oid)
    if not o:
        errs.append(f"{cid}: ORDER MISSING")
        continue

    # 1. Case assessment
    ca=out["case_assessment"]
    status=o["order_status"]
    paycnt=len(pays.get(oid,[]))
    del_cust=pd(o.get("order_delivered_customer_date"))
    est=pd(o.get("order_estimated_delivery_date"))
    da_after = del_cust is not None and est is not None and del_cust>est

    itms=items.get(oid,[])
    per_seller={}
    for r in itms:
        sid=r.get("seller_id")
        lim=pd(r.get("shipping_limit_date"))
        if lim is not None:
            if sid not in per_seller or lim<per_seller[sid]: per_seller[sid]=lim
    carrier=pd(o.get("order_delivered_carrier_date"))
    late_sellers=[sid for sid in sorted(per_seller) if carrier and per_seller[sid] and carrier>per_seller[sid]]

    pm=pays.get(oid,[])
    recon=abs(sum(float(r.get("payment_value",0) or 0) for r in pm)-sum(float(r.get("price",0) or 0) for r in itms)-sum(float(r.get("freight_value",0) or 0) for r in itms))<=0.10

    if status=="canceled" and paycnt>0: exp="canceled_order_paid"
    elif status=="unavailable" and paycnt>0: exp="unavailable_order_paid"
    elif da_after and late_sellers: exp="late_delivery_seller"
    elif da_after and not late_sellers: exp="late_delivery_logistics"
    elif paycnt>=2 and recon: exp="valid_split_payment"
    else: exp="unsupported_late_claim"

    got=ca["primary_issue"]
    if got!=exp:
        errs.append(f"{cid}: primary={got} expected={exp} (st={status} da={da_after} late={late_sellers} pcnt={paycnt} recon={recon})")

    # refund vs case_status
    ref=out.get("financial_resolution",{}).get("recommended_refund_brl",0) or 0
    cs=ca["case_status"]
    if cs=="action_required" and ref==0:
        errs.append(f"{cid}: case_status=action_required but refund=0")
    if cs=="no_action" and ref>0:
        errs.append(f"{cid}: case_status=no_action but refund={ref}")

    # item/payment/seller ids
    ae=out["affected_entities"]
    true_items=[f"{oid}:{r['order_item_id']}" for r in sorted(itms,key=lambda r:int(r.get("order_item_id","0") or 0))]
    true_sells=[]
    seen=set()
    for r in sorted(itms,key=lambda r:int(r.get("order_item_id","0") or 0)):
        sid=r.get("seller_id")
        if sid and sid not in seen: seen.add(sid); true_sells.append(sid)
    true_pays=[f"{oid}:{r['payment_sequential']}" for r in sorted(pays.get(oid,[]),key=lambda r:int(r.get("payment_sequential","0") or 0))]
    if ae.get("item_ids")!=true_items[:5]:
        errs.append(f"{cid}: item_ids mismatch out={ae.get('item_ids')} true={true_items[:5]}")
    if ae.get("seller_ids")!=true_sells[:3]:
        errs.append(f"{cid}: seller_ids mismatch out={ae.get('seller_ids')} true={true_sells[:3]}")
    if ae.get("payment_ids")!=true_pays[:5]:
        errs.append(f"{cid}: payment_ids mismatch out={ae.get('payment_ids')} true={true_pays[:5]}")

    # customer context
    cc=out["customer_context"]
    o2=ords[oid]
    c=custs.get(o2["customer_id"])
    cuid=c["customer_unique_id"] if c else None
    rel=sorted(cuord.get(cuid,set())-{oid})[:5]
    if cc.get("customer_unique_id")!=cuid:
        errs.append(f"{cid}: customer_unique_id out={cc.get('customer_unique_id')} true={cuid}")
    if cc.get("related_order_ids")!=rel:
        errs.append(f"{cid}: related_order_ids out={cc.get('related_order_ids')} true={rel}")

    # product context
    pc=out["product_context"]
    true_pids=[]
    true_cats=[]
    ps=set(); cs2=set()
    for r in sorted(itms,key=lambda r:int(r.get("order_item_id","0") or 0)):
        pid=r.get("product_id")
        if pid and pid not in ps: ps.add(pid); true_pids.append(pid)
        pr=prods.get(pid) if pid else None
        pt=pr.get("product_category_name") if pr else None
        en=cats.get(pt) if pt else None
        if en and en not in cs2: cs2.add(en); true_cats.append(en)
    if sorted(pc.get("product_ids",[]))!=sorted(true_pids[:5]):
        errs.append(f"{cid}: product_ids mismatch out={pc.get('product_ids')} true={true_pids[:5]}")
    if pc.get("category_names")!=true_cats[:5]:
        errs.append(f"{cid}: category_names out={pc.get('category_names')} true={true_cats[:5]}")

for e in errs: print(e)
print(f"\nTotal errors: {len(errs)}")
