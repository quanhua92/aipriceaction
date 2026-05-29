# AIPriceAction Skills

[English](SKILLS.md)

AI Agent Skills miễn phí cho phân tích thị trường tài chính — cổ phiếu Việt Nam, crypto, thị trường quốc tế, vàng SJC. Không cần đăng ký, không cần API Key.

**100% FREE** · Không cần tài khoản · Không cần xác thực

---

## Cài đặt trong 30 giây

### Cách 1: Skills (khuyến nghị)

```bash
npx skills add quanhua92/aipriceaction
```

Chọn skills (aipa-data, aipa-analyze, aipa-research), chọn AI agent (Claude Code, Gemini CLI, Codex, Cursor...), xong — restart agent và bắt đầu hỏi.

### Cách 2: AGENTS.md (không cần cài skills)

```bash
curl -sSL https://raw.githubusercontent.com/quanhua92/aipriceaction/main/AGENTS.md -o AGENTS.md
```

Với Claude Code, tạo symlink:

```bash
ln -s AGENTS.md CLAUDE.md
```

Gemini CLI tự nhận diện AGENTS.md. AI agent tự cài `aipa-cli` khi chạy lệnh lần đầu.

### Yêu cầu

- Python 3.10+ (cho `aipa-cli`, tự cài qua `uvx`)
- **Không cần API Key** khi dùng với AI agent — agent tự đọc dữ liệu và phân tích
- `OPENAI_API_KEY` chỉ cần nếu chạy `aipa analyze` trực tiếp từ terminal mà không qua AI agent

### Cập nhật

```bash
npx skills update
```

CLI luôn tự cập nhật khi dùng `uvx aipa-cli` — luôn là bản mới nhất.

---

## 3 Skills cho mọi nhu cầu phân tích

### aipa-data — Dữ liệu thị trường thời gian thực

Dữ liệu thô, không cần AI, không cần API Key. OHLCV candles, volume profile, top performers, live data, watchlists.

- `Giá VCB hôm nay là bao nhiêu?`
- `Top 10 cổ phiếu có giá trị giao dịch cao nhất`
- `So sánh giá vàng SJC với vàng quốc tế GC=F`
- `Volume profile cho BTCUSDT — POC ở đâu?`
- `Liệt kê các cổ phiếu ngành ngân hàng`
- `Thị trường hôm nay thế nào? Cổ phiếu nào tăng mạnh nhất?`

### aipa-analyze — Phân tích kỹ thuật bằng AI

Phân tích đơn hoặc nhiều cổ phiếu cùng lúc với Wyckoff, VPA, Smart Money, MA Momentum. Khi dùng với AI agent (Claude Code, Gemini CLI...), agent tự đọc dữ liệu và phân tích — không cần API Key.

- `Phân tích VCB, TCB, MBB — ngân hàng nào có xu hướng mạnh nhất?`
- `Wyckoff analysis cho HPG`
- `Phân tích BTCUSDT khung 4h`
- `Phát hiện cổ phiếu có biến động bất thường và tìm nguyên nhân`
- `So sánh FPT, VNM, VIC với phân tích MA Momentum`
- `Cổ phiếu nào đang tích lũy? Dấu hiệu smart money thế nào?`

**Templates phân tích sẵn có:**
- Trading Opportunity (cơ hội giao dịch, Wyckoff, Smart Money)
- News & Events Research (phát hiện biến động bất thường, tìm tin tức)
- Price Action & Volume (VPA, dấu chân smart money)
- MA Momentum & Trend (xu hướng, crossover, xác nhận khối lượng)
- Wyckoff Method (phases, Spring, Upthrust, SOS, mục tiêu giá)
- Bob Volman Price Action (micro pullback, breakout/fading)

### aipa-research — Nghiên cứu thị trường toàn diện

Phân tích đa ngành với pipeline multi-agent: Supervisor chia task, Workers phân tích song song, Aggregator tổng hợp, Reviewer kiểm tra chất lượng. Agent-driven mode (khuyến nghị) không cần API Key.

- `Deep research ngành ngân hàng: top 10 ngân hàng, xu hướng, VPA signals`
- `Phân tích toàn diện thị trường chứng khoán Việt Nam`
- `Nghiên cứu crypto: Layer 1 vs DeFi vs AI tokens`
- `Ngành nào đang dẫn đầu thị trường?`
- `Phân tích toàn diện thị trường crypto trong tuần này`

**Pipeline:**

```
Supervisor → chia thành 3-5 ngành (Banking, Securities, Real Estate...)
     ↓
Parallel Workers → mỗi worker phân tích 1 ngành (~10 cổ phiếu mỗi ngành)
     ↓
Aggregator → tổng hợp cross-sector, bảng ranking
     ↓
Reviewer → kiểm tra chất lượng (MA scores, data integrity)
     ↓
Báo cáo cuối cùng
```

