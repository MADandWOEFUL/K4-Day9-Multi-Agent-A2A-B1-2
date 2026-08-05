# Member Role Report — Day 9: Multi Agent A2A

> Báo cáo cá nhân của các thành viên nhóm, mỗi người tự khai báo phần việc
> và mức hiểu của mình. Mục đích là chứng minh ai đã trực tiếp thực hiện
> phần nào của pipeline.

---

## Nguyễn Tuấn Phong — 2A202601038

### 1. Thông tin cá nhân

| Thông tin       | Nội dung                                         |
| --------------- | ------------------------------------------------ |
| Họ và tên       | Nguyễn Tuấn Phong                                |
| MSSV            | 2A202601038                                      |
| Khóa/Lớp        | K4                                               |
| Vai trò chính   | Data Layer & Schema Verifier                     |
| Ngày hoàn thành | 2026-08-05                                       |

### 2. Vai trò và phạm vi công việc

#### Phần việc sở hữu

| Module/deliverable                        | File/hàm phụ trách                                    | Input nhận vào                | Output bàn giao                                                              | Trạng thái     |
| ----------------------------------------- | ----------------------------------------------------- | ----------------------------- | ---------------------------------------------------------------------------- | -------------- |
| Olist CSV index loader                    | `agents/data_loader.py::DataIndex`                    | 7 CSV Olist trong `data/`     | 7 dict index O(1) + `category_en` translation map                           | Hoàn thành     |
| Pipeline path constants                   | `agents/base.py::IN_DIR`, `OUT_DIR`, `LOG_DIR`        | —                             | `Path` tới `input/`, `output/`, `logging/`, tự `mkdir`                       | Hoàn thành     |
| Schema/limits bảng                        | `agents/verifier_agent.py::_LIMITS`                   | —                             | Dict các cap array (`order_ids:5`, `evidence_ids:20`, …)                     | Hoàn thành     |
| Evidence reconstruction & existence check | `agents/verifier_agent.py::VerifierAgent._valid_evidence` | state cuối pipeline        | `evidence_ids` chỉ chứa ID dựng được từ CSV theo format README mục 5          | Hoàn thành     |
| Final assembly + JSON writer              | `agents/verifier_agent.py::VerifierAgent.run`         | state từ PolicyAgent          | `output/EC_xxx.json` đúng schema README mục 6                                 | Hoàn thành     |

#### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                                                       | Thành viên/module được hỗ trợ          | Kết quả                                                       |
| --------------------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------- |
| Cross-check 50 output JSON với CSV để xác nhận evidence          | PolicyAgent & Coordinator              | 0/50 case có evidence ID không reconstruct được               |
| Kiểm tra `delivery_variance_hours` không bị lệch do parse timestamp | DeliveryAgent                         | Format chuẩn `%Y-%m-%d %H:%M:%S` ăn khớp cả 50 case          |

### 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện                          | File/hàm/artifact liên quan            | Kết quả bàn giao                                | Cách xác minh                                       |
| ----------------------------------------------- | -------------------------------------- | ----------------------------------------------- | --------------------------------------------------- |
| Index 7 CSV thành in-memory dict O(1)           | `agents/data_loader.py::DataIndex._load` | 99 441 orders, 112 650 items, 103 886 payments  | In `len(idx.orders_by_id)`                          |
| Build `orders_by_customer_unique`               | `DataIndex._load` (loop join)          | `related_order_ids` chính xác cho 50/50 case    | Đọc `output/EC_001.json.customer_context`           |
| Category translation PT → EN                    | `DataIndex.category_en`, `category_english` | `category_names` tiếng Anh, fallback PT nếu thiếu | Đọc `output/EC_002.json.product_context.category_names` |
| Cap 5/3/20 theo spec                            | `agents/verifier_agent.py::_LIMITS`    | Mọi output không bao giờ vượt cap               | Spot-check 50 file, đếm len từng mảng               |
| Evidence filter NaN/None prefix                 | `VerifierAgent._valid_evidence` + `_filter_evidence` | Mọi evidence là `order:`, `item:`, `payment:`, `seller:`, `policy:` | Re-parse evidence, tra trong CSV, 0 bad ID |

Một output cụ thể: `output/EC_002.json` —
- `affected_entities.seller_ids = ["f9e355bd86f543..."]` (1 seller) — cap từ 3 nhưng không bị over-cap.
- `affected_entities.payment_ids = ["ec15f99a9e...:1", "ec15f99a9e...:2"]` — 2 payment rows, đầy đủ prefix.
- `evidence_ids = ["order:ec15f99a9e...", "item:ec15f99a9e...:1", "payment:ec15f99a9e...:1", "payment:ec15f99a9e...:2", "seller:f9e355bd...", "policy:LATE_DELIVERY_SELLER"]` — đúng format.

