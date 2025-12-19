#!/usr/bin/env python3
"""
Fetch all top cryptocurrency historical data from Binance Vision (zipped format)

This script reads top_crypto.json (from CoinGecko) and downloads
historical zipped data for each cryptocurrency from Binance Vision API.

Usage:
    python scripts/fetch_all_crypto.py

The data will be saved to the spot/ directory with subdirectories for each symbol.
Downloads all timeframes (1d, 1h, 1m) in zip format - extraction done when needed.
"""

import json
import os
import sys
import requests
import signal
from datetime import datetime
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Event

# Global event for graceful shutdown
shutdown_event = Event()

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    print("\n\n🛑 Shutdown signal received! Stopping all downloads...")
    shutdown_event.set()
    sys.exit(0)

def load_top_crypto():
    """Load top crypto symbols from top_crypto.json"""
    try:
        # Use test file if exists, otherwise use full file
        filename = 'top_crypto_test.json' if os.path.exists('top_crypto_test.json') else 'top_crypto.json'
        with open(filename, 'r') as f:
            data = json.load(f)

        symbols = []
        for crypto in data['data']:
            symbol = crypto['symbol']
            # Use binance_symbol directly from top_crypto.json if available, otherwise fall back to conversion
            if 'binance_symbol' in crypto:
                binance_symbol = crypto['binance_symbol']
            else:
                # Legacy fallback for backward compatibility
                if symbol in ['USDT', 'USDC', 'BUSD', 'DAI']:
                    # Stablecoins - try USD pairs or other cryptos
                    binance_symbol = f"{symbol}USD"
                elif symbol in ['BTC', 'ETH']:
                    # Major cryptos trade against USDT
                    binance_symbol = f"{symbol}USDT"
                elif symbol.endswith('USD') or symbol.endswith('USDT'):
                    # Already has USD/USDT suffix
                    binance_symbol = symbol
                else:
                    # Most cryptos trade against USDT
                    binance_symbol = f"{symbol}USDT"

            symbols.append({
                'original': symbol,
                'binance_symbol': binance_symbol,
                'name': crypto['name'],
                'rank': crypto['rank'],
                'market_cap': crypto.get('market_cap', 'N/A')
            })

        print(f"📋 Loaded {len(symbols)} crypto symbols from top_crypto.json")
        return symbols

    except FileNotFoundError:
        print("❌ Error: top_crypto.json not found!")
        print("💡 Run 'python scripts/get_top_crypto.py' first to generate the file")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error loading crypto symbols: {str(e)}")
        sys.exit(1)

def download_zip_file(zip_url, local_path):
    """Download a zip file from URL with temporary extension"""
    try:
        print(f"⬇️  Downloading {os.path.basename(local_path)}...")

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # Use temporary .downloading extension
        temp_path = f"{local_path}.downloading"

        # Remove any existing temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)

        # Download the file to temporary location
        response = requests.get(zip_url, timeout=30, stream=True)
        response.raise_for_status()

        # Stream download to handle large files
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Rename to final .zip only after successful download
        os.rename(temp_path, local_path)

        file_size = os.path.getsize(local_path)
        print(f"✅ Downloaded {os.path.basename(local_path)} ({file_size:,} bytes)")
        return True

    except Exception as e:
        # Clean up temp file on error
        temp_path = f"{local_path}.downloading"
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"❌ Error downloading {zip_url}: {str(e)}")
        return False


