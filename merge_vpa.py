import re
import argparse
from collections import defaultdict

# --- Argument Parsing ---
parser = argparse.ArgumentParser(
    description='Merge new VPA analysis into a main VPA file.'
)
parser.add_argument(
    '--week',
    action='store_true',
    help='If specified, reads from and writes to VPA_week.md instead of VPA.md.'
)
args = parser.parse_args()

# --- File Configuration ---
if args.week:
    main_vpa_filename = 'VPA_week.md'
else:
    main_vpa_filename = 'VPA.md'

new_vpa_filename = 'VPA_NEW.md'

print(f"Using main VPA file: {main_vpa_filename}")
print(f"Using new data from: {new_vpa_filename}")

# Read files
with open(main_vpa_filename, 'r', encoding='utf-8') as f:
    vpa_content = f.read()
with open(new_vpa_filename, 'r', encoding='utf-8') as f:
    vpa_new_content = f.read()

def extract_ticker_blocks(md_text):
    # Find all ticker headers (e.g. # TICKER) and their content
    pattern = re.compile(r'(^# ([A-Z0-9]+)\n)(.*?)(?=^# [A-Z0-9]+\n|\Z)', re.DOTALL | re.MULTILINE)
    blocks = {}
    for m in pattern.finditer(md_text):
        header, ticker, body = m.groups()
        blocks[ticker] = header
        if body:
            # Split by ---
            parts = re.split(r'\n---+\n', body)
            for part in parts:
                part = part.strip()
                if part:
                    blocks[ticker] += part + '\n'
    return blocks

def extract_new_lines(md_text):
    # For each ticker, extract the lines (excluding the header)
    pattern = re.compile(r'^# ([A-Z0-9]+)\n(.*?)(?=^# [A-Z0-9]+\n|\Z)', re.DOTALL | re.MULTILINE)
    ticker_lines = defaultdict(list)
    for m in pattern.finditer(md_text):
        ticker = m.group(1)
        body = m.group(2).strip()
        if body:
            # Split by ---
            parts = re.split(r'\n---+\n', body)
            for part in parts:
                part = part.strip()
                if part:
                    ticker_lines[ticker].append(part)
    return ticker_lines

# Extract ticker blocks from VPA.md
vpa_blocks = extract_ticker_blocks(vpa_content)
# Extract new lines from VPA_NEW.md
vpa_new_lines = extract_new_lines(vpa_new_content)

# Merge: for each ticker in vpa_new_lines, if exists in vpa_blocks, append new lines after old content, else create new block
for ticker, new_parts in vpa_new_lines.items():
    if ticker in vpa_blocks:
        old_block = vpa_blocks[ticker].rstrip('\n')
        for part in new_parts:
            content = part.strip()
            if not content.endswith('---'):
                content += '\n\n---\n'
            old_block += '\n' + content
        vpa_blocks[ticker] = old_block + '\n'
    else:
        block = f'# {ticker}\n'
        for i, part in enumerate(new_parts):
            if i > 0:
                block += '\n'
            content = part.strip()
            if not content.endswith('---'):
                content += '\n\n---\n'
            block += content
        vpa_blocks[ticker] = block + '\n'

# Sort tickers by name
sorted_tickers = sorted(vpa_blocks.keys())

# Write to new VPA.md
with open(main_vpa_filename, 'w', encoding='utf-8') as f:
    for ticker in sorted_tickers:
        f.write('\n' + vpa_blocks[ticker].strip() + '\n')

# Post-process: Ensure blank lines before and after each # TICKER header
with open(main_vpa_filename, 'r', encoding='utf-8') as f:
    merged = f.read()

# Ensure a blank line before each # TICKER (except at start)
merged = re.sub(r'([^\n])\n# ([A-Z0-9]+)', r'\1\n\n# \2', merged)
# Ensure a blank line after each # TICKER
merged = re.sub(r'# ([A-Z0-9]+)\n([^\n])', r'# \1\n\n\2', merged)
# Normalize extra blank lines to just two
merged = re.sub(r'\n{3,}', r'\n\n', merged)

# We use a regular expression with a negative lookbehind `(?<!...)`.
# This finds any header `\n\n# TICKER` that is NOT already preceded by the `\n---\n` line.
# The `\n\n` at the start of the pattern ensures we only modify headers that follow previous content,
# leaving the very first header of the file untouched.
merged = re.sub(r'(?<!\n---\n)\n\n(# [A-Z0-9]+)', r'\n\n---\n\n\1', merged)

# One final normalization to ensure no more than two consecutive newlines exist anywhere.
merged = re.sub(r'\n{3,}', r'\n\n', merged)

with open(main_vpa_filename, 'w', encoding='utf-8') as f:
    f.write(merged)
