# Volume Profile - User Guide

**A Simple Guide to Using Volume Profile for Better Trading Decisions**

---

## What is Volume Profile?

Volume Profile shows you **where the most trading activity happened** at different price levels during a trading day. Instead of just seeing volume over time, you see volume at each price.

Think of it like a heat map:
- **Hot zones** (high volume) = Where lots of buyers and sellers agreed on price
- **Cold zones** (low volume) = Where price moved quickly without much trading

### Why Should You Care?

Volume Profile helps you answer critical trading questions:
- **"Where will the stock find support or resistance?"** → Look at high volume areas
- **"What's a fair price for this stock today?"** → The Point of Control (POC)
- **"Where might price move quickly?"** → Low volume areas
- **"Is this breakout real?"** → Check if volume confirms it

---

## Key Concepts (Simple Explanations)

### 1. Point of Control (POC)
**What it is**: The price where the MOST trading happened today.

**Why it matters**:
- This is where buyers and sellers agreed the most
- Think of it as "today's fair value"
- Price often returns to POC like a magnet
- POC from previous days becomes support/resistance

**Trading tip**: If price is far from POC, it might return there.

---

### 2. Value Area (VA)
**What it is**: The price range where 70% of trading happened (centered around POC).

**Why it matters**:
- Prices inside VA = "Normal" trading range
- Prices outside VA = "Unusual" or extreme levels
- VA boundaries become important support/resistance

**Trading tip**:
- Buying below VA = Potential bargain
- Selling above VA = Potential overbought

---

### 3. High Volume Nodes (HVN)
**What it is**: Price levels with lots of volume (thick bars on the profile).

**Why it matters**:
- Strong support when price falls to HVN
- Strong resistance when price rises to HVN
- Price tends to slow down or consolidate at HVN

**Trading tip**: Set your stop-loss just below HVN for support.

---

### 4. Low Volume Nodes (LVN)
**What it is**: Price levels with very little volume (thin areas on the profile).

**Why it matters**:
- Price moves FAST through LVN areas (no one wanted to trade there)
- LVN creates "air pockets" - easy for price to gap through
- Often leads to breakouts or breakdowns

**Trading tip**: Don't place stop-loss in LVN areas - price can gap through them quickly.

---

## How to Use the API

### Basic Request (Single Day)

Get volume profile for VCB on January 15, 2024:

```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-15"
```

### Multi-Day Analysis (Date Range)

Analyze volume profile across multiple trading days:

```bash
# Weekly profile (Monday to Friday)
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&start_date=2024-01-15&end_date=2024-01-19"

# Just start_date (same as single day)
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&start_date=2024-01-15"
```

**Why use multi-day profiles?**
- Identifies **stronger** support/resistance levels (consistent across days)
- Shows **composite** POC that represents fair value over longer period
- Reduces noise from single-day anomalies

### What You Get Back

```json
{
  "data": {
    "symbol": "VCB",
    "total_volume": 15234500,

    "poc": {
      "price": 60400,
      "volume": 850000,
      "percentage": 5.58
    },

    "value_area": {
      "low": 60200,
      "high": 60600,
      "volume": 10664150,
      "percentage": 70.0
    },

    "price_range": {
      "low": 60100,
      "high": 60800,
      "spread": 700
    },

    "profile": [
      {"price": 60100, "volume": 125000, "percentage": 0.82},
      {"price": 60150, "volume": 340000, "percentage": 2.23},
      ...
    ]
  }
}
```

---

## Practical Trading Strategies

### Strategy 1: Mean Reversion to POC

**The Setup**:
- Price moved far away from POC during the day
- No major news or fundamental changes

**The Trade**:
- When price is 5%+ above POC → Consider selling/shorting
- When price is 5%+ below POC → Consider buying
- Target: Return to POC

**Example**:
```
POC = 60,400 VND
Current price = 60,800 VND (66% above POC, outside Value Area)
Action: Look for short entry, target 60,400
```

---

### Strategy 2: Support/Resistance from High Volume Nodes

**The Setup**:
- Identify HVN from previous day's profile
- Price approaching that level

**The Trade**:
- **Support**: Buy when price touches HVN from above
- **Resistance**: Sell when price touches HVN from below
- Stop-loss: Just outside the HVN zone

**Example**:
```
Yesterday's HVN at 60,200 VND (large volume)
Today's price falls to 60,250 → Watch for support
If holds above 60,200 → Buy signal
Stop-loss: 60,100 (below HVN)
```

---

### Strategy 3: Breakout Confirmation

**The Setup**:
- Price breaking above/below Value Area
- You want to confirm if breakout is real

