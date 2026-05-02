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
                "1. Detect extreme moves: Check if {ticker} changed more than \u00b16.7% or Volume exceeded >150% of the 20-period average.\n\n"
                "2. CRITICAL \u2014 Perform a real internet search NOW: If significant moves are detected, you MUST use your web search tool RIGHT NOW to search for recent news about {ticker}. Search queries must include the ticker name, date, and keywords like \"earnings\", \"financial report\", or \"corporate event\". DO NOT generate or hallucinate news \u2014 only report facts from actual search results. Cite the source URL for each piece of information. If your search tool returns no results, say so explicitly.\n\n"
                "3. VPA & Wyckoff analysis: Combine ONLY verified search findings with price/volume data. Is this Effort or Result? Is the news being used to \"rationalize\" an Accumulation or Distribution process?\n\n"
                "4. Action & risk management: Based on verified news and technicals, identify supply exhaustion zones (quantified), entry/exit levels, and Stop Loss."
            ),
        },
        {
            "title": "Price Action & Volume",
            "snippet": "Analyze price-volume relationship, identify smart money footprints, supply/demand zones, and actionable entry/exit levels",
            "question": (
                "1. Read the price-volume narrative for {ticker}: Identify the current trend phase (Accumulation, Markup, Distribution, or Markdown), and assess whether Volume confirms or diverges from price movement (e.g., rising price on declining Volume = potential weakness; falling price on declining Volume = supply drying up).\n\n"
                "2. Detect smart money footprints: Quantify Volume anomalies relative to MA20 (Volume >150% = high activity, Volume <50% = quiet/absorption). Look for Effort vs Result divergences \u2014 large Volume (Effort) with small price movement (no Result), indicating absorption by the opposite side.\n\n"
                "3. Map key supply and demand zones: Identify price levels where previous high Volume occurred (potential supply ceilings) and where low Volume support was established (demand floors). Flag any Test for Supply (No Supply Bar) or Signs of Strength (SOS) candlestick patterns at these zones.\n\n"
                "4. Synthesize into actionable plan: Based on the VPA analysis, identify specific entry zones (with Volume confirmation criteria), Stop Loss levels (below demand zones or Wyckoff structure lows), and realistic price targets. State whether the current setup favors buyers or sellers and what would invalidate the thesis."
            ),
        },
        {
            "title": "MA Momentum & Trend",
            "snippet": "Assess MA alignment and momentum, detect crossovers with volume confirmation, and identify trend continuation/reversal signals",
            "question": (
                "1. Assess MA alignment and momentum profile for {ticker}: Report the MA Score for each period (MA10, MA20, MA50, MA100, MA200). Determine if MAs are stacked bullishly (shorter above longer) or bearishly (shorter below longer), and how wide the spread is \u2014 a widening spread signals accelerating momentum.\n\n"
                "2. Detect MA crossover signals with Volume confirmation: Identify any recent or impending MA crossovers (e.g., MA10 crossing above MA20 = short-term bullish). For each crossover, check whether Volume was above MA20 at the time \u2014 crossovers with high Volume are far more reliable than those on low Volume (which often fail and become whipsaws).\n\n"
                "3. Interpret MA scores through a Wyckoff lens: Align MA Score behavior with Wyckoff phases. For example, MA Score turning positive after an extended negative period near support could indicate Accumulation Phase C-D (Sign of Strength). Conversely, MA Score diverging negatively while price remains near highs could signal Distribution (Sign of Weakness before an Upthrust).\n\n"
                "4. Actionable MA-based trading plan: Based on the MA analysis, identify whether {ticker} is in a trending or mean-reverting regime. Provide specific entry triggers (e.g., buy on pullback to MA20 with Volume >MA20), Stop Loss levels (e.g., below MA50), and the MA structure violation that would invalidate the current trend."
            ),
        },
        {
            "title": "Wyckoff Method Analysis",
            "snippet": "Identify Wyckoff phases, key events (Spring/Upthrust/SOS), horizontal price targets, and Effort-Result volume confirmation",
            "question": (
                "1. Determine the current Wyckoff phase for {ticker}: Classify into Accumulation (A through E), Markup, Distribution (A through E), or Markdown. Support your classification with specific evidence from price and volume data (e.g., \"Phase C \u2014 selling climax followed by increased Volume on up-days indicates potential Spring\").\n\n"
                "2. Identify key Wyckoff events and structures: Scan for Springs (false breakdowns below support with quick reversal and low Volume on the breakdown bar), Upthrusts (false breakouts above resistance), Signs of Strength (SOS \u2014 wide-range up-bars on increasing Volume), Signs of Weakness (SOW \u2014 wide-range down-bars on increasing Volume), Last Point of Support (LPS), and Last Point of Supply (LPSY). Mark each event with the date.\n\n"
                "3. Calculate price targets and measure the Cause: Apply Wyckoff's horizontal counting method \u2014 measure the width of the Accumulation or Distribution trading range (the Cause) and project it upward (from breakout) or downward (from breakdown) to estimate the Effect (price target). Check if Volume at key levels supports the projected move.\n\n"
                "4. Confirm with Effort vs Result and provide action plan: Analyze whether Volume (Effort) is producing proportional price movement (Result). A large-effort/small-result divergence at resistance warns of Distribution; the same at support warns of Accumulation absorption. Provide a concrete trading plan with entry zone, Stop Loss (below Spring low or above Upthrust high), position sizing guidance, and the specific price action that would invalidate the Wyckoff thesis."
            ),
        },
        {
            "title": "Bob Volman Price Action",
            "snippet": "Identify dominant trend, micro pullback entries, breakout/fading setups with Volume confirmation, and risk-managed exit levels",
            "question": (
                "1. Establish the dominant trend and market structure for {ticker}: Define the current trend using swing highs and lows (Higher Highs + Higher Lows = uptrend; Lower Highs + Lower Lows = downtrend). Identify the most recent Break of Structure (BOS) or Change of Character (CHoCH) to confirm whether the trend is intact or reversing.\n\n"
                "2. Identify Volman-style entry setups: Scan for micro pullback setups \u2014 a pullback of 3+ consecutive bars against the trend followed by a strong reversal bar or breakout candle. For each setup, specify the exact entry price and assess whether Volume at the pullback zone shows diminishing selling (in uptrend) or diminishing buying (in downtrend), indicating supply/demand exhaustion.\n\n"
                "3. Evaluate breakout and fading setups: Identify breakout setups where price breaks a significant swing level on strong momentum (wide-range bar with Volume >MA20). Separately, check for fading setups at key supply/demand zones where price action shows rejection patterns (pin bars, long wicks, engulfing candles) \u2014 these are counter-trend setups requiring tight Stop Losses.\n\n"
                "4. Define the complete trade plan: For the highest-probability setup identified, provide exact entry price, Stop Loss (placed beyond the setup's invalidation level), and take-profit targets (based on swing structure or minimum 2:1 risk-reward). State the Volume profile that would confirm the trade is working and the Volume/price action that signals early exit."
            ),
        },
    ],
    "vn": [
        {
            "title": "C\u1edf H\u1ed9i Giao D\u1ecbch",
            "snippet": "Nh\u1eadn di\u1ec7n c\u01a1 h\u1ed9i giao d\u1ecbch, ph\u00e2n t\u00edch cung-c\u1ea7u, l\u1ed9 tr\u00ecnh gi\u1ea3i ng\u00e2n v\u00e0 qu\u1ea3n tr\u1ecb r\u1ee7i ro",
            "question": (
                "1. X\u00e1c \u0111\u1ecbnh c\u00e1c c\u01a1 h\u1ed9i giao d\u1ecbch c\u00f3 th\u1ec3 h\u00e0nh \u0111\u1ed9ng cho {ticker} d\u1ef1a tr\u00ean d\u1eef li\u1ec7u hi\u1ec7n t\u1ea1i v\u00e0 b\u1ed1i c\u1ea3nh th\u1ecb tr\u01b0\u1eddng chung.\n\n"
                "2. Nh\u1eadn di\u1ec7n h\u00e0nh vi 'Smart Money': Ch\u1ec9 r\u00f5 c\u00e1c t\u00edn hi\u1ec7u x\u00e1c nh\u1eadn (No Supply, Test for Supply, Spring, Upthrust, hay SOS) t\u1ea1i c\u00e1c v\u00f9ng gi\u00e1 then ch\u1ed1t, k\u00e8m theo \u0111\u00e1nh gi\u00e1 s\u1ef1 b\u1ea5t \u0111\u1ed1i x\u1ee9ng gi\u1eefa N\u1ed7 l\u1ef1c (Volume) v\u00e0 K\u1ebft qu\u1ea3 (Gi\u00e1).\n\n"
                "L\u01afU \u00d0 QUAN TR\u1edcNG: N\u1ebfu c\u00f3 th\u00f4ng b\u00e1o 'Th\u1ecb tr\u01b0\u1eddng \u0111ang m\u1edf c\u1eeda', ph\u1ea3i coi d\u1eef li\u1ec7u Volume c\u1ee7a b\u1ea3n ghi cu\u1ed1i c\u00f9ng l\u00e0 ch\u01b0a ho\u00e0n t\u1ea5t. Trong tr\u01b0\u1eddng h\u1ee3p n\u00e0y, tuy\u1ec7t \u0111\u1ed1i kh\u00f4ng k\u1ebft lu\u1eadn 'Ki\u1ec7t cung' m\u1ed9t c\u00e1ch kh\u1eb3ng \u0111\u1ecbnh. Thay v\u00e0o \u0111\u00f3, ph\u1ea3i \u0111\u01b0a ra ghi ch\u00fa r\u1eb1ng \u0111\u00e2y l\u00e0 t\u00edn hi\u1ec7u \u0111ang h\u00ecnh th\u00e0nh v\u00e0 c\u1ea7n \u0111\u01b0\u1ee3c x\u00e1c nh\u1eadn l\u1ea1i sau khi \u0111\u00f3ng phi\u00ean ATC.\n\n"
                "3. Thi\u1ebft l\u1eadp l\u1ed9 tr\u00ecnh gi\u1ea3i ng\u00e2n th\u1ef1c chi\u1ebfn: Chia r\u00f5 t\u1ef7 l\u1ec7 v\u1ecb th\u1ebf th\u0103m d\u00f2 (t\u1ea1i v\u00f9ng Vol th\u1ea5p) v\u00e0 v\u1ecb th\u1ebf gia t\u0103ng (khi c\u00f3 x\u00e1c nh\u1eadn N\u1ed7 l\u1ef1c-K\u1ebft qu\u1ea3), k\u00e8m theo \u0111i\u1ec3m Stop Loss (SL) c\u1ee5 th\u1ec3 d\u1ef1a tr\u00ean vi ph\u1ea1m c\u1ea5u tr\u00fac Wyckoff.\n\n"
                "4. C\u00e2n nh\u1eafc t\u1ef7 l\u1ec7 r\u1ee7i ro-ph\u1ea7n th\u01b0\u1edfng v\u00e0 c\u00e1c r\u1ee7i ro ch\u00ednh c\u1ea7n theo d\u00f5i (Buying Climax, Upthrust, hay r\u1ee7i ro t\u1eeb VNINDEX)."
            ),
        },
        {
            "title": "T\u00ecm Ki\u1ebfm Tin T\u1ee9c & S\u1ef1 Ki\u1ec7n",
            "snippet": "Ph\u00e1t hi\u1ec7n bi\u1ebfn \u0111\u1ed9ng gi\u00e1 m\u1ea1nh, tra c\u1ee9u nguy\u00ean nh\u00e2n, k\u1ebft h\u1ee3p ph\u00e2n t\u00edch VPA & Wyckoff",
            "question": (
                "1. Ki\u1ec3m tra bi\u1ebfn \u0111\u1ed9ng c\u1ef1c \u0111\u1ea1i: X\u00e1c \u0111\u1ecbnh xem {ticker} c\u00f3 thay \u0111\u1ed5i gi\u00e1 qu\u00e1 \u00b16.7% ho\u1eb7c kh\u1ed1i l\u01b0\u1ee3ng (Volume) v\u01b0\u1ee3t >150% trung b\u00ecnh 20 phi\u00ean kh\u00f4ng.\n\n"
                "2. B\u1eaft BU\u1ed8C \u2014 Th\u1ef1c hi\u1ec7n t\u00ecm ki\u1ebfm internet th\u1ef1c t\u1ebf NGAY L\u1eacP T\u1ee8C: N\u1ebfu ph\u00e1t hi\u1ec7n bi\u1ebfn \u0111\u1ed9ng l\u1edbn, b\u1ea1n PH\u1ea2I s\u1eed d\u1ee5ng c\u00f4ng c\u1ee5 t\u00ecm ki\u1ebfm web NGAY L\u1eacP T\u1ee8C \u0111\u1ec3 t\u00ecm tin t\u1ee9c g\u1ea7n \u0111\u00e2y v\u1ec1 {ticker}. Truy v\u1ea5n t\u00ecm ki\u1ebfm ph\u1ea3i bao g\u1ed3m t\u00ean m\u00e3, ng\u00e0y v\u00e0 t\u1eeb kh\u00f3a nh\u01b0 \"b\u00e1o c\u00e1o t\u00e0i ch\u00ednh\", \"s\u1ef1 ki\u1ec7n doanh nghi\u1ec7p\", \"tin t\u1ee9c v\u0129 m\u00f4\". KH\u00d4NG t\u1ef1 b\u1ecb ho\u1eb7c b\u1ecb ra tin t\u1ee9c \u2014 ch\u1ec9 b\u00e1o c\u00e1o s\u1ef1 th\u1eadt t\u1eeb k\u1ebft qu\u1ea3 t\u00ecm ki\u1ebfm th\u1ef1c t\u1ebf. Tr\u00edch d\u1eabn URL ngu\u1ed3n cho m\u1ed7i th\u00f4ng tin. N\u1ebfu t\u00ecm ki\u1ebfm kh\u00f4ng tr\u1ea3 v\u1ec1 k\u1ebft qu\u1ea3 n\u00e0o, ph\u1ea3i n\u00f3i r\u00f5 \u0111i\u1ec1u \u0111\u00f3.\n\n"
                "3. Ph\u00e2n t\u00edch VPA & Wyckoff: K\u1ebft h\u1ee3p CH\u1ec8 th\u00f4ng tin \u0111\u00e3 x\u00e1c minh t\u1eeb t\u00ecm ki\u1ebfm v\u1edbi d\u1eef li\u1ec7u gi\u00e1/kh\u1ed1i l\u01b0\u1ee3ng. \u0110\u00e2y l\u00e0 N\u1ed7 l\u1ef1c (Effort) hay K\u1ebft qu\u1ea3 (Result)? \u0110\u00e2y l\u00e0 tin t\u1ee9c \u0111\u1ec3 \"h\u1ee3p th\u1ee9c h\u00f3a\" qu\u00e1 tr\u00ecnh T\u00edch l\u0169y hay Ph\u00e2n ph\u1ed1i?\n\n"
                "4. H\u00e0nh \u0111\u1ed9ng & Qu\u1ea3n tr\u1ecb: D\u1ef1a tr\u00ean tin t\u1ee9c \u0111\u00e3 x\u00e1c minh v\u00e0 k\u1ef9 thu\u1eadt, x\u00e1c \u0111\u1ecbnh v\u00f9ng ki\u1ec7t cung (\u0111\u1ecbnh l\u01b0\u1ee3ng c\u1ee5 th\u1ec3), \u0111i\u1ec3m v\u00e0o/ra, v\u00e0 Stop Loss."
            ),
        },
        {
            "title": "H\u00e0nh \u0110\u1ed9ng Gi\u00e1 & Kh\u1ed1i L\u01b0\u1ee3ng",
            "snippet": "Ph\u00e2n t\u00edch m\u1ed1i quan h\u1ec7 gi\u00e1-kh\u1ed1i l\u01b0\u1ee3ng, nh\u1eadn di\u1ec7n d\u1ea5u ch\u00e2n ti\u1ec1n th\u00f4ng minh, v\u00f9ng cung/c\u1ea7u v\u00e0 \u0111i\u1ec3m v\u00e0o/ra c\u1ee5 th\u1ec3",
            "question": (
                "1. \u0110\u1ecdc c\u00e2u chuy\u1ec7n gi\u00e1-kh\u1ed1i l\u01b0\u1ee3ng cho {ticker}: X\u00e1c \u0111\u1ecbnh giai \u0111o\u1ea1n xu h\u01b0\u1edbng hi\u1ec7n t\u1ea1i (T\u00edch l\u0169y, T\u0103ng Gi\u00e1, Ph\u00e2n Ph\u1ed1i hay Gi\u1ea3m Gi\u00e1), v\u00e0 \u0111\u00e1nh gi\u00e1 xem Kh\u1ed1i l\u01b0\u1ee3ng x\u00e1c nh\u1eadn hay ph\u00e2n k\u1ef3 v\u1edbi chuy\u1ec3n \u0111\u1ed9ng gi\u00e1 (v\u00ed d\u1ee5: gi\u00e1 t\u0103ng nh\u01b0ng kh\u1ed1i l\u01b0\u1ee3ng gi\u1ea3m = ti\u1ec1m n\u0103ng suy y\u1ebfu; gi\u00e1 gi\u1ea3m nh\u01b0ng kh\u1ed1i l\u01b0\u1ee3ng gi\u1ea3m = cung \u0111ang c\u1ea1n ki\u1ec7t).\n\n"
                "2. Ph\u00e1t hi\u1ec7n d\u1ea5u ch\u00e2n ti\u1ec1n th\u00f4ng minh: \u0110\u1ecbnh l\u01b0\u1ee3ng c\u00e1c b\u1ea5t th\u01b0\u1eddng kh\u1ed1i l\u01b0\u1ee3ng so v\u1edbi MA20 (Volume >150% = ho\u1ea1t \u0111\u1ed9ng m\u1ea1nh, Volume <50% = y\u00ean t\u0129nh/t\u00edch l\u0169y). T\u00ecm ki\u1ebfm ph\u00e2n k\u1ef3 N\u1ed7 l\u1ef1c-K\u1ebft qu\u1ea3 \u2014 kh\u1ed1i l\u01b0\u1ee3ng l\u1edbn (N\u1ed7 l\u1ef1c) nh\u01b0ng bi\u1ebfn \u0111\u1ed9ng gi\u00e1 nh\u1ecf (kh\u00f4ng c\u00f3 K\u1ebft qu\u1ea3), cho th\u1ea5y b\u00ean \u0111\u1ed1i l\u1eadp \u0111ang h\u1ea5p th\u1ee5.\n\n"
                "3. L\u1eadp b\u1ea3n \u0111\u1ed3 v\u00f9ng cung v\u00e0 c\u1ea7u: X\u00e1c \u0111\u1ecbnh c\u00e1c m\u1ee9c gi\u00e1 n\u01a1i kh\u1ed1i l\u01b0\u1ee3ng cao t\u1eebng x\u1ea3y ra (tr\u1ea7n cung ti\u1ec1m n\u0103ng) v\u00e0 n\u01a1i h\u1ed7 tr\u1ee3 kh\u1ed1i l\u01b0\u1ee3ng th\u1ea5p \u0111\u01b0\u1ee3c thi\u1ebft l\u1eadp (s\u00e0n c\u1ea7u). \u0110\u00e1nh d\u1ea5u c\u00e1c m\u00f4 h\u00ecnh n\u1ebfn Test for Supply (No Supply Bar) ho\u1eb7c Sign of Strength (SOS) t\u1ea1i c\u00e1c v\u00f9ng n\u00e0y.\n\n"
                "4. T\u1ed5ng h\u1ee3p th\u00e0nh k\u1ebf ho\u1ea1ch h\u00e0nh \u0111\u1ed9ng: D\u1ef1a tr\u00ean ph\u00e2n t\u00edch VPA, x\u00e1c \u0111\u1ecbnh v\u00f9ng v\u00e0o l\u1ec7nh c\u1ee5 th\u1ec3 (v\u1edbi ti\u00eau ch\u00ed x\u00e1c nh\u1eadn kh\u1ed1i l\u01b0\u1ee3ng), m\u1ee9c Stop Loss (d\u01b0\u1edbi v\u00f9ng c\u1ea7u ho\u1eb7c \u0111\u00e1y c\u1ea5u tr\u00fac Wyckoff), v\u00e0 m\u1ee5c ti\u00eau gi\u00e1 th\u1ef1c t\u1ebf. N\u00eau r\u00f5 thi\u1ebft l\u1eadp hi\u1ec7n t\u1ea1i \u01b0u ti\u00ean ng\u01b0\u1eddi mua hay ng\u01b0\u1eddi b\u00e1n v\u00e0 \u0111i\u1ec1u g\u00ec s\u1ebd l\u00e0m v\u00f4 hi\u1ec7u lu\u1eadn \u0111i\u1ec3m."
            ),
        },
        {
            "title": "\u0110\u1ed9ng L\u1ef1c MA & Xu H\u01b0\u1edbng",
            "snippet": "\u0110\u00e1nh gi\u00e1 x\u1ebfp h\u1ea1ng MA v\u00e0 \u0111\u1ed9ng l\u1ef1c, ph\u00e1t hi\u1ec7n c\u1eaft ch\u00e9o c\u00f3 x\u00e1c nh\u1eadn kh\u1ed1i l\u01b0\u1ee3ng, nh\u1eadn di\u1ec7n t\u00edn hi\u1ec7u ti\u1ebfp t\u1ee5c/\u0111\u1ea3o chi\u1ec1u xu h\u01b0\u1edbng",
            "question": (
                "1. \u0110\u00e1nh gi\u00e1 x\u1ebfp h\u1ea1ng MA v\u00e0 h\u1ed3 s\u01a1 \u0111\u1ed9ng l\u1ef1c cho {ticker}: B\u00e1o c\u00e1o \u0111i\u1ec3m MA Score cho t\u1eebng chu k\u1ef3 (MA10, MA20, MA50, MA100, MA200). X\u00e1c \u0111\u1ecbnh xem c\u00e1c MA \u0111ang x\u1ebfp h\u1ea1ng t\u0103ng (MA ng\u1eafn \u1edf tr\u00ean MA d\u00e0i) hay gi\u1ea3m (MA ng\u1eafn \u1edf d\u01b0\u1edbi MA d\u00e0i), v\u00e0 kho\u1ea3ng c\u00e1ch gi\u1eefa ch\u00fang \u2014 kho\u1ea3ng c\u00e1ch m\u1edf r\u1ed9ng b\u00e1o hi\u1ec7u \u0111\u1ed9ng l\u1ef1c t\u0103ng t\u1ed1c.\n\n"
                "2. Ph\u00e1t hi\u1ec7n t\u00edn hi\u1ec7u c\u1eaft ch\u00e9o MA c\u00f3 x\u00e1c nh\u1eadn kh\u1ed1i l\u01b0\u1ee3ng: Nh\u1eadn di\u1ec7n c\u1eaft ch\u00e9o MA g\u1ea7n \u0111\u00e2y ho\u1eb7c s\u1eafp di\u1ec5n ra (v\u00ed d\u1ee5: MA10 c\u1eaft l\u00ean tr\u00ean MA20 = t\u0103ng gi\u00e1 ng\u1eafn h\u1ea1n). V\u1edbi m\u1ed7i c\u1eaft ch\u00e9o, ki\u1ec3m tra xem kh\u1ed1i l\u01b0\u1ee3ng c\u00f3 cao h\u01a1n MA20 t\u1ea1i th\u1eddi \u0111i\u1ec3m \u0111\u00f3 kh\u00f4ng \u2014 c\u1eaft ch\u00e9o c\u00f3 kh\u1ed1i l\u01b0\u1ee3ng cao \u0111\u00e1ng tin c\u1eady h\u01a1n nhi\u1ec1u so v\u1edbi c\u1eaft ch\u00e9o kh\u1ed1i l\u01b0\u1ee3ng th\u1ea5p (th\u01b0\u1eddng th\u1ea5t b\u1ea1i v\u00e0 tr\u1edf th\u00e0nh whipsaw).\n\n"
                "3. Di\u1ec5n gi\u1ea3i \u0111i\u1ec3m MA qua l\u00e1ng k\u00ednh Wyckoff: Li\u00ean k\u1ebft h\u00e0nh vi \u0111i\u1ec3m MA v\u1edbi c\u00e1c giai \u0111o\u1ea1n Wyckoff. V\u00ed d\u1ee5, \u0111i\u1ec3m MA chuy\u1ec3n d\u01b0\u01a1ng sau giai \u0111o\u1ea1n \u00e2m k\u00e9o d\u00e0i g\u1ea7n h\u1ed7 tr\u1ee3 c\u00f3 th\u1ec3 b\u00e1o hi\u1ec7u T\u00edch l\u0169y Giai \u0111o\u1ea1n C-D (Sign of Strength). Ng\u01b0\u1ee3c l\u1ea1i, \u0111i\u1ec3m MA ph\u00e2n k\u1ef3 \u00e2m trong khi gi\u00e1 v\u1eabn g\u1ea7n \u0111\u1ec9nh c\u00f3 th\u1ec3 b\u00e1o hi\u1ec7u Ph\u00e2n Ph\u1ed1i (Sign of Weakness tr\u01b0\u1edbc Upthrust).\n\n"
                "4. K\u1ebf ho\u1ea1ch giao d\u1ecbch d\u1ef1a tr\u00ean MA: D\u1ef1a tr\u00ean ph\u00e2n t\u00edch MA, x\u00e1c \u0111\u1ecbnh {ticker} \u0111ang trong ch\u1ebf \u0111\u1ed9 xu h\u01b0\u1edbng hay h\u1ed3i quy trung b\u00ecnh. Cung c\u1ea5p \u0111i\u1ec1u ki\u1ec7n k\u00edch ho\u1ea1t v\u00e0o l\u1ec7nh c\u1ee5 th\u1ec3 (v\u00ed d\u1ee5: mua khi \u0111i\u1ec1u ch\u1ec9nh v\u1ec1 MA20 v\u1edbi Volume >MA20), m\u1ee9c Stop Loss (v\u00ed d\u1ee5: d\u01b0\u1edbi MA50), v\u00e0 vi ph\u1ea1m c\u1ea5u tr\u00fac MA n\u00e0o s\u1ebd l\u00e0m v\u00f4 hi\u1ec7u xu h\u01b0\u1edbng hi\u1ec7n t\u1ea1i."
            ),
        },
        {
            "title": "Ph\u00e2n T\u00edch Ph\u01b0\u01a1ng Ph\u00e1p Wyckoff",
            "snippet": "Nh\u1eadn di\u1ec7n giai \u0111o\u1ea1n Wyckoff, s\u1ef1 ki\u1ec7n then ch\u1ed1t (Spring/Upthrust/SOS), m\u1ee5c ti\u00eau gi\u00e1 ngang v\u00e0 x\u00e1c nh\u1eadn kh\u1ed1i l\u01b0\u1ee3ng N\u1ed7 L\u1ef1c-K\u1ebft Qu\u1ea3",
            "question": (
                "1. X\u00e1c \u0111\u1ecbnh giai \u0111o\u1ea1n Wyckoff hi\u1ec7n t\u1ea1i cho {ticker}: Ph\u00e2n lo\u1ea1i v\u00e0o T\u00edch l\u0169y (A \u0111\u1ebfn E), T\u0103ng Gi\u00e1, Ph\u00e2n Ph\u1ed1i (A \u0111\u1ebfn E), ho\u1eb7c Gi\u1ea3m Gi\u00e1. B\u1ed5 sung ph\u00e2n lo\u1ea1i b\u1eb1ng b\u1eb1ng ch\u1ee9ng c\u1ee5 th\u1ec3 t\u1eeb d\u1eef li\u1ec7u gi\u00e1 v\u00e0 kh\u1ed1i l\u01b0\u1ee3ng (v\u00ed d\u1ee5: \"Giai \u0111o\u1ea1n C \u2014 selling climax theo sau b\u1edfi kh\u1ed1i l\u01b0\u1ee3ng t\u0103ng tr\u00ean c\u00e1c phi\u00ean t\u0103ng b\u00e1o hi\u1ec7u Spring ti\u1ec1m n\u0103ng\").\n\n"
                "2. Nh\u1eadn di\u1ec7n s\u1ef1 ki\u1ec7n v\u00e0 c\u1ea5u tr\u00fac Wyckoff then ch\u1ed1t: Qu\u00e9t t\u00ecm Spring (ph\u00e1 v\u1ee7 gi\u1ea3 d\u01b0\u1edbi h\u1ed7 tr\u1ee3 v\u1edbi \u0111\u1ea3o chi\u1ec1u nhanh v\u00e0 kh\u1ed1i l\u01b0\u1ee3ng th\u1ea5p tr\u00ean thanh ph\u00e1 v\u1ee7), Upthrust (ph\u00e1 v\u1ee7 gi\u1ea3 tr\u00ean kh\u00e1ng c\u1ef1), Sign of Strength (SOS \u2014 thanh t\u0103ng bi\u00ean r\u1ed9ng tr\u00ean kh\u1ed1i l\u01b0\u1ee3ng t\u0103ng), Sign of Weakness (SOW \u2014 thanh gi\u1ea3m bi\u00ean r\u1ed9ng tr\u00ean kh\u1ed1i l\u01b0\u1ee3ng t\u0103ng), Last Point of Support (LPS), v\u00e0 Last Point of Supply (LPSY). \u0110\u00e1nh d\u1ea5u t\u1eebng s\u1ef1 ki\u1ec7n v\u1edbi ng\u00e0y.\n\n"
                "3. T\u00ednh to\u00e1n m\u1ee5c ti\u00eau gi\u00e1 v\u00e0 \u0111o l\u01b0\u1eddng Nguy\u00ean nh\u00e2n: \u00c1p d\u1ee5ng ph\u01b0\u01a1ng ph\u00e1p \u0111\u1ebfm ngang c\u1ee7a Wyckoff \u2014 \u0111o chi\u1ec1u r\u1ed9ng v\u00f9ng giao d\u1ecbch T\u00edch l\u0169y ho\u1eb7c Ph\u00e2n Ph\u1ed1i (Nguy\u00ean nh\u00e2n) v\u00e0 chi\u00eau l\u00ean tr\u00ean (t\u1eeb breakout) ho\u1eb7c xu\u1ed1ng d\u01b0\u1edbi (t\u1eeb breakdown) \u0111\u1ec3 \u01b0\u1edbc t\u00ednh K\u1ebft qu\u1ea3 (m\u1ee5c ti\u00eau gi\u00e1). Ki\u1ec3m tra xem kh\u1ed1i l\u01b0\u1ee3ng t\u1ea1i c\u00e1c m\u1ee9c then ch\u1ed1t c\u00f3 h\u1ed7 tr\u1ee3 chuy\u1ec3n \u0111\u1ed9ng d\u1ef1 ki\u1ebfn kh\u00f4ng.\n\n"
                "4. X\u00e1c nh\u1eadn b\u1eb1ng N\u1ed7 L\u1ef1c vs K\u1ebft Qu\u1ea3 v\u00e0 cung c\u1ea5p k\u1ebf ho\u1ea1ch h\u00e0nh \u0111\u1ed9ng: Ph\u00e2n t\u00edch xem kh\u1ed1i l\u01b0\u1ee3ng (N\u1ed7 L\u1ef1c) c\u00f3 t\u1ea1o ra chuy\u1ec3n \u0111\u1ed9ng gi\u00e1 t\u1ef7 l\u1ec7 (K\u1ebft Qu\u1ea3) kh\u00f4ng. Ph\u00e2n k\u1ef3 n\u1ed7 l\u1ef1c l\u1edbn/k\u1ebft qu\u1ea3 nh\u1ecf t\u1ea1i kh\u00e1ng c\u1ef1 c\u1ea3nh b\u00e1o Ph\u00e2n Ph\u1ed1i; ph\u00e2n k\u1ef3 t\u01b0\u01a1ng t\u1ef1 t\u1ea1i h\u1ed7 tr\u1ee3 c\u1ea3nh b\u00e1o h\u1ea5p th\u1ee5 T\u00edch l\u0169y. Cung c\u1ea5p k\u1ebf ho\u1ea1ch giao d\u1ecbch c\u1ee5 th\u1ec3 v\u1edbi v\u00f9ng v\u00e0o l\u1ec7nh, Stop Loss (d\u01b0\u1edbi \u0111\u00e1y Spring ho\u1eb7c tr\u00ean \u0111\u1ec9nh Upthrust), h\u01b0\u1edbng d\u1eabn t\u1ef7 l\u1ec7 v\u1ecb th\u1ebf, v\u00e0 h\u00e0nh \u0111\u1ed9ng gi\u00e1 c\u1ee5 th\u1ec3 n\u00e0o s\u1ebd l\u00e0m v\u00f4 hi\u1ec7u lu\u1eadn \u0111i\u1ec3m Wyckoff."
            ),
        },
        {
            "title": "H\u00e0nh \u0110\u1ed9ng Gi\u00e1 Bob Volman",
            "snippet": "Nh\u1eadn di\u1ec7n xu h\u01b0\u1edbng ch\u1ee7 \u0111\u1ea1o, \u0111i\u1ec3m v\u00e0o micro pullback, thi\u1ebft l\u1eadp breakout/fading c\u00f3 x\u00e1c nh\u1eadn Volume v\u00e0 m\u1ee9c c\u1eaft l\u1ed7 \u0111\u01b0\u1ee3c qu\u1ea3n tr\u1ecb r\u1ee7i ro",
            "question": (
                "1. Thi\u1ebft l\u1eadp xu h\u01b0\u1edbng ch\u1ee7 \u0111\u1ea1o v\u00e0 c\u1ea5u tr\u00fac th\u1ecb tr\u01b0\u1eddng cho {ticker}: X\u00e1c \u0111\u1ecbnh xu h\u01b0\u1edbng hi\u1ec7n t\u1ea1i th\u00f4ng qua c\u00e1c \u0111\u1ec9nh v\u00e0 \u0111\u00e1y dao \u0111\u1ed9ng (\u0110\u1ec9nh Cao H\u01a1n + \u0110\u00e1y Cao H\u01a1n = xu h\u01b0\u1edbng t\u0103ng; \u0110\u1ec9nh Th\u1ea5p H\u01a1n + \u0110\u00e1y Th\u1ea5p H\u01a1n = xu h\u01b0\u1edbng gi\u1ea3m). Nh\u1eadn di\u1ec7n Break of Structure (BOS) ho\u1eb7c Change of Character (CHoCH) g\u1ea7n nh\u1ea5t \u0111\u1ec3 x\u00e1c nh\u1eadn xu h\u01b0\u1edbng v\u1eabn nguy\u00ean hay \u0111ang \u0111\u1ea3o chi\u1ec1u.\n\n"
                "2. Nh\u1eadn di\u1ec7n thi\u1ebft l\u1eadp v\u00e0o l\u1ec7nh theo Volman: Qu\u00e9t t\u00ecm thi\u1ebft l\u1eadp micro pullback \u2014 nh\u1ecbp \u0111i\u1ec1u ch\u1ec9nh 3+ n\u1ebfn li\u00ean ti\u1ebfp ng\u01b0\u1ee3c xu h\u01b0\u1edbng theo sau b\u1edfi n\u1ebfn \u0111\u1ea3o chi\u1ec1u m\u1ea1nh ho\u1eb7c n\u1ebfn breakout. V\u1edbi m\u1ed7i thi\u1ebft l\u1eadp, x\u00e1c \u0111\u1ecbnh gi\u00e1 v\u00e0o l\u1ec7nh ch\u00ednh x\u00e1c v\u00e0 \u0111\u00e1nh gi\u00e1 xem kh\u1ed1i l\u01b0\u1ee3ng t\u1ea1i v\u00f9ng \u0111i\u1ec1u ch\u1ec9nh c\u00f3 cho th\u1ea5y l\u1ef1c b\u00e1n gi\u1ea3m (trong xu h\u01b0\u1edbng t\u0103ng) hay l\u1ef1c mua gi\u1ea3m (trong xu h\u01b0\u1edbng gi\u1ea3m), b\u00e1o hi\u1ec7u ki\u1ec7t cung/ki\u1ec7t c\u1ea7u.\n\n"
                "3. \u0110\u00e1nh gi\u00e1 thi\u1ebft l\u1eadp breakout v\u00e0 fading: Nh\u1eadn di\u1ec7n thi\u1ebft l\u1eadp breakout khi gi\u00e1 ph\u00e1 v\u1ee7 m\u1ed9t m\u1ee9c dao \u0111\u1ed9ng quan tr\u1ecdng v\u1edbi \u0111\u1ed9ng l\u1ef1c m\u1ea1nh (thanh bi\u00ean r\u1ed9ng v\u1edbi Volume >MA20). \u0110\u1ed9c l\u1eadp, ki\u1ec3m tra thi\u1ebft l\u1eadp fading t\u1ea1i v\u00f9ng cung/c\u1ea7u then ch\u1ed1t khi h\u00e0nh \u0111\u1ed9ng gi\u00e1 cho th\u1ea5y m\u00f4 h\u00ecnh t\u1eeb ch\u1ed1i (pin bar, b\u00f3ng n\u1ebfn d\u00e0i, engulfing) \u2014 \u0111\u00e2y l\u00e0 thi\u1ebft l\u1eadp ng\u01b0\u1ee3c xu h\u01b0\u1edbng y\u00eau c\u1ea7u Stop Loss ch\u1eb7t.\n\n"
                "4. X\u00e1c \u0111\u1ecbnh k\u1ebf ho\u1ea1ch giao d\u1ecbch ho\u00e0n ch\u1ec9nh: V\u1edbi thi\u1ebft l\u1eadp c\u00f3 x\u00e1c su\u1ea5t cao nh\u1ea5t, cung c\u1ea5p gi\u00e1 v\u00e0o l\u1ec7nh ch\u00ednh x\u00e1c, Stop Loss (\u0111\u1eb7t v\u01b0\u1ee3t m\u1ee9c v\u00f4 hi\u1ec7u h\u00f3a c\u1ee7a thi\u1ebft l\u1eadp), v\u00e0 m\u1ee5c ti\u00eau ch\u1ed1t l\u1eddi (d\u1ef1a tr\u00ean c\u1ea5u tr\u00fac dao \u0111\u1ed9ng ho\u1eb7c t\u1ef7 l\u1ec7 r\u1ee7i ro-ph\u1ea7n th\u01b0\u1edfng t\u1ed1i thi\u1ec3u 2:1). N\u00eau h\u1ed3 s\u01a1 kh\u1ed1i l\u01b0\u1ee3ng x\u00e1c nh\u1eadn giao d\u1ecbch \u0111ang ho\u1ea1t \u0111\u1ed9ng v\u00e0 h\u00e0nh \u0111\u1ed9ng gi\u00e1/kh\u1ed1i l\u01b0\u1ee3ng n\u00e0o b\u00e1o hi\u1ec7u tho\u00e1t s\u1edbm."
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