def download_single_date(symbol, timeframe, date_str, base_url, symbol_dir, progress_lock):
    """Download a single date/timeframe combination (thread-safe)"""
    # Check for shutdown signal
    if shutdown_event.is_set():
        return False, date_str

    zip_filename = f"{symbol}-{timeframe}-{date_str}.zip"
    zip_url = f"{base_url}{symbol}/{timeframe}/{zip_filename}"
    local_zip_path = f"{symbol_dir}/{zip_filename}"

    # Skip if already exists and log it
    if os.path.exists(local_zip_path):
        file_size = os.path.getsize(local_zip_path)
        print(f"⏭️  {symbol}/{timeframe}: {date_str} - EXISTS ({file_size:,} bytes)")
        return True, date_str

    # Debug: log URL and response code for every download (unlimited logging)
    print(f"⬇️  {symbol}/{timeframe}: {date_str} - DOWNLOADING")
    print(f"    URL: {zip_url}")

    # Remove any existing .downloading file
    temp_path = f"{local_zip_path}.downloading"
    if os.path.exists(temp_path):
        os.remove(temp_path)

    # Download the zip file
    try:
        response = requests.get(zip_url, timeout=30, stream=True)

        # Log HTTP status code for EVERY request
        status_code = response.status_code
        print(f"📡 {symbol}/{timeframe}: {date_str} - HTTP {status_code}")

        if status_code != 200:
            print(f"❌ FAILED {symbol}/{timeframe}: {date_str} - HTTP {status_code} - {response.reason}")
            # Special return value for 404 to signal stop processing this symbol
            if status_code == 404:
                return "STOP_404", date_str
            return False, date_str

        # Check for common rate limiting headers
        if 'X-RateLimit-Remaining' in response.headers:
            rate_limit = response.headers.get('X-RateLimit-Remaining', 'unknown')
            if date_str in ['2025-12-17', '2025-12-16', '2025-12-15', '2024-12-31']:
                print(f"🚦 {symbol}/{timeframe}: {date_str} - Rate limit remaining: {rate_limit}")

        response.raise_for_status()

        # Download to temp file
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Rename to final location
        os.rename(temp_path, local_zip_path)

        # Get file size and log success for EVERY file
        file_size = os.path.getsize(local_zip_path)
        print(f"✅ SAVED {symbol}/{timeframe}: {date_str} - {file_size:,} bytes")

        # Just keep the ZIP file (no extraction)
        return True, date_str

    except requests.exceptions.Timeout:
        # Clean up temp file on timeout
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"⏰ TIMEOUT {symbol}/{timeframe}: {date_str} - 30s")
        return False, date_str
    except requests.exceptions.RequestException as e:
        # Clean up temp file on network error
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"🌐 NETWORK ERROR {symbol}/{timeframe}: {date_str} - {str(e)[:50]}")
        return False, date_str
    except Exception as e:
        # Clean up temp file on other errors
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"❓ ERROR {symbol}/{timeframe}: {date_str} - {str(e)[:50]}")
        return False, date_str