**The Trade**:
- ✅ **Real breakout**: High volume AT the breakout level
- ❌ **False breakout**: Low volume AT the breakout level

**Example**:
```
Value Area High: 60,600 VND
Price breaks to 60,700 VND

Check volume profile at 60,700:
- If volume > 3% of total → Strong breakout, consider buying
- If volume < 1% of total → Weak breakout, likely to reverse
```

---

### Strategy 4: Gap Fill Prediction

**The Setup**:
- Price gaps up or down at open
- You want to know if gap will fill

**The Trade**:
- Check if gap area is LVN (low volume) or HVN (high volume)
- **LVN gaps** = More likely to fill (no support/resistance)
- **HVN gaps** = Less likely to fill (strong level)

**Example**:
```
Gap from 60,200 → 60,400 (200 VND gap)
Check volume in 60,200-60,400 range:
- If LVN (thin volume) → Gap likely to fill
- If HVN (thick volume) → Gap likely to hold
```

---

### Strategy 5: Intraday Range Trading

**The Setup**:
- Market is ranging (not trending)
- Value Area defines the range

**The Trade**:
- Buy near Value Area Low → Sell near Value Area High
- Repeat as long as price stays in Value Area

**Example**:
```
Value Area: 60,200 - 60,600 VND
POC: 60,400 VND

Buy zone: 60,200 - 60,300
Sell zone: 60,500 - 60,600
Stop trading if price breaks outside VA
```

---

## Vietnamese Market Patterns

### Morning Session (9:00-11:30)

**Common Pattern**: High volume in first 30 minutes

**What to look for**:
- Opening range POC often defines the day
- If morning POC holds in afternoon → Strong level
- Morning HVN becomes afternoon support/resistance

**Trading tip**: Get volume profile at 10:00 AM to identify morning POC.

---

### Lunch Break (11:30-13:00)

**Common Pattern**: Price often drifts before lunch

**What to look for**:
- Volume drops before lunch
- Price may test morning's Value Area boundaries

**Trading tip**: Avoid trading 11:00-13:00 (low liquidity).

---

### Afternoon Session (13:00-15:00)

**Common Pattern**: Volume picks up at 13:00 and 14:30

**What to look for**:
- 13:00: Test of morning POC (accept or reject?)
- 14:30-15:00: Closing rush creates new HVN

**Trading tip**:
- If afternoon rejects morning POC → Reversal likely
- If afternoon accepts morning POC → Trend continues

---

### End of Day (14:30-15:00)

**Common Pattern**: Highest volume in last 30 minutes

**What to look for**:
- Closing POC sets up tomorrow's initial reference
- Strong closes above/below Value Area = next day bias

**Trading tip**: Today's closing POC often becomes tomorrow's support/resistance.

---

## Real-World Examples

### Example 1: VCB Support Trade

**Scenario**: VCB trending down, you want to buy the dip.

**Step 1**: Get yesterday's volume profile
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-14"
```

**Step 2**: Identify HVN levels
```
Yesterday's HVN:
- 60,400 VND (5.2% of volume)
- 60,000 VND (4.8% of volume)
```

**Step 3**: Wait for price to test HVN
```
Today's price drops to 60,050 VND
Approaching HVN at 60,000
```

**Step 4**: Execute trade
```
Buy: 60,050 VND (just above HVN)
Stop-loss: 59,900 VND (below HVN)
Target: 60,400 VND (next HVN up)
Risk/Reward: 150 VND risk / 350 VND reward = 1:2.3
```

---

### Example 2: FPT Breakout Trade

**Scenario**: FPT consolidating, looking for breakout.

**Step 1**: Get today's intraday profile at 2:00 PM
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=FPT&date=2024-01-15"
```

**Step 2**: Check Value Area boundaries
```
Value Area: 95,200 - 95,800 VND
POC: 95,500 VND
Current price: 95,750 VND (near VA high)
```

**Step 3**: Confirm breakout volume
```
Volume at 95,800 level: 2.1% of total (weak)
Volume at 95,500 level: 5.8% of total (strong POC)
```

**Step 4**: Trading decision
```
⚠️ Don't chase breakout!
Volume is weak at resistance (95,800)
Strong POC below (95,500) will pull price back
Better to wait for pullback to POC at 95,500
```

---

### Example 3: HPG Gap Fill Prediction

**Scenario**: HPG gaps up at open, will it fill?

**Step 1**: Identify the gap
```
Yesterday's close: 25,400 VND
Today's open: 25,700 VND (300 VND gap)
```

