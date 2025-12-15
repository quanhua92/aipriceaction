#!/usr/bin/env python3
"""
SQLite to CSV Sync Verification Script

This script compares both SQLite databases (market_data.db and crypto_data.db)
with their corresponding CSV folders to verify they are in sync.

Usage:
    python scripts/verify_sqlite_sync.py [--verbose] [--details]
"""

import sqlite3
import os
import csv
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import sys

# Data directories
PROJECT_ROOT = Path(__file__).parent.parent
VN_CSV_DIR = PROJECT_ROOT / "market_data"
CRYPTO_CSV_DIR = PROJECT_ROOT / "crypto_data"
VN_DB = PROJECT_ROOT / "market_data.db"
CRYPTO_DB = PROJECT_ROOT / "crypto_data.db"

# CSV intervals mapping
INTERVAL_MAP = {
    '1D': '1D.csv',
    '1H': '1h.csv',  # SQLite uses 1H, CSV uses 1h
    '1m': '1m.csv',  # SQLite uses 1m, CSV uses 1m
}

class SyncVerifier:
    def __init__(self, verbose=False, details=False):
        self.verbose = verbose
        self.details = details
        self.errors = []
        self.warnings = []

    def log(self, message, level="INFO"):
        """Log message with timestamp"""
        if self.verbose or level in ["ERROR", "WARNING", "SUCCESS"]:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] {level}: {message}")

    def get_sqlite_data(self, db_path, ticker=None, interval=None):
        """Get data from SQLite database"""
        if not db_path.exists():
            self.log(f"Database not found: {db_path}", "ERROR")
            return {}

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            query = "SELECT ticker, interval, COUNT(*) as count, "
            query += "MIN(timestamp) as earliest, MAX(timestamp) as latest "
            query += "FROM market_data WHERE 1=1"

            params = []
            if ticker:
                query += " AND ticker = ?"
                params.append(ticker)
            if interval:
                query += " AND interval = ?"
                params.append(interval)

            query += " GROUP BY ticker, interval ORDER BY ticker, interval"

            cursor = conn.execute(query, params)
            data = {}
            for row in cursor.fetchall():
                key = (row['ticker'], row['interval'])
                data[key] = {
                    'count': row['count'],
                    'earliest': row['earliest'],
                    'latest': row['latest']
                }

            conn.close()
            return data

        except Exception as e:
            self.log(f"Error reading SQLite database {db_path}: {e}", "ERROR")
            return {}

    def get_csv_data(self, csv_dir, ticker=None, interval=None):
        """Get data from CSV files"""
        data = {}
        csv_path = Path(csv_dir)

        if not csv_path.exists():
            self.log(f"CSV directory not found: {csv_path}", "ERROR")
            return data

        # Determine which tickers to check
        if ticker:
            tickers = [ticker]
        else:
            tickers = [d.name for d in csv_path.iterdir() if d.is_dir() and d.name != 'archive']

        for ticker_name in tickers:
            ticker_dir = csv_path / ticker_name
            if not ticker_dir.exists():
                continue

            # Check intervals
            intervals_to_check = [interval] if interval else INTERVAL_MAP.keys()

            for interval_key in intervals_to_check:
                csv_file = ticker_dir / INTERVAL_MAP[interval_key]
                if not csv_file.exists():
                    continue

                try:
                    count = 0
                    earliest = None
                    latest = None

                    with open(csv_file, 'r', newline='', encoding='utf-8') as f:
                        # Increase field size limit to handle large CSV files
                        csv.field_size_limit(1000000)
                        reader = csv.reader(f)
                        header = next(reader)  # Skip header

                        for row in reader:
                            if len(row) < 2:  # Skip invalid rows
                                continue

                            count += 1
                            timestamp_str = row[1].strip()

                            # Parse timestamp based on interval
                            try:
                                if interval_key == '1D':
                                    # Daily: "2024-01-01"
                                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d")
                                else:
                                    # Hourly/Minute: "2024-01-01 09:00:00" or "2024-01-01T09:00:00"
                                    if 'T' in timestamp_str:
                                        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S")
                                    else:
                                        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

                                if earliest is None or timestamp < earliest:
                                    earliest = timestamp
                                if latest is None or timestamp > latest:
                                    latest = timestamp

                            except ValueError as e:
                                if self.details:
                                    self.log(f"Skipping invalid timestamp {timestamp_str} in {csv_file.name}: {e}", "WARNING")
                                continue

                    key = (ticker_name, interval_key)
                    data[key] = {
                        'count': count,
                        'earliest': earliest,
                        'latest': latest
                    }

                except Exception as e:
                    self.log(f"Error reading CSV {csv_file}: {e}", "ERROR")

        return data

    def compare_data(self, sqlite_data, csv_data, data_type=""):
        """Compare SQLite and CSV data"""
        sqlite_keys = set(sqlite_data.keys())
        csv_keys = set(csv_data.keys())

        # Find missing in SQLite
        missing_in_sqlite = csv_keys - sqlite_keys
        for key in missing_in_sqlite:
            ticker, interval = key
            self.errors.append(f"{data_type} {ticker}/{interval}: Found in CSV but missing in SQLite")

        # Find missing in CSV
        missing_in_csv = sqlite_keys - csv_keys
        for key in missing_in_csv:
            ticker, interval = key
            self.warnings.append(f"{data_type} {ticker}/{interval}: Found in SQLite but missing in CSV")

        # Compare common data
        common_keys = sqlite_keys & csv_keys
        mismatches = []

        for key in common_keys:
            ticker, interval = key
            sqlite_info = sqlite_data[key]
            csv_info = csv_data[key]

            # Check count difference
            if sqlite_info['count'] != csv_info['count']:
                mismatches.append({
                    'ticker': ticker,
                    'interval': interval,
                    'type': 'count',
                    'sqlite': sqlite_info['count'],
                    'csv': csv_info['count']
                })

            # Check date range (only if both have data)
            if sqlite_info['earliest'] and csv_info['earliest']:
                # Parse SQLite timestamp (ISO format with potential timezone)
                sqlite_earliest_str = sqlite_info['earliest'].replace('Z', '+00:00')
                sqlite_latest_str = sqlite_info['latest'].replace('Z', '+00:00')

                try:
                    if '+' in sqlite_earliest_str:
                        sqlite_earliest = datetime.fromisoformat(sqlite_earliest_str.split('+')[0])
                    else:
                        sqlite_earliest = datetime.fromisoformat(sqlite_earliest_str)

                    if '+' in sqlite_latest_str:
                        sqlite_latest = datetime.fromisoformat(sqlite_latest_str.split('+')[0])
                    else:
                        sqlite_latest = datetime.fromisoformat(sqlite_latest_str)

                    # Compare dates only (ignore time differences)
                    sqlite_earliest_date = sqlite_earliest.date()
                    sqlite_latest_date = sqlite_latest.date()
                    csv_earliest_date = csv_info['earliest'].date()
                    csv_latest_date = csv_info['latest'].date()

                    if sqlite_earliest_date != csv_earliest_date:
                        mismatches.append({
                            'ticker': ticker,
                            'interval': interval,
                            'type': 'earliest_date',
                            'sqlite': sqlite_earliest_date.strftime('%Y-%m-%d'),
                            'csv': csv_earliest_date.strftime('%Y-%m-%d')
                        })

                    if sqlite_latest_date != csv_latest_date:
                        mismatches.append({
                            'ticker': ticker,
                            'interval': interval,
                            'type': 'latest_date',
                            'sqlite': sqlite_latest_date.strftime('%Y-%m-%d'),
                            'csv': csv_latest_date.strftime('%Y-%m-%d')
                        })
                except Exception as e:
                    if self.details:
                        self.log(f"Date comparison failed for {ticker}/{interval}: {e}", "WARNING")

        return mismatches

    def verify_vn_data(self):
        """Verify Vietnamese stock data"""
        self.log("=== Verifying VN Market Data ===")

        sqlite_data = self.get_sqlite_data(VN_DB)
        csv_data = self.get_csv_data(VN_CSV_DIR)

        self.log(f"SQLite: Found {len(sqlite_data)} ticker/interval combinations")
        self.log(f"CSV: Found {len(csv_data)} ticker/interval combinations")

        mismatches = self.compare_data(sqlite_data, csv_data, "VN")

        if mismatches:
            self.log(f"Found {len(mismatches)} data mismatches in VN data:", "WARNING")
            for m in mismatches[:10]:  # Show first 10
                if m['type'] == 'count':
                    self.log(f"  {m['ticker']}/{m['interval']}: SQLite={m['sqlite']} records, CSV={m['csv']} records", "WARNING")
                else:
                    self.log(f"  {m['ticker']}/{m['interval']}: {m['type']} SQLite={m['sqlite']}, CSV={m['csv']}", "WARNING")

            if len(mismatches) > 10:
                self.log(f"  ... and {len(mismatches) - 10} more mismatches", "WARNING")
        else:
            self.log("VN data: ✅ SQLite and CSV are in sync", "SUCCESS")

        return len(mismatches) == 0

    def verify_crypto_data(self):
        """Verify cryptocurrency data"""
        self.log("\n=== Verifying Crypto Data ===")

        sqlite_data = self.get_sqlite_data(CRYPTO_DB)
        csv_data = self.get_csv_data(CRYPTO_CSV_DIR)

        self.log(f"SQLite: Found {len(sqlite_data)} ticker/interval combinations")
        self.log(f"CSV: Found {len(csv_data)} ticker/interval combinations")

        mismatches = self.compare_data(sqlite_data, csv_data, "Crypto")

        if mismatches:
            self.log(f"Found {len(mismatches)} data mismatches in crypto data:", "WARNING")
            for m in mismatches[:10]:  # Show first 10
                if m['type'] == 'count':
                    self.log(f"  {m['ticker']}/{m['interval']}: SQLite={m['sqlite']} records, CSV={m['csv']} records", "WARNING")
                else:
                    self.log(f"  {m['ticker']}/{m['interval']}: {m['type']} SQLite={m['sqlite']}, CSV={m['csv']}", "WARNING")

            if len(mismatches) > 10:
                self.log(f"  ... and {len(mismatches) - 10} more mismatches", "WARNING")
        else:
            self.log("Crypto data: ✅ SQLite and CSV are in sync", "SUCCESS")

        return len(mismatches) == 0

    def print_summary(self, vn_sync, crypto_sync):
        """Print final summary"""
        self.log("\n" + "="*50)
        self.log("SYNC VERIFICATION SUMMARY", "INFO")
        self.log("="*50)

        self.log(f"VN Market Data: {'✅ IN SYNC' if vn_sync else '❌ OUT OF SYNC'}")
        self.log(f"Crypto Data: {'✅ IN SYNC' if crypto_sync else '❌ OUT OF SYNC'}")

        if self.errors:
            self.log(f"\n❌ Errors found: {len(self.errors)}", "ERROR")
            for error in self.errors[:5]:
                self.log(f"  - {error}", "ERROR")
            if len(self.errors) > 5:
                self.log(f"  ... and {len(self.errors) - 5} more errors", "ERROR")

        if self.warnings:
            self.log(f"\n⚠️  Warnings: {len(self.warnings)}", "WARNING")
            for warning in self.warnings[:5]:
                self.log(f"  - {warning}", "WARNING")
            if len(self.warnings) > 5:
                self.log(f"  ... and {len(self.warnings) - 5} more warnings", "WARNING")

        if vn_sync and crypto_sync and not self.errors:
            self.log("\n🎉 All databases are fully in sync with CSV files!", "SUCCESS")
        else:
            self.log("\n💡 Some sync issues detected. Check details above.", "INFO")

def main():
    parser = argparse.ArgumentParser(description="Verify SQLite to CSV sync")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--details", "-d", action="store_true", help="Show detailed parsing issues")
    parser.add_argument("--vn-only", action="store_true", help="Check VN data only")
    parser.add_argument("--crypto-only", action="store_true", help="Check crypto data only")

    args = parser.parse_args()

    verifier = SyncVerifier(verbose=args.verbose, details=args.details)

    try:
        vn_sync = True
        crypto_sync = True

        if not args.crypto_only:
            vn_sync = verifier.verify_vn_data()

        if not args.vn_only:
            crypto_sync = verifier.verify_crypto_data()

        verifier.print_summary(vn_sync, crypto_sync)

        # Exit with error code if sync issues detected
        if not vn_sync or not crypto_sync or verifier.errors:
            sys.exit(1)
        else:
            sys.exit(0)

    except KeyboardInterrupt:
        verifier.log("\nVerification interrupted by user", "WARNING")
        sys.exit(130)
    except Exception as e:
        verifier.log(f"Unexpected error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()