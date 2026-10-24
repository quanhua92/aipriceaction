# AIPriceAction

AIPriceAction cung cấp dữ liệu thị trường, chỉ báo kỹ thuật, API phân tích và
các công cụ Python/CLI cho cổ phiếu Việt Nam, crypto và tài sản quốc tế.

## Trạng thái hệ thống

API Python trong [`aipriceaction-api/`](aipriceaction-api/README.md) là hệ thống
được hỗ trợ. Mã nguồn API Rust cũ đã chuyển vào
[`legacy/rust/`](legacy/rust/README.md); API Rust đã deprecated và chỉ giữ để
tham khảo. Xem
[hướng dẫn migration](aipriceaction-api/MIGRATION.md) để biết các bước triển
khai.

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
cp .env.example .env  # chỉ chạy lần đầu; giữ lại .env đang có
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
# Dán giá trị vừa tạo vào REFRESH_SECRET trong .env.
# Nếu HTTP_PROXIES trống, worker tự kết nối trực tiếp tới nhà cung cấp VN.
# Nếu có proxy, đặt ALLOW_DIRECT=true để cho phép kết nối trực tiếp dự phòng.
docker compose up -d --build
docker compose ps
curl --fail http://localhost:8899/health
```

API dùng cổng host `8899`; explorer tích hợp nằm tại
<http://localhost:8899/explorer>. Cổng này có thể truy cập từ LAN nếu firewall
cho phép. RustFS dùng cổng `9100` và `9101`, mặc định chỉ bind loopback. Khởi
tạo lần đầu chỉ tải metadata mã chứng khoán; lịch sử giá cũ từ Rust/PostgreSQL
không được tự động chuyển sang.

Website cần một checkout riêng của `aipriceaction-web` đã cài dependencies
Node. Từ thư mục `aipriceaction-api`, tạo môi trường Python và chạy launcher
bằng đường dẫn checkout web thực tế. Launcher chỉ tạo bản sao tạm và proxy
`/aipriceaction-api` tới Python, không sửa repository web:

```sh
uv sync --locked --group dev
WEB_DIR=/path/to/aipriceaction-web
uv run python scripts/serve_local_web.py \
  --web-dir "$WEB_DIR" \
  --api http://127.0.0.1:8899 \
  --port 5173
```

## Tài liệu

- [Kiến trúc và tính năng](FEATURES.md)
- [Nguồn dữ liệu](docs/DATA_SOURCES.md)
- [Hợp đồng API](aipriceaction-api/CONTRACT.md)
- [Vận hành API Python](aipriceaction-api/README.md)
- [Kế hoạch chuyển production và rollback](aipriceaction-api/MIGRATION.md)
- [Bằng chứng kiểm thử gần nhất](aipriceaction-api/VALIDATION.md)
- [Quy trình phân tích và hướng dẫn CLI](AGENTS.md)