### 4. Giải thích phần kỹ thuật đã thực hiện

**Vấn đề.** Hai vấn đề phải giải quyết: (1) load 7 file CSV ~300 MB thành
tra cứu O(1) mà không phụ thuộc pandas; (2) đảm bảo output JSON luôn
không vượt spec, mọi evidence ID reconstruct được từ CSV và không chứa
NaN/null prefix.

**Cách triển khai.**

**(a) DataLoader (`agents/data_loader.py`)**

`DataIndex._load()` đọc từng CSV đúng 1 lần bằng `csv.DictReader` (stdlib),
đẩy vào các dict chính:
- `orders_by_id: Dict[order_id → row]`
- `items_by_order: Dict[order_id → List[row]]` (grouped trong lúc load)
- `payments_by_order: Dict[order_id → List[row]]`
- `customer_by_id: Dict[customer_id → row]`
- `product_by_id`, `seller_by_id`, `reviews_by_order`

Sau đó 1 lần duy nhất join `orders ↔ customers` để build
`orders_by_customer_unique: Dict[customer_unique_id → List[order_id]]`.

Cuối cùng đọc `product_category_name_translation.csv`, filter `if pt and en`
để tránh `KeyError` ở các dòng trống cuối file — guard trước khi `.get()`.
Mọi accessor (`order()`, `items()`, `payments()`, `customer()`, …) đều O(1).

**(b) VerifierAgent (`agents/verifier_agent.py`)**

`_LIMITS` là dict cap array hard-coded theo spec README mục 6. Mỗi output
mảng đều được slice theo cap này (`order_ids:5`, `seller_ids:3`,
`evidence_ids:20`, …). `confidence` ép về `[0, 1]` + `round(_, 2)`.

`_valid_evidence(state)` lắp ráp evidence theo format README mục 5:
- `order:<order_id>` — chỉ thêm nếu `index.order(order_id) is not None`.
- `item:<order_id>:<order_item_id>` — lặp `state["items"]`.
- `payment:<order_id>:<sequential>` — lặp `_payment_ids`, prepend prefix
  nếu thiếu.
- `seller:<seller_id>` — lấy từ `state["seller_ids"]` hoặc fall-back
  `delivery_analysis.late_handoff_seller_ids`.
- `policy:<root_cause_code>` — lấy `ranked_causes[0].cause_code`.

Sau đó `_filter_evidence()` strip các entry không phải string hoặc không
chứa `:` (defensive); dedup theo thứ tự ổn định; cap 20.

`run()` ở cuối pipeline:
1. Lấy tất cả dict từ state, ép kiểu, cap.
2. Clamp `confidence ∈ [0, 1]`.
3. Build `final` dict đúng schema README mục 6.
4. `OUT_DIR / f"{case_id}.json"` — `json.dumps(..., ensure_ascii=False, indent=2)`.
5. Trace event `verifier_agent` với `output` path và `evidence_count`.

**Input, output và contract**

| Thành phần              | Mô tả                                                                 |
| ----------------------- | --------------------------------------------------------------------- |
| Input (DataLoader)      | 7 CSV trong `data/` (`olist_*_dataset.csv`, `product_category_name_translation.csv`) |
| Output (DataLoader)     | `DataIndex` instance với 8 dict lookup                                |
| Input (Verifier)        | state đầy đủ từ Coordinator (sau 5 agent trước)                       |
| Output (Verifier)       | `output/EC_xxx.json` + state.verified = True                          |
| Module phụ thuộc        | `csv`, `pathlib`, `json` (stdlib only, không pandas)                  |
| Module sử dụng output   | Autograder, hệ thống chấm điểm tự động                               |
| Điều kiện lỗi cần xử lý | CSV thiếu dòng → filter `pt and en`; order missing → `None` timestamps, mảng rỗng |

**Cách xác minh**

```bash
.venv/bin/python.exe run_pipeline.py
ls output/ | wc -l                                       # = 50
cat output/EC_001.json | python -c "
import json, sys
d = json.load(sys.stdin)
caps = {'order_ids': 5, 'item_ids': 5, 'payment_ids': 5,
        'related_order_ids': 5, 'product_ids': 5,
        'category_names': 5, 'seller_ids': 3,
        'ranked_causes': 3, 'responsible_parties': 3,
        'evidence_ids': 20, 'resolution_actions': 5}
for k, n in caps.items():
    v = d.get(k) or (d.get('affected_entities', {}).get(k) if k in {'order_ids','item_ids','seller_ids','payment_ids'} else None)
    if isinstance(v, list):
        assert len(v) <= n, f'{k}={len(v)} > {n}'
        print(f'{k}: {len(v)}/{n} OK')
"
wc -l logging/trace.jsonl                                 # = 450 (9 events × 50 case)
```

