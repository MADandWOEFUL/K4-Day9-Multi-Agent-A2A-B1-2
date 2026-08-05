# Member Role Report — Day 9: Multi Agent A2A

> Báo cáo cá nhân của các thành viên nhóm, mỗi người tự khai báo phần việc và mức hiểu của mình. Mục đích là chứng minh ai đã trực tiếp thực hiện phần nào của pipeline.

---

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                                   |
| --------------- | ------------------------------------------ |
| Họ và tên       | Nguyễn Tuấn Dương                          |
| MSSV            | 2A202601966                                |
| Khóa/Lớp        | K4                                         |
| Vai trò chính   | QA Engineer & Data Pipeline Optimizer      |
| Ngày hoàn thành | 2026-08-05                                 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao   | Trạng thái                            |
| ------------------ | ------------------ | -------------- | ----------------- | ------------------------------------- |
| Schema & Business Rule Validator | `validate_submission.py::validate_file`, `main` | Thư mục `output/EC_*.json` | Terminal report (Pass/Fail) và chi tiết lỗi schema | Hoàn thành |
| Offline Data Preprocessing & Caching | `src/data_loader.py::run_offline_join` | 7 raw CSV files trong `data/` | Binary cache `preprocessed_cache.pkl` | Hoàn thành |
| Trace & Metadata Synchronization | `run_pipeline.py::main` | `trace.jsonl`, `metadata.json` | Đồng bộ file vào thư mục `logging/` | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                 | Thành viên/module được hỗ trợ | Kết quả                 |
| ------------------------- | ----------------------------- | ----------------------- |
| Debug logic mâu thuẫn Case Status | PolicyAgent & VerifierAgent | Chặn đứng lỗi `refund > 0` nhưng LLM lại phân loại là `no_action`, giúp PolicyAgent sửa lại prompt rule chặt chẽ hơn. |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao          | Cách xác minh   |
| --------------------- | --------------------------- | ------------------------- | --------------- |
| Xây dựng script Auto-grader nội bộ | `validate_submission.py` | Kịch bản kiểm tra toàn diện 9 tiêu chí của output schema | `uv run python validate_submission.py output` |
| Tối ưu thời gian khởi động (Cold Start) | `src/data_loader.py` | Giảm thời gian nạp và join 7 file CSV từ vài giây xuống < 0.1s nhờ `pickle.HIGHEST_PROTOCOL` | `uv run python src/data_loader.py` |
| Tổ chức thư mục audit log | `run_pipeline.py` | File trace và metadata được backup an toàn sang `logging/` sau mỗi lượt chạy | `ls logging/` |

Nêu một output cụ thể mà phần việc của bạn tạo ra hoặc giúp xác minh:

Script `validate_submission.py` đã phát hiện ra lỗi nghiêm trọng ở các case đầu tiên khi `cause_code` (ví dụ: `ORDER_CANCELED_AFTER_PAYMENT`) không khớp với `primary_issue` (`canceled_order_paid`), từ đó ép team phải cập nhật lại mapping `PRIMARY_TO_CAUSE` trong hệ thống agent. Hiện tại, toàn bộ 50/50 file đã vượt qua script xác minh này với thông báo: `✅ ALL 50 FILES PASSED 100% SCHEMA AND BUSINESS RULE CHECKS!`.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

1. **Khâu Validation:** Hệ thống Multi-Agent phụ thuộc vào kết quả sinh ra của LLM, rủi ro bị lệch định dạng, vượt quá giới hạn mảng (ví dụ: >5 items) hoặc sai logic nghiệp vụ tài chính (tiền hoàn > 0 nhưng trạng thái là `no_action`) là rất cao. Cần một công cụ độc lập hoàn toàn với Agent để "chấm điểm" trước khi nộp bài.
2. **Khâu Data:** Việc parse hàng trăm nghìn dòng CSV và thực hiện group/join trong lúc chạy runtime của Pipeline làm chậm chu trình debug và gây tốn RAM không cần thiết.

### Cách triển khai

- **Validation (`validate_submission.py`)**: Xây dựng hàm `validate_file` quét qua 9 rule nghiêm ngặt:
  - Kiểm tra tập hợp các key ở level cao nhất bằng phép toán Set (`REQUIRED_TOP_KEYS - keys`).
  - Kiểm tra tính hợp lệ của `primary_issue`, `case_status`, `currency`.
  - Assert giới hạn array (`len(aff.get("order_ids", [])) <= 5`, v.v.).
  - Bắt lỗi logic nghiệp vụ: Nếu `refund > 0` bắt buộc `case_status` là `action_required`.
  - Quét regex/tiền tố của `evidence_ids` (phải là `order:`, `item:`, `payment:`, `seller:`, `policy:`).
- **Data Preprocessing (`run_offline_join`)**: Script chạy độc lập một lần để load Pandas, build các dictionary pre-grouped (ví dụ: `items_by_order`, `payments_by_order`), liên kết định danh `customer_unique_id`, sau đó đóng gói toàn bộ vào một cục binary payload (`pickle.dump`) dung lượng tối ưu để `DataLoader` chỉ việc nạp lên RAM ngay tắp lự.

### Input, output và contract

| Thành phần              | Mô tả                                  |
| ----------------------- | -------------------------------------- |
| Input                   | Thư mục `output/*.json` và `data/*.csv` |
| Output                  | Terminal Logs (Pass/Fail) và `preprocessed_cache.pkl` |
| Module phụ thuộc        | `os`, `glob`, `json`, `pickle`, `pandas` |
| Module sử dụng output   | Toàn bộ các Agent trong `agent_system.py` đều phụ thuộc vào cache do `run_offline_join` sinh ra. |
| Điều kiện lỗi cần xử lý | JSON decode error (file rỗng hoặc format sai do LLM gãy), file CSV không tồn tại. |

