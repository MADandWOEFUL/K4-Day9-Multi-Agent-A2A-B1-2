# Member Role Report — Day 9: Multi Agent A2A

> Báo cáo cá nhân của các thành viên nhóm, mỗi người tự khai báo phần việc
> và mức hiểu của mình. Mục đích là chứng minh ai đã trực tiếp thực hiện
> phần nào của pipeline.

---

## Nguyễn Tuấn Dương — 2A202601966

### 1. Thông tin cá nhân

| Thông tin       | Nội dung                            |
| --------------- | ----------------------------------- |
| Họ và tên       | Nguyễn Tuấn Dương                   |
| MSSV            | 2A202601966                         |
| Khóa/Lớp        | K4                                  |
| Vai trò chính   | Kiến trúc hệ thống & Coordinator    |
| Ngày hoàn thành | 2026-08-05                          |

### 2. Vai trò và phạm vi công việc

#### Phần việc sở hữu

| Module/deliverable                       | File/hàm phụ trách                | Input nhận vào                                | Output bàn giao                       | Trạng thái     |
| ---------------------------------------- | --------------------------------- | --------------------------------------------- | ------------------------------------- | -------------- |
| Thiết kế tổng thể multi-agent           | `architecture.md`                 | README + lab brief                            | Sơ đồ agent + handoff contract        | Hoàn thành     |
| Coordinator (orchestrator)               | `agents/coordinator.py`           | 50 input JSON                                 | Gọi 6 agent theo thứ tự, ghi trace    | Hoàn thành     |
| Pipeline runner                          | `run_pipeline.py`                 | `input/EC_*.json`                             | `output/EC_*.json`, `metadata.json`   | Hoàn thành     |
| Base types, trace, time/money helpers     | `agents/base.py`                  | —                                             | `Agent`, `CaseState`, `trace()`, `round2()`, `parse_dt()` | Hoàn thành |
| LLM client (OpenRouter)                  | `agents/llm.py`                   | `OPENROUTER_API_KEY`                          | `chat_json()` cho `PolicyAgent`       | Hoàn thành     |

#### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                                                       | Thành viên/module được hỗ trợ          | Kết quả                                                       |
| --------------------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------- |
| Review lại policy rules, debug `valid_split_payment` edge cases | Các agent khác                         | Sửa condition `payment_count >= 2 and reconciled`             |
| Đóng gói output zip theo brief                                  | Toàn nhóm                              | `output.zip` 50 file JSON, không chứa file lạ                 |

### 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện                              | File/hàm/artifact liên quan                | Kết quả bàn giao                              | Cách xác minh                                       |
| --------------------------------------------------- | ------------------------------------------ | --------------------------------------------- | --------------------------------------------------- |
| Thiết kế kiến trúc 7 agent và handoff contract     | `architecture.md`                          | Sơ đồ + data-flow + LLM usage policy          | Đọc `architecture.md` mục 2–10                      |
| Code Coordinator + pipeline runner                  | `agents/coordinator.py`, `run_pipeline.py` | Chạy 50/50 case trong ~0.2s                   | `python .venv/bin/python.exe run_pipeline.py`        |
| Trace ghi vào `logging/trace.jsonl`                | `agents/base.py::_trace_append`            | Một JSON-line per agent invocation per case   | `wc -l logging/trace.jsonl`                          |

Một output cụ thể: `output/EC_002.json` đúng schema và khớp ví dụ trong README
mục 6 — `late_delivery_seller`, refund 18.27 BRL (= freight), action
`refund_freight` + `review_seller_handoff`.

### 4. Giải thích phần kỹ thuật đã thực hiện

**Vấn đề cần giải quyết.** Phải đảm bảo pipeline chạy đúng thứ tự, mỗi agent
thấy đầy đủ state từ agent trước, và khi có exception không làm hỏng cả 50 case.

**Cách triển khai.** Một `CaseState` (dict) được truyền tuần tự qua 6 domain
agents; mỗi agent chỉ ghi các key mà handoff protocol quy định. Trace dùng
`threading.Lock` để an toàn khi chạy nhiều case (mặc dù mặc định chạy tuần tự).
LLM client có retry 3 lần với exponential back-off nhưng luôn có deterministic
fallback (không bao giờ fail cả pipeline).

**Input, output và contract**

| Thành phần              | Mô tả                                                                 |
| ----------------------- | --------------------------------------------------------------------- |
| Input                   | `input/EC_xxx.json` theo schema README mục 3                         |
| Output                  | `output/EC_xxx.json` + append `logging/trace.jsonl`                  |
| Module phụ thuộc        | `agents/data_loader.py` (cùng chỉ mục CSV)                           |
| Module sử dụng output   | Verifier (cuối pipeline) ghi output                                  |
| Điều kiện lỗi cần xử lý | Missing order → `null` timestamps; missing items → `null` financials |

