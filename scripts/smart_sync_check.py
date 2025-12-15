#!/usr/bin/env python3
"""
Smart SQLite Sync Check and Partial Migration

This script:
1. Reads the last line of each CSV file
2. Checks if SQLite has matching data
3. Triggers partial migration for missing/Outdated data
"""

import sqlite3
import csv
import os
from pathlib import Path
from datetime import datetime
import sys
import subprocess

PROJECT_ROOT = Path(__file__).parent.parent
VN_DB = PROJECT_ROOT / "market_data.db"
CRYPTO_DB = PROJECT_ROOT / "crypto_data.db"
VN_CSV_DIR = PROJECT_ROOT / "market_data"
CRYPTO_CSV_DIR = PROJECT_ROOT / "crypto_data"

class SmartSyncChecker:
    def __init__(self):
        self.missing_data = []
        self.outdated_data = []

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")

    def get_csv_last_line(self, csv_file):
        """Get the last data line from CSV file"""
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                # Skip to the end and read backwards
                f.seek(0, 2)
                file_size = f.tell()
                buffer_size = 4096
                newline_char = '\n'

                # Read backwards to find the last complete line
                position = file_size
                last_line = None

                while position > 0:
                    # Read a chunk
                    read_size = min(buffer_size, position)
                    position -= read_size
                    f.seek(position)
                    chunk = f.read(read_size)

                    # Find complete lines
                    lines = chunk.split(newline_char)

                    # If we're not at the start of the file, the first line might be partial
                    if position > 0:
                        lines = lines[1:]

                    if lines:
                        # Find the last non-empty line
                        for line in reversed(lines):
                            line = line.strip()
                            if line and not line.startswith('ticker,'):
                                last_line = line
                                break

                        if last_line:
                            break

                return last_line
        except Exception as e:
            self.log(f"Error reading last line from {csv_file}: {e}", "ERROR")
            return None

    def parse_csv_line(self, line, interval):
        """Parse a CSV line into components"""
        try:
            reader = csv.reader([line])
            row = next(reader)

            if len(row) < 7:
                return None

            ticker = row[0]
            timestamp_str = row[1]

            # Parse timestamp
            if interval == '1D':
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d")
            else:
                if 'T' in timestamp_str:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S")
                else:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

            return {
                'ticker': ticker,
                'timestamp': timestamp,
                'timestamp_str': timestamp_str,
                'close': float(row[5]) if row[5] else 0.0,
                'volume': int(row[6]) if row[6] else 0
            }
        except Exception as e:
            if self.verbose:
                self.log(f"Error parsing CSV line '{line}': {e}", "WARNING")
            return None

    def check_sqlite_latest(self, db_path, ticker, interval):
        """Get the latest record from SQLite for a ticker/interval"""
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT timestamp, close, volume
                FROM market_data
                WHERE ticker = ? AND interval = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (ticker, interval))

            result = cursor.fetchone()
            conn.close()

            if result:
                timestamp_str, close, volume = result
                # Parse timestamp
                if '+' in timestamp_str:
                    timestamp = datetime.fromisoformat(timestamp_str.split('+')[0])
                else:
                    timestamp = datetime.fromisoformat(timestamp_str)

                return {
                    'timestamp': timestamp,
                    'timestamp_str': timestamp_str,
                    'close': close,
                    'volume': volume
                }

            return None
        except Exception as e:
            self.log(f"Error checking SQLite for {ticker}/{interval}: {e}", "ERROR")
            return None

    def check_ticker_sync(self, csv_dir, db_path, ticker, interval):
        """Check if a specific ticker/interval is in sync"""
        csv_file = csv_dir / ticker / ('1D.csv' if interval == '1D' else ('1h.csv' if interval == '1H' else '1m.csv'))

        if not csv_file.exists():
            return  # No CSV file to check

        # Get last line from CSV
        csv_last_line = self.get_csv_last_line(csv_file)
        if not csv_last_line:
            self.log(f"No data found in CSV {csv_file}", "WARNING")
            return

        csv_data = self.parse_csv_line(csv_last_line, interval)
        if not csv_data:
            self.log(f"Failed to parse CSV last line for {ticker}/{interval}", "WARNING")
            return

        # Get latest from SQLite
        sqlite_data = self.check_sqlite_latest(db_path, ticker, interval)

        if not sqlite_data:
            # Missing from SQLite entirely
            self.missing_data.append({
                'ticker': ticker,
                'interval': interval,
                'csv_latest': csv_data,
                'csv_file': csv_file
            })
            self.log(f"❌ {ticker}/{interval}: Missing from SQLite (CSV latest: {csv_data['timestamp_str']})")
        else:
            # Compare dates - allow 1 day tolerance for daily data
            date_diff = abs((csv_data['timestamp'] - sqlite_data['timestamp']).days)

            if interval == '1D':
                if date_diff > 1:  # More than 1 day difference
                    self.outdated_data.append({
                        'ticker': ticker,
                        'interval': interval,
                        'csv_latest': csv_data,
                        'sqlite_latest': sqlite_data,
                        'csv_file': csv_file
                    })
                    self.log(f"⚠️  {ticker}/{interval}: Outdated (CSV: {csv_data['timestamp_str']}, SQLite: {sqlite_data['timestamp_str']})")
            else:
                # For hourly/minute, check if same day
                if csv_data['timestamp'].date() != sqlite_data['timestamp'].date():
                    self.outdated_data.append({
                        'ticker': ticker,
                        'interval': interval,
                        'csv_latest': csv_data,
                        'sqlite_latest': sqlite_data,
                        'csv_file': csv_file
                    })
                    self.log(f"⚠️  {ticker}/{interval}: Outdated (CSV: {csv_data['timestamp_str']}, SQLite: {sqlite_data['timestamp_str']})")

    def check_directory_sync(self, csv_dir, db_path, dir_name):
        """Check sync status for a directory"""
        self.log(f"\n=== Checking {dir_name} Sync ===")

        if not csv_dir.exists():
            self.log(f"CSV directory not found: {csv_dir}", "ERROR")
            return

        if not db_path.exists():
            self.log(f"Database not found: {db_path}", "ERROR")
            return

        # Get all ticker directories
        ticker_dirs = [d for d in csv_dir.iterdir() if d.is_dir()]
        self.log(f"Found {len(ticker_dirs)} ticker directories")

        total_checked = 0
        for ticker_dir in ticker_dirs:
            ticker = ticker_dir.name

            # Check all intervals
            for interval in ['1D', '1H', '1m']:
                csv_file = ticker_dir / ('1D.csv' if interval == '1D' else ('1h.csv' if interval == '1H' else '1m.csv'))
                if csv_file.exists():
                    self.check_ticker_sync(csv_dir, db_path, ticker, interval)
                    total_checked += 1

        self.log(f"Checked {total_checked} ticker/interval combinations")

    def trigger_partial_migration(self, missing_items, db_path):
        """Trigger partial migration for missing data"""
        if not missing_items:
            return

        self.log(f"\n=== Triggering Partial Migration for {len(missing_items)} items ===")

        # Group by interval for efficient migration
        by_interval = {}
        for item in missing_items:
            interval = item['interval']
            if interval not in by_interval:
                by_interval[interval] = []
            by_interval[interval].append(item)

        for interval, items in by_interval.items():
            self.log(f"Migrating {interval} data for {len(items)} tickers")

            # Build migration command
            csv_files = [str(item['csv_file']) for item in items]

            # Convert interval naming
            interval_arg = interval
            if interval == '1H':
                interval_arg = '1h'
            elif interval == '1m':
                interval_arg = '1m'

            cmd = [
                './target/release/migrate_to_sqlite',
                '--csv-dir', str(csv_files[0]).replace(f'/{items[0]["ticker"]}/{items[0]["csv_file"].name}', ''),
                '--db', str(db_path),
                '--batch-size', '1000',
                '--interval', interval_arg
            ]

            try:
                self.log(f"Running: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

                if result.returncode == 0:
                    self.log(f"✅ Migration completed for {interval}")
                else:
                    self.log(f"❌ Migration failed for {interval}: {result.stderr}", "ERROR")
            except subprocess.TimeoutExpired:
                self.log(f"⏰ Migration timeout for {interval}", "ERROR")
            except Exception as e:
                self.log(f"❌ Migration error for {interval}: {e}", "ERROR")

    def run_check(self, verbose=False):
        """Run the complete sync check"""
        self.verbose = verbose

        self.log("=" * 60)
        self.log("SMART SQLITE SYNC CHECK")
        self.log("=" * 60)

        # Check VN data
        self.check_directory_sync(VN_CSV_DIR, VN_DB, "VN")

        # Check Crypto data
        self.check_directory_sync(CRYPTO_CSV_DIR, CRYPTO_DB, "Crypto")

        # Summary
        self.log(f"\n=== SUMMARY ===")
        self.log(f"Missing data items: {len(self.missing_data)}")
        self.log(f"Outdated data items: {len(self.outdated_data)}")

        if self.missing_data:
            self.log(f"\nMissing data details:")
            for item in self.missing_data[:10]:
                self.log(f"  - {item['ticker']}/{item['interval']}")
            if len(self.missing_data) > 10:
                self.log(f"  ... and {len(self.missing_data) - 10} more")

        # Trigger migration for missing data
        if self.missing_data:
            self.trigger_partial_migration(self.missing_data, VN_DB)
            # Note: Add crypto migration if needed

        return len(self.missing_data) == 0 and len(self.outdated_data) == 0

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Smart SQLite sync check")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--vn-only", action="store_true", help="Check VN data only")
    parser.add_argument("--crypto-only", action="store_true", help="Check crypto data only")
    parser.add_argument("--check-only", action="store_true", help="Only check, don't migrate")

    args = parser.parse_args()

    checker = SmartSyncChecker()

    try:
        # Modify run_check to accept arguments
        if args.vn_only:
            checker.check_directory_sync(VN_CSV_DIR, VN_DB, "VN")
        elif args.crypto_only:
            checker.check_directory_sync(CRYPTO_CSV_DIR, CRYPTO_DB, "Crypto")
        else:
            checker.check_directory_sync(VN_CSV_DIR, VN_DB, "VN")
            checker.check_directory_sync(CRYPTO_CSV_DIR, CRYPTO_DB, "Crypto")

        # Summary
        print(f"\nMissing data items: {len(checker.missing_data)}")
        print(f"Outdated data items: {len(checker.outdated_data)}")

        if not args.check_only and checker.missing_data:
            checker.trigger_partial_migration(checker.missing_data, VN_DB)

        # Exit code
        if checker.missing_data or checker.outdated_data:
            sys.exit(1)
        else:
            print("✅ All data is in sync!")
            sys.exit(0)

    except KeyboardInterrupt:
        print("\nCheck interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()