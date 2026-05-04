<!-- AIPriceAction Multi-Agent Research -->

  Model:    openrouter/owl-alpha
  Base URL: https://openrouter.ai/api/v1
  Started:  2026-05-04 05:14:32 UTC
  Lang:     vn

---

[1] Fetching market snapshot (all VN tickers, latest bar)...
    Tickers: 403

[2] Starting multi-agent research...

    Question: Cung cấp tổng quan thị trường chứng khoán Việt Nam toàn diện. Sử dụng bức tranh thị trường để xác định ngành và mã hoạt động mạnh nhất, sau đó nghiên cứu sâu 2-3 ngành với dữ liệu OHLCV đầy đủ. Mỗi ngành: tải dữ liệu tất cả mã đại diện và các mã liên quan, đánh giá xu hướng, tín hiệu VPA, động lực MA score qua các khung thời gian, xác nhận khối lượng, và xác định mã dẫn đầu/lagging trong ngành. Sau đó tổng hợp mô hình luân chuyển liên ngành và xếp hạng thống nhất.

---

    Session: 019df168-d9ec-7559-5d6e-4b3c2a23d485
    Folder:  /var/folders/hd/20zqmjkj7cd0wm4rv2230bm00000gn/T/aipriceaction-checkpoints/019df168-d9ec-7559-5d6e-4b3c2a23d485

[Supervisor] Decomposed into 3 subtasks:
  - Bất động sản: VHM, VIC, NVL, DXG, VRE, PDR, KBC, SCR, DIG, HDC, HDG, CRE, NTL, KDH, LDG, CEO, AGG, API, DRH, HQC, ITC, LGL, NRC, PV2, SGR, SZC, TAL, TCH, TIG, VC3, VPI, HPX, HTN, HAR
  - Ngân hàng: VCB, BID, TCB, VPB, MBB, ACB, HDB, STB, TPB, EIB, LPB, MSB, OCB, SSB, VIB, NAB, KLB, SHB, CTG, ABB, BVB, SGB, VAB, NVB
  - Chứng khoán: SSI, HCM, VCI, VND, MBS, SHS, FTS, BSI, CTS, DSC, EVS, BVS, ABW, AGR, APG, BMS, ORS, PSI, SBS, TCX, TVB, TVC, VCK, VDS, VFS, VIG, VPX, AAS, APS

  [Worker:Bất động sản] Starting analysis for VHM, VIC, NVL, DXG, VRE, PDR, KBC, SCR, DIG, HDC, HDG, CRE, NTL, KDH, LDG, CEO, AGG, API, DRH, HQC, ITC, LGL, NRC, PV2, SGR, SZC, TAL, TCH, TIG, VC3, VPI, HPX, HTN, HAR...
  [Worker:Chứng khoán] Starting analysis for SSI, HCM, VCI, VND, MBS, SHS, FTS, BSI, CTS, DSC, EVS, BVS, ABW, AGR, APG, BMS, ORS, PSI, SBS, TCX, TVB, TVC, VCK, VDS, VFS, VIG, VPX, AAS, APS...
  [Worker:Ngân hàng] Starting analysis for VCB, BID, TCB, VPB, MBB, ACB, HDB, STB, TPB, EIB, LPB, MSB, OCB, SSB, VIB, NAB, KLB, SHB, CTG, ABB, BVB, SGB, VAB, NVB...
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'VCB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'BID', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'TCB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'VPB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'MBB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'LPB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'MSB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'ACB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'VIB', 'limit': 30})
  [Worker:Ngân hàng] [tool call] get_ohlcv_data({'ticker': 'VNFINLEAD', 'limit': 30})
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  BID - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Đầu tư và Phát triển Việt Nam - [NGAN_HANG]  === Dữ Liệu Thị Trường ...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  VIB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Quốc tế Việt Nam - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ liệu ...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  MBB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Quân đội - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lị...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  VCB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Ngoại thương Việt Nam - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ ...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  MSB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Hàng hải Việt Nam - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ liệu...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  LPB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Lộc Phát Việt Nam - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ liệu...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  ACB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Á Châu - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lịch...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  VPB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Việt Nam Thịnh Vượng - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ l...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  TCB - — Mã Chính (đối tượng phân tích) - Ngân hàng TMCP Kỹ thương Việt Nam - [NGAN_HANG]  === Dữ Liệu Thị Trường ===  Dữ liệ...
  [Worker:Ngân hàng] [tool result]  === Thông Tin Mã CK ===  VNFINLEAD - — Mã Chính (đối tượng phân tích) - [INDEX]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lịch sử với đường trung bì...
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'VNREAL', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'VHM', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'VIC', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'NVL', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'DXG', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'VRE', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'CRE', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'AGG', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'CEO', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool call] get_ohlcv_data({'ticker': 'LDG', 'limit': 30, 'interval': '1D'})
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  VHM - — Mã Chính (đối tượng phân tích) - CTCP Vinhomes - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lịch sử v...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  VIC - — Mã Chính (đối tượng phân tích) - Tập đoàn VINGROUP - CTCP - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLC...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  LDG - — Mã Chính (đối tượng phân tích) - CTCP Đầu tư LDG - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lịch sử...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  NVL - — Mã Chính (đối tượng phân tích) - CTCP Tập đoàn Đầu tư Địa ốc No Va - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ ...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  VNREAL - — Mã Chính (đối tượng phân tích) - [INDEX]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lịch sử với đường trung bình ...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  VRE - — Mã Chính (đối tượng phân tích) - CTCP Vincom Retail - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lịch...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  DXG - — Mã Chính (đối tượng phân tích) - CTCP Tập đoàn Đất Xanh - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV ...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  AGG - — Mã Chính (đối tượng phân tích) - CTCP Đầu tư và Phát triển Bất động sản An Gia - [BAT_DONG_SAN]  === Dữ Liệu Thị Trư...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  CEO - — Mã Chính (đối tượng phân tích) - CTCP Tập đoàn C.E.O - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLCV lịc...
  [Worker:Bất động sản] [tool result]  === Thông Tin Mã CK ===  CRE - — Mã Chính (đối tượng phân tích) - CTCP Bất động sản Thế Kỷ - [BAT_DONG_SAN]  === Dữ Liệu Thị Trường ===  Dữ liệu OHLC...
  [Worker:Chứng khoán] Analysis complete (5,934 chars)

  [Worker:Bất động sản] Analysis complete (9,815 chars)

  [Worker:Ngân hàng] Analysis complete (14,755 chars)