---

## 4 thị trường, 1 công cụ

### Cổ phiếu Việt Nam

Dữ liệu từ VCI, Vietstock, VNDirect, VPS, DNSE. Hỗ trợ VNINDEX, VN30 và 1600+ mã cổ phiếu.

**Khung thời gian:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 2W

**Watchlists sẵn có:** VN30 (30 mã), VINGROUP, MASAN, TM, INDEX (22 chỉ số), CROSS (cross-market)

### Crypto

Dữ liệu từ Binance. BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT và hơn 300 cặp.

**Khung thời gian:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 2W · Thị trường 24/7

### Thị trường quốc tế

Dữ liệu từ Yahoo Finance. AAPL, TSLA, NVDA, SPY, ^GSPC và hàng ngàn mã khác.

**Khung thời gian:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 2W

### Vàng SJC

Dữ liệu từ sjc.com.vn. Giá vàng SJC hàng ngày.

**Khung thời gian:** 1D

---

## AI Agent hỗ trợ

| AI Agent | Hỗ trợ |
|---|---|
| Claude Code | Yes |
| Gemini CLI | Yes |
| Codex | Yes |
| Cursor | Yes |
| openCode | Yes |
| Bất kỳ AI agent nào đọc file + chạy terminal | Yes (qua AGENTS.md) |

---

## Tại sao chọn chúng tôi?

| | **AIPriceAction Skills** | **Giải pháp khác** |
|---|---|---|
| Giá | Miễn phí vĩnh viễn | Miễn phí (Beta), chưa rõ sau này |
| Đăng ký / Auth | **Không cần** | Cần API Key |
| Cổ phiếu Việt Nam | Yes (VCI, Vietstock, VNDirect, VPS) | Yes |
| Crypto | Yes (Binance) | Hạn chế hoặc không |
| Thị trường quốc tế | Yes (Yahoo Finance) | Hạn chế hoặc không |
| Vàng SJC | Yes | Không |
| Phân tích kỹ thuật | Wyckoff, VPA, Smart Money, Bob Volman | Tóm tắt thị trường |
| Volume Profile | Yes (POC, Value Area, multi-day) | Không |
| Deep Research | Yes (multi-agent pipeline) | Không |
| AI Agents hỗ trợ | Claude Code, Gemini CLI, Codex, Cursor, openCode | Hạn chế hơn |
| Nguồn mở | Yes (MIT) | Yes |

---

## FAQ

**Có miễn phí không?**
Hoàn toàn miễn phí. Khi dùng với AI agent như Claude Code hay Gemini CLI, agent tự đọc dữ liệu và phân tích — không cần API Key nào cả.

**Tại sao không cần API Key cho dữ liệu?**
Dữ liệu OHLCV được cung cấp qua S3 archive công khai — truy cập bằng HTTP, không cần xác thực. Volume profile, performers, live-data đều hoạt động mà không cần credential.

**Tôi có cần cài đặt Python không?**
`aipa-cli` cần Python 3.10+, nhưng bạn không cần cài thủ công. AI agent tự cài đặt qua `uvx` khi chạy lệnh lần đầu. `uvx` quản lý môi trường ảo riêng, không ảnh hưởng đến hệ thống.

**Sự khác biệt giữa 3 skills là gì?**
- **aipa-data:** Dữ liệu thô — candles, volume profile, performers, live data. Không cần AI, không cần API Key.
- **aipa-analyze:** Phân tích kỹ thuật — Wyckoff, VPA, Smart Money, MA Momentum. AI agent tự đọc và phân tích, không cần API Key.
- **aipa-research:** Nghiên cứu chuyên sâu — pipeline multi-agent phân tích toàn ngành. Agent-driven mode không cần API Key.

**Dữ liệu có chính xác không?**
Dữ liệu tổng hợp từ nhiều nguồn uy tín (VCI, Vietstock, VNDirect, Binance, Yahoo Finance, sjc.com.vn). Phân tích do AI tạo ra nên có thể chứa sai sót — luôn kiểm chứng trước khi ra quyết định giao dịch.

**Có đặt lệnh giao dịch được không?**
Không. AIPriceAction Skills tập trung vào phân tích dữ liệu thị trường, không thực hiện lệnh mua bán. Đây là công cụ thông tin, không phải nền tảng giao dịch.

---

> AIPriceAction Skills là công cụ thông tin và phân tích. Phân tích do AI tạo ra và có thể chứa sai sót. Không phải tư vấn đầu tư, không phải khuyến nghị mua bán. Giao dịch chứng khoán, crypto đều có rủi ro. Hiệu suất quá khứ không đảm bảo kết quả tương lai.

[Github](https://github.com/quanhua92/aipriceaction) · [Website](https://aipriceaction.com) · [PyPI](https://pypi.org/project/aipa-cli/) · License: MIT