**Step 2**: Get yesterday's volume profile
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=HPG&date=2024-01-14"
```

**Step 3**: Check volume in gap area (25,400-25,700)
```
Volume at 25,500: 0.8% (LVN - very low)
Volume at 25,600: 1.1% (LVN - low)
Total volume in gap: < 2% (LVN zone)
```

**Step 4**: Trading decision
```
✅ Gap likely to fill!
Gap area is LVN (no support)
Trade: Short at 25,700, target 25,400
Rationale: No volume = no support = gap fills
```

---

## Common Mistakes to Avoid

### ❌ Mistake 1: Ignoring the POC
**Problem**: Trading without checking where POC is.

**Solution**: Always check POC first - it's the most important reference point.

---

### ❌ Mistake 2: Using Wrong Timeframe
**Problem**: Using today's profile to predict next week.

**Solution**: Volume profiles are for short-term (1-2 days). For longer trades, look at weekly profiles.

---

### ❌ Mistake 3: Trading in LVN Areas
**Problem**: Placing stop-loss in low volume areas.

**Solution**: Price can gap through LVN quickly. Always use HVN for stop-loss placement.

---

### ❌ Mistake 4: Ignoring Value Area
**Problem**: Buying when price is already above Value Area.

**Solution**: Price above VA = Overbought. Wait for pullback into VA.

---

### ❌ Mistake 5: Assuming POC = Support Always
**Problem**: Buying at POC without context.

**Solution**: POC is reference, not guarantee. Check overall trend and volume confirmation.

---

## Advanced Tips

### Tip 1: Compare Multiple Days
Get profiles for last 3 days and compare:
- Consistent POC levels → Very strong support/resistance
- POC moving up daily → Uptrend
- POC moving down daily → Downtrend

```bash
# Option 1: Get composite profile for the entire range (recommended)
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&start_date=2024-01-13&end_date=2024-01-15"

# Option 2: Get individual days to compare
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-15"
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-14"
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-13"
```

---

### Tip 2: Sector Rotation Analysis
Check volume profiles across sector leaders:
- Banking: VCB, BID, CTG
- Tech: FPT, CMG
- Real Estate: VHM, VIC

If all banking stocks have POC moving up → Sector strength.

---

### Tip 3: Market Index Reference
Always check VNINDEX profile:
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VNINDEX&date=2024-01-15"
```

If VNINDEX POC breaking up → Market bullish, easier to trade long.

---

### Tip 4: Volume Percentage Threshold
Use these rules of thumb:
- Volume > 5% at a level → Very strong HVN
- Volume 3-5% → Strong HVN
- Volume 1-3% → Moderate level
- Volume < 1% → LVN (weak)

---

### Tip 5: Combine with Moving Averages
Best setup:
- Price pulls back to HVN
- HVN coincides with MA20 or MA50
- Double confirmation = Higher probability trade

---

## Quick Reference Checklist

Before every trade, ask yourself:

**✓ Where is today's POC?**
- Is price above or below POC?
- How far from POC? (>5% = extreme)

**✓ Where is Value Area?**
- Is price inside or outside VA?
- Outside VA = Reversal likely

**✓ What are the HVN levels?**
- Nearest HVN below price = Support
- Nearest HVN above price = Resistance

**✓ Any LVN between here and my target?**
- LVN = Price can move fast (good for targets)
- LVN = Don't place stops here (bad for risk)

**✓ What's the volume at my entry?**
- High volume = Strong level
- Low volume = Weak level

---

## Frequently Asked Questions

### Q1: How often should I check volume profile?
**A**:
- Day traders: Check every hour
- Swing traders: Check once per day (end of day)
- Position traders: Check weekly

---

### Q2: Does volume profile work for all stocks?
**A**: Works best for:
- ✅ High liquidity stocks (VN30)
- ✅ Large cap stocks (VCB, FPT, HPG)
- ⚠️ Less reliable for small cap (thin volume)

---

### Q3: Can I use yesterday's POC for today's trade?
**A**: Yes! Yesterday's POC often becomes today's support/resistance. Very powerful when price tests it.

---

### Q4: What if POC and VA keep changing during the day?
**A**: Normal! Profile builds during the day. Most reliable after 2:00 PM when 70% of trading is done.

---

### Q5: Should I trade based on volume profile alone?
**A**: No. Combine with:
- Trend analysis (is market going up or down?)
- Moving averages (MA20, MA50)
- Market sentiment (VNINDEX direction)
- News and fundamentals

---

