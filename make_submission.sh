#!/usr/bin/env bash
# make_submission.sh
# Tạo file zip nộp bài với cấu trúc: submission.zip/output/EC_001.json ... EC_050.json

set -e

SRC_DIR="${1:-output_qwen3-8b}"          # thư mục nguồn, mặc định là output/
OUT_ZIP="${2:-output.zip}"  # tên file zip, mặc định là submission.zip

echo "📦 Đóng gói từ '${SRC_DIR}/' -> '${OUT_ZIP}' ..."

# Kiểm tra thư mục nguồn
if [ ! -d "${SRC_DIR}" ]; then
    echo "❌ Không tìm thấy thư mục '${SRC_DIR}'"
    exit 1
fi

# Đếm file JSON
COUNT=$(ls "${SRC_DIR}"/EC_*.json 2>/dev/null | wc -l)
if [ "${COUNT}" -ne 50 ]; then
    echo "❌ Cần đúng 50 file, hiện có: ${COUNT}"
    exit 1
fi

# Tạo zip với cấu trúc output/EC_*.json
rm -f "${OUT_ZIP}"
python3 -c "
import zipfile, glob, os, sys
src = sys.argv[1]; out = sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
    for f in sorted(glob.glob(f'{src}/EC_*.json')):
        zf.write(f, f'output/{os.path.basename(f)}')
# Verify
z = zipfile.ZipFile(out)
names = sorted(z.namelist())
print(f'✅ {out}: {len(names)} files ({os.path.getsize(out)/1024:.1f} KB)')
print(f'   {names[0]}  ->  {names[-1]}')
" "${SRC_DIR}" "${OUT_ZIP}"
