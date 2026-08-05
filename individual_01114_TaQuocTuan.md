# Member Role Report — Day 9: Multi Agent A2A

> Báo cáo cá nhân của các thành viên nhóm, mỗi người tự khai báo phần việc
> và mức hiểu của mình. Mục đích là chứng minh ai đã trực tiếp thực hiện
> phần nào của pipeline.

---

## Tạ Quốc Tuấn — 2A202601114

### 1. Thông tin cá nhân

| Thông tin       | Nội dung                       |
| --------------- | ------------------------------ |
| Họ và tên       | Tạ Quốc Tuấn                   |
| MSSV            | 2A202601114                    |
| Khóa/Lớp        | K4                             |
| Vai trò chính   | Order + Payment + Delivery     |
| Ngày hoàn thành | 2026-08-05                     |

### 2. Vai trò và phạm vi công việc

#### Phần việc sở hữu

| Module/deliverable                              | File/hàm phụ trách            | Input nhận vào     | Output bàn giao                                                          | Trạng thái |
| ----------------------------------------------- | ----------------------------- | ------------------ | ------------------------------------------------------------------------ | ---------- |
| Order & entities agent                          | `agents/order_agent.py`       | order_id           | order row, items, sellers, products, categories                          | Hoàn thành |
| Payment reconciliation agent                    | `agents/payment_agent.py`     | order + items      | `payment_reconciliation` với totals, `reconciled`, `payment_types`        | Hoàn thành |
| Delivery + seller handoff agent                 | `agents/delivery_agent.py`    | order + items      | `delivery_analysis`, `seller_handoff_analysis`, `late_handoff_seller_ids` | Hoàn thành |

#### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                                              | Thành viên/module được hỗ trợ | Kết quả                              |
| ------------------------------------------------------ | ----------------------------- | ------------------------------------ |
| Tinh chỉnh cách cap `seller_ids` trong `Verifier`      | Verifier                      | Cap 3, dedup, stable order           |

### 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện                       | File/hàm/artifact liên quan    | Kết quả bàn giao                              | Cách xác minh                                |
| -------------------------------------------- | ------------------------------ | --------------------------------------------- | -------------------------------------------- |
| Order entity extraction                       | `agents/order_agent.py`        | Sorted item_ids, dedup sellers                | EC_001 → seller_ids=1, item_ids=2            |
| Payment reconciliation (item+freight vs sum)  | `agents/payment_agent.py`      | Diff BRL, `reconciled` bool, payment_types    | EC_001 → diff=0.00, reconciled=true           |
| Delivery variance & handoff                   | `agents/delivery_agent.py`     | Variance hours, late_handoff_seller_ids      | EC_002 → variance=87.39, late_handoff=true    |

Một output cụ thể: `output/EC_002.json` — `payment_reconciliation` khớp
đúng ví dụ trong README mục 6 (item_total_brl=194.0, freight=18.27,
diff=0.0, reconciled=true).

### 4. Giải thích phần kỹ thuật đã thực hiện

**Vấn đề.** Tính toán hơn 10 giá trị numeric mà không được phép sai lệch
— đây là phần PolicyAgent quyết định primary issue dựa trên.

**Cách triển khai.**

- `OrderAgent`: sort theo `order_item_id` int, dedup set cho
  `seller_ids`/`product_ids`. Cap 3 sellers, 5 products vì brief.
- `PaymentAgent`: sum `price` + `freight_value` so với `sum(payment_value)`,
  ngưỡng `≤ 0.10 BRL` cho `reconciled`. Cache payments qua
  `state["_payments_cache"]` tránh lookup lặp.
- `DeliveryAgent`: bucket items by `seller_id`, lấy `min(shipping_limit_date)`
  per seller, so với `order_delivered_carrier_date`.

**Input, output và contract**

| Thành phần              | Mô tả                                                                  |
| ----------------------- | ---------------------------------------------------------------------- |
| Input                   | `state["order"]`, `state["items"]`, `state["_payments_cache"]`          |
| Output                  | `affected_entities`, `delivery_analysis`, `payment_reconciliation`     |
| Module phụ thuộc        | `DataIndex.order/items/payments/product`                               |
| Module sử dụng output   | `PolicyAgent` (variance, reconciled, late_handoff_seller_ids)          |
| Điều kiện lỗi cần xử lý | Order missing → fields = None; items missing → expected_total null     |

**Cách xác minh**

```bash
.venv/bin/python.exe -c "
import json
d = json.loads(open('output/EC_002.json', encoding='utf-8').read())
print(d['delivery_analysis']['delivery_variance_hours'])
"
```

- Kết quả mong đợi: `87.39` 
- Kết quả thực tế: `87.39`.

### 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Có nên cache `payments_by_order` ở `DataIndex` hay load on-demand?
- **Phương án đã cân nhắc:**
  1. Cache toàn bộ `payments_by_order` dict (đã làm).
  2. Streaming generator.
- **Phương án đã chọn:** Cache — 50 case × ~2 payments avg là ~100 lookup,
  cache O(1) vẫn nhanh hơn đọc CSV mỗi case.
- **Lý do:** Trade-off RAM <100 MB để có runtime 0.2s.

### 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng:** `payment_agent` dùng `cap(...)` nhưng thiếu import.
- **Nguyên nhân gốc:** Refactor move `cap` vào `base.py` mà quên update import.
- **Cách xử lý:** Thêm `cap` vào `from .base import ...`.
- **Cách xác minh:** Pipeline chạy 50/50.

### 7. Hiểu biết về luồng end-to-end

Ba agent này sinh ra **mọi con số** xuất hiện trong output. PolicyAgent
chỉ **đọc** các số này và chọn rule phù hợp — không tính lại. Đây là ranh
giới giữa "tính toán xác định" và "quyết định nghiệp vụ" giúp chấm điểm
trùng khớp.

### 8. Cam kết của thành viên

- [x] Nội dung phản ánh đúng phần việc.
- [x] Giải thích được end-to-end.
- [x] Không ghi "đã chạy thành công" nếu chưa kiểm chứng.
- [x] Không chứa `.env`/secret.
- [x] Không phải bản sao nguyên văn.

**Họ và tên:** Tạ Quốc Tuấn
**Ngày xác nhận:** 2026-08-05

