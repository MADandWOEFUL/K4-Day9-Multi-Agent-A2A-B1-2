"""CSV index loader — loads the 9 Olist CSVs once and builds O(1) lookups."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import DATA_DIR


class DataIndex:
    """Read-only in-memory index of the Olist CSVs."""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.data_dir = data_dir
        # primary indexes
        self.orders_by_id: Dict[str, Dict[str, Any]] = {}
        self.items_by_order: Dict[str, List[Dict[str, Any]]] = {}
        self.payments_by_order: Dict[str, List[Dict[str, Any]]] = {}
        self.customer_by_id: Dict[str, Dict[str, Any]] = {}
        self.product_by_id: Dict[str, Dict[str, Any]] = {}
        self.seller_by_id: Dict[str, Dict[str, Any]] = {}
        self.reviews_by_order: Dict[str, List[Dict[str, Any]]] = {}
        # secondary
        self.orders_by_customer_unique: Dict[str, List[str]] = {}
        self.category_en: Dict[str, str] = {}
        self._load()

    # ----- low-level ---------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> List[Dict[str, Any]]:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    # ----- loaders -----------------------------------------------------------

    def _load(self) -> None:
        for row in self._read(self.data_dir / "olist_orders_dataset.csv"):
            self.orders_by_id[row["order_id"]] = row
        for row in self._read(self.data_dir / "olist_order_items_dataset.csv"):
            self.items_by_order.setdefault(row["order_id"], []).append(row)
        for row in self._read(self.data_dir / "olist_order_payments_dataset.csv"):
            self.payments_by_order.setdefault(row["order_id"], []).append(row)
        for row in self._read(self.data_dir / "olist_customers_dataset.csv"):
            self.customer_by_id[row["customer_id"]] = row
            cuid = row["customer_unique_id"]
            # link order_id -> customer_unique via orders table below
        for row in self._read(self.data_dir / "olist_products_dataset.csv"):
            self.product_by_id[row["product_id"]] = row
        for row in self._read(self.data_dir / "olist_sellers_dataset.csv"):
            self.seller_by_id[row["seller_id"]] = row
        for row in self._read(self.data_dir / "olist_order_reviews_dataset.csv"):
            self.reviews_by_order.setdefault(row["order_id"], []).append(row)

        # build orders_by_customer_unique using the orders↔customers link
        for order_id, orow in self.orders_by_id.items():
            cust = self.customer_by_id.get(orow["customer_id"])
            if not cust:
                continue
            self.orders_by_customer_unique.setdefault(cust["customer_unique_id"], []).append(
                order_id
            )

        # category translation (PT → EN); keep PT if no translation.
        for row in self._read(
            self.data_dir / "product_category_name_translation.csv"
        ):
            pt = (row.get("product_category_name") or "").strip()
            en = (row.get("product_category_name_english") or "").strip()
            if pt and en:
                self.category_en[pt] = en
    # ----- accessors ---------------------------------------------------------

    def order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return self.orders_by_id.get(order_id)

    def items(self, order_id: str) -> List[Dict[str, Any]]:
        return self.items_by_order.get(order_id, [])

    def payments(self, order_id: str) -> List[Dict[str, Any]]:
        return self.payments_by_order.get(order_id, [])

    def product(self, product_id: str) -> Optional[Dict[str, Any]]:
        return self.product_by_id.get(product_id)

    def seller(self, seller_id: str) -> Optional[Dict[str, Any]]:
        return self.seller_by_id.get(seller_id)

    def customer(self, customer_id: str) -> Optional[Dict[str, Any]]:
        return self.customer_by_id.get(customer_id)

    def orders_for_customer_unique(self, customer_unique_id: str) -> List[str]:
        return list(self.orders_by_customer_unique.get(customer_unique_id, []))

    def category_english(self, pt_name: Optional[str]) -> Optional[str]:
        if not pt_name:
            return None
        return self.category_en.get(pt_name, pt_name)