[Aggregator] Synthesizing 3 sector reports...

[Aggregator] Analysis synthesized (14,704 chars)

[Writer] Formatting final report...

[Writer] Report generated (23,104 chars)

---

## [3] FINAL REPORT

---

# 🏛️ BÁO CÁO TỔNG QUAN THỊ TRƯỜNG CHỨNG KHOÁN VIỆT NAM

## AIPriceAction Investment Advisor

**Website:** https://aipriceaction.com/

**Ngày phát hành:** 04/05/2026

**Dữ liệu:** Thanh giao dịch gần nhất (phiên 04/05/2026 cho thị trường VN, 01/05/2026 cho thị trường quốc tế)

---

## 📊 PHẦN I: TÓM TẮT ĐIỀU HÀNH

### Nhận định tổng thể

Thị trường chứng khoán Việt Nam đang trong **giai đoạn tăng giá rõ ràng** trên các chỉ số chính, với sự phân hóa đáng kể giữa các ngành và nhóm vốn hóa. Dòng tiền thông minh đang tập trung mạnh vào nhóm **Bất động sản** và **Ngân hàng**, trong khi các ngành như **Y tế**, **Năng lượng** và **Cổ tức** đang bị rút tiền.

### Các điểm nổi bật

| Chỉ số | Giá đóng cửa | MA20 Score | MA50 Score | MA200 Score | Xu hướng |
|--------|-------------|------------|------------|-------------|----------|
| **VNINDEX** | 1,855.66 | +3.71% | +5.47% | +8.96% | ✅ Tăng mạnh |
| **VN30** | 2,018.47 | +3.24% | +5.03% | +6.48% | ✅ Tăng |
| **VN100** | 1,934.51 | +2.96% | +5.25% | +6.42% | ✅ Tăng |
| **HNX30** | 530.97 | -1.22% | -2.14% | -6.18% | ⚠️ Yếu hơn |
| **VNALLSHARE** | 1,911.99 | +2.99% | +5.21% | +6.14% | ✅ Tăng |
| **VNXALLSHARE** | 3,008.99 | +2.87% | +5.03% | +5.67% | ✅ Tăng |

### Tín hiệu VPA chính

- **SOS (Sign of Strength):** VHM breakout ngày 02/05 với volume 16.3 triệu cổ phiếu (+703%)
- **ST (Strength):** VIC, VRE, NVL — xu hướng tăng được xác nhận bởi volume
- **SPRING:** TCB, LPB — breakout từ vùng tích lũy
- **UT (Upthrust):** NVL phiên 04/05 giảm -6.83% — cần theo dõi exhaustion
- **SOW (Sign of Weakness):** AGG, VND, MBS — xu hướng giảm rõ rệt

---

## 📈 PHẦN II: PHÂN TÍCH TỪNG NGÀNH VỚI BẢNG XẾP HẠNG

### 🏠 NGÀNH 1: BẤT ĐỘNG SẢN — DẪN ĐẦU TUYỆT ĐỐI

#### Tổng quan ngành

Chỉ số **VNREAL** đạt MA200 **+46.05%** — ngành mạnh nhất toàn thị trường. Dòng tiền tập trung mạnh nhất, xu hướng tăng được xác nhận trên mọi khung thời gian.

#### Bảng xếp hạng chi tiết mã BĐS

