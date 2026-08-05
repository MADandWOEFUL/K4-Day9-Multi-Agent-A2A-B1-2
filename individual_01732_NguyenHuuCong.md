# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Nguyễn Hữu Công |
| MSSV | K4-A2A-01732 |
| Khóa/Lớp | K4 |
| Vai trò chính | Lead Multi-Agent Architect & Policy Engineer |
| Ngày hoàn thành | 2026-08-05 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Data Loader & Indexer | `src/data_loader.py` | 9 file CSV dữ liệu Olist | In-memory lookup tables & translation dict | Hoàn thành |
| Domain Agents System | `src/agent_system.py` | Case JSON & CSV Data | Context states, Policy resolution, Verifier JSON | Hoàn thành |
| Pipeline & Trace Logger | `run_pipeline.py` | 50 Input JSONs (`input/EC_*.json`) | 50 Output JSONs (`output/EC_*.json`) & `trace.jsonl` | Hoàn thành |
| System Architecture Doc | `architecture.md` | Thiết kế hệ thống | Document sơ đồ & luồng handoff A2A | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Schema & Rule Verification | Verifier Agent & Output Validation | Đảm bảo 100% output tuân thủ schema bounds & non-null rules |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Triển khai 7 Agents chuyên biệt | `src/agent_system.py` | `CoordinatorAgent`, `CustomerAgent`, `OrderProductAgent`, `PaymentAgent`, `DeliveryAgent`, `PolicyAgent`, `VerifierAgent` | Trace execution trong `trace.jsonl` |
| Thực thi 50 case khiếu nại | `run_pipeline.py` | 50 file JSON trong `output/` | Script kiểm tra `assert len(glob('output/*.json')) == 50` |
| Tạo gói nộp bài | `output.zip` | Archive chứa 50 file JSON chuẩn | Zip command & size verification |

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết
Bài toán yêu cầu điều tra 50 ca khiếu nại thương mại điện tử phức tạp từ tập dữ liệu Olist bằng hệ thống Multi-Agent có phân công vai trò, trao đổi dữ liệu (handoff) và kiểm định kết quả.

### Cách triển khai
- **DataLoader**: Đọc 9 file CSV Olist và tạo các chỉ mục (indexes) theo `order_id`, `customer_id`, `customer_unique_id`, `product_id` để tối ưu hóa tốc độ truy xuất.
- **Domain Agents**:
  - `CustomerAgent`: Định danh `customer_unique_id` và lịch sử các đơn hàng liên quan.
  - `OrderProductAgent`: Bóc tách items, sellers, products và trích xuất category từ dữ liệu gốc chuẩn xác.
  - `PaymentAgent`: Tính toán tổng tiền thanh toán, tiền hàng + phí vận chuyển và kiểm tra tính đối soát (`reconciled`).
  - `DeliveryAgent`: Tính toán độ lệch giờ giao hàng (`delivery_variance_hours`) và độ lệch giờ bàn giao của từng seller (`seller_handoff_analysis`).
  - `PolicyAgent`: Áp dụng ma trận quyết định `EC_POLICY_V2` với mô hình LLM chuyên biệt và bộ luật xác định để phân loại nguyên nhân chính/phụ, trách nhiệm, khoản hoàn và hành động.
  - `VerifierAgent`: Kiểm định và ép mảng không vượt quá giới hạn tối đa theo schema.
  - `CoordinatorAgent`: Ghi nhận trace log A2A dạng JSONL tại mỗi bước xử lý.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | File `input/EC_xxx.json` chứa `claimed_order_id` và phạm vi điều tra |
| Output | File `output/EC_xxx.json` chuẩn hóa theo output schema quy định |
| Module phụ thuộc | `pandas`, `pydantic`, `datetime` |
| Module sử dụng output | Hệ thống chấm điểm tự động |
| Điều kiện lỗi cần xử lý | Đơn hàng bị hủy/không có item row (trả về `null` cho các trường tổng tiền hàng và mảng rỗng) |

### Cách xác minh

```bash
uv run python run_pipeline.py
```

- **Kết quả mong đợi:** Xử lý 50 case không lỗi, ghi đủ 50 file JSON vào `output/` và ghi vết `trace.jsonl`.
- **Kết quả thực tế:** 50/50 cases hoàn thành thành công.
- **Artifact/log:** `trace.jsonl`, `output/*.json`

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Lựa chọn phương án tính toán dữ liệu và áp dụng rule engine cho ma trận `EC_POLICY_V2`.
- **Các phương án đã cân nhắc:**
  1. Dùng 1 LLM duy nhất với prompt rất dài để vừa truy vấn dữ liệu vừa đưa ra kết luận.
  2. Phân rã thành các Agent độc lập (Customer, Payment, Delivery, Policy, Verifier) với dữ liệu được xử lý chính xác tuyệt đối qua Pandas và log vết handoff chi tiết.
- **Phương án đã chọn:** Phương án 2.
- **Lý do:** Đảm bảo độ chính xác 100% về mặt số liệu (tiền tệ, số giờ variance) không bị hallucination, đồng thời đáp ứng đúng tiêu chí kiến trúc Multi-Agent có handoff và verifier độc lập.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Với các đơn hàng không có item row (ví dụ đơn bị hủy sớm), việc tính toán `expected_total_brl` gây ra lỗi đếm mảng hoặc trả về giá trị `0.0` thay vì `null`.
- **Nguyên nhân gốc:** Đơn hàng thiếu bản ghi trong file `olist_order_items_dataset.csv`.
- **Cách xử lý:** Thêm logic kiểm tra `if items:` trong `PaymentAgent` và `DeliveryAgent`, nếu không có item row thì gán các giá trị tổng tiền và variance bằng `None` (`null` trong JSON), các mảng sản phẩm/seller trả về mảng rỗng `[]`.
- **Cách xác minh sau khi sửa:** Chạy lại pipeline với case `EC_004` (canceled order) và xác nhận output JSON chứa `expected_total_brl: null`.

## 7. Hiểu biết về luồng end-to-end

1. Dữ liệu đi từ các file CSV dữ liệu thô qua `DataLoader` được index theo các khóa chính để truy xuất trong thời gian thực $O(1)$.
2. Mỗi case khiếu nại lần lượt đi qua chuỗi Agent: `Customer` $\rightarrow$ `OrderProduct` $\rightarrow$ `Payment` $\rightarrow$ `Delivery` $\rightarrow$ `Policy` $\rightarrow$ `Verifier`.
3. `CoordinatorAgent` thu thập trạng thái từng Agent, bàn giao (handoff) sang Agent tiếp theo và ghi vết lại toàn bộ luồng vào `trace.jsonl`.
4. `VerifierAgent` đảm bảo mọi giới hạn dữ liệu (tối đa 5 order IDs, 20 evidence IDs, ...) được tuân thủ nghiêm ngặt trước khi ghi file.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Nguyễn Hữu Công  
**Ngày xác nhận:** 2026-08-05
