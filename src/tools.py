import pandas as pd
import os
from datetime import datetime
from typing import Dict, Any, List

# Define paths
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

# Lazy load DataFrames
_df_cache = {}

def dedupe_preserve_order(seq):
    """Loại bỏ trùng lặp nhưng giữ nguyên thứ tự xuất hiện gốc."""
    seen = set()
    seen_add = seen.add
    return [x for x in seq if not (x in seen or seen_add(x))]

def get_df(filename: str) -> pd.DataFrame:
    if filename not in _df_cache:
        file_path = os.path.join(DATA_DIR, filename)
        if os.path.exists(file_path):
            _df_cache[filename] = pd.read_csv(file_path)
        else:
            raise FileNotFoundError(f"Missing data file: {file_path}")
    return _df_cache[filename]

def get_order_details(order_id: str) -> Dict[str, Any]:
    df_orders = get_df('olist_orders_dataset.csv')
    order = df_orders[df_orders['order_id'] == order_id]
    if order.empty:
        return {}
    return order.iloc[0].to_dict()

def get_order_items(order_id: str) -> List[Dict[str, Any]]:
    df_items = get_df('olist_order_items_dataset.csv')
    items = df_items[df_items['order_id'] == order_id]
    return items.to_dict('records')

def get_order_payments(order_id: str) -> List[Dict[str, Any]]:
    df_payments = get_df('olist_order_payments_dataset.csv')
    payments = df_payments[df_payments['order_id'] == order_id]
    return payments.to_dict('records')

def get_customer_history(customer_id: str) -> Dict[str, Any]:
    df_customers = get_df('olist_customers_dataset.csv')
    customer = df_customers[df_customers['customer_id'] == customer_id]
    if customer.empty:
        return {}
    customer_unique_id = customer.iloc[0]['customer_unique_id']
    
    # Get all related orders
    all_customer_entries = df_customers[df_customers['customer_unique_id'] == customer_unique_id]
    related_customer_ids = all_customer_entries['customer_id'].tolist()
    
    df_orders = get_df('olist_orders_dataset.csv')
    related_orders = df_orders[df_orders['customer_id'].isin(related_customer_ids)]
    
    return {
        "customer_unique_id": customer_unique_id,
        "related_order_ids": related_orders['order_id'].tolist()
    }

def get_product_categories(product_ids: List[str]) -> List[str]:
    df_products = get_df('olist_products_dataset.csv')
    products = df_products[df_products['product_id'].isin(product_ids)]
    categories = products['product_category_name'].dropna().unique().tolist()
    return categories

def tool_check_order_exists(order_id: str) -> bool:
    df_orders = get_df('olist_orders_dataset.csv')
    return order_id in df_orders['order_id'].values

def tool_calculate_time_variance(time_a: str, time_b: str) -> float:
    """Tính variance hours giữa 2 mốc thời gian. time_a - time_b"""
    if pd.isna(time_a) or pd.isna(time_b):
        return None
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        dt_a = datetime.strptime(str(time_a), fmt)
        dt_b = datetime.strptime(str(time_b), fmt)
        variance = (dt_a - dt_b).total_seconds() / 3600.0
        return round(variance, 2)
    except Exception:
        return None

def tool_calculate_payment_math(item_total: float, freight_total: float, payment_total: float) -> Dict[str, Any]:
    """Tool cho Payment Agent: Làm toán cộng trừ và làm tròn 2 chữ số thập phân."""
    expected = round(item_total + freight_total, 2)
    difference = round(payment_total - expected, 2)
    reconciled = abs(difference) <= 0.10
    
    return {
        "expected_total_brl": expected,
        "difference_brl": difference,
        "reconciled": reconciled
    }

def tool_validate_evidence_format(evidence_id: str) -> bool:
    """Tool cho Verifier Agent: Check regex/format cứng."""
    valid_prefixes = ["order:", "item:", "payment:", "seller:", "policy:"]
    return any(evidence_id.startswith(prefix) for prefix in valid_prefixes)