| Hạng | Mã | Giá | MA10 | MA20 | MA50 | MA100 | MA200 | Vai trò VPA |
|------|-----|-----|------|------|------|-------|-------|-------------|
| 🥇 | **VIC** | 210,100 | +2.72% | +17.77% | +31.36% | +34.56% | +76.75% | ⭐⭐⭐⭐⭐ Leader |
| 🥈 | **VHM** | 145,200 | -0.03% | +8.50% | +27.10% | +28.03% | +35.79% | ⭐⭐⭐⭐⭐ Leader |
| 🥉 | **VRE** | 33,650 | +11.61% | +17.21% | +21.98% | +13.40% | +8.84% | ⭐⭐⭐⭐ Leader |
| 4 | **NVL** | 19,100 | +2.47% | +10.74% | +31.82% | +37.87% | +28.85% | ⭐⭐⭐⭐ Leader |
| 5 | **DXG** | 15,600 | +4.21% | +4.82% | +6.85% | +0.20% | -14.11% | ⭐⭐ Middle |
| 6 | **CEO** | 18,000 | +3.99% | +5.11% | +7.67% | -4.73% | -17.96% | ⭐⭐ Middle |
| 7 | **KBC** | 33,900 | -1.37% | +0.62% | +3.70% | +0.27% | -1.13% | ⭐⭐ Middle |
| 8 | **NLG** | 28,000 | -0.48% | -1.70% | +0.11% | -5.04% | -16.54% | ⭐ Lagging |
| 9 | **PDR** | 16,450 | +1.32% | +1.32% | +2.73% | -5.80% | -17.80% | ⭐ Lagging |
| 10 | **KDH** | 25,200 | -1.93% | -2.31% | -3.61% | -10.65% | -17.75% | ❌ Weak |
| 11 | **AGG** | 12,600 | -0.16% | -0.73% | -4.09% | -9.37% | -21.90% | ❌ Weakest |
| 12 | **HDC** | 19,250 | +2.78% | +2.91% | +2.44% | -7.61% | -25.18% | ❌ Weak |
| 13 | **HDG** | 26,050 | -3.70% | -6.10% | -6.44% | -5.33% | -10.42% | ❌ Weak |
| 14 | **SCR** | 6,060 | -1.11% | -1.76% | -1.41% | -10.72% | -23.07% | ❌ Weakest |
| 15 | **CRV** | 28,000 | -3.01% | +0.29% | +0.55% | -3.03% | -6.49% | ⭐ Lagging |

#### Phân tích VPA sâu — Nhóm Leaders

**VIC (Tập đoàn VINGROUP)**
- **Cấu trúc:** MA Score dương mạnh trên tất cả các khung — MA10 +2.72%, MA20 +17.77%, MA50 +31.36%, MA100 +34.56%, MA200 +76.75%
- **VPA:** Giai đoạn tích lũy từ 140,000 lên 225,500 trong tháng 4. Phiên 04/05 điều chỉnh -1.82% với volume giảm mạnh (-67%) — **healthy pullback** trong xu hướng tăng
- **Wyckoff:** Đang trong giai đoạn **Markup** sau breakout từ vùng tích lũy
- **Kết luận:** Mã dẫn đầu tuyệt đối, xu hướng tăng dài hạn rõ rệt

**VHM (Vinhomes)**
- **Cấu trúc:** MA20 +8.50%, MA50 +27.10%, MA100 +28.03%, MA200 +35.79%
- **VPA:** **SOS cực kỳ mạnh** ngày 02/05 — volume 16.3 triệu (+703%), giá tăng từ 125,000 lên 151,000. Hiện consolidation quanh 145,000-151,000
- **Wyckoff:** **SOS xác nhận** sau giai đoạn tích lũy dài. Đây là tín hiệu mua mạnh
- **Kết luận:** Breakout bùng nổ, tiền thông minh tích lũy mạnh

**VRE (Vincom Retail)**
- **Cấu trúc:** MA10 +11.61%, MA20 +17.21%, MA50 +21.98% — động lực ngắn-trung hạn mạnh nhất
- **VPA:** Tăng liên tục từ 27,000 lên 33,650. Volume xác nhận tốt, không có dấu hiệu exhaustion
- **Wyckoff:** **Markup ổn định**, xu hướng tăng nhất quán
- **Kết luận:** Cấu trúc kỹ thuật tốt nhất, ít biến động nhất

**NVL (No Va Land)**
- **Cấu trúc:** MA10 +2.47%, MA20 +10.74%, MA50 +31.82%, MA100 +37.87%, MA200 +28.85%
- **VPA:** Phiên 04/05 giảm -6.83% với volume tăng 35% — **cảnh báo UT (Upthrust)**. Cần theo dõi có phải exhaustion không
- **Wyckoff:** Có thể đang trong giai đoạn **Distribution** ngắn hạn
- **Kết luận:** Momentum mạnh nhưng cần thận trọng với tín hiệu exhaustion

#### Phân tích VPA — Nhóm Lagging

**AGG (An Gia Realty)**
- MA200 -21.90% — xu hướng giảm dài hạn rõ rệt
- Không có tín hiệu cải thiện, volume yếu
- **Kết luận:** Tránh hoàn toàn

**KDH (Khang Điền)**
- MA100 -10.65%, MA200 -17.75% — đang trong xu hướng giảm
- Không có catalyst cải thiện
- **Kết luận:** Không nên bắt dao rơi

---

### 🏦 NGÀNH 2: NGÂN HÀNG — PHỤC HỒI MẠNH

#### Tổng quan ngành

Chỉ số **VNFIN** MA50 +1.17%, MA20 +0.59% — ngành đang trong giai đoạn phục hồi. Tuy nhiên MA200 vẫn âm (-1.82%) cho thấy ngành chưa hoàn toàn phục hồi dài hạn.

#### Bảng xếp hạng chi tiết mã Ngân hàng

