import os
import pickle
import time
import pandas as pd
from typing import Dict, Any, List, Optional

class DataLoader:
    """
    DataLoader loads Olist datasets into memory and indexes them
    for fast O(1) key-based retrieval across agents.
    
    Supports instant loading from a precomputed binary cache (data/preprocessed_cache.pkl).
    If cache is missing, it will automatically load from CSV and create the cache.
    """
    def __init__(self, data_dir: str = "data", cache_file: str = "data/preprocessed_cache.pkl", force_reload: bool = False):
        self.data_dir = data_dir
        self.cache_file = cache_file
        self.force_reload = force_reload

        self.category_translation: Dict[str, str] = {}
        self.orders_by_id: Dict[str, Dict[str, Any]] = {}
        self.customers_by_id: Dict[str, Dict[str, Any]] = {}
        self.items_by_order: Dict[str, List[Dict[str, Any]]] = {}
        self.payments_by_order: Dict[str, List[Dict[str, Any]]] = {}
        self.orders_by_customer_unique_id: Dict[str, List[str]] = {}
        self.products_by_id: Dict[str, Dict[str, Any]] = {}
        self.sellers_by_id: Dict[str, Dict[str, Any]] = {}

        self._load_data()

    def _load_data(self):
        if not self.force_reload and os.path.exists(self.cache_file):
            start = time.time()
            with open(self.cache_file, "rb") as f:
                cached = pickle.load(f)
            self.category_translation = cached["category_translation"]
            self.orders_by_id = cached["orders_by_id"]
            self.customers_by_id = cached["customers_by_id"]
            self.items_by_order = cached["items_by_order"]
            self.payments_by_order = cached["payments_by_order"]
            self.orders_by_customer_unique_id = cached["orders_by_customer_unique_id"]
            self.products_by_id = cached["products_by_id"]
            self.sellers_by_id = cached.get("sellers_by_id", {})
            elapsed = time.time() - start
            print(f"[DataLoader] Loaded preprocessed dataset from '{self.cache_file}' in {elapsed:.3f}s.")
        else:
            self._load_from_csv_and_cache()

    def _load_from_csv_and_cache(self):
        start = time.time()
        print(f"[DataLoader] Reading CSVs from '{self.data_dir}' and building pre-joined indexes...")
        orders_path = os.path.join(self.data_dir, "olist_orders_dataset.csv")
        customers_path = os.path.join(self.data_dir, "olist_customers_dataset.csv")
        items_path = os.path.join(self.data_dir, "olist_order_items_dataset.csv")
        payments_path = os.path.join(self.data_dir, "olist_order_payments_dataset.csv")
        products_path = os.path.join(self.data_dir, "olist_products_dataset.csv")
        sellers_path = os.path.join(self.data_dir, "olist_sellers_dataset.csv")
        trans_path = os.path.join(self.data_dir, "product_category_name_translation.csv")

        orders_df = pd.read_csv(orders_path)
        customers_df = pd.read_csv(customers_path)
        items_df = pd.read_csv(items_path)
        payments_df = pd.read_csv(payments_path)
        products_df = pd.read_csv(products_path)
        sellers_df = pd.read_csv(sellers_path)
        trans_df = pd.read_csv(trans_path)

        # Build translation map
        self.category_translation = dict(
            zip(trans_df["product_category_name"], trans_df["product_category_name_english"])
        )

        orders_records = orders_df.to_dict(orient="records")
        self.orders_by_id = {row["order_id"]: row for row in orders_records}
        self.customers_by_id = {row["customer_id"]: row for row in customers_df.to_dict(orient="records")}

        # Pre-group items and payments
        items_by_order: Dict[str, List[Dict[str, Any]]] = {}
        for item in items_df.to_dict(orient="records"):
            oid = item["order_id"]
            if oid not in items_by_order:
                items_by_order[oid] = []
            items_by_order[oid].append(item)
        self.items_by_order = items_by_order

        payments_by_order: Dict[str, List[Dict[str, Any]]] = {}
        for pay in payments_df.to_dict(orient="records"):
            oid = pay["order_id"]
            if oid not in payments_by_order:
                payments_by_order[oid] = []
            payments_by_order[oid].append(pay)
        self.payments_by_order = payments_by_order
        
        # Sort orders chronologically by order_purchase_timestamp
        sorted_orders = sorted(
            orders_records,
            key=lambda r: str(r.get("order_purchase_timestamp", ""))
        )

        # Build map of customer_unique_id to list of order_ids
        customer_orders: Dict[str, List[str]] = {}
        for row in sorted_orders:
            cid = row["customer_id"]
            if cid in self.customers_by_id:
                uniq_id = self.customers_by_id[cid]["customer_unique_id"]
                if uniq_id not in customer_orders:
                    customer_orders[uniq_id] = []
                customer_orders[uniq_id].append(row["order_id"])
        self.orders_by_customer_unique_id = customer_orders

        # Build product & seller map
        self.products_by_id = {row["product_id"]: row for row in products_df.to_dict(orient="records")}
        self.sellers_by_id = {row["seller_id"]: row for row in sellers_df.to_dict(orient="records")}

        # Cache to disk for instant future loads
        try:
            payload = {
                "category_translation": self.category_translation,
                "orders_by_id": self.orders_by_id,
                "customers_by_id": self.customers_by_id,
                "items_by_order": self.items_by_order,
                "payments_by_order": self.payments_by_order,
                "orders_by_customer_unique_id": self.orders_by_customer_unique_id,
                "products_by_id": self.products_by_id,
                "sellers_by_id": self.sellers_by_id,
            }
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            print(f"[DataLoader] Successfully cached pre-joined data to '{self.cache_file}'.")
        except Exception as e:
            print(f"[DataLoader] Warning: Could not write cache file '{self.cache_file}': {e}")

        elapsed = time.time() - start
        print(f"[DataLoader] CSV loading and indexing completed in {elapsed:.2f}s.")

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return self.orders_by_id.get(order_id)

    def get_customer(self, customer_id: str) -> Optional[Dict[str, Any]]:
        return self.customers_by_id.get(customer_id)

    def get_items(self, order_id: str) -> List[Dict[str, Any]]:
        return self.items_by_order.get(order_id, [])

    def get_payments(self, order_id: str) -> List[Dict[str, Any]]:
        return self.payments_by_order.get(order_id, [])

    def get_customer_related_orders(self, customer_unique_id: str, claimed_order_id: str) -> List[str]:
        all_orders = self.orders_by_customer_unique_id.get(customer_unique_id, [])
        return [oid for oid in all_orders if oid != claimed_order_id]

    def get_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        return self.products_by_id.get(product_id)

    def get_seller(self, seller_id: str) -> Optional[Dict[str, Any]]:
        return self.sellers_by_id.get(seller_id)

    def translate_category(self, category_name: str) -> str:
        if pd.isna(category_name) or not category_name:
            return "unknown"
        return self.category_translation.get(category_name, category_name)
