# AIPriceAction Data Pipeline

This project is a flexible and efficient data pipeline designed to download, analyze, and visualize stock data from the Vietnamese market.

It automatically fetches daily price data for a configurable list of tickers, generates detailed candlestick charts, caches data locally to avoid redundant downloads, and integrates qualitative analysis into a single, comprehensive markdown report.

---

## 🚀 View the Latest Analysis

The primary output of this project is the **`REPORT.md`** file. This file is automatically regenerated with the latest data and analysis every time the script runs.

**➡️ [Click here to view the Daily Market Report](REPORT.md)**

**➡️ [Click here to view the Weekly Market Report](REPORT_week.md)**

**🎢 [Click here to view the latest Market Leaders](LEADER.md)**

**🐎 [Click here to view the Market Impact Contributors](IMPACT.md)**

**🏦 [Click here to view the Fund Performance Deep-Dive](FUNDS.md)**

---

## 🎯 View the Trading Plan

Based on the latest analysis, the **`PLAN.md`** file outlines potential trading strategies and top opportunities. This plan synthesizes VPA signals for a mid-term perspective.

**➡️ [Click here to view the trading plan](PLAN.md)**

---
## Analysis Sources

1. **Daily Analysis Sources:**

* **`REPORT.md`**: For the most recent daily signals and price/volume activity (last 10 days).
* **`VPA.md`**: For the detailed, multi-session daily VPA narrative of each ticker. 
* **`market_data.txt`**: For the raw daily price, volume, and OHLC data (last 40 days) used to verify daily signals. 

2. **Weekly Analysis Sources:**

* **`REPORT_week.md`**: For the most recent weekly signals, providing a strategic, big-picture view.
* **`VPA_week.md`**: For the broader historical context and the multi-week VPA narrative.
* **`market_data_week.txt`**: For the raw weekly OHLC data (last 40 weeks) to verify long-term signals. 


3. **Contextual & Grouping Sources:**

* **`LEADER.md`**: For assessing the **industry context** based on weekly analysis. You must use this to determine if a ticker is in a strong (`Dẫn dắt Đồng Thuận`), weakening, or weak (`Yếu/Phân Phối`) industry group.
* **`GROUP.md`**: The definitive source for mapping individual tickers to their respective industry groups.
* **`IMPACT.md`**: Identifies the top stocks and sectors driving or holding back the VN-Index, providing insight into market-wide momentum.

### 4. Fund Universe Analysis

*   **`FUNDS.md`**: A comprehensive deep-dive into the performance, risk profiles, and portfolio compositions of major Vietnamese investment funds. Use this to compare professional money managers against the market and each other.

---

## 📚 VPA & Wyckoff Method Tutorial System

This project includes a comprehensive Vietnamese-language tutorial system covering Volume Price Analysis (VPA) and Wyckoff Method principles with real market data examples.

### 📖 Tutorial Chapters

**Khái Niệm Cơ Bản:**
- [Chương 1.1: VPA Cơ Bản (Tiếng Việt)](docs/tutorials/chapter-1-1-vpa-basics.md)
- [Chương 1.2: Các Định Luật Wyckoff (Tiếng Việt)](docs/tutorials/chapter-1-2-wyckoff-laws.md)
- [Chương 1.3: Composite Man (Tiếng Việt)](docs/tutorials/chapter-1-3-composite-man.md)

**Các Giai Đoạn Thị Trường:**
- [Chương 2.1: Các Pha Tích Lũy (Tiếng Việt)](docs/tutorials/chapter-2-1-accumulation-phases.md)
- [Chương 2.2: Các Pha Phân Phối (Tiếng Việt)](docs/tutorials/chapter-2-2-distribution-phases.md)

**Nhận Dạng Tín Hiệu:**
- [Chương 3.1: Tín Hiệu VPA Tăng Giá (Tiếng Việt)](docs/tutorials/chapter-3-1-bullish-vpa-signals.md)
- [Chương 3.2: Tín Hiệu VPA Giảm Giá (Tiếng Việt)](docs/tutorials/chapter-3-2-bearish-vpa-signals.md)

