# AIPriceAction

AIPriceAction cung cấp dữ liệu thị trường, chỉ báo kỹ thuật, API phân tích và
các công cụ Python/CLI cho cổ phiếu Việt Nam, crypto và tài sản quốc tế.

## Trạng thái hệ thống

API Python trong [`aipriceaction-api/`](aipriceaction-api/README.md) là hệ thống
được hỗ trợ cho đợt chuyển đổi này. Mã nguồn API Rust cũ đã được chuyển vào
[`legacy/rust/`](legacy/rust/README.md) và được đánh dấu không còn phát triển.
Bằng chứng triển khai gần nhất (2026-10-20) vẫn cho thấy production đang chạy
Rust; tài liệu này không khẳng định production đã chuyển đổi. Xem
[hướng dẫn migration](aipriceaction-api/MIGRATION.md) để biết các điều kiện,
bước chuyển đổi và rollback.

## Nguồn dữ liệu và lưu trữ

Dịch vụ Python dùng VPS, VNDirect và DNSE cho dữ liệu Việt Nam; Binance cho
crypto; Yahoo Finance cho tài sản quốc tế; và báo giá hằng ngày của SJC cho
vàng. VCI chỉ là nguồn dự phòng tùy chọn cho dữ liệu 1 phút lịch sử của Việt
Nam. Dữ liệu mới và trạng thái vận hành nằm trong SQLite. Lịch sử đã xác minh
được lưu thành Parquet có checksum trong kho tương thích S3 và đọc bằng DuckDB.
Redis là tùy chọn, không phải nguồn dữ liệu chuẩn. Docker Compose cục bộ dùng
RustFS làm object store tương thích S3. Xem
[chi tiết nguồn và lưu trữ](docs/DATA_SOURCES.md) và [luồng dữ liệu](DATA_FLOW.md).

## Chạy API cục bộ

```sh
cd aipriceaction-api
# Nếu chưa có .env, sao chép .env.example và đặt REFRESH_SECRET khác rỗng.
docker compose up -d --build
curl --fail http://localhost:8899/health
```

API dùng cổng host `8899`; explorer tích hợp nằm tại
<http://localhost:8899/explorer>. Website biểu đồ chính là ứng dụng Vite riêng.
Script cục bộ chạy bản sao tạm thời và proxy `/aipriceaction-api` tới Python mà
không sửa repository web bên cạnh:

```sh
cd aipriceaction-api
.venv/bin/python scripts/serve_local_web.py --api http://127.0.0.1:8899 --port 5173
```

## Tài liệu

- [Kiến trúc và tính năng](FEATURES.md)
- [Nguồn dữ liệu](docs/DATA_SOURCES.md)
- [Hợp đồng API](aipriceaction-api/CONTRACT.md)
- [Vận hành API Python](aipriceaction-api/README.md)
- [Kế hoạch chuyển production và rollback](aipriceaction-api/MIGRATION.md)
- [Bằng chứng kiểm thử gần nhất](aipriceaction-api/VALIDATION.md)
- [Quy trình phân tích và hướng dẫn CLI](AGENTS.md)