**Cách xác minh**

```bash
.venv/bin/python.exe run_pipeline.py
wc -l logging/trace.jsonl        # phải > 50
ls output/ | wc -l               # phải = 50
```

- Kết quả mong đợi: 50/50 trong < 5s, 0 case lỗi.
- Kết quả thực tế: `Processed 50/50 cases in 0.2s`, 0 case lỗi.
- Artifact: `logging/trace.jsonl`, `logging/metadata.json`.

### 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Có nên dùng pandas cho việc load CSV không?
- **Phương án đã cân nhắc:**
  1. `pandas.read_csv` — phổ biến nhưng Python 3.14 chưa có wheel.
  2. `csv.DictReader` (stdlib) — nhỏ, có sẵn, đủ cho dataset ~100k dòng.
- **Phương án đã chọn:** `csv.DictReader` + 3 dict-index (`orders_by_id`,
  `items_by_order`, `payments_by_order`).
- **Lý do:** Deterministic, không phụ thuộc build toolchain; load 1 lần
  trong <1s, tra cứu O(1), 50 case chạy trong 0.2s.
- **Bằng chứng:** Runtime ghi trong `logging/metadata.json`.

### 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng:** `KeyError: 'product_category_name'` khi load translation CSV.
- **Lệnh tái hiện:** `.venv/bin/python.exe run_pipeline.py --limit 2`.
- **Nguyên nhân gốc:** File dịch category có những dòng trống cuối file mà
  `DictReader` vẫn yield ra dict rỗng.
- **Cách xử lý:** Filter `pt and en` trong `data_loader.py` mục translation.
- **Cách xác minh:** Chạy lại runner; 50/50 success.
- **Bài học:** Luôn guard key access khi đọc CSV ngoài — dữ liệu thật có
  dirty rows.

### 7. Hiểu biết về luồng end-to-end

1. Case đi từ `input/EC_xxx.json` → `Coordinator.run_case()` → 6 agent nối
   tiếp → `Verifier` ghi `output/EC_xxx.json` + append trace.
2. Mỗi domain agent (Order/Payment/Delivery) **tính toán deterministic**
   bằng Python trên CSV; chỉ `PolicyAgent` gọi LLM để lấy `confidence` và
   LLM output được verify lại với fact đã có.
3. Evidence chỉ chứa ID reconstruct từ CSV; bất kỳ ID không khớp bị filter
   cuối cùng (defensive).
4. Limits (5 order, 5 item, 5 payment, 3 seller, 20 evidence, 5 action…)
   được enforce ở `VerifierAgent` — đây là chốt chặn cuối cùng trước khi
   ghi file.
5. Trace ghi **một JSON-line cho mỗi agent invocation**, đủ để replay từng
   case và truy vết bug.

### 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc thành viên khác.

**Họ và tên:** Nguyễn Tuấn Dương
**Ngày xác nhận:** 2026-08-05

---

## Nguyễn Hữu Công — 2A202601732

### 1. Thông tin cá nhân

| Thông tin       | Nội dung                        |
| --------------- | ------------------------------- |
| Họ và tên       | Nguyễn Hữu Công                 |
| MSSV            | 2A202601732                     |
| Khóa/Lớp        | K4                              |
| Vai trò chính   | Customer + Data Loader          |
| Ngày hoàn thành | 2026-08-05                      |

### 2. Vai trò và phạm vi côc việc

#### Phần việc sở hữu

| Module/deliverable        | File/hàm phụ trách            | Input nhận vào            | Output bàn giao                            | Trạng thái |
| ------------------------- | ----------------------------- | ------------------------- | ------------------------------------------ | ---------- |
| Data loader + indexes     | `agents/data_loader.py`       | 9 file CSV trong `data/`  | `DataIndex` (7 dict + helper)               | Hoàn thành |
| Customer identity agent   | `agents/customer_agent.py`    | `claimed_order_id`        | `customer_unique_id`, `related_order_ids`  | Hoàn thành |

#### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                                       | Thành viên/module được hỗ trợ | Kết quả                            |
| ----------------------------------------------- | ----------------------------- | ---------------------------------- |
| Sửa bug `KeyError` translation CSV              | Coordinator                   | Filter empty rows                   |
| Xác minh `related_order_ids` không trùng current | Verifier                      | Sort + cap 5                       |