**Hệ Thống Giao Dịch:**
- [Chương 4.1: Hệ Thống Giao Dịch (Tiếng Việt)](docs/tutorials/chapter-4-1-trading-systems.md)

**Khái Niệm Nâng Cao:**
- [Chương 5.1: Nhận Dạng Mô Hình Nâng Cao](docs/tutorials/advanced/chapter-5-1-quantitative-vpa-framework.md)
- [Chương 5.2: Engine Backtesting Tổ Chức](docs/tutorials/advanced/chapter-5-2-backtesting-engine.md)
- [Chương 5.3: Phân Tích Dòng Tiền Thông Minh](docs/tutorials/advanced/chapter-5-3-smart-money-flow-analysis.md)
- [Chương 5.4: Nhận Dạng Mô Hình Machine Learning](docs/tutorials/advanced/chapter-5-4-machine-learning-vpa.md)
- [Chương 5.5: Phân Tích Liên Thị Trường](docs/tutorials/advanced/chapter-5-5-cross-market-analysis.md)
- [Chương 5.6: Hệ Thống Cảnh Báo Thông Minh](docs/tutorials/advanced/chapter-5-6-intelligent-alert-system.md)
- [Chương 5.7: Tối Ưu Hóa Danh Mục](docs/tutorials/advanced/chapter-5-7-portfolio-optimization.md)
- [Chương 5.8: Phân Bổ Hiệu Suất](docs/tutorials/advanced/chapter-5-8-performance-attribution.md)
- [Chương 5.9: Triển Khai Production](docs/tutorials/advanced/chapter-5-9-production-deployment.md)

### 🎯 Nghiên Cứu Tình Huống: Chiến Dịch Tích Lũy 2025

Phân tích chi tiết các chiến dịch tích lũy tổ chức trong cổ phiếu Việt Nam năm 2025:

**➡️ [Nghiên Cứu Tình Huống: VIC - Chiến Dịch Tích Lũy Bất Động Sản 2025](docs/tutorials/case-studies/vic-accumulation-2025.md)**
- Phân tích pattern tích lũy VIC Vingroup với chuỗi VPA hoàn hảo
- Thể hiện sức mạnh leader bất động sản trong phục hồi thị trường

**➡️ [Nghiên Cứu Tình Huống: VHM - Chiến Dịch Tích Lũy Biến Động 2025](docs/tutorials/case-studies/vhm-accumulation-2025.md)**
- Pattern tích lũy với biến động cao của VHM Vinhomes
- Minh họa đặc tính beta cao trong ngành bất động sản

**➡️ [Nghiên Cứu Tình Huống: SSI - Chiến Dịch Tích Lũy Nhà Vô Địch Ngành Chứng Khoán 2025](docs/tutorials/case-studies/ssi-accumulation-2025.md)**
- Phân tích Shakeout pattern và luân chuyển ngành chứng khoán
- Thể hiện đặc tính dịch vụ tài chính trong chu kỳ phục hồi

**➡️ [Nghiên Cứu Tình Huống: VIX - Chiến Dịch Tích Lũy Gã Khổng Lồ Cơ Sở Hạ Tầng 2025](docs/tutorials/case-studies/vix-accumulation-2025.md)**
- Pattern đảo chiều từ phân phối sang tích lũy
- Minh họa đặc tính chu kỳ cơ sở hạ tầng

**➡️ [Nghiên Cứu Tình Huống: LPB - Chiến Dịch Tích Lũy Chuyển Đổi Ngân Hàng 2025](docs/tutorials/case-studies/lpb-accumulation-2025.md)**
- Chuỗi VPA kinh điển: No Supply → Test for Supply → Sign of Strength
- Thể hiện câu chuyện chuyển đổi ngân hàng khu vực

**➡️ [Nghiên Cứu Tình Huống: VCB - Chiến Dịch Tích Lũy Ngân Hàng Quốc Gia 2025](docs/tutorials/case-studies/vcb-accumulation-2025.md)**
- Phân tích pattern tích lũy của ngân hàng lớn nhất Việt Nam
- Thể hiện đặc tính blue-chip banking trong chu kỳ phục hồi