### Q6: How accurate is volume profile for predicting price?
**A**: Volume profile doesn't predict, it shows you WHERE to watch:
- Support/resistance levels
- Fair value (POC)
- Areas of agreement vs disagreement
- You still need to read price action at these levels

---

### Q7: Can I use this for crypto trading?
**A**: Yes! Just add `mode=crypto`:
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=BTC&date=2024-01-15&mode=crypto"
```

---

## Getting Started (3 Steps)

### Step 1: Pick Your Stock
Choose a liquid stock you're familiar with:
- VCB (Banking)
- FPT (Technology)
- HPG (Steel)
- VIC (Real Estate)

---

### Step 2: Get Today's Profile (After 2:00 PM)
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-15"
```

---

### Step 3: Mark These Levels on Your Chart
1. **POC** (most important) - Draw horizontal line
2. **Value Area High** - Draw horizontal line
3. **Value Area Low** - Draw horizontal line
4. **Top 3 HVN levels** - Draw horizontal lines

Now watch how price reacts to these levels tomorrow!

---

## Example Daily Routine

### Morning (Before Market Open - 8:30 AM)

**1. Get yesterday's profile for your watchlist**
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-14"
curl "http://localhost:3000/analysis/volume-profile?symbol=FPT&date=2024-01-14"
curl "http://localhost:3000/analysis/volume-profile?symbol=HPG&date=2024-01-14"
```

**2. Mark key levels on your chart:**
- Yesterday's POC
- Yesterday's Value Area
- Top 3 HVN levels

**3. Set price alerts:**
- Alert when price tests yesterday's POC
- Alert when price breaks Value Area

---

### During Market (9:00 AM - 11:30 AM)

**Watch price reaction at key levels:**
- Does price respect yesterday's POC? → Level is strong
- Does price ignore yesterday's POC? → Level is weak

**No need to check profile yet** (not enough data).

---

### Lunch Break (11:30 AM - 1:00 PM)

**Optional: Check morning profile**
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-15"
```

See where morning POC formed. This often becomes afternoon reference.

---

### Afternoon Session (2:00 PM)

**Get updated profile** (70% of volume now complete):
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-15"
```

**Look for:**
- Where is POC forming today?
- Is it near yesterday's POC? (Strong level)
- Is Value Area expanding or contracting?

**Trading opportunities:**
- Price at Value Area boundaries
- Price testing POC
- Breakouts from Value Area with volume

---

### End of Day (3:30 PM)

**Get final profile:**
```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-15"
```

**Review for tomorrow:**
- Today's POC = Tomorrow's reference
- Today's HVN = Tomorrow's support/resistance
- Where did price close relative to POC and VA?
  - Close above VA = Tomorrow bias bullish
  - Close below VA = Tomorrow bias bearish
  - Close at POC = Balanced, watch for direction

---

## Success Stories Pattern

### Pattern 1: "The POC Bounce"
**Setup**: Price dumps in morning, tests yesterday's POC
**Signal**: High volume at POC + Price holds
**Result**: Bounce to Value Area High
**Profit**: 1-2%

---

### Pattern 2: "The Value Area Breakout"
**Setup**: Price consolidates in Value Area
**Signal**: Break above VA High with volume > 3%
**Result**: Run to next HVN resistance
**Profit**: 2-3%

---

### Pattern 3: "The Gap Fill"
**Setup**: Gap up/down at open into LVN
**Signal**: No volume support in gap area
**Result**: Gap fills by 2:00 PM
**Profit**: 1-2%

---

## Final Thoughts

Volume Profile is **not a magic formula** - it's a tool that shows you:
- Where traders agreed on value (POC)
- Where strong support/resistance exists (HVN)
- Where price can move fast (LVN)
- What's normal vs extreme (Value Area)

**The key to success:**
1. ✅ Study volume profile daily for 1 month
2. ✅ Mark levels on your chart every day
3. ✅ Watch how price reacts to these levels
4. ✅ Combine with other analysis (trend, MA, news)
5. ✅ Start with small positions until confident

**Remember**: The best trades happen when:
- Price is at a strong HVN level
- Volume confirms the level
- Risk/reward is at least 1:2
- Overall market trend supports your direction

---

## Need Help?

- **API Documentation**: See `VOLUME_PROFILE.md` for technical details
- **API Reference**: See `API_ANALYSIS.md` for all endpoints
- **Test the API**: Run `./scripts/test-analysis.sh`

---

**Happy Trading! 📈**

*Remember: Past volume doesn't guarantee future results. Always manage your risk and never trade with money you can't afford to lose.*

---

*Last Updated: January 2025*
*Compatible with: AIPriceAction v0.1.33+*