### 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện                       | File/hàm/artifact liên quan     | Kết quả bàn giao                              | Cách xác minh                                |
| -------------------------------------------- | ------------------------------- | --------------------------------------------- | -------------------------------------------- |
| Index 9 CSV với 7 in-memory dict            | `agents/data_loader.py`         | Truy cập O(1), load ~2s                       | In `len(self.orders_by_id)`                  |
| Map `customer_unique_id -> [order_id]`       | `orders_by_customer_unique`     | 50/50 case có `related_order_ids` chính xác   | Đọc `output/EC_001.json` `customer_context` |

Một output cụ thể: `output/EC_001.json` `customer_context.customer_unique_id =
"bbf65e7823171a84e70a495dd6c34ceb"` và `related_order_ids = ["65bbd0719855..."]`
— khớp với `customer_id` của order trong CSV.

### 4. Giải thích phần kỹ thuật đã thực hiện

**Vấn đề.** Phải chuyển 9 CSV lớn (~300 MB sau khi nén) thành tra cứu O(1)
mà không phụ thuộc pandas (vì lý do toolchain ở Python 3.14).

**Cách triển khai.** `DataIndex._load()` mở mỗi CSV đúng 1 lần, đẩy vào
dict. Sau khi load orders + customers, xây `orders_by_customer_unique` qua
một vòng lặp join. `CustomerAgent` chỉ sort + cap-5.

**Input, output và contract**

| Thành phần              | Mô tả                                                  |
| ----------------------- | ------------------------------------------------------ |
| Input                   | `claimed_order_id` (string)                            |
| Output                  | `customer_unique_id`, `related_order_ids` (sorted, ≤5) |
| Module phụ thuộc        | `DataIndex.customer()`, `orders_for_customer_unique()` |
| Module sử dụng output   | `PolicyAgent.responsible_parties`, `Verifier`          |
| Điều kiện lỗi cần xử lý | Order không tồn tại trong CSV → `None` + `[]`          |

**Cách xác minh**

```python
from agents.data_loader import DataIndex
idx = DataIndex()
print(len(idx.orders_by_id), len(idx.items_by_order))
```

- Kết quả mong đợi: `99441` orders, `98666` orders có item.
- Kết quả thực tế: in ra tương ứng.

### 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Có nên pre-compute `customer_unique_id` cho mỗi order tại load?
- **Phương án đã cân nhắc:**
  1. Lưu `customer_unique_id` ngay trong `orders_by_id`.
  2. Lazy-lookup qua `customer_by_id`.
- **Phương án đã chọn:** Tách riêng `customer_by_id` rồi join on-demand
  qua `orders_by_customer_unique`.
- **Lý do:** RAM-O(1) thay vì RAM-O(2); chỉ CustomerAgent cần
  `customer_unique_id` nên không phải duplicate vào 99k order rows.

### 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng:** `KeyError: 'product_category_name'` ở translation CSV.
- **Nguyên nhân gốc:** Empty rows ở cuối file dịch category.
- **Cách xử lý:** Guard `if pt and en` trong `data_loader._load()`.
- **Cách xác minh:** Pipeline 50/50 success.
- **Bài học:** Khi load CSV ngoài, luôn filter `None` cho key access.

### 7. Hiểu biết về luồng end-to-end

`CustomerAgent` chỉ là bước tiền xử lý — output của nó được tiêu thụ
gián tiếp qua `PolicyAgent` (để set `repeat_customer` secondary issue) và
qua `Verifier` (để populate `customer_context`). Không có zero-shot reasoning;
toàn bộ dựa trên CSV. Trace ghi lại `customer_unique_id` và `related_count`
mỗi case.

### 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc.
- [x] Giải thích được luồng end-to-end.
- [x] Không ghi "đã chạy thành công" nếu chưa kiểm chứng.
- [x] Không chứa `.env`/secret.
- [x] Không phải bản sao nguyên văn báo cáo khác.

**Họ và tên:** Nguyễn Hữu Công
**Ngày xác nhận:** 2026-08-05

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

- Kết quả mong đợi: `87.39` (từ ví dụ README).
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

---

## Nguyễn Tuấn Phong — 2A202601038

### 1. Thông tin cá nhân

| Thông tin       | Nội dung                                |
| --------------- | --------------------------------------- |
| Họ và tên       | Nguyễn Tuấn Phong                       |
| MSSV            | 2A202601038                             |
| Khóa/Lớp        | K4                                      |
| Vai trò chính   | Policy + Verifier                       |
| Ngày hoàn thành | 2026-08-05                              |

### 2. Vai trò và phạm vi công việc

#### Phần việc sở hown

| Module/deliverable                | File/hàm phụ trách             | Input nhận vào                | Output bàn giao                                                 | Trạng thái |
| --------------------------------- | ------------------------------ | ----------------------------- | --------------------------------------------------------------- | ---------- |
| Policy rules + LLM-assisted pick  | `agents/policy_agent.py`       | state sau 3 agent trước       | `case_assessment`, `financial_resolution`, `resolution_actions` | Hoàn thành |
| Verifier + final assembly         | `agents/verifier_agent.py`     | full `CaseState`              | `output/EC_xxx.json` (final JSON theo README mục 6)             | Hoàn thành |

#### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                                              | Thành viên/module được hỗ trợ | Kết quả                              |
| ------------------------------------------------------ | ----------------------------- | ------------------------------------ |
| Viết test thủ công evidence-vs-CSV cho từng case       | Toàn nhóm                     | 0/50 case có evidence không khớp CSV  |

### 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện                  | File/hàm/artifact liên quan       | Kết quả bàn giao                                | Cách xác minh                                       |
| --------------------------------------- | --------------------------------- | ----------------------------------------------- | --------------------------------------------------- |
| Implement 6 row policy priority table   | `agents/policy_agent.py`          | `PRIMARY_RULES`, `SECONDARY_RULES`              | Distribution in §3 (5 nhóm primary)                 |
| Cap evidence theo format README mục 5   | `agents/verifier_agent.py`        | `evidence_ids` ∈ {order, item, payment, seller, policy} | `python validate.py` trả 0 bad evidence      |

Một output cụ thể: `output/EC_004.json` — một order `canceled` có
payment → `canceled_order_paid`, refund = payment_total, action
`issue_full_refund`, evidence gồm `order`, items, payments, policy
`ORDER_CANCELED_AFTER_PAYMENT`.

### 4. Giải thích phần kỹ thuật đã thực hiện

**Vấn đề.** Áp dụng đúng priority table của `EC_POLICY_V2` (Rule 1→6,
first-match wins) và xây evidence list khớp format README.

**Cách triển khai.**

- `PolicyAgent.deterministic_decide()`: lặp `PRIMARY_RULES` (thứ tự ưu
  tiên), chấm rule active, set refund theo `payment_total_brl` /
  `freight_total_brl`. Sau đó mới thêm secondary issues theo
  `SECONDARY_RULES`. Sau cùng LLM chỉ được set `confidence`, không
  đè primary.
- `VerifierAgent`: enforce schema (limits, caps), reconstruct
  `evidence_ids` từ CSV rows (order→item→payment→seller→policy),
  dedup + cap 20.

**Input, output và contract**

| Thành phần              | Mô tả                                                       |
| ----------------------- | ----------------------------------------------------------- |
| Input                   | full `CaseState` từ Coordinator                             |
| Output                  | `output/EC_xxx.json` theo README §6                         |
| Module phụ thuộc        | Toàn bộ agent khác (đọc state)                              |
| Module sử dụng output   | Coordinator (ghi file); trace                               |
| Điều kiện lỗi cần xử lý | Order missing → fallback `unsupported_late_claim`           |

**Cách xác minh**

```bash
.venv/bin/python.exe -c "
import json, glob, csv
# evidence validator
..."
```

- Kết quả mong đợi: 0 evidence không reconstructable.
- Kết quả thực tế: in ra `bad evidence ids: 0`.

### 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** LLM có nên quyết định `primary_issue` không?
- **Phương án đã cân nhắc:**
  1. LLM tự do pick primary_issue.
  2. Deterministic Agent pick, LLM chỉ set confidence.
- **Phương án đã chọn:** (2). Deterministic Agent pick, LLM chỉ set
  confidence trong khoảng [0,1]. Verifier re-check deterministic rules.
- **Lý do:** Brief nói rõ "evidence wins over narrative reasoning".
  Deterministic đảm bảo repro và chấm điểm deterministic.
- **Bằng chứng:** 0 policy violation trong sample.

### 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng:** Evidence chứa `"payment:..."` không match format yêu cầu.
- **Nguyên nhân gốc:** Ban đầu lưu payment IDs không có prefix.
- **Cách xử lý:** Verifier prepend `payment:` nếu thiếu.
- **Cách xác minh:** Re-run validator — 0 bad evidence.

### 7. Hiểu biết về luồng end-to-end

`PolicyAgent` quyết định `primary_issue` deterministic, `Verifier` đảm
bảo schema + evidence reconstructable. Cả hai là chốt chặn cuối — bất kỳ
case nào vi phạm sẽ bị Verifier harden về safe default và flagged trong
trace. Đây là lý do 50/50 pass kiểm chứng.

### 8. Cam kết của thành viên

- [x] Nội dung phản ánh đúng phần việc.
- [x] Giải thích được end-to-end.
- [x] Không ghi "đã chạy thành công" nếu chưa kiểm chứng.
- [x] Không chứa `.env`/secret.
- [x] Không phải bản sao nguyên văn.

**Họ và tên:** Nguyễn Tuấn Phong
**Ngày xác nhận:** 2026-08-05
