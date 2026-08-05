# Fix: build customer_unique_orders map
customers_data = {}
with open("data/olist_customers_dataset.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        customers_data[row["customer_id"]] = row

customer_unique_orders = defaultdict(set)
for oid, orow in orders.items():
    cid = orow["customer_id"]
    if cid in customers_data:
        cuid = customers_data[cid]["customer_unique_id"]
        customer_unique_orders[cuid].add(oid)
