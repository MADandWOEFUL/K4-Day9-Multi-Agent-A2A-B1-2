import os
import pickle
import time
import pandas as pd
from typing import Dict, Any, List

def run_offline_join(data_dir: str = "data", output_cache_path: str = "data/preprocessed_cache.pkl"):
    """
    Offline pre-join and indexing for Olist datasets.
    Loads raw CSV files, performs joins/groupings in ultra-fast single pass,
    and dumps a serialized binary cache so DataLoader can start in < 0.1s.
    """
    start_time = time.time()
    print(f"[*] Starting offline data join and indexing from '{data_dir}'...")

    orders_path = os.path.join(data_dir, "olist_orders_dataset.csv")
    customers_path = os.path.join(data_dir, "olist_customers_dataset.csv")
    items_path = os.path.join(data_dir, "olist_order_items_dataset.csv")
    payments_path = os.path.join(data_dir, "olist_order_payments_dataset.csv")
    products_path = os.path.join(data_dir, "olist_products_dataset.csv")
    sellers_path = os.path.join(data_dir, "olist_sellers_dataset.csv")
    trans_path = os.path.join(data_dir, "product_category_name_translation.csv")

    print("  -> Loading CSV files into pandas...")
    orders_df = pd.read_csv(orders_path)
    customers_df = pd.read_csv(customers_path)
    items_df = pd.read_csv(items_path)
    payments_df = pd.read_csv(payments_path)
    products_df = pd.read_csv(products_path)
    sellers_df = pd.read_csv(sellers_path)
    trans_df = pd.read_csv(trans_path)

    print("  -> Building translation map...")
    category_translation = dict(
        zip(trans_df["product_category_name"], trans_df["product_category_name_english"])
    )

    print("  -> Fast indexing orders and customers by ID...")
    orders_records = orders_df.to_dict(orient="records")
    orders_by_id = {row["order_id"]: row for row in orders_records}
    customers_by_id = {row["customer_id"]: row for row in customers_df.to_dict(orient="records")}

    print("  -> Fast pre-grouping items by order_id...")
    items_by_order: Dict[str, List[Dict[str, Any]]] = {}
    for item in items_df.to_dict(orient="records"):
        oid = item["order_id"]
        if oid not in items_by_order:
            items_by_order[oid] = []
        items_by_order[oid].append(item)

    print("  -> Fast pre-grouping payments by order_id...")
    payments_by_order: Dict[str, List[Dict[str, Any]]] = {}
    for pay in payments_df.to_dict(orient="records"):
        oid = pay["order_id"]
        if oid not in payments_by_order:
            payments_by_order[oid] = []
        payments_by_order[oid].append(pay)

    print("  -> Fast building customer_unique_id to orders map...")
    customer_orders: Dict[str, List[str]] = {}
    for row in orders_records:
        cid = row["customer_id"]
        if cid in customers_by_id:
            uniq_id = customers_by_id[cid]["customer_unique_id"]
            if uniq_id not in customer_orders:
                customer_orders[uniq_id] = []
            customer_orders[uniq_id].append(row["order_id"])

    print("  -> Fast indexing products and sellers by ID...")
    products_by_id = {row["product_id"]: row for row in products_df.to_dict(orient="records")}
    sellers_by_id = {row["seller_id"]: row for row in sellers_df.to_dict(orient="records")}

    payload = {
        "category_translation": category_translation,
        "orders_by_id": orders_by_id,
        "customers_by_id": customers_by_id,
        "items_by_order": items_by_order,
        "payments_by_order": payments_by_order,
        "orders_by_customer_unique_id": customer_orders,
        "products_by_id": products_by_id,
        "sellers_by_id": sellers_by_id,
    }

    print(f"  -> Writing preprocessed binary cache to '{output_cache_path}'...")
    os.makedirs(os.path.dirname(output_cache_path), exist_ok=True)
    with open(output_cache_path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    cache_size_mb = os.path.getsize(output_cache_path) / (1024 * 1024)
    elapsed = time.time() - start_time
    print(f"[✓] Offline join complete in {elapsed:.2f}s! Cache size: {cache_size_mb:.2f} MB.")
    return output_cache_path

if __name__ == "__main__":
    run_offline_join()