def download_symbol_data(symbol_info):
    """Download all timeframes (1m, 1h, 1d) zip data for a single symbol using parallel workers"""
    symbol = symbol_info['binance_symbol']
    original = symbol_info['original']
    name = symbol_info['name']
    rank = symbol_info['rank']
    market_cap = symbol_info['market_cap']

    print(f"\n{'='*60}")
    print(f"🚀 Processing #{rank} {name} ({original} -> {symbol})")
    print(f"💰 Market Cap: {market_cap}")
    print(f"{'='*60}")

    # Binance Vision API base URL
    base_url = "https://data.binance.vision/data/spot/daily/klines/"

    # Find the latest available date by checking today, yesterday, today-2
    from datetime import date, timedelta

    def check_latest_available_date(symbol):
        """Check if today, yesterday, or today-2 has data"""
        base_url = "https://data.binance.vision/data/spot/daily/klines/"

        for days_back in [0, 1, 2]:  # today, yesterday, today-2
            test_date = date.today() - timedelta(days=days_back)
            test_url = f"{base_url}{symbol}/1d/{symbol}-1d-{test_date.strftime('%Y-%m-%d')}.zip"

            try:
                response = requests.head(test_url, timeout=10)
                if response.status_code == 200:
                    print(f"✅ Found available data for {symbol}: {test_date} (HTTP {response.status_code})")
                    return test_date
                else:
                    print(f"❌ No data for {symbol}: {test_date} (HTTP {response.status_code})")
            except Exception as e:
                print(f"❌ Error checking {symbol}: {test_date} - {str(e)[:50]}")

        print(f"⚠️  Could not find any recent data for {symbol}, using yesterday as fallback")
        return date.today() - timedelta(days=1)

    # Test with BTCUSDT to find the latest available date
    print("🔍 Checking for latest available data...")
    latest_date = check_latest_available_date("BTCUSDT")

    # Use last 7 days for testing, change to full range for production
    use_test_range = len([s for s in os.listdir('.') if s.startswith('top_crypto_test')]) > 0
    if use_test_range:
        start_date = date.today() - timedelta(days=7)  # Last 7 days for testing
        print("🧪 Using test range: Last 7 days")
    else:
        start_date = date(2017, 8, 17)  # Binance launch date for production
        print("📅 Starting from latest available date and going backwards to 2017-08-17")

    end_date = latest_date  # Use the latest available date we found
    total_days = (end_date - start_date).days + 1
    print(f"📅 Downloading from {start_date} to {end_date} ({total_days} days)")
    print(f"🎯 Latest available data found: {end_date}")

    # Download all timeframes
    timeframes = ["1d", "1h", "1m"]
    overall_success = True
    progress_lock = Lock()

    for timeframe in timeframes:
        print(f"\n📊 Downloading {timeframe} data for {symbol} (4 parallel workers)...")

        # Create timeframe directory
        symbol_dir = f"spot/daily/klines/{symbol}/{timeframe}"
        os.makedirs(symbol_dir, exist_ok=True)

        # Generate all download tasks (backwards from today to 2017)
        download_tasks = []
        current_date = end_date  # Start from yesterday
        while current_date >= start_date:
            date_str = current_date.strftime('%Y-%m-%d')
            download_tasks.append((symbol, timeframe, date_str, base_url, symbol_dir))
            current_date -= timedelta(days=1)

        # Debug: Show sample of download tasks
        if timeframe == '1d' and symbol == 'USDTUSD':  # Only for first symbol to avoid spam
            print(f"📅 Date range debug:")
            print(f"   Start: {start_date}")
            print(f"   End: {end_date}")
            print(f"   Total days: {len(download_tasks)}")
            print(f"   Sample dates: {download_tasks[0][2]}, {download_tasks[1][2]}, {download_tasks[2][2]}")
            print(f"   Last 3 dates: {download_tasks[-3][2]}, {download_tasks[-2][2]}, {download_tasks[-1][2]}")

        # Progress tracking
        successful_downloads = 0
        failed_days = []
        total_tasks = len(download_tasks)
        hit_404 = False
        first_404_date = None

        # Debug: show what we're about to do
        print(f"📋 {symbol} {timeframe}: {total_tasks} files from {end_date} backwards to {start_date}")

        # Count how many files already exist
        existing_files = 0
        for task in download_tasks:
            date_str = task[2]
            zip_filename = f"{symbol}-{timeframe}-{date_str}.zip"
            local_zip_path = f"{symbol_dir}/{zip_filename}"
            if os.path.exists(local_zip_path):
                existing_files += 1

        if existing_files > 0:
            print(f"⏭️  {existing_files}/{total_tasks} files already exist, will skip")
        else:
            print(f"🆕 All {total_tasks} files need to be downloaded")

        # Download with 4 parallel workers for maximum speed
        with ThreadPoolExecutor(max_workers=4) as executor:
            # Submit all tasks
            future_to_date = {
                executor.submit(download_single_date, *task, progress_lock): task[2]
                for task in download_tasks
            }

            # Process completed tasks
            for future in as_completed(future_to_date):
                # Check for shutdown
                if shutdown_event.is_set():
                    print(f"\n⚠️  {timeframe} download interrupted by user")
                    executor.shutdown(wait=False)
                    return False

                success, date_str = future.result()

                # Check for STOP_404 signal
                if success == "STOP_404":
                    if not hit_404:
                        hit_404 = True
                        first_404_date = date_str
                        print(f"🛑 {symbol}/{timeframe}: First 404 at {date_str} - STOPPING further downloads for this symbol")
                        # Cancel remaining futures
                        for remaining_future in future_to_date:
                            remaining_future.cancel()
                        executor.shutdown(wait=False)
                        break
                elif success:
                    successful_downloads += 1
                else:
                    failed_days.append(date_str)

                # Progress indicator (much less frequent, show both downloads and total progress)
                if successful_downloads % 200 == 0 or successful_downloads == total_tasks:
                    # Count how many files already existed
                    already_skipped = existing_files - successful_downloads
                    total_processed = successful_downloads + already_skipped
                    progress = (total_processed / total_tasks) * 100
                    print(f"📊 {timeframe} Progress: {progress:.1f}% (new: {successful_downloads}, skipped: {already_skipped}, total: {total_processed}/{total_tasks})")

                # Also show progress every 50 total files processed
                if successful_downloads % 50 == 0:
                    already_skipped = existing_files - successful_downloads
                    total_processed = successful_downloads + already_skipped
                    progress = (total_processed / total_tasks) * 100
                    print(f"📊 {timeframe} Progress: {progress:.1f}% (total: {total_processed}/{total_tasks})")

        # Report results for this timeframe
        if hit_404:
            print(f"✅ {symbol}/{timeframe}: Stopped at first 404 ({first_404_date}) - {successful_downloads} files downloaded")
        elif successful_downloads == total_tasks:
            print(f"✅ Successfully downloaded all {timeframe} data for {symbol}")
        else:
            failed_count = len(failed_days)
            print(f"⚠️  Partial {timeframe} download for {symbol}: {successful_downloads}/{total_tasks} days ({failed_count} failed)")
            if failed_days and failed_count <= 10:
                print(f"   Failed dates: {', '.join(failed_days)}")
            elif failed_count > 0:
                print(f"   First 10 failed dates: {', '.join(failed_days[:10])}")
            overall_success = False

    return overall_success