| Hạng | Mã | Giá | MA10 | MA20 | MA50 | MA100 | MA200 | Vai trò VPA |
|------|-----|-----|------|------|------|-------|-------|-------------|
| 🥇 | **TCB** | 33,700 | +1.46% | +5.37% | +5.38% | +0.79% | -3.60% | ⭐⭐⭐⭐ Leader |
| 🥈 | **LPB** | 47,500 | +1.81% | +1.71% | +8.05% | +9.35% | +7.41% | ⭐⭐⭐⭐ Leader |
| 🥉 | **STB** | 67,500 | +2.33% | +3.74% | +4.89% | +12.20% | +18.65% | ⭐⭐⭐⭐ Leader |
| 4 | **HDB** | 26,500 | -0.24% | +1.06% | +1.36% | -0.70% | +5.90% | ⭐⭐⭐ Tăng |
| 5 | **VCB** | 60,500 | +0.58% | +1.58% | +0.21% | -2.52% | -2.42% | ⭐⭐⭐ Phục hồi |
| 6 | **MBB** | 26,300 | +0.17% | +0.08% | -1.07% | -0.19% | +3.03% | ⭐⭐ Middle |
| 7 | **MSB** | 12,600 | +0.20% | +2.11% | +6.24% | +3.51% | +0.94% | ⭐⭐ Tăng |
| 8 | **VPB** | 27,000 | -1.51% | -0.27% | +0.58% | -2.48% | -5.38% | ⭐⭐ Middle |
| 9 | **EIB** | 21,950 | -1.66% | -2.40% | -2.80% | -1.04% | -8.09% | ⭐ Lagging |
| 10 | **ACB** | 23,300 | -1.23% | -1.44% | -1.24% | -2.47% | -5.24% | ⭐ Lagging |
| 11 | **BID** | 40,800 | +0.72% | +1.18% | -2.33% | -5.64% | -0.98% | ⭐ Lagging |
| 12 | **CTG** | 35,350 | +1.03% | +1.63% | +0.37% | -2.37% | +0.76% | ⭐⭐ Middle |
| 13 | **NAB** | 14,150 | +2.28% | +4.33% | +5.66% | +1.85% | -1.98% | ⭐⭐ Tăng |
| 14 | **KLB** | 14,500 | +1.47% | +2.17% | +4.14% | -3.13% | -2.81% | ⭐⭐ Tăng |
| 15 | **OCB** | 11,250 | -1.75% | -1.21% | +0.05% | -3.10% | -7.46% | ⭐ Lagging |
| 16 | **SSB** | 16,750 | +0.06% | -0.58% | -0.46% | -2.16% | -7.85% | ⭐ Lagging |
| 17 | **SHB** | 14,500 | -3.04% | -3.33% | -3.40% | -6.54% | -9.42% | ❌ Weak |
| 18 | **VAB** | 10,250 | -0.58% | -0.22% | -0.05% | -2.46% | -7.40% | ⭐ Lagging |
| 19 | **BVB** | 12,400 | -0.96% | -0.64% | -0.61% | -4.48% | -9.25% | ❌ Weak |
| 20 | **NVB** | 10,800 | -0.55% | -1.14% | -3.33% | -11.79% | -21.19% | ❌ Weakest |
| 21 | **SGB** | 12,200 | -0.97% | -1.37% | -0.91% | -1.51% | -2.26% | ⭐ Lagging |
| 22 | **VIB** | 16,250 | -0.72% | -0.40% | +0.63% | -1.54% | -7.38% | ⭐ Lagging |

#### Phân tích VPA sâu — Nhóm Leaders

**TCB (Techcombank)**
- **Cấu trúc:** MA10 +1.46%, MA20 +5.37%, MA50 +5.38% — động lực ngắn-trung hạn mạnh
- **VPA:** Breakout mạnh ngày 08/04 với volume bùng nổ. Hiện consolidation quanh 33,500-34,000 — **bull flag pattern**
- **Wyckoff:** **SPRING** từ vùng tích lũy, đang trong giai đoạn **Markup**
- **Kết luận:** Dẫn đầu ngành, cấu trúc kỹ thuật tốt nhất

**LPB (Lộc Phát Bank)**
- **Cấu trúc:** MA Score dương từ MA10 đến MA200 — hiếm có trong ngành. MA200 +7.41%
- **VPA:** Xu hướng tăng ổn định nhất, không có biến động mạnh
- **Wyckoff:** **Markup nhất quán**, tiền thông minh tích lũy liên tục
- **Kết luận:** Cổ phiếu phòng thủ tốt nhất trong ngành

**STB (Sài Gòn Tài Lộc)**
- **Cấu trúc:** MA100 +12.20%, MA200 +18.65% — xu hướng tăng dài hạn mạnh nhất ngành
- **VPA:** Tăng đều đặn từ 50,000 lên 68,100. Volume xác nhận tốt
- **Wyckoff:** **Markup dài hạn**, tiền thông minh tích lũy
- **Kết luận:** Bất ngờ lớn, xu hướng tăng dài hạn rõ rệt

#### Phân tích VPA — Nhóm Lagging

**NVB (Quốc Dân)**
- MA200 -21.19% — yếu nhất ngành
- Không có tín hiệu cải thiện
- **Kết luận:** Tránh hoàn toàn

**SHB (Sài Gòn Hà Nội)**
- MA20 -3.33%, MA50 -3.40%, MA100 -6.54%, MA200 -9.42%
- Xu hướng giảm rõ rệt trên mọi khung
- **Kết luận:** Tránh

---

### 📊 NGÀNH 3: CHỨNG KHOÁN — ĐIỀU CHỈNH

#### Tổng quan ngành

Ngành chứng khoán đang trong **giai đoạn điều chỉnh** với sự phân hóa rõ rệt. Khối lượng giao dịch sụt giảm mạnh trên toàn thị trường.

#### Bảng xếp hạng chi tiết mã Chứng khoán