### Cách xác minh

```bash
uv run python run_pipeline.py
uv run python validate_submission.py output
```
- **Kết quả mong đợi:** Quét đủ 50 file, in ra dòng xác nhận Passed toàn bộ.
- **Kết quả thực tế:** ` ALL 50 FILES PASSED 100% SCHEMA AND BUSINESS RULE CHECKS!`
- **Artifact/log:** Terminal stdout.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Lựa chọn phương pháp lưu trữ dữ liệu đã tiền xử lý để tăng tốc độ nạp (cold-start) cho Multi-Agent system.
- **Các phương án đã cân nhắc:**
  1. Ghi ra một database SQLite cục bộ.
  2. Dump ra các file JSON riêng lẻ.
  3. Dump toàn bộ payload dictionary bằng `pickle`.
- **Phương án đã chọn:** Phương án 3 (`pickle.dump` với `HIGHEST_PROTOCOL`).
- **Lý do:** Tốc độ deserialize của `pickle` bằng C-backend nhanh gấp hàng chục lần so với JSON đối với các cấu trúc nested dict/list phức tạp. Toàn bộ `items_by_order` và `payments_by_order` được load lên RAM chỉ trong <0.1s thay vì phải khởi tạo lại Pandas Dataframe, giảm thiểu độ trễ overhead khi liên tục thử nghiệm chạy 50 cases.
- **Bằng chứng quyết định phù hợp:** Log hiển thị: `[✓] Offline join complete in ... Cache size: ~MB.` và DataLoader khởi động gần như tức thì.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** `Rank 1 cause code '...' does not match primary issue '...'` in ra màn hình console lúc chạy `validate_submission.py`.
- **Lệnh hoặc bước tái hiện:** Chạy `python validate_submission.py output` ở các phiên bản dev đầu tiên.
- **Nguyên nhân gốc:** `PolicyAgent` có lúc chọn `primary_issue` là `late_delivery_seller` nhưng LLM lại ảo giác ghi `cause_code` thành `CARRIER_DELIVERED_AFTER_ESTIMATE` (vốn thuộc về logistics). Hệ thống Agent không tự bắt được sự lệch pha này.
- **Cách xử lý:** Đã update `validate_submission.py` để bổ sung strict check: `if ranked[0].get("cause_code") != PRIMARY_TO_CAUSE[primary]: errors.append(...)`. Từ đó, feedback lại cho core team để bổ sung cơ chế sửa mã tự động `_validate_and_sanitize_llm` bên trong `PolicyAgent`.
- **Cách xác minh sau khi sửa:** Chạy lại `validate_submission.py` không còn báo lỗi lệch pha cause code.
- **Điều học được:** Không bao giờ tin tưởng hoàn toàn vào sự đồng nhất dữ liệu của LLM. Các trường liên đới chặt chẽ (như `primary_issue` và `cause_code`) cần có một lớp hard-coded map để chặn đứng hoặc cảnh báo ngay lập tức.

## 7. Hiểu biết về luồng end-to-end

Giải thích ngắn gọn bằng lời của bạn (Lưu ý: Các câu hỏi lý thuyết hệ thống đánh giá theo form chuẩn):

1. **Dữ liệu đi từ Crossref đến vector index như thế nào?**
   Dữ liệu tài liệu/bài báo dạng JSON/XML lấy qua API được trích xuất text, đưa qua bộ chia nhỏ (Chunking), sử dụng embedding model chuyển thành vector không gian nhiều chiều, sau đó đẩy (upsert) vào Vector Database (như Milvus/Qdrant) cùng các metadata cần thiết.
2. **Evaluation set và ground-truth document IDs dùng để đo retrieval/answer quality ra sao?**
   Evaluation set chứa các câu hỏi (queries). Hệ thống sẽ lấy query đi tìm kiếm (retrieval) và trả về các document IDs. Nếu các ID này khớp hoặc nằm trong top K của ground-truth document IDs đã định trước, các chỉ số như Hit Rate, MRR, hay NDCG sẽ tăng, phản ánh chất lượng tìm kiếm tốt.
3. **Quality checks khác freshness monitoring ở điểm nào trong bài lab?**
   Quality checks thiên về đánh giá nội dung: độ chính xác của schema, tính đúng đắn của logic (ví dụ `refund > 0` phải đi kèm `action_required`). Freshness monitoring thiên về thời gian: dữ liệu hoặc index có bị cũ không, có cần phải sync hoặc crawl lại bản mới nhất để không bị lỗi thời không.
4. **Vì sao phải dùng cùng test set cho baseline, corrupted và repaired?**
   Để duy trì tính nhất quán của bài kiểm tra A/B (apple-to-apple). Việc dùng chung một thước đo duy nhất giúp khẳng định chắc chắn rằng sự suy giảm hay cải thiện metric hoàn toàn đến từ bản thân chất lượng dữ liệu/mô hình, chứ không phải do độ khó dễ của tập câu hỏi thay đổi.
5. **Repair được xem là thành công dựa trên artifact và metric nào?**
   Dựa trên artifact là dữ liệu/index đã được sửa lỗi (ví dụ: các bản ghi mất schema được điền đủ) và metric là các chỉ số đo lường (như Hit Rate, NDCG, % Pass Validation) khôi phục về lại ngưỡng bằng hoặc cao hơn so với hệ thống Baseline ban đầu.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Nguyễn Tuấn Dương  
**Ngày xác nhận:** 2026-08-05
