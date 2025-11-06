#!/bin/bash
# Fix corrupted CSV files by detecting and removing bad lines

MARKET_DATA_DIR="market_data"

echo "🔍 Scanning for corrupted CSV files..."

python3 << 'PYTHON_SCRIPT'
import csv
import glob
import os

corrupted_files = []

for file in glob.glob('market_data/*/1D.csv'):
    try:
        with open(file, 'r') as f:
            reader = csv.reader(f)
            header = next(reader)

            corrupted_lines = []
            for i, row in enumerate(reader, start=2):
                # Check for wrong field count (7 basic or 15 enhanced)
                if len(row) not in [7, 15]:
                    corrupted_lines.append((i, len(row), "wrong field count"))
                    continue

                # Check for invalid numeric fields
                for idx, field_name in [(2, 'open'), (3, 'high'), (4, 'low'), (5, 'close'), (6, 'volume')]:
                    if idx < len(row):
                        field = row[idx].strip()
                        if field and len(field) > 20:  # Suspiciously long
                            corrupted_lines.append((i, len(row), f"suspicious {field_name}: len={len(field)}"))
                            break
                        if field:
                            try:
                                float(field)
                            except ValueError:
                                corrupted_lines.append((i, len(row), f"invalid {field_name}: {field[:50]}"))
                                break

            if corrupted_lines:
                ticker = os.path.basename(os.path.dirname(file))
                print(f"\n❌ {ticker}/1D.csv - Found {len(corrupted_lines)} corrupted lines:")
                for line_no, field_count, reason in corrupted_lines[:5]:  # Show first 5
                    print(f"   Line {line_no}: {field_count} fields, {reason}")

                # Find first corrupted line
                first_corrupted = corrupted_lines[0][0]
                print(f"   → Recommend deleting from line {first_corrupted} onward")
                corrupted_files.append((file, first_corrupted))

    except Exception as e:
        print(f"❌ {file} - Error scanning: {e}")

if corrupted_files:
    print(f"\n📋 Summary: {len(corrupted_files)} corrupted files found")
    print("\nTo fix, run:")
    for file, line_no in corrupted_files:
        ticker = os.path.basename(os.path.dirname(file))
        print(f"  head -{line_no - 1} {file} > /tmp/{ticker}_fixed.csv && mv /tmp/{ticker}_fixed.csv {file}")
else:
    print("\n✅ No corrupted files found!")
PYTHON_SCRIPT
