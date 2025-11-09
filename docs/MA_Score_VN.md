# MA Score (Điểm Trung Bình Động)

## Giải Thiệu Về MA Score

Mặc dù trong giới tài chính, bạn có thể nghe thấy các khái niệm tương tự được gọi là "MA Deviation" (Độ lệch trung bình động), MA Score của chúng tôi được thiết kế để cung cấp cho bạn một số liệu vượt trội, rõ ràng và định hướng hành động để phân tích động lực thị trường.

### MA Score là gì?

MA Score đo sự khác biệt phần trăm giữa giá đóng cửa hiện tại và giá trung bình của nó trong một khoảng thời gian cụ thể (ví dụ: 10, 50 hoặc 200 ngày gần nhất). Nó giúp xác định khi nào giá của một tài sản bị "căng quá mức" (overstretched) và có thể sắp sửa có sự điều chỉnh hoặc đảo chiều tiềm năng.

### Tính ưu việt của MA Score: So sánh & Sắp xếp mạnh mẽ

MA Score không chỉ là một độ lệch; nó là một số liệu chuẩn hóa, mạnh mẽ, mang lại sự dễ dàng vô song trong phân tích trên toàn bộ thị trường.

#### 1. Tiêu chuẩn hóa để so sánh dễ dàng

MA Score sử dụng phép tính phần trăm đơn giản, phổ quát:

MA Score = ((close - ma) / ma) × 100

Phép tính này là chìa khóa cho sức mạnh của nó. Điểm số +5.0 về cơ bản có cùng ý nghĩa đối với một cổ phiếu giá 1.000 VNĐ cũng như đối với một cổ phiếu giá 100.000 VNĐ: giá chính xác cao hơn 5% so với đường trung bình động của nó.

Sự tiêu chuẩn hóa này loại bỏ nhu cầu giải thích sự khác biệt giá tuyệt đối, cho phép bạn ngay lập tức so sánh động lượng và trạng thái "căng quá mức" của các loại chứng khoán hoàn toàn khác nhau.

#### 2. Công cụ tối ưu để sàng lọc và sắp xếp

Bởi vì MA Score là một tỷ lệ phần trăm trực tiếp, nó trở thành một công cụ sàng lọc và sắp xếp cực kỳ mạnh mẽ trong ứng dụng của bạn:

- **Tìm kiếm mã "Quá mua nhiều nhất"**: Bạn có thể ngay lập tức sắp xếp toàn bộ danh sách thị trường theo MA10 Score theo thứ tự giảm dần để tìm các cổ phiếu đang chịu áp lực tăng mạnh nhất, tức thời nhất và có nhiều khả năng sắp có sự điều chỉnh giảm.

- **Xác định cơ hội "Quá bán"**: Sắp xếp theo MA50 Score theo thứ tự tăng dần sẽ nhanh chóng làm nổi bật các cổ phiếu bị bán quá mức so với xu hướng trung hạn của chúng, chỉ ra các ứng cử viên tiềm năng để bật lên.

- **Lọc theo sự đồng thuận xu hướng**: Bạn có thể dễ dàng lọc các tài sản mà MA10 Score, MA50 Score, và MA200 Score đều dương, cho thấy sự đồng thuận mạnh mẽ trên tất cả các khung thời gian.

#### 3. Tín hiệu quá mua/quá bán có thể hành động

Điểm số hoạt động như một chỉ báo "dây cao su" có độ nhạy cao:

- **Điểm dương cao (ví dụ: +5 trở lên)**: Giá có khả năng quá mua và có thể sớm bật xuống gần đường trung bình động.
- **Điểm âm cao (ví dụ: -5 trở xuống)**: Giá có khả năng quá bán và có thể sớm bật lên gần đường trung bình động.

## Triển Khai trong aipriceaction

### Các Kỳ Hạn MA Có Sẵn

Hệ thống tính toán MA scores cho 5 kỳ hạn:

- **MA10**: Trung bình động 10 ngày (xu hướng ngắn hạn)
- **MA20**: Trung bình động 20 ngày (xu hướng trung hạn)
- **MA50**: Trung bình động 50 ngày (xu hướng trung gian)
- **MA100**: Trung bình động 100 ngày (xu hướng dài hạn)
- **MA200**: Trung bình động 200 ngày (xu hướng rất dài hạn)

### Định Dạng Dữ Liệu

