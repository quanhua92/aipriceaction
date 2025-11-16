# CoinMarketCap Top 100 Crypto Scraper

A TypeScript/Playwright script that scrapes the top 100 cryptocurrencies by market cap from [CoinMarketCap](https://coinmarketcap.com/) and saves them to JSON.

## Features

- 🚀 Headless browser scraping with Playwright
- 📊 Extracts complete data: rank, name, symbol, price, changes, market cap, volume, supply
- 💾 Saves to structured JSON file
- 🎯 Handles lazy loading and dynamic content
- ⚡ Fast and reliable

## Installation

```bash
cd scripts/crypto

# Install dependencies
npm install

# Install Chromium browser (required by Playwright)
npm run install-browsers
```

## Usage

### Quick Start

```bash
# Run the scraper
npm run fetch

# Or use tsx directly
npx tsx fetch-top-100.ts
```

### Output

The script generates `crypto_top_100.json` with the following structure:

```json
{
  "fetched_at": "2025-11-16T10:30:00.000Z",
  "count": 100,
  "data": [
    {
      "rank": 1,
      "name": "Bitcoin",
      "symbol": "BTC",
      "price": "$95,879.11",
      "change_24h": "+0.56%",
      "change_7d": "+2.34%",
      "market_cap": "$1,910,000,000,000",
      "volume_24h": "$49,360,000,000",
      "circulating_supply": "19,234,567 BTC"
    },
    {
      "rank": 2,
      "name": "Ethereum",
      "symbol": "ETH",
      "price": "$3,206.30",
      "change_24h": "+0.81%",
      "change_7d": "+1.45%",
      "market_cap": "$387,210,000,000",
      "volume_24h": "$19,300,000,000",
      "circulating_supply": "120,567,890 ETH"
    }
    // ... 98 more entries
  ]
}
```

## Example Output

```
🚀 Launching browser...
📡 Navigating to CoinMarketCap...
⏳ Waiting for crypto table to load...
📜 Scrolling to load all 100 entries...
🔍 Extracting data...
✅ Successfully extracted 100 cryptocurrencies

📊 Summary:
   Total cryptocurrencies: 100
   Time taken: 12.34s
   Output file: /path/to/crypto_top_100.json

🎉 Done!

Top 10 Preview:
1. Bitcoin (BTC) - $95,879.11 (+0.56%)
2. Ethereum (ETH) - $3,206.30 (+0.81%)
3. Tether (USDT) - $0.9993 (+0.01%)
4. XRP (XRP) - $2.26 (+0.81%)
5. BNB (BNB) - $946.31 (+1.37%)
6. Solana (SOL) - $141.86 (+0.81%)
7. USDC (USDC) - $0.9999 (+0.01%)
8. TRON (TRX) - $0.2972 (+0.97%)
9. Dogecoin (DOGE) - $0.1636 (+0.26%)
10. Cardano (ADA) - $0.5029 (+1.73%)
```

## Data Fields

Each cryptocurrency entry includes:

- **rank**: Position in market cap ranking (1-100)
- **name**: Full name (e.g., "Bitcoin")
- **symbol**: Ticker symbol (e.g., "BTC")
- **price**: Current price in USD
- **change_24h**: 24-hour price change percentage
- **change_7d**: 7-day price change percentage
- **market_cap**: Total market capitalization
- **volume_24h**: 24-hour trading volume
- **circulating_supply**: Circulating supply with unit

## Troubleshooting

### Browser Installation Issues

```bash
# Manually install Chromium
npx playwright install chromium

# Or install all browsers
npx playwright install
```

### Scraping Errors

If the script fails with extraction errors, the CoinMarketCap page structure may have changed. Check:

1. CSS selectors in `fetch-top-100.ts`
2. Wait times for dynamic content
3. Table structure on the website

### Timeout Errors

If the page takes too long to load:

1. Increase timeout in `page.waitForSelector()` (currently 30s)
2. Check your internet connection
3. CoinMarketCap may be rate-limiting requests

## Advanced Usage

### Run with Visible Browser (Debug Mode)

Edit `fetch-top-100.ts` and change:
```typescript
const browser = await chromium.launch({ headless: false }); // Show browser
```

### Customize Output Path

Modify the output path in the script:
```typescript
const outputPath = join('/custom/path', 'crypto_top_100.json');
```

### Automate with Cron

Run periodically (e.g., every hour):
```bash
# Add to crontab
0 * * * * cd /path/to/scripts/crypto && npm run fetch
```

## Dependencies

- **playwright**: ^1.48.0 - Browser automation
- **tsx**: ^4.19.0 - TypeScript execution
- **typescript**: ^5.6.0 - TypeScript compiler

## License

MIT
