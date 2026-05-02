SINGLE_TEMPLATES = {
    "en": [
        {
            "title": "Trading Opportunity",
            "snippet": "Identify opportunities, supply-demand analysis, position sizing roadmap, and risk management",
            "question": (
                "1. Identify actionable trading opportunities for {ticker} based on current data and overall market context.\n\n"
                "2. Identify 'Smart Money' behavior: Specify confirmation signals (No Supply, Test for Supply, Spring, Upthrust, or SOS) at key price zones, along with an assessment of the asymmetry between Effort (Volume) and Result (Price).\n\n"
                "IMPORTANT: If the market is currently open, the Volume of the latest bar must be considered incomplete. In this case, NEVER assertively conclude 'Supply Exhaustion'. Instead, note that the signal is still forming and needs confirmation after the ATC session closes.\n\n"
                "3. Establish a practical deployment roadmap: Clearly differentiate between exploratory position sizing (at low Volume zones) and scaled-up positions (upon Effort-Result confirmation), with specific Stop Loss (SL) levels based on Wyckoff structure violations.\n\n"
                "4. Consider the risk-reward ratio and key risks to monitor (Buying Climax, Upthrust, or VNINDEX-related risks)."
            ),
        },
        {
            "title": "News & Events Research",
            "snippet": "Detect extreme moves, research causes, combine with VPA/Wyckoff analysis",
            "question": (
                "1. Detect extreme moves: Check if {ticker} changed more than ±6.7% or Volume exceeded >150% of the 20-period average.\n\n"
                '2. CRITICAL — Perform a real internet search NOW: If significant moves are detected, you MUST use your web search tool RIGHT NOW to search for recent news about {ticker}. Search queries must include the ticker name, date, and keywords like "earnings", "financial report", or "corporate event". DO NOT generate or hallucinate news — only report facts from actual search results. Cite the source URL for each piece of information. If your search tool returns no results, say so explicitly.\n\n'
                '3. VPA & Wyckoff analysis: Combine ONLY verified search findings with price/volume data. Is this Effort or Result? Is the news being used to "rationalize" an Accumulation or Distribution process?\n\n'
                "4. Action & risk management: Based on verified news and technicals, identify supply exhaustion zones (quantified), entry/exit levels, and Stop Loss."
            ),
        },
        {
            "title": "Price Action & Volume",
            "snippet": "Analyze price-volume relationship, identify smart money footprints, supply/demand zones, and actionable entry/exit levels",
            "question": (
                "1. Read the price-volume narrative for {ticker}: Identify the current trend phase (Accumulation, Markup, Distribution, or Markdown), and assess whether Volume confirms or diverges from price movement (e.g., rising price on declining Volume = potential weakness; falling price on declining Volume = supply drying up).\n\n"
                "2. Detect smart money footprints: Quantify Volume anomalies relative to MA20 (Volume >150% = high activity, Volume <50% = quiet/absorption). Look for Effort vs Result divergences — large Volume (Effort) with small price movement (no Result), indicating absorption by the opposite side.\n\n"
                "3. Map key supply and demand zones: Identify price levels where previous high Volume occurred (potential supply ceilings) and where low Volume support was established (demand floors). Flag any Test for Supply (No Supply Bar) or Signs of Strength (SOS) candlestick patterns at these zones.\n\n"
                "4. Synthesize into actionable plan: Based on the VPA analysis, identify specific entry zones (with Volume confirmation criteria), Stop Loss levels (below demand zones or Wyckoff structure lows), and realistic price targets. State whether the current setup favors buyers or sellers and what would invalidate the thesis."
            ),
        },
        {
            "title": "MA Momentum & Trend",
            "snippet": "Assess MA alignment and momentum, detect crossovers with volume confirmation, and identify trend continuation/reversal signals",
            "question": (
                "1. Assess MA alignment and momentum profile for {ticker}: Report the MA Score for each period (MA10, MA20, MA50, MA100, MA200). Determine if MAs are stacked bullishly (shorter above longer) or bearishly (shorter below longer), and how wide the spread is — a widening spread signals accelerating momentum.\n\n"
                "2. Detect MA crossover signals with Volume confirmation: Identify any recent or impending MA crossovers (e.g., MA10 crossing above MA20 = short-term bullish). For each crossover, check whether Volume was above MA20 at the time — crossovers with high Volume are far more reliable than those on low Volume (which often fail and become whipsaws).\n\n"
                "3. Interpret MA scores through a Wyckoff lens: Align MA Score behavior with Wyckoff phases. For example, MA Score turning positive after an extended negative period near support could indicate Accumulation Phase C-D (Sign of Strength). Conversely, MA Score diverging negatively while price remains near highs could signal Distribution (Sign of Weakness before an Upthrust).\n\n"
                "4. Actionable MA-based trading plan: Based on the MA analysis, identify whether {ticker} is in a trending or mean-reverting regime. Provide specific entry triggers (e.g., buy on pullback to MA20 with Volume >MA20), Stop Loss levels (e.g., below MA50), and the MA structure violation that would invalidate the current trend."
            ),
        },
        {
            "title": "Wyckoff Method Analysis",
            "snippet": "Identify Wyckoff phases, key events (Spring/Upthrust/SOS), horizontal price targets, and Effort-Result volume confirmation",
            "question": (
                '1. Determine the current Wyckoff phase for {ticker}: Classify into Accumulation (A through E), Markup, Distribution (A through E), or Markdown. Support your classification with specific evidence from price and volume data (e.g., "Phase C — selling climax followed by increased Volume on up-days indicates potential Spring").\n\n'
                "2. Identify key Wyckoff events and structures: Scan for Springs (false breakdowns below support with quick reversal and low Volume on the breakdown bar), Upthrusts (false breakouts above resistance), Signs of Strength (SOS — wide-range up-bars on increasing Volume), Signs of Weakness (SOW — wide-range down-bars on increasing Volume), Last Point of Support (LPS), and Last Point of Supply (LPSY). Mark each event with the date.\n\n"
                "3. Calculate price targets and measure the Cause: Apply Wyckoff's horizontal counting method — measure the width of the Accumulation or Distribution trading range (the Cause) and project it upward (from breakout) or downward (from breakdown) to estimate the Effect (price target). Check if Volume at key levels supports the projected move.\n\n"
                "4. Confirm with Effort vs Result and provide action plan: Analyze whether Volume (Effort) is producing proportional price movement (Result). A large-effort/small-result divergence at resistance warns of Distribution; the same at support warns of Accumulation absorption. Provide a concrete trading plan with entry zone, Stop Loss (below Spring low or above Upthrust high), position sizing guidance, and the specific price action that would invalidate the Wyckoff thesis."
            ),
        },
        {
            "title": "Bob Volman Price Action",
            "snippet": "Identify dominant trend, micro pullback entries, breakout/fading setups with Volume confirmation, and risk-managed exit levels",
            "question": (
                "1. Establish the dominant trend and market structure for {ticker}: Define the current trend using swing highs and lows (Higher Highs + Higher Lows = uptrend; Lower Highs + Lower Lows = downtrend). Identify the most recent Break of Structure (BOS) or Change of Character (CHoCH) to confirm whether the trend is intact or reversing.\n\n"
                "2. Identify Volman-style entry setups: Scan for micro pullback setups — a pullback of 3+ consecutive bars against the trend followed by a strong reversal bar or breakout candle. For each setup, specify the exact entry price and assess whether Volume at the pullback zone shows diminishing selling (in uptrend) or diminishing buying (in downtrend), indicating supply/demand exhaustion.\n\n"
                "3. Evaluate breakout and fading setups: Identify breakout setups where price breaks a significant swing level on strong momentum (wide-range bar with Volume >MA20). Separately, check for fading setups at key supply/demand zones where price action shows rejection patterns (pin bars, long wicks, engulfing candles) — these are counter-trend setups requiring tight Stop Losses.\n\n"
                "4. Define the complete trade plan: For the highest-probability setup identified, provide exact entry price, Stop Loss (placed beyond the setup's invalidation level), and take-profit targets (based on swing structure or minimum 2:1 risk-reward). State the Volume profile that would confirm the trade is working and the Volume/price action that signals early exit."
            ),
        },
    ],
    "vn": [
        {
            "title": "Cơ Hội Giao Dịch",
            "snippet": "Nhận diện cơ hội giao dịch, phân tích cung-cầu, lộ trình giải ngân và quản trị rủi ro",
            "question": (
                "1. Xác định các cơ hội giao dịch có thể hành động cho {ticker} dựa trên dữ liệu hiện tại và bối cảnh thị trường chung.\n\n"
                "2. Nhận diện hành vi 'Smart Money': Chỉ rõ các tín hiệu xác nhận (No Supply, Test for Supply, Spring, Upthrust, hay SOS) tại các vùng giá then chốt, kèm theo đánh giá sự bất đối xứng giữa Nỗ lực (Volume) và Kết quả (Giá).\n\n"
                "LƯU Ð QUAN TRỜNG: Nếu có thông báo 'Thị trường đang mở cửa', phải coi dữ liệu Volume của bản ghi cuối cùng là chưa hoàn tất. Trong trường hợp này, tuyệt đối không kết luận 'Kiệt cung' một cách khẳng định. Thay vào đó, phải đưa ra ghi chú rằng đây là tín hiệu đang hình thành và cần được xác nhận lại sau khi đóng phiên ATC.\n\n"
                "3. Thiết lập lộ trình giải ngân thực chiến: Chia rõ tỷ lệ vị thế thăm dò (tại vùng Vol thấp) và vị thế gia tăng (khi có xác nhận Nỗ lực-Kết quả), kèm theo điểm Stop Loss (SL) cụ thể dựa trên vi phạm cấu trúc Wyckoff.\n\n"
                "4. Cân nhắc tỷ lệ rủi ro-phần thưởng và các rủi ro chính cần theo dõi (Buying Climax, Upthrust, hay rủi ro từ VNINDEX)."
            ),
        },
        {
            "title": "Tìm Kiếm Tin Tức & Sự Kiện",
            "snippet": "Phát hiện biến động giá mạnh, tra cứu nguyên nhân, kết hợp phân tích VPA & Wyckoff",
            "question": (
                "1. Kiểm tra biến động cực đại: Xác định xem {ticker} có thay đổi giá quá ±6.7% hoặc khối lượng (Volume) vượt >150% trung bình 20 phiên không.\n\n"
                '2. Bắt BUỘC — Thực hiện tìm kiếm internet thực tế NGAY LẬP TỨC: Nếu phát hiện biến động lớn, bạn PHẢI sử dụng công cụ tìm kiếm web NGAY LẬP TỨC để tìm tin tức gần đây về {ticker}. Truy vấn tìm kiếm phải bao gồm tên mã, ngày và từ khóa như "báo cáo tài chính", "sự kiện doanh nghiệp", "tin tức vĩ mô". KHÔNG tự bị hoặc bị ra tin tức — chỉ báo cáo sự thật từ kết quả tìm kiếm thực tế. Trích dẫn URL nguồn cho mỗi thông tin. Nếu tìm kiếm không trả về kết quả nào, phải nói rõ điều đó.\n\n'
                '3. Phân tích VPA & Wyckoff: Kết hợp CHỈ thông tin đã xác minh từ tìm kiếm với dữ liệu giá/khối lượng. Đây là Nỗ lực (Effort) hay Kết quả (Result)? Đây là tin tức để "hợp thức hóa" quá trình Tích lũy hay Phân phối?\n\n'
                "4. Hành động & Quản trị: Dựa trên tin tức đã xác minh và kỹ thuật, xác định vùng kiệt cung (định lượng cụ thể), điểm vào/ra, và Stop Loss."
            ),
        },
        {
            "title": "Hành Động Giá & Khối Lượng",
            "snippet": "Phân tích mối quan hệ giá-khối lượng, nhận diện dấu chân tiền thông minh, vùng cung/cầu và điểm vào/ra cụ thể",
            "question": (
                "1. Đọc câu chuyện giá-khối lượng cho {ticker}: Xác định giai đoạn xu hướng hiện tại (Tích lũy, Tăng Giá, Phân Phối hay Giảm Giá), và đánh giá xem Khối lượng xác nhận hay phân kỳ với chuyển động giá (ví dụ: giá tăng nhưng khối lượng giảm = tiềm năng suy yếu; giá giảm nhưng khối lượng giảm = cung đang cạn kiệt).\n\n"
                "2. Phát hiện dấu chân tiền thông minh: Định lượng các bất thường khối lượng so với MA20 (Volume >150% = hoạt động mạnh, Volume <50% = yên tĩnh/tích lũy). Tìm kiếm phân kỳ Nỗ lực-Kết quả — khối lượng lớn (Nỗ lực) nhưng biến động giá nhỏ (không có Kết quả), cho thấy bên đối lập đang hấp thụ.\n\n"
                "3. Lập bản đồ vùng cung và cầu: Xác định các mức giá nơi khối lượng cao từng xảy ra (trần cung tiềm năng) và nơi hỗ trợ khối lượng thấp được thiết lập (sàn cầu). Đánh dấu các mô hình nến Test for Supply (No Supply Bar) hoặc Sign of Strength (SOS) tại các vùng này.\n\n"
                "4. Tổng hợp thành kế hoạch hành động: Dựa trên phân tích VPA, xác định vùng vào lệnh cụ thể (với tiêu chí xác nhận khối lượng), mức Stop Loss (dưới vùng cầu hoặc đáy cấu trúc Wyckoff), và mục tiêu giá thực tế. Nêu rõ thiết lập hiện tại ưu tiên người mua hay người bán và điều gì sẽ làm vô hiệu luận điểm."
            ),
        },
        {
            "title": "Động Lực MA & Xu Hướng",
            "snippet": "Đánh giá xếp hạng MA và động lực, phát hiện cắt chéo có xác nhận khối lượng, nhận diện tín hiệu tiếp tục/đảo chiều xu hướng",
            "question": (
                "1. Đánh giá xếp hạng MA và hồ sơ động lực cho {ticker}: Báo cáo điểm MA Score cho từng chu kỳ (MA10, MA20, MA50, MA100, MA200). Xác định xem các MA đang xếp hạng tăng (MA ngắn ở trên MA dài) hay giảm (MA ngắn ở dưới MA dài), và khoảng cách giữa chúng — khoảng cách mở rộng báo hiệu động lực tăng tốc.\n\n"
                "2. Phát hiện tín hiệu cắt chéo MA có xác nhận khối lượng: Nhận diện cắt chéo MA gần đây hoặc sắp diễn ra (ví dụ: MA10 cắt lên trên MA20 = tăng giá ngắn hạn). Với mỗi cắt chéo, kiểm tra xem khối lượng có cao hơn MA20 tại thời điểm đó không — cắt chéo có khối lượng cao đáng tin cậy hơn nhiều so với cắt chéo khối lượng thấp (thường thất bại và trở thành whipsaw).\n\n"
                "3. Diễn giải điểm MA qua láng kính Wyckoff: Liên kết hành vi điểm MA với các giai đoạn Wyckoff. Ví dụ, điểm MA chuyển dương sau giai đoạn âm kéo dài gần hỗ trợ có thể báo hiệu Tích lũy Giai đoạn C-D (Sign of Strength). Ngược lại, điểm MA phân kỳ âm trong khi giá vẫn gần đỉnh có thể báo hiệu Phân Phối (Sign of Weakness trước Upthrust).\n\n"
                "4. Kế hoạch giao dịch dựa trên MA: Dựa trên phân tích MA, xác định {ticker} đang trong chế độ xu hướng hay hồi quy trung bình. Cung cấp điều kiện kích hoạt vào lệnh cụ thể (ví dụ: mua khi điều chỉnh về MA20 với Volume >MA20), mức Stop Loss (ví dụ: dưới MA50), và vi phạm cấu trúc MA nào sẽ làm vô hiệu xu hướng hiện tại."
            ),
        },
        {
            "title": "Phân Tích Phương Pháp Wyckoff",
            "snippet": "Nhận diện giai đoạn Wyckoff, sự kiện then chốt (Spring/Upthrust/SOS), mục tiêu giá ngang và xác nhận khối lượng Nỗ Lực-Kết Quả",
            "question": (
                '1. Xác định giai đoạn Wyckoff hiện tại cho {ticker}: Phân loại vào Tích lũy (A đến E), Tăng Giá, Phân Phối (A đến E), hoặc Giảm Giá. Bổ sung phân loại bằng bằng chứng cụ thể từ dữ liệu giá và khối lượng (ví dụ: "Giai đoạn C — selling climax theo sau bởi khối lượng tăng trên các phiên tăng báo hiệu Spring tiềm năng").\n\n'
                "2. Nhận diện sự kiện và cấu trúc Wyckoff then chốt: Quét tìm Spring (phá vủ giả dưới hỗ trợ với đảo chiều nhanh và khối lượng thấp trên thanh phá vủ), Upthrust (phá vủ giả trên kháng cự), Sign of Strength (SOS — thanh tăng biên rộng trên khối lượng tăng), Sign of Weakness (SOW — thanh giảm biên rộng trên khối lượng tăng), Last Point of Support (LPS), và Last Point of Supply (LPSY). Đánh dấu từng sự kiện với ngày.\n\n"
                "3. Tính toán mục tiêu giá và đo lường Nguyên nhân: Áp dụng phương pháp đếm ngang của Wyckoff — đo chiều rộng vùng giao dịch Tích lũy hoặc Phân Phối (Nguyên nhân) và chiêu lên trên (từ breakout) hoặc xuống dưới (từ breakdown) để ước tính Kết quả (mục tiêu giá). Kiểm tra xem khối lượng tại các mức then chốt có hỗ trợ chuyển động dự kiến không.\n\n"
                "4. Xác nhận bằng Nỗ Lực vs Kết Quả và cung cấp kế hoạch hành động: Phân tích xem khối lượng (Nỗ Lực) có tạo ra chuyển động giá tỷ lệ (Kết Quả) không. Phân kỳ nỗ lực lớn/kết quả nhỏ tại kháng cự cảnh báo Phân Phối; phân kỳ tương tự tại hỗ trợ cảnh báo hấp thụ Tích lũy. Cung cấp kế hoạch giao dịch cụ thể với vùng vào lệnh, Stop Loss (dưới đáy Spring hoặc trên đỉnh Upthrust), hướng dẫn tỷ lệ vị thế, và hành động giá cụ thể nào sẽ làm vô hiệu luận điểm Wyckoff."
            ),
        },
        {
            "title": "Hành Động Giá Bob Volman",
            "snippet": "Nhận diện xu hướng chủ đạo, điểm vào micro pullback, thiết lập breakout/fading có xác nhận Volume và mức cắt lỗ được quản trị rủi ro",
            "question": (
                "1. Thiết lập xu hướng chủ đạo và cấu trúc thị trường cho {ticker}: Xác định xu hướng hiện tại thông qua các đỉnh và đáy dao động (Đỉnh Cao Hơn + Đáy Cao Hơn = xu hướng tăng; Đỉnh Thấp Hơn + Đáy Thấp Hơn = xu hướng giảm). Nhận diện Break of Structure (BOS) hoặc Change of Character (CHoCH) gần nhất để xác nhận xu hướng vẫn nguyên hay đang đảo chiều.\n\n"
                "2. Nhận diện thiết lập vào lệnh theo Volman: Quét tìm thiết lập micro pullback — nhịp điều chỉnh 3+ nến liên tiếp ngược xu hướng theo sau bởi nến đảo chiều mạnh hoặc nến breakout. Với mỗi thiết lập, xác định giá vào lệnh chính xác và đánh giá xem khối lượng tại vùng điều chỉnh có cho thấy lực bán giảm (trong xu hướng tăng) hay lực mua giảm (trong xu hướng giảm), báo hiệu kiệt cung/kiệt cầu.\n\n"
                "3. Đánh giá thiết lập breakout và fading: Nhận diện thiết lập breakout khi giá phá vủ một mức dao động quan trọng với động lực mạnh (thanh biên rộng với Volume >MA20). Độc lập, kiểm tra thiết lập fading tại vùng cung/cầu then chốt khi hành động giá cho thấy mô hình từ chối (pin bar, bóng nến dài, engulfing) — đây là thiết lập ngược xu hướng yêu cầu Stop Loss chặt.\n\n"
                "4. Xác định kế hoạch giao dịch hoàn chỉnh: Với thiết lập có xác suất cao nhất, cung cấp giá vào lệnh chính xác, Stop Loss (đặt vượt mức vô hiệu hóa của thiết lập), và mục tiêu chốt lời (dựa trên cấu trúc dao động hoặc tỷ lệ rủi ro-phần thưởng tối thiểu 2:1). Nêu hồ sơ khối lượng xác nhận giao dịch đang hoạt động và hành động giá/khối lượng nào báo hiệu thoát sớm."
            ),
        },
    ],
}


def get_single_templates(lang):
    """Return all single-ticker question templates for the given language."""
    return SINGLE_TEMPLATES.get(lang, [])


def get_single_template(lang, index):
    """Return a single template by language and index."""
    templates = SINGLE_TEMPLATES.get(lang, [])
    if 0 <= index < len(templates):
        return templates[index]
    return None