MA Score được tích hợp vào định dạng CSV 19 cột của hệ thống:

```
ticker,time,open,high,low,close,volume,
ma10,ma20,ma50,ma100,ma200,
ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,
close_changed,volume_changed
```

**Cột 13-17**: MA Scores cho từng kỳ hạn (tính bằng phần trăm)

### Xử Lý Định Dạng Giá

**Quan trọng**: Hệ thống xử lý khác nhau cho cổ phiếu và chỉ số:

**Cổ phiếu (VCB, FPT, v.v.):**
- Giá lưu trữ: 23.2 (chia cho 1000 trong CSV)
- Tính toán nội bộ: 23200.0 (định dạng VNĐ đầy đủ)
- MA scores: Luôn là phần trăm (không phụ thuộc định dạng)

**Chỉ số thị trường (VNINDEX, VN30):**
- Giá lưu trữ: 1250.5 (giá trị thực)
- Tính toán nội bộ: 1250.5 (không chuyển đổi)
- MA scores: Luôn là phần trăm (không phụ thuộc định dạng)

### API Endpoints

MA Score có sẵn qua các endpoint sau:

- **GET /tickers**: Trả về MA scores cho cổ phiếu cụ thể
- **GET /analysis/top-performers**: Sắp xếp theo MA scores
- **GET /analysis/ma-scores-by-sector**: Phân tích MA theo ngành

### Ví dụ Response API

```json
{
  "time": "2025-11-10",
  "close": 60100,
  "ma10": 59840,
  "ma10_score": 0.4345,
  "ma20": 61160,
  "ma20_score": -1.7332,
  "ma50": 63017.8,
  "ma50_score": -4.6301
}
```

## Sử Dụng Thực Tế

### Hướng Dẫn Diễn Giải

- **Điểm dương**: Giá trên MA (động lượng tăng giá)
- **Điểm âm**: Giá dưới MA (động lượng giảm giá)
- **Độ lớn**: Sức mạnh của xu hướng
  - > 5%: Động lượng mạnh
  - 2-5%: Động lượng vừa phải
  - < 2%: Động lượng yếu

### Trường Hợp Sử Dụng Phổ Biến

1. **Động lượng ngắn hạn**: MA10 scores cho giao dịch trong ngày
2. **Xu hướng trung hạn**: MA20 scores cho swing trading
3. **Phân tích dài hạn**: MA50/MA200 scores cho quyết định đầu tư
4. **Phân tích ngành**: MA scores trung bình theo nhóm ngành
5. **Bộ lọc**: Cổ phiếu trên/dưới các ngưỡng cụ thể

### Ví dụ Code (SDK)

```typescript
// Sắp xếp theo MA20 score
const topMA20 = await client.getTopPerformers({
  sort_by: SortMetric.MA20Score,
  direction: SortDirection.Descending
});

// Phân tích ngành với ngưỡng MA50
const sectorMA50 = await client.getMAScoresBySector({
  ma_period: MAPeriod.MA50,
  min_score: 2.0,
  above_threshold_only: true
});
```

## Tính Năng Kỹ Thuật

### Quy Tắc Tính Toán

- **Bảo vệ chia zero**: Nếu MA = 0, score = 0.0
- **Dữ liệu không đủ**: Score = None/cho đến khi có đủ điểm dữ liệu
- **Độ chính xác**: Scores làm tròn đến 4 chữ số thập phân
- **Chỉ báo thay đổi**: Tính từ dòng trước (không có dữ liệu dòng đầu tiên)

### Yêu Cầu Dữ Liệu

- **MA10**: Cần 10+ bản ghi
- **MA50**: Cần 50+ bản ghi
- **MA200**: Cần 200+ bản ghi
- **Chỉ báo thay đổi**: Cần bản ghi trước đó

### Tối Ưu Hiệu Suất

- **Tăng cường một lần**: Tất cả chỉ báo được tính trong bộ nhớ
- **Không I/O đĩa dư thừa**: Ghi một lần, tăng cường trong bộ nhớ
- **Khóa file an toàn**: Truy cập đồng thời an toàn trong quá trình sync nền

---

Bằng cách tận dụng MA Score, bạn có được một công cụ mạnh mẽ, trực quan, chuyển hành động giá phức tạp thành các điểm dữ liệu đơn giản, có thể so sánh và sắp xếp, giúp bạn đưa ra quyết định giao dịch nhanh hơn, thông minh hơn.