#!/usr/bin/env tsx

/**
 * CoinMarketCap Top 100 Crypto Scraper
 *
 * Fetches the top 100 cryptocurrencies by market cap from CoinMarketCap
 * and saves them to a JSON file.
 *
 * Usage:
 *   npx tsx fetch-top-100.ts
 */

import { chromium } from 'playwright';
import { writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Cryptos to ignore (no reliable CryptoCompare API data)
const IGNORED_CRYPTOS = ['MNT', 'IOTA'];

interface Crypto {
  rank: number;
  name: string;
  symbol: string;
  price: string;
  change_24h: string;
  change_7d: string;
  market_cap: string;
  volume_24h: string;
  circulating_supply: string;
}

async function fetchTop100Cryptos(): Promise<Crypto[]> {
  console.log('🚀 Launching browser...');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();

  try {
    console.log('📡 Navigating to CoinMarketCap...');
    await page.goto('https://coinmarketcap.com/', {
      waitUntil: 'domcontentloaded',
      timeout: 60000
    });

    // Wait for the table to load
    console.log('⏳ Waiting for crypto table to load...');
    await page.waitForSelector('table tbody tr', { timeout: 30000 });

    // Scroll down to load all 100 rows (CoinMarketCap lazy loads)
    console.log('📜 Scrolling to load all 100 entries...');

    // Scroll to bottom first
    await page.evaluate(() => {
      window.scrollTo(0, document.body.scrollHeight);
    });
    await page.waitForTimeout(3000);

    // Then scroll back up to ensure all content is loaded
    await page.evaluate(() => {
      window.scrollTo(0, 0);
    });
    await page.waitForTimeout(1000);

    // Scroll down slowly to trigger lazy loading
    for (let i = 0; i < 15; i++) {
      await page.evaluate((step) => {
        const row = document.querySelectorAll('table tbody tr')[Math.min(step * 7, 99)];
        row?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, i);
      await page.waitForTimeout(300);
    }

    await page.waitForTimeout(3000);

    const rowCount = await page.evaluate(() => document.querySelectorAll('table tbody tr').length);
    console.log(`   Loaded ${rowCount} rows`);

    // Extract cryptocurrency data
    console.log('🔍 Extracting data...');
    const cryptos = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll('table tbody tr'));
      const data: Crypto[] = [];
      const skipped: number[] = [];

      for (let i = 0; i < Math.min(rows.length, 100); i++) {
        try {
          const row = rows[i];
          const cells = row.querySelectorAll('td');
          if (cells.length < 9) {
            skipped.push(i + 1);
            continue;
          }

          // Rank - second cell (first is star icon)
          const rankText = cells[1]?.textContent?.trim() || '';
          const rank = rankText ? parseInt(rankText) : 0;
          if (!rank || isNaN(rank)) {
            skipped.push(i + 1);
            continue;
          }

          // Name and Symbol - third cell (has multiple <p> tags)
          const nameSymbolCell = cells[2];
          const allText = nameSymbolCell?.textContent?.trim() || '';
          const paragraphs = nameSymbolCell?.querySelectorAll('p');

          let name = '';
          let symbol = '';

          if (paragraphs && paragraphs.length >= 2) {
            name = paragraphs[0]?.textContent?.trim() || '';
            symbol = paragraphs[1]?.textContent?.trim() || '';
          } else if (allText) {
            // Fallback: try to split the text
            const parts = allText.split('\n').map(s => s.trim()).filter(Boolean);
            name = parts[0] || '';
            symbol = parts[1] || '';
          }

          if (!name || !symbol) {
            skipped.push(i + 1);
            continue;
          }

          // Price - fourth cell (first span only)
          const priceSpan = cells[3]?.querySelector('span');
          const price = priceSpan?.textContent?.trim() || cells[3]?.textContent?.trim() || 'N/A';

          // 24h Change - fifth cell (first span only)
          const change24hSpan = cells[4]?.querySelector('span');
          const change_24h = change24hSpan?.textContent?.trim() || cells[4]?.textContent?.trim() || 'N/A';

          // 7d Change - sixth cell (first span only)
          const change7dSpan = cells[5]?.querySelector('span');
          const change_7d = change7dSpan?.textContent?.trim() || cells[5]?.textContent?.trim() || 'N/A';

          // Market Cap - seventh cell
          // Extract all text and find the market cap value ($X.XXT format)
          const marketCapFullText = cells[6]?.textContent?.trim() || '';
          const marketCapParts = marketCapFullText.split(/\s+/).filter(part => part.startsWith('$'));
          // Take the one with T, B, or M suffix (not just $ with percentage)
          const market_cap = marketCapParts.find(p => /\$[\d,.]+[TBM]/.test(p)) || 'N/A';

          // Volume 24h - eighth cell (first <p> or <a>)
          const volumePara = cells[7]?.querySelector('p, a');
          // Extract just the number, not percentage
          const volumeFullText = cells[7]?.textContent?.trim() || 'N/A';
          const volumeMatch = volumeFullText.match(/\$[\d,\.]+[KMBT]?/);
          const volume_24h = volumeMatch ? volumeMatch[0] : volumePara?.textContent?.trim() || 'N/A';

          // Circulating Supply - ninth cell (first <p>)
          const supplyPara = cells[8]?.querySelector('p');
          const supplyText = supplyPara?.textContent?.trim() || cells[8]?.textContent?.trim() || 'N/A';
          // Extract just the supply number with unit
          const supplyMatch = supplyText.match(/[\d,\.]+\s*[A-Z]+/);
          const circulating_supply = supplyMatch ? supplyMatch[0] : supplyText;

          data.push({
            rank,
            name,
            symbol,
            price,
            change_24h,
            change_7d,
            market_cap,
            volume_24h,
            circulating_supply
          });
        } catch (err) {
          // Silently skip problematic rows
        }
      }

      return { data, skipped };
    });

    if (cryptos.skipped.length > 0) {
      console.log(`   Skipped ${cryptos.skipped.length} rows: ${cryptos.skipped.slice(0, 10).join(', ')}${cryptos.skipped.length > 10 ? '...' : ''}`);
    }

    console.log(`✅ Successfully extracted ${cryptos.data.length} cryptocurrencies`);
    return cryptos.data;

  } catch (error) {
    console.error('❌ Error fetching data:', error);
    throw error;
  } finally {
    await browser.close();
  }
}

