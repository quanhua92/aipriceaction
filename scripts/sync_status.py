#!/usr/bin/env python3
"""
SQLite to CSV Sync Status Report

Shows current sync status between SQLite databases and CSV files
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
VN_DB = PROJECT_ROOT / "market_data.db"
CRYPTO_DB = PROJECT_ROOT / "crypto_data.db"
VN_CSV_DIR = PROJECT_ROOT / "market_data"
CRYPTO_CSV_DIR = PROJECT_ROOT / "crypto_data"

def get_sqlite_summary(db_path, db_name):
    """Get SQLite database summary"""
    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    summary = {}
    for interval in ['1D', '1H', '1m']:
        cursor.execute("SELECT COUNT(DISTINCT ticker), COUNT(*) FROM market_data WHERE interval = ?", (interval,))
        result = cursor.fetchone()
        summary[interval] = {
            'tickers': result[0] or 0,
            'records': result[1] or 0
        }

    conn.close()
    return summary

def get_csv_summary(csv_dir, dir_name):
    """Get CSV files summary"""
    if not csv_dir.exists():
        return None

    summary = {}
    ticker_dirs = [d for d in csv_dir.iterdir() if d.is_dir()]

    for interval, csv_file in [('1D', '1D.csv'), ('1H', '1h.csv'), ('1m', '1m.csv')]:
        count = 0
        total_size = 0

        for ticker_dir in ticker_dirs:
            file_path = ticker_dir / csv_file
            if file_path.exists():
                count += 1
                total_size += file_path.stat().st_size

        summary[interval] = {
            'files': count,
            'total_size_mb': total_size / (1024 * 1024)
        }

    return summary

def main():
    print("=" * 60)
    print("SQLITE TO CSV SYNC STATUS REPORT")
    print("=" * 60)

    # VN Market Data
    print("\n📈 VN MARKET DATA")
    print("-" * 40)

    vn_sqlite = get_sqlite_summary(VN_DB, "VN")
    vn_csv = get_csv_summary(VN_CSV_DIR, "VN")

    if vn_sqlite:
        print("SQLite Database:")
        for interval in ['1D', '1H', '1m']:
            data = vn_sqlite[interval]
            if data['tickers'] > 0:
                print(f"  {interval}: {data['tickers']} tickers, {data['records']:,} records")
            else:
                print(f"  {interval}: No data")

    if vn_csv:
        print("\nCSV Files:")
        for interval in ['1D', '1H', '1m']:
            data = vn_csv[interval]
            if data['files'] > 0:
                print(f"  {interval}: {data['files']} files, {data['total_size_mb']:.1f} MB total")
            else:
                print(f"  {interval}: No files")

    # Crypto Data
    print("\n🪙 CRYPTO DATA")
    print("-" * 40)

    crypto_sqlite = get_sqlite_summary(CRYPTO_DB, "Crypto")
    crypto_csv = get_csv_summary(CRYPTO_CSV_DIR, "Crypto")

    if crypto_sqlite:
        print("SQLite Database:")
        for interval in ['1D', '1H', '1m']:
            data = crypto_sqlite[interval]
            if data['tickers'] > 0:
                print(f"  {interval}: {data['tickers']} tickers, {data['records']:,} records")
            else:
                print(f"  {interval}: No data")

    if crypto_csv:
        print("\nCSV Files:")
        for interval in ['1D', '1H', '1m']:
            data = crypto_csv[interval]
            if data['files'] > 0:
                print(f"  {interval}: {data['files']} files, {data['total_size_mb']:.1f} MB total")
            else:
                print(f"  {interval}: No files")

    # Sync Status
    print("\n📋 SYNC STATUS")
    print("-" * 40)

    if vn_sqlite and vn_csv:
        print("VN Market Data:")
        for interval in ['1D', '1H', '1m']:
            sqlite_tickers = vn_sqlite[interval]['tickers']
            csv_files = vn_csv[interval]['files']

            if sqlite_tickers == csv_files and sqlite_tickers > 0:
                print(f"  {interval}: ✅ IN SYNC ({sqlite_tickers} tickers)")
            elif sqlite_tickers > 0 and csv_files == 0:
                print(f"  {interval}: ⚠️  SQLite only ({sqlite_tickers} tickers)")
            elif csv_files > 0 and sqlite_tickers == 0:
                print(f"  {interval}: ⚠️  CSV only ({csv_files} tickers)")
            else:
                print(f"  {interval}: ❌ OUT OF SYNC (SQLite: {sqlite_tickers}, CSV: {csv_files})")

    if crypto_sqlite and crypto_csv:
        print("\nCrypto Data:")
        for interval in ['1D', '1H', '1m']:
            sqlite_tickers = crypto_sqlite[interval]['tickers']
            csv_files = crypto_csv[interval]['files']

            if sqlite_tickers == csv_files and sqlite_tickers > 0:
                print(f"  {interval}: ✅ IN SYNC ({sqlite_tickers} tickers)")
            elif sqlite_tickers > 0 and csv_files == 0:
                print(f"  {interval}: ⚠️  SQLite only ({sqlite_tickers} tickers)")
            elif csv_files > 0 and sqlite_tickers == 0:
                print(f"  {interval}: ⚠️  CSV only ({csv_files} tickers)")
            else:
                print(f"  {interval}: ❌ OUT OF SYNC (SQLite: {sqlite_tickers}, CSV: {csv_files})")

    print("\n" + "=" * 60)
    print("CONCLUSION:")
    print("=" * 60)

    # Check if daily data is synced
    vn_daily_sync = (vn_sqlite and vn_csv and
                    vn_sqlite['1D']['tickers'] == vn_csv['1D']['files'] and
                    vn_sqlite['1D']['tickers'] > 0)

    crypto_daily_sync = (crypto_sqlite and crypto_csv and
                        crypto_sqlite['1D']['tickers'] == crypto_csv['1D']['files'] and
                        crypto_sqlite['1D']['tickers'] > 0)

    if vn_daily_sync and crypto_daily_sync:
        print("✅ Daily data (1D) is fully synced between SQLite and CSV")
        print("✅ SQLite migration is working correctly for daily data")
        print("💡 Note: Hourly/minute data may still be in CSV files awaiting migration")
    else:
        print("❌ Daily data sync issues detected")

if __name__ == "__main__":
    main()