- Kết quả mong đợi: 50/50 pass, mọi array ≤ cap, evidence ≥ 1 và đúng format.
- Kết quả thực tế: 50/50 pass, `validate_submission.py` 100% Passed.

### 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Có nên cache `DataIndex` qua run hay build lại mỗi case?
- **Phương án đã cân nhắc:**
  1. Pickle `DataIndex` ra `preprocessed_cache.pkl`, load lại mỗi run.
  2. Build `DataIndex` trực tiếp từ CSV mỗi run (như code hiện tại).
- **Phương án đã chọn:** (2). Build lại mỗi run với `csv.DictReader` stdlib.
- **Lý do:** Olist CSV tổng cộng ~300 MB sau khi nén, parse bằng stdlib
  mất < 2s một lần (chỉ chạy khi start pipeline). Trade-off không cần thêm
  file binary cache trong repo (git-friendly, không phụ thuộc Python pickle
  version), thời gian load không đáng kể so với LLM call. Deterministic
  và portable.
- **Bằng chứng:** `run_pipeline.py` log "Loading Olist dataset..." < 3s,
  toàn bộ 50 case xử lý trong vài giây (không kể LLM), `metadata.json` runtime.

### 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng:** Lúc build `category_en` map, một số dòng cuối file
  `product_category_name_translation.csv` là dòng trống — `DictReader`
  yield dict rỗng → `KeyError: 'product_category_name'` ở `.get()`.
- **Nguyên nhân gốc:** File CSV có dòng cuối không chứa key
  `product_category_name`, dict rỗng được `DictReader` vẫn yield ra.
- **Cách xử lý:** Trong `DataIndex._load()`, sau khi đọc translation CSV,
  filter `if pt and en` trước khi gán `self.category_en[pt] = en`. Các
  dòng rỗng hoặc thiếu key bị bỏ qua — fallback trả về chính tên tiếng
  Bồ Đào Nha qua `category_english(pt_name)`.
- **Cách xác minh:** Chạy lại pipeline 50 case; không còn `KeyError`;
  `output/*.json.product_context.category_names` đầy đủ.
- **Bài học:** Khi load CSV ngoài bằng `DictReader`, luôn guard empty row
  ở cuối file bằng cách check các key bắt buộc tồn tại và khác rỗng.

### 7. Hiểu biết về luồng end-to-end

1. **Data load:** `agents.build_pipeline()` → `DataIndex()` → 7 CSV được
   index trong 7 dict chính + 1 translation map. Một lần, < 3s.
2. **Coordinator điều phối:** `Coordinator.run_case()` nhận `case` JSON,
   tạo `state` dict (case_id, claimed_order_id, _index, _payments_cache)
   rồi lần lượt gọi 6 agent trong tuple: `customer → order → payment →
   delivery → policy → verifier`. Mỗi agent `run(state)` trả về state
   mới (in-place update).
3. **Deterministic state accumulation:** CustomerAgent set `customer_unique_id`
   + `related_order_ids`. OrderAgent set `order`, `items`, `item_ids`,
   `seller_ids`, `product_ids`, `category_names`. PaymentAgent set
   `payment_reconciliation` (totals + `_payment_ids` + reconciled bool).
   DeliveryAgent set `delivery_analysis` (`delivered_at`, `carrier_handoff_at`,
   `delivery_variance_hours`, `seller_handoff_analysis`,
   `late_handoff_seller_ids`, `_delivered_after_estimate`).
4. **Policy reasoning:** PolicyAgent đọc tất cả state trên, lặp
   `PRIMARY_RULES` priority table, set primary_issue + refund + responsible
   + actions. LLM (Nemotron) chỉ set `confidence`, deterministic pick wins.
5. **Verifier seal:** VerifierAgent đọc state cuối, build `evidence_ids`
   từ CSV (không tin field `evidence` của agent khác), enforce `_LIMITS`
   cho tất cả mảng, clamp `confidence`, build `final` dict, ghi
   `output/EC_xxx.json`. Cuối run: `reset_trace()` truncate `logging/trace.jsonl`.
6. **Trace format:** 9 events per case × 50 = 450 dòng trong
   `logging/trace.jsonl` — `pipeline.start` → 6 agent → `pipeline.end`,
   cộng `llm.*` event nếu LLM được gọi.

### 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Nguyễn Tuấn Phong
**Ngày xác nhận:** 2026-08-05