**➡️ [Nghiên Cứu Tình Huống: Phân Tích Luân Chuyển Ngành](docs/tutorials/case-studies/sector-rotation-analysis.md)**
- Nghiên cứu về luân chuyển ngành trong thị trường Việt Nam
- Chiến lược đầu tư theo chu kỳ ngành

**➡️ [Nghiên Cứu Tình Huống: Phân Tích Phân Phối VN-Index](docs/tutorials/case-studies/vnindex-distribution-analysis.md)**
- Phân tích pattern phân phối của VN-Index
- Nhận dạng tín hiệu đảo chiều thị trường

### 🗺️ Điều Hướng

**➡️ [Bản Đồ Tutorial & Tổng Quan Nội Dung](docs/methods/MAP_OF_CONTENT.md)**
**➡️ [Phương Pháp & Phương Pháp Luận VPA](docs/methods/README.md)**
**➡️ [Hướng Dẫn Sử Dụng Dữ Liệu Thị Trường](docs/tutorials/data-integration/how-to-use-market-data.md)**

---

## 🚀 Hướng Dẫn Bắt Đầu Nhanh

### Tính Năng Chính

-   **Danh Sách Ticker Có Thể Cấu Hình**: Dễ dàng quản lý các cổ phiếu cần phân tích bằng cách chỉnh sửa file `TICKERS.csv` đơn giản.
-   **Smart Data Caching**: Tự động lưu dữ liệu đã tải và tải lại từ file local trong các lần chạy tiếp theo, tiết kiệm thời gian và yêu cầu mạng.
-   **Tích Hợp VPA**: Đọc phân tích định tính của bạn từ file `VPA.md` và tích hợp liền mạch vào báo cáo cuối cùng.
-   **Báo Cáo Chi Tiết**: Tạo file `REPORT.md` chính với bảng tóm tắt, mục lục, và phân tích chi tiết cho từng ticker.
-   **Biểu Đồ Nâng Cao**: Tạo biểu đồ nến chuyên nghiệp cho từng ticker, đầy đủ với khối lượng và nhiều đường trung bình động.

### Thiết Lập và Sử Dụng

#### 1. Cấu Hình Tickers

Tạo và chỉnh sửa file **`TICKERS.csv`** trong thư mục dự án chính. Thêm các ký hiệu ticker bạn muốn phân tích, mỗi dòng một ticker, dưới header `ticker`.

_Ví dụ `TICKERS.csv`:_

```csv
ticker
VNINDEX
TCB
FPT
```

#### 2. (Tùy Chọn) Thêm Phân Tích Của Bạn

Bạn có thể thêm phân tích price action của riêng mình vào file **`VPA.md`**. Script sẽ phân tích file này và hiển thị ghi chú của bạn cùng với ticker tương ứng trong báo cáo cuối cùng. Sử dụng markdown header cho từng ticker.

_Ví dụ `VPA.md`:_

```markdown
# FPT

-   Xu hướng tăng mạnh tiếp tục.
-   Pullback về đường MA 20 ngày có thể là cơ hội mua vào.

# TCB

-   Cho thấy dấu hiệu tích lũy trong vùng hiện tại.
```

#### 3. Cài Đặt Dependencies

Trước khi chạy script lần đầu tiên, cài đặt các thư viện Python cần thiết sử dụng file `requirements.txt`.

Mở terminal và chạy:

```bash
pip install -r requirements.txt
```

#### 4. Chạy Pipeline

Để thực thi data pipeline, đơn giản chỉ cần chạy script `main.py` từ terminal:

```bash
python main.py
```

## 🤖 VPA Processing Coordinator

Dự án bao gồm một script mạnh mẽ `main_process_vpa.py` để tự động hóa toàn bộ quy trình phân tích VPA (Volume Price Analysis) sử dụng AI agent coordination.

### Tính Năng Chính