async function main() {
  try {
    const startTime = Date.now();

    // Fetch data
    const cryptos = await fetchTop100Cryptos();

    if (cryptos.length === 0) {
      throw new Error('No data extracted. The page structure may have changed.');
    }

    // Filter out ignored cryptos
    const originalCount = cryptos.length;
    const filteredCryptos = cryptos.filter(c => !IGNORED_CRYPTOS.includes(c.symbol));
    const ignoredCount = originalCount - filteredCryptos.length;

    if (ignoredCount > 0) {
      const ignoredSymbols = IGNORED_CRYPTOS.filter(s => cryptos.some(c => c.symbol === s));
      console.log(`\nℹ️  Filtered out ${ignoredCount} ignored crypto(s) (${ignoredSymbols.join(', ')}) - no reliable CryptoCompare API data`);
    }

    // Prepare output
    const output = {
      fetched_at: new Date().toISOString(),
      count: filteredCryptos.length,
      data: filteredCryptos
    };

    // Save to JSON file in project root (two levels up from scripts/crypto/)
    const outputPath = join(__dirname, '../../crypto_top_100.json');
    writeFileSync(outputPath, JSON.stringify(output, null, 2));

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(2);

    console.log('\n📊 Summary:');
    console.log(`   Total cryptocurrencies: ${filteredCryptos.length}`);
    console.log(`   Time taken: ${elapsed}s`);
    console.log(`   Output file: ${outputPath}`);
    console.log('\n🎉 Done!\n');

    // Print top 10 preview
    console.log('Top 10 Preview:');
    filteredCryptos.slice(0, 10).forEach(crypto => {
      console.log(`${crypto.rank}. ${crypto.name} (${crypto.symbol}) - ${crypto.price} (${crypto.change_24h})`);
    });

  } catch (error) {
    console.error('❌ Fatal error:', error);
    process.exit(1);
  }
}

main();
