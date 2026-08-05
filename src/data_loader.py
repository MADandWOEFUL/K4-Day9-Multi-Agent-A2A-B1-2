import os
import pandas as pd
from typing import Dict, Any, List, Optional

class DataLoader:
    """
    DataLoader loads all Olist CSV datasets into memory and indexes them
    for fast key-based retrieval across agents.
    """
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self._load_datasets()

    def _load_datasets(self):
        orders_path = os.path.join(self.data_dir, "olist_orders_dataset.csv")
        customers_path = os.path.join(self.data_dir, "olist_customers_dataset.csv")
        items_path = os.path.join(self.data_dir, "olist_order_items_dataset.csv")
        payments_path = os.path.join(self.data_dir, "olist_order_payments_dataset.csv")
        products_path = os.path.join(self.data_dir, "olist_products_dataset.csv")
        sellers_path = os.path.join(self.data_dir, "olist_sellers_dataset.csv")
        trans_path = os.path.join(self.data_dir, "product_category_name_translation.csv")

        self.orders_df = pd.read_csv(orders_path)
        self.customers_df = pd.read_csv(customers_path)
        self.items_df = pd.read_csv(items_path)
        self.payments_df = pd.read_csv(payments_path)
        self.products_df = pd.read_csv(products_path)
        self.sellers_df = pd.read_csv(sellers_path)
        self.trans_df = pd.read_csv(trans_path)

        # Build translation map
        self.category_translation = dict(
            zip(self.trans_df["product_category_name"], self.trans_df["product_category_name_english"])
        )

        # Pre-group items and payments for fast lookups
        self.items_by_order = {
            order_id: group.to_dict(orient="records")
            for order_id, group in self.items_df.groupby("order_id")
        }
        self.payments_by_order = {
            order_id: group.to_dict(orient="records")
            for order_id, group in self.payments_df.groupby("order_id")
        }
        self.orders_by_id = {
            row["order_id"]: row
            for row in self.orders_df.to_dict(orient="records")
        }
        self.customers_by_id = {
            row["customer_id"]: row
            for row in self.customers_df.to_dict(orient="records")
        }
        
        # Build map of customer_unique_id to list of order_ids
        customer_orders = {}
        for row in self.orders_df.to_dict(orient="records"):
            cid = row["customer_id"]
            if cid in self.customers_by_id:
                uniq_id = self.customers_by_id[cid]["customer_unique_id"]
                if uniq_id not in customer_orders:
                    customer_orders[uniq_id] = []
                customer_orders[uniq_id].append(row["order_id"])
        self.orders_by_customer_unique_id = customer_orders

        # Build product map
        self.products_by_id = {
            row["product_id"]: row
            for row in self.products_df.to_dict(orient="records")
        }

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

    def translate_category(self, category_name: str) -> str:
        if pd.isna(category_name) or not category_name:
            return "unknown"
        return self.category_translation.get(category_name, category_name)