- **Đa AI Agent**: Hỗ trợ cả Claude và Gemini CLI
- **Phân Tích Thông Minh**: Tự động bỏ qua dữ liệu đã được phân tích
- **Logging Toàn Diện**: Ghi log chi tiết với timestamp, lưu tại `/tmp`
- **Kiểm Tra Cổ Tức**: Tự động phát hiện cần điều chỉnh cổ tức
- **Xử Lý Lỗi Mạnh Mẽ**: Tiếp tục xử lý khi gặp lỗi với ticker cụ thể

### Cách Sử Dụng

#### Phân Tích Hàng Ngày (Daily)

```bash
# Sử dụng Claude (mặc định)
python main_process_vpa.py

# Sử dụng Gemini
python main_process_vpa.py --agent gemini

# Bật debug logging
python main_process_vpa.py --debug

# Claude với debug
python main_process_vpa.py --agent claude --debug
```

#### Phân Tích Hàng Tuần (Weekly)

```bash
# Phân tích tuần với Claude
python main_process_vpa.py --week

# Phân tích tuần với Gemini
python main_process_vpa.py --week --agent gemini

# Debug mode cho phân tích tuần
python main_process_vpa.py --week --agent gemini --debug
```

### Quy Trình Hoạt Động

1. **Kiểm Tra Cổ Tức**: Tự động phát hiện thư mục `market_data_check_dividends/` hoặc `market_data_check_dividends_week/`
2. **Phân Tích Thông Minh**: 
   - Đọc dữ liệu CSV mới nhất từ `market_data/` hoặc `market_data_week/`
   - So sánh với phân tích VPA hiện có trong `vpa_data/` hoặc `vpa_data_week/`
   - Chỉ xử lý ticker có dữ liệu mới chưa được phân tích
3. **AI Coordination**: 
   - Gọi `claude -p` hoặc `gemini -p` với context đầy đủ
   - Mỗi ticker được xử lý với thông tin giá/khối lượng và VPA lịch sử
4. **Merge Tự Động**: Gọi `merge_vpa.py` để tổng hợp kết quả vào `VPA.md` hoặc `VPA_week.md`

### Thông Số Dòng Lệnh

| Tham số | Mô tả | Mặc định |
|---------|-------|----------|
| `--week` | Chế độ phân tích hàng tuần | Daily |
| `--agent` | AI agent sử dụng (`claude` hoặc `gemini`) | `claude` |
| `--debug` | Bật debug logging chi tiết | False |

### Log Files

- **Vị trí**: `/tmp/vpa_processing_YYYYMMDD_HHMMSS.log`
- **Format**: `HH:MM:SS - LEVEL - MESSAGE`
- **Levels**: INFO (standard), DEBUG (với --debug)

### Ví Dụ Output

```
15:30:22 - INFO - 🚀 Starting VPA Processing Coordinator
15:30:22 - INFO - 📅 Mode: Daily
15:30:22 - INFO - 🤖 AI Agent: CLAUDE
15:30:22 - INFO - ✓ No dividend folder found
15:30:23 - INFO - 📊 Loaded 108 tickers from TICKERS.csv
15:30:24 - INFO - 📊 15 out of 108 tickers need analysis
15:30:25 - INFO - 🤖 Calling CLAUDE for VHM analysis...
15:30:45 - INFO - ✅ VHM: Analysis completed successfully
15:31:02 - INFO - 📊 VPA Analysis Summary:
15:31:02 - INFO -    ✓ Successful: 15
15:31:02 - INFO -    ❌ Failed: 0
15:31:02 - INFO -    📈 Success rate: 100.0%
15:31:03 - INFO - ✓ VPA analysis merged successfully
15:31:03 - INFO - 🎉 VPA Processing Complete!
```

### Tích Hợp với CLAUDE.md

Script tuân theo hoàn toàn các giao thức trong `tasks/DAILY_VPA.md` và `tasks/WEEKLY_VPA.md`, bao gồm:

- ✅ Kiểm tra dividends trước khi phân tích  
- ✅ Sử dụng reliable Python cho CSV operations
- ✅ Gọi claude/gemini -p cho complex analysis tasks
- ✅ Chỉ append analysis mới, không ghi đè existing data
- ✅ Sử dụng proper Vietnamese VPA terminology
- ✅ Manual verification và error handling

---