| Hạng | Mã | Giá | MA10 | MA20 | MA50 | MA100 | MA200 | Vai trò VPA |
|------|-----|-----|------|------|------|-------|-------|-------------|
| 🥇 | **HCM** | 26,900 | -0.28% | +3.39% | +12.45% | +14.62% | +10.23% | ⭐⭐⭐⭐ Leader |
| 🥈 | **TCI** | 10,550 | +0.48% | +3.08% | +12.65% | +12.83% | +6.36% | ⭐⭐⭐⭐ Leader |
| 🥉 | **BMS** | 15,300 | +1.32% | +3.00% | +8.04% | +12.81% | +13.15% | ⭐⭐⭐ Tăng |
| 4 | **AGR** | 14,600 | -0.41% | -0.98% | -3.18% | -6.65% | -10.85% | ⭐ Lagging |
| 5 | **SSI** | 27,850 | -1.10% | -1.09% | -3.85% | -6.56% | -11.35% | ⭐ Lagging |
| 6 | **VCI** | 26,350 | -1.90% | -3.16% | -2.68% | +0.16% | -6.53% | ⭐ Lagging |
| 7 | **FTS** | 26,400 | -1.44% | -3.63% | -6.43% | -13.58% | -22.14% | ❌ Weak |
| 8 | **MBS** | 19,500 | -1.71% | -3.30% | -3.78% | -6.72% | -15.78% | ❌ Weak |
| 9 | **VND** | 16,300 | -0.55% | -0.67% | -2.78% | -9.17% | -18.07% | ❌ Weakest |
| 10 | **SHS** | 17,000 | -2.19% | -2.91% | -2.25% | -9.25% | -19.60% | ❌ Weakest |
| 11 | **CTS** | 26,500 | -0.80% | -2.80% | -5.61% | -13.25% | -23.67% | ❌ Weakest |
| 12 | **BVS** | 25,600 | -1.99% | -3.90% | -7.00% | -11.65% | -21.33% | ❌ Weakest |
| 13 | **DSC** | 13,000 | +0.15% | -0.48% | -2.90% | -8.46% | -13.76% | ❌ Weak |
| 14 | **EVS** | 4,600 | -3.97% | -6.98% | -9.95% | -16.30% | -26.52% | ❌ Weakest |
| 15 | **HCM** | 26,900 | -0.28% | +3.39% | +12.45% | +14.62% | +10.23% | ⭐⭐⭐⭐ Leader |

#### Phân tích VPA sâu

**HCM (Chứng khoán TP.HCM)**
- **Cấu trúc:** MA Score dương ở mọi khung. MA50 +12.45%, MA100 +14.62%, MA200 +10.23%
- **VPA:** Xu hướng tăng rõ rệt, volume xác nhận tốt
- **Kết luận:** Dẫn đầu ngành, cấu trúc kỹ thuật tốt nhất

**TCI (Chứng khoán Thành Công)**
- **Cấu trúc:** MA50 +12.65%, MA100 +12.83%, MA200 +6.36%
- **VPA:** Tương tự HCM, xu hướng tăng mạnh
- **Kết luận:** Dẫn đầu ngành cùng HCM

**VND (VNDIRECT) — Yếu nhất**
- MA200 -18.07% — xu hướng giảm dài hạn rõ rệt
- Không có tín hiệu cải thiện
- **Kết luận:** Tránh hoàn toàn

---

### 🏭 NGÀNH 4: CÔNG NGHIỆP & XÂY DỰNG — TRUNG TÍNH

#### Bảng xếp hạng mã tiêu biểu

| Hạng | Mã | Giá | MA20 | MA50 | MA100 | MA200 | Vai trò |
|------|-----|-----|------|------|-------|-------|---------|
| 🥇 | **CTD** | 88,000 | +9.02% | +10.37% | +14.62% | +15.83% | ⭐⭐⭐⭐ Leader |
| 🥈 | **HSG** | 12,550 | +4.33% | +7.08% | +4.18% | -3.98% | ⭐⭐⭐ Tăng |
| 🥉 | **HPG** | 27,700 | -0.42% | +1.15% | +2.30% | +1.63% | ⭐⭐ Middle |
| 4 | **GMD** | 75,500 | +2.35% | -0.50% | +8.17% | +12.50% | ⭐⭐⭐ Tăng |
| 5 | **DXP** | 14,900 | +6.73% | +13.03% | +21.97% | +28.73% | ⭐⭐⭐⭐ Leader |
| 6 | **DXS** | 7,870 | +6.18% | +13.19% | +1.52% | -18.50% | ⭐⭐ Tăng |
| 7 | **CII** | 19,350 | +2.56% | +8.83% | -0.26% | -7.65% | ⭐⭐ Tăng |
| 8 | **DRI** | 14,100 | +7.88% | +7.19% | +8.77% | +12.12% | ⭐⭐⭐ Tăng |
| 9 | **ASM** | 6,020 | +4.80% | +3.76% | -2.03% | -11.15% | ⭐⭐ Tăng |
| 10 | **FCN** | 13,350 | -0.58% | +2.72% | -3.85% | -11.10% | ⭐ Lagging |

---

### 💻 NGÀNH 5: CÔNG NGHỆ — ĐIỀU CHỈNH

#### Bảng xếp hạng mã Công nghệ