def list_downloaded_files():
    """Summary of downloaded files"""
    try:
        spot_path = "spot/daily/klines/"
        if not os.path.exists(spot_path):
            print("\n📁 No spot directory found")
            return

        total_symbols = 0
        total_zip_files = 0
        total_csv_files = 0

        # List all symbol directories
        for symbol_dir in os.listdir(spot_path):
            symbol_path = os.path.join(spot_path, symbol_dir)
            if os.path.isdir(symbol_path):
                total_symbols += 1

                # Count zip files for all timeframes
                for timeframe in ['1d', '1h', '1m']:
                    timeframe_dir = os.path.join(symbol_path, timeframe)
                    if os.path.exists(timeframe_dir):
                        zip_files = [f for f in os.listdir(timeframe_dir) if f.endswith('.zip')]
                        total_zip_files += len(zip_files)

                print(f"   📁 {symbol_dir}: ZIP files downloaded")

        print(f"\n📊 Total: {total_symbols} symbols, {total_zip_files} zip files")

    except Exception as e:
        print(f"⚠️  Error listing downloaded files: {str(e)}")

def main():
    try:
        # Register signal handler for Ctrl+C
        signal.signal(signal.SIGINT, signal_handler)

        print("🚀 Starting bulk crypto data download from Binance Vision (zip format)...")
        print(f"📁 Download directory: {os.getcwd()}")
        print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("📦 Format: ZIP files (all timeframes: 1d, 1h, 1m)")
        print("⚡ 4 parallel workers - Press Ctrl+C to stop gracefully")
        print()

        # Load top crypto symbols
        symbols = load_top_crypto()

        print(f"🎯 Processing {len(symbols)} symbols (ranked by market cap)")
        print(f"📦 Downloading all timeframes in ZIP format: 1d, 1h, 1m")
        print(f"🌐 Using Binance Vision API (4 parallel workers, no rate limits)")
        print()

        # Statistics with thread safety
        successful_symbols = 0
        failed_symbols = []
        stats_lock = Lock()

        # Process symbols sequentially (1 at a time) but with parallel downloads within each symbol
        for i, symbol_info in enumerate(symbols, 1):
            # Check for shutdown
            if shutdown_event.is_set():
                print(f"\n⚠️  Processing interrupted by user")
                break

            print(f"\n🔄 Processing symbol {i}/{len(symbols)}: {symbol_info['name']} ({symbol_info['binance_symbol']})")

            success = download_symbol_data(symbol_info)

            with stats_lock:
                if success:
                    successful_symbols += 1
                else:
                    failed_symbols.append(symbol_info['original'])

            print(f"✅ Completed {symbol_info['binance_symbol']} - {successful_symbols}/{i} symbols successful")

        # Final summary
        print(f"\n{'='*80}")
        if shutdown_event.is_set():
            print("🛑 DOWNLOAD INTERRUPTED BY USER")
        else:
            print("🎉 DOWNLOAD SUMMARY")
        print(f"{'='*80}")
        print(f"⏰ Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📊 Successfully downloaded: {successful_symbols}/{len(symbols)} symbols")
        print(f"📈 Success rate: {(successful_symbols/len(symbols)*100):.1f}%")
        print(f"📦 Format: ZIP files (all timeframes: 1d, 1h, 1m)")

        if shutdown_event.is_set():
            print("\n💡 Download was interrupted but will resume from where it left off")
            print("💡 Run the same command again to continue downloading")

        if failed_symbols:
            print(f"\n❌ Failed symbols ({len(failed_symbols)}):")
            for symbol in failed_symbols[:10]:  # Show first 10 failed symbols
                print(f"   - {symbol}")
            if len(failed_symbols) > 10:
                print(f"   ... and {len(failed_symbols) - 10} more")

        # List downloaded files
        print(f"\n📁 Downloaded files summary:")
        list_downloaded_files()

        print(f"\n✅ All done!")
        print(f"💡 ZIP files saved - extract when needed for analysis")
        print(f"💡 Data spans from 2017-08 to present ({(datetime.now().year - 2017) * 12 + (datetime.now().month - 7)}+ months)")

    except ImportError:
        print("❌ Error: requests library not found!")
        print("📦 Install it with: pip install requests")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()