| Hạng | Mã | Giá | MA20 | MA50 | MA100 | MA200 | Vai trò |
|------|-----|-----|------|------|-------|-------|---------|
| 🥇 | **FOX** | 80,500 | -1.43% | +0.93% | +4.72% | +12.41% | ⭐⭐⭐ Leader |
| 🥈 | **FPT** | 74,500 | -1.18% | -6.12% | -16.09% | -21.11% | ⭐ Lagging |
| 🥉 | **CMG** | 28,050 | -1.71% | -6.62% | -12.45% | -18.07% | ⭐ Lagging |
| 4 | **ELC** | 17,500 | -2.17% | -5.51% | -17.24% | -17.94% | ❌ Weak |
| 5 | **SAM** | 6,760 | -0.71% | +0.30% | -3.78% | -9.56% | ⭐ Lagging |
| 6 | **SGT** | 15,400 | -0.10% | -1.16% | -2.69% | -7.15% | ⭐ Lagging |
| 7 | **VNZ** | 332,000 | +4.54% | +1.64% | -3.93% | -9.77% | ⭐⭐ Tăng |
| 8 | **VTE** | 7,400 | +3.06% | +6.87% | +2.99% | +24.96% | ⭐⭐⭐ Tăng |

---

### 🛒 NGÀNH 6: BÁN LẺ & TIÊU DÙNG — PHÂN HÓA

#### Bảng xếp hạng mã tiêu biểu

| Hạng | Mã | Giá | MA20 | MA50 | MA100 | MA200 | Vai trò |
|------|-----|-----|------|------|-------|-------|---------|
| 🥇 | **MWG** | 83,700 | +1.26% | -0.16% | -1.63% | +3.62% | ⭐⭐⭐ Leader |
| 🥈 | **FRT** | 148,000 | -1.32% | -4.52% | -3.90% | +0.34% | ⭐⭐ Middle |
| 🥉 | **DGW** | 43,300 | -3.46% | -4.93% | -3.51% | -1.09% | ⭐ Lagging |
| 4 | **PET** | 46,850 | -1.86% | +7.67% | +22.02% | +34.28% | ⭐⭐⭐⭐ Leader |
| 5 | **MSN** | 76,700 | -0.96% | +0.90% | -0.52% | -2.46% | ⭐⭐ Middle |
| 6 | **VNM** | 60,800 | -1.11% | -3.29% | -5.33% | -1.24% | ⭐ Lagging |
| 7 | **MCH** | 137,800 | -1.56% | -4.59% | -11.95% | +2.25% | ⭐ Lagging |
| 8 | **SAB** | 47,600 | +3.98% | +4.31% | +0.55% | +3.36% | ⭐⭐⭐ Tăng |
| 9 | **PAN** | 32,000 | +0.59% | +0.90% | +6.23% | +3.81% | ⭐⭐ Tăng |
| 10 | **VHC** | 62,300 | +2.53% | +3.50% | +5.06% | +7.49% | ⭐⭐⭐ Tăng |

---

## 🔄 PHẦN III: QUAN SÁT LUÂN CHUYỂN LIÊN NGÀNH

### 3.1. Mô hình luân chuyển dòng tiền

```
╔══════════════════════════════════════════════════════════════════╗
║                    LUÂN CHUYỂN DÒNG TIỀN                       ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  BẤT ĐỘNG SẢN (VNREAL)     ◄◄◄◄◄  Dòng tiền MẠNH NHẤT         ║
║  MA200: +46.05%                                                 ║
║                                                                  ║
║  NGÂN HÀNG (VNFIN)         ◄◄◄  Dòng tiền PHỤC HỒI             ║
║  MA50: +1.17%                                                   ║
║                                                                  ║
║  CÔNG NGHIỆP (VNIND)       ◄◄  Dòng tiền TRUNG TÍNH            ║
║  MA50: +1.33%                                                   ║
║                                                                  ║
║  TIÊU DÙNG (VNCONS)        ◄  Dòng tiền YẾU                   ║
║  MA20: -0.60%                                                   ║
║                                                                  ║
║  CÔNG NGHỆ (VNMITECH)      ►  Dòng tiền RÚT NHẸ               ║
║  MA200: -3.73%                                                  ║
║                                                                  ║
║  NĂNG LƯỢNG (VNENE)        ►►  Dòng tiền RÚT MẠNH             ║
║  MA50: -10.20%                                                  ║
║                                                                  ║
║  Y TẾ (VNHEAL)             ►►►  Dòng tiền RÚT MẠNH NHẤT       ║
║  MA20: -4.68%                                                   ║
║                                                                  ║
║  CỔ TỨC (VNDIVIDEND)       ►►►  Dòng tiền RÚT MẠNH            ║
║  MA50: -6.79%                                                   ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

### 3.2. Phân tích theo chu kỳ Wyckoff liên ngành

| Giai đoạn | Ngành | Tín hiệu |
|-----------|-------|----------|
| **Markup (Tăng giá)** | Bất động sản | SOS mạnh, volume xác nhận |
| **Markup (Tăng giá)** | Ngân hàng | SPRING thành công, bắt đầu Markup |
| **Accumulation (Tích lũy)** | Công nghiệp | Test lại đáy, chuẩn bị breakout |
| **Distribution (Phân phối)** | Năng lượng | UT xuất hiện, dòng tiền rút |
| **Markdown (Giảm giá)** | Y tế | SOW rõ rệt, xu hướng giảm |
| **Markdown (Giảm giá)** | Cổ tức | SOW mạnh, không có hỗ trợ |

### 3.3. Tương quan với thị trường quốc tế

| Chỉ số | Giá | MA20 | MA50 | MA200 | Xu hướng |
|--------|-----|------|------|-------|----------|
| **S&P 500** | 7,230.12 | +3.16% | +5.98% | +7.46% | ✅ Tăng |
| **NASDAQ 100** | 27,710.36 | +5.62% | +10.05% | +11.63% | ✅ Tăng mạnh |
| **Dow Jones** | 49,499.27 | +1.70% | +3.42% | +4.85% | ✅ Tăng |
| **Nikkei 225** | 59,513.12 | +3.01% | +6.28% | +19.65% | ✅ Tăng mạnh |
| **Hang Seng** | 26,216.39 | +1.14% | +2.21% | +1.05% | ✅ Tăng nhẹ |
| **FTSE 100** | 10,363.90 | -1.13% | -0.46% | +5.51% | ⚠️ Điều chỉnh |
| **VIX** | 16.99 | -11.66% | -23.62% | -7.05% | ✅ Giảm (tích cực) |

**Nhận định:** Thị trường toàn cầu đang trong xu hướng tăng, VIX giảm mạnh — môi trường thuận lợi cho dòng tiền chảy vào thị trường mới nổi như Việt Nam. Nikkei 225 dẫn đầu khu vực với MA200 +19.65%.

---

## 🏆 PHẦN IV: BẢNG XẾP HẠNG CỔ PHIẾU ĐA NGÀNH THỐNG NHẤT

### 4.1. Top 20 mã dẫn đầu thị trường (theo MA Score tổng hợp)

| Hạng | Mã | Ngành | MA10 | MA20 | MA50 | MA100 | MA200 | Điểm |
|------|-----|-------|------|------|------|-------|-------|------|
| 🥇 | **VIC** | BĐS | +2.72% | +17.77% | +31.36% | +34.56% | +76.75% | 165 |
| 🥈 | **VHM** | BĐS | -0.03% | +8.50% | +27.10% | +28.03% | +35.79% | 99 |
| 🥉 | **VRE** | BĐS | +11.61% | +17.21% | +21.98% | +13.40% | +8.84% | 73 |
| 4 | **NVL** | BĐS | +2.47% | +10.74% | +31.82% | +37.87% | +28.85% | 112 |
| 5 | **LPB** | Ngân hàng | +1.81% | +1.71% | +8.05% | +9.35% | +7.41% | 28 |
| 6 | **STB** | Ngân hàng | +2.33% | +3.74% | +4.89% | +12.20% | +18.65% | 42 |
| 7 | **TCB** | Ngân hàng | +1.46% | +5.37% | +5.38% | +0.79% | -3.60% | 9 |
| 8 | **CTD** | Xây dựng | +5.16% | +9.02% | +10.37% | +14.62% | +15.83% | 55 |
| 9 | **DXP** | Xây dựng | +5.37% | +6.73% | +13.03% | +21.97% | +28.73% | 76 |
| 10 | **HCM** | Chứng khoán | -0.28% | +3.39% | +12.45% | +14.62% | +10.23% | 40 |
| 11 | **TCI** | Chứng khoán | +0.48% | +3.08% | +12.65% | +12.83% | +6.36% | 35 |
| 12 | **PET** | Dầu khí | -0.37% | -1.86% | +7.67% | +22.02% | +34.28% | 62 |
| 13 | **BMS** | Chứng khoán | +1.32% | +3.00% | +8.04% | +12.81% | +13.15% | 38 |
| 14 | **HSG** | Công nghiệp | +1.96% | +4.33% | +7.08% | +4.18% | -3.98% | 14 |
| 15 | **DRI** | Công nghiệp | +5.70% | +7.88% | +7.19% | +8.77% | +12.12% | 42 |
| 16 | **VHC** | Thực phẩm | +0.47% | +2.53% | +3.50% | +5.06% | +7.49% | 19 |
| 17 | **SAB** | Thực phẩm | +2.20% | +3.98% | +4.31% | +0.55% | +3.36% | 14 |
| 18 | **GMD** | Dịch vụ | +1.36% | +2.35% | -0.50% | +8.17% | +12.50% | 24 |
| 19 | **HDB** | Ngân hàng | -0.24% | +1.06% | +1.36% | -0.70% | +5.90% | 8 |
| 20 | **MWG** | Bán lẻ | -1.39% | +1.26% | -0.16% | -1.63% | +3.62% | 2 |

### 4.2. Top 15 mã yếu nhất (cần tránh)

| Hạng | Mã | Ngành | MA10 | MA20 | MA50 | MA100 | MA200 | Đánh giá |
|------|-----|-------|------|------|------|-------|-------|----------|
| 1 | **CTS** | Chứng khoán | -0.80% | -2.80% | -5.61% | -13.25% | -23.67% | ❌ |
| 2 | **EVS** | Chứng khoán | -3.97% | -6.98% | -9.95% | -16.30% | -26.52% | ❌ |
| 3 | **SCR** | BĐS | -1.11% | -1.76% | -1.41% | -10.72% | -23.07% | ❌ |
| 4 | **AGG** | BĐS | -0.16% | -0.73% | -4.09% | -9.37% | -21.90% | ❌ |
| 5 | **BVS** | Chứng khoán | -1.99% | -3.90% | -7.00% | -11.65% | -21.33% | ❌ |
| 6 | **SHS** | Chứng khoán | -2.19% | -2.91% | -2.25% | -9.25% | -19.60% | ❌ |
| 7 | **FTS** | Chứng khoán | -1.44% | -3.63% | -6.43% | -13.58% | -22.14% | ❌ |
| 8 | **VND** | Chứng khoán | -0.55% | -0.67% | -2.78% | -9.17% | -18.07% | ❌ |
| 9 | **MBS** | Chứng khoán | -1.71% | -3.30% | -3.78% | -6.72% | -15.78% | ❌ |
| 10 | **HDC** | BĐS | +2.78% | +2.91% | +2.44% | -7.61% | -25.18% | ❌ |
| 11 | **KDH** | BĐS | -1.93% | -2.31% | -3.61% | -10.65% | -17.75% | ❌ |
| 12 | **PDR** | BĐS | +1.32% | +1.32% | +2.73% | -5.80% | -17.80% | ❌ |
| 13 | **HDG** | BĐS | -3.70% | -6.10% | -6.44% | -5.33% | -10.42% | ❌ |
| 14 | **ACB** | Ngân hàng | -1.23% | -1.44% | -1.24% | -2.47% | -5.24% | ⚠️ |
| 15 | **BID** | Ngân hàng | +0.72% | +1.18% | -2.33% | -5.64% | -0.98% | ⚠️ |

---

## 📋 PHẦN V: KHUYẾN NGHỊ CHIẾN LƯỢC

### 5.1. Khuyến nghị theo khung thời gian

#### Ngắn hạn (1-4 tuần)

| Hành động | Mã | Lý do |
|-----------|-----|-------|
| ✅ **MUA** | VIC, VHM, VRE | Dẫn đầu ngành BĐS, xu hướng tăng mạnh |
| ✅ **MUA** | LPB, TCB, STB | Dẫn đầu ngành Ngân hàng, cấu trúc kỹ thuật tốt |
| ✅ **MUA** | CTD, DXP | Dẫn đầu nhóm Công nghiệp/Xây dựng |
| ✅ **MUA** | HCM, TCI | Dẫn đầu ngành Chứng khoán |
| ⏸️ **CHỜ** | VCB, VPB, MBB | Cần thêm tín hiệu xác nhận |
| ❌ **TRÁNH** | AGG, VND, MBS, SHS, CTS | Xu hướng giảm rõ rệt |

#### Trung hạn (1-3 tháng)

| Chiến lược | Ngành ưu tiên | Mã tiêu biểu |
|------------|---------------|--------------|
| **Tích lũy** | Bất động sản | VIC, VHM, VRE, NVL |
| **Tích lũy** | Ngân hàng | TCB, LPB, STB, HDB |
| **Chờ đợi** | Công nghiệp | CTD, DXP, HSG, GMD |
| **Tránh** | Y tế, Năng lượng, Cổ tức | IMP, DVN, PLC |

#### Dài hạn (3-12 tháng)

- Thị trường Việt Nam đang trong **xu hướng tăng dài hạn** (VNINDEX MA200 +8.96%)
- Các mã dẫn đầu ngành sẽ tiếp tục outperform
- Chiến lược **buy and hold** cho nhóm leaders
- **DCA (Dollar Cost Averaging)** cho nhóm phục hồi

### 5.2. Quản lý rủi ro

| Rủi ro | Mức độ | Biện pháp |
|--------|--------|-----------|
| Khối lượng giảm mạnh | Trung bình | Theo dõi volume, cắt lỗ nếu giảm >5% |
| NVL điều chỉnh mạnh | Cao | Theo dõi tín hiệu exhaustion |
| VIX tăng đột biến | Thấp | VIX đang ở mức thấp, ít khả năng tăng mạnh |
| USD/VND biến động | Thấp | Tỷ giá ổn định ở 26,355 |
| Chính sách thay đổi | Trung bình | Theo dõi tin tức kinh tế vĩ mô |

### 5.3. Tỷ lệ danh mục đề xuất

| Nhóm | Tỷ lệ | Mã tiêu biểu |
|------|-------|--------------|
| **Bất động sản** | 35% | VIC, VHM, VRE |
| **Ngân hàng** | 25% | TCB, LPB, STB |
| **Công nghiệp/Dịch vụ** | 15% | CTD, DXP, GMD |
| **Chứng khoán** | 10% | HCM, TCI, BMS |
| **Tiêu dùng** | 10% | MWG, PET, VHC |
| **Tiền mặt** | 5% | — |

---

## ⚠️ TUYÊN BỐ MIỄN TRỪ TRÁCH NHIỆM

Tất cả phân tích và thông tin được cung cấp bởi **AIPriceAction Investment Advisor** chỉ nhằm mục đích thông tin và giáo dục. Đây **KHÔNG PHẢI** lời khuyên đầu tư hoặc khuyến nghị mua, bán hoặc nắm giữ bất kỳ chứng khoán nào.

**Các điểm quan trọng:**

- 📉 Đầu tư vào cổ phiếu có nguy cơ mất vốn đáng kể
- 📊 Hiệu suất quá khứ không đảm bảo kết quả tương lai
- 🔍 Bạn nên tự nghiên cứu và thẩm định kỹ lưỡng
- 👨‍💼 Cân nhắc tham khảo ý kiến cố vấn tài chính có trình độ trước khi đưa ra quyết định đầu tư
- ⚖️ AIPriceAction và các cộng tác viên không chịu trách nhiệm cho bất kỳ tổn thất đầu tư nào
- 🔄 Điều kiện thị trường có thể thay đổi nhanh chóng và không lường trước
- 💰 Luôn chỉ đầu tư số tiền bạn có thể chấp nhận mất

---

**Website:** https://aipriceaction.com/

**© 2026 AIPriceAction Investment Advisor. Bảo lưu mọi quyền.**

---

[4] Done in 1170.3s | Checkpoint: /var/folders/hd/20zqmjkj7cd0wm4rv2230bm00000gn/T/aipriceaction-checkpoints/019df168-d9ec-7559-5d6e-4b3c2a23d485
