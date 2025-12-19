#!/usr/bin/env python3
"""
Get top 100 cryptocurrencies by market cap from CoinGecko API

This script fetches the top cryptocurrencies by market cap from CoinGecko
without requiring any API key or registration.

Usage:
    python scripts/get_coingecko_top_crypto.py [limit]

Default limit: 100
"""

import requests
import json
import sys
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_coingecko_top_crypto(limit=200):
    """Get top cryptocurrencies by market cap from CoinGecko"""
    try:
        print("🌐 Fetching top cryptocurrencies from CoinGecko...")

        # CoinGecko API endpoint for top coins by market cap
        # Fetch more than needed to ensure we get 100 unique trading pairs after deduplication
        url = f"https://api.coingecko.com/api/v3/coins/markets"
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': limit,
            'page': 1,
            'sparkline': 'false',
            'price_change_percentage': '24h'
        }

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()
        return data

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data from CoinGecko: {str(e)}")
        return []
    except Exception as e:
        print(f"❌ Error processing CoinGecko data: {str(e)}")
        return []

def create_top_crypto_json(coingecko_data, filename='top_crypto.json'):
    """Create JSON file with top cryptocurrency data"""
    result = {
        'fetched_at': datetime.now().isoformat() + 'Z',
        'source': 'CoinGecko API',
        'count': len(coingecko_data),
        'ranking_by': 'market_cap',
        'data': []
    }

    for i, coin in enumerate(coingecko_data, 1):
        # Handle missing data gracefully
        market_cap = coin.get('market_cap', 0)
        volume = coin.get('total_volume', 0)
        price = coin.get('current_price', 0)
        change = coin.get('price_change_percentage_24h', 0)

        result['data'].append({
            'rank': i,
            'symbol': coin.get('symbol', '').upper(),
            'name': coin.get('name', ''),
            'price': f"{price:,.8f}" if price < 0.01 else f"{price:,.2f}",
            'change_24h': f"{change:+.2f}%" if change is not None else "N/A",
            'market_cap': f"${market_cap:,.0f}" if market_cap > 0 else "N/A",
            'volume_24h': f"${volume:,.0f}" if volume > 0 else "N/A",
            'circulating_supply': f"{coin.get('circulating_supply', 0):,.0f}" if coin.get('circulating_supply') else "N/A",
            'coingecko_id': coin.get('id', ''),
            'image': coin.get('image', '')
        })

    # Save to file
    with open(filename, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"💾 Saved {len(coingecko_data)} top cryptocurrencies to {filename}")
    return result

def map_to_binance_pairs(coingecko_data):
    """Try to map CoinGecko symbols to Binance trading pairs"""
    print("🔄 Mapping to Binance trading pairs...")

    # Common Binance quote currencies to try
    quote_currencies = ['USDT', 'USDC', 'BTC', 'ETH', 'BNB']

    mappings = []
    mapped_count = 0

    for coin in coingecko_data:
        symbol = coin.get('symbol', '').upper()
        name = coin.get('name', '')
        rank = coin.get('market_cap_rank', 0)

        # Try different Binance pair formats
        binance_pairs = []
        for quote in quote_currencies:
            binance_pairs.append(f"{symbol}{quote}")

        # Skip obvious non-trading pairs
        if symbol in ['USDT', 'USDC', 'BUSD', 'DAI']:
            # Try different quote for stablecoins
            binance_pairs = [f"{symbol}USD", f"{symbol}BTC", f"{symbol}ETH"]

        # Add the best guess pair
        best_pair = binance_pairs[0] if binance_pairs else f"{symbol}USDT"

        mappings.append({
            'coingecko_symbol': symbol,
            'coingecko_name': name,
            'rank': rank,
            'suggested_binance_pair': best_pair,
            'market_cap': coin.get('market_cap', 0)
        })

    return mappings


def validate_binance_pair(binance_symbol):
    """Validate if a Binance trading pair exists by checking local directory first, then API"""
    # First check if directory already exists locally (most reliable)
    if os.path.exists(f"spot/daily/klines/{binance_symbol}"):
        return True

    # If not local, try a smart date range starting from newest
    test_dates = []

    # Try recent dates first (most likely to have data)
    import datetime
    today = datetime.datetime.now()
    for i in range(7):  # Last 7 days
        test_date = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        test_dates.append(test_date)

    # Then try some historical dates that are likely to exist
    test_dates.extend(["2025-11-18", "2025-11-17", "2025-10-31", "2025-09-30"])

    for date in test_dates:
        try:
            test_url = f"https://data.binance.vision/data/spot/daily/klines/{binance_symbol}/1d/{binance_symbol}-1d-{date}.zip"
            response = requests.head(test_url, timeout=3)  # Faster timeout
            if response.status_code == 200:
                return True
        except Exception:
            continue

    return False


def validate_binance_pairs_parallel(pairs, max_workers=10):
    """Validate multiple Binance pairs in parallel"""
    print(f"\n🔍 Validating {len(pairs)} Binance trading pairs...")

    valid_pairs = []
    invalid_pairs = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all validation tasks
        future_to_pair = {
            executor.submit(validate_binance_pair, pair['binance_symbol']): pair
            for pair in pairs
        }

        # Process results as they complete
        completed = 0
        for future in as_completed(future_to_pair):
            pair = future_to_pair[future]
            try:
                is_valid = future.result()
                completed += 1

                if is_valid:
                    valid_pairs.append(pair)
                    print(f"✅ {pair['binance_symbol']} - VALID")
                else:
                    invalid_pairs.append(pair)
                    print(f"❌ {pair['binance_symbol']} - INVALID")

                # Show progress
                if completed % 10 == 0 or completed == len(pairs):
                    print(f"📊 Progress: {completed}/{len(pairs)} pairs validated")

            except Exception as e:
                invalid_pairs.append(pair)
                print(f"❌ {pair['binance_symbol']} - ERROR: {str(e)}")

    print(f"\n📈 Validation Results:")
    print(f"   Valid pairs: {len(valid_pairs)}/{len(pairs)} ({len(valid_pairs)/len(pairs)*100:.1f}%)")
    print(f"   Invalid pairs: {len(invalid_pairs)}")

    if invalid_pairs:
        print(f"\n❌ Invalid pairs:")
        for pair in invalid_pairs[:10]:  # Show first 10 invalid pairs
            print(f"   {pair['binance_symbol']} ({pair['coingecko_symbol']})")
        if len(invalid_pairs) > 10:
            print(f"   ... and {len(invalid_pairs) - 10} more")

    return valid_pairs, invalid_pairs


def main():
    # Parse limit from command line
    fetch_limit = 200  # Fetch more to account for duplicates
    target_count = 100  # Target unique trading pairs
    if len(sys.argv) > 1:
        try:
            target_count = int(sys.argv[1])
            fetch_limit = max(target_count * 2, 200)  # Fetch at least double to handle duplicates
        except ValueError:
            print("❌ Invalid limit. Usage: python get_coingecko_top_crypto.py [unique_count]")
            sys.exit(1)

    print("🚀 CoinGecko Top Cryptocurrencies by Market Cap")
    print("=" * 55)
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🎯 Target: {target_count} unique cryptocurrency trading pairs")
    print(f"📊 Fetching {fetch_limit} cryptocurrencies to ensure unique pairs")
    print(f"🌐 Using CoinGecko API (free, no registration required)")
    print()

    # Get data from CoinGecko
    coingecko_data = get_coingecko_top_crypto(fetch_limit)

    if not coingecko_data:
        print("❌ Failed to get data from CoinGecko")
        sys.exit(1)

    print(f"✅ Retrieved {len(coingecko_data)} cryptocurrencies from CoinGecko")

    # Create unique trading pairs (deduplicate by binance_symbol)
    unique_pairs = []
    seen_binance_symbols = set()
    seen_coingecko_symbols = set()

    for coin in coingecko_data:
        coingecko_symbol = coin.get('symbol', '').upper()

        # Skip if we've already processed this CoinGecko symbol
        if coingecko_symbol in seen_coingecko_symbols:
            continue
        seen_coingecko_symbols.add(coingecko_symbol)

        # Convert to Binance format using same logic as fetch_all_crypto.py
        if coingecko_symbol in ['USDT', 'USDC', 'BUSD', 'DAI']:
            binance_symbol = f"{coingecko_symbol}USD"
        elif coingecko_symbol in ['BTC', 'ETH']:
            binance_symbol = f"{coingecko_symbol}USDT"
        elif coingecko_symbol.endswith('USD') or coingecko_symbol.endswith('USDT'):
            binance_symbol = coingecko_symbol
        else:
            binance_symbol = f"{coingecko_symbol}USDT"

        # Only add if we haven't seen this Binance symbol yet
        if binance_symbol not in seen_binance_symbols:
            seen_binance_symbols.add(binance_symbol)
            unique_pairs.append({
                'coingecko_symbol': coingecko_symbol,
                'binance_symbol': binance_symbol,
                'coingecko_name': coin.get('name', ''),
                'rank': len(unique_pairs) + 1,  # Will be updated below
                'market_cap': coin.get('market_cap', 0),
                'current_price': coin.get('current_price', 0),
                'price_change_percentage_24h': coin.get('price_change_percentage_24h', 0)
            })

        # Stop if we have enough unique pairs
        if len(unique_pairs) >= target_count:
            break

    # Take only the target count
    unique_pairs = unique_pairs[:target_count]
    print(f"✅ Found {len(unique_pairs)} unique trading pairs after deduplication")

    # Display top 15
    print(f"\n📊 Top 15 unique trading pairs by market cap:")
    print(f"{'Rank':<4} {'Symbol':<8} {'Binance Pair':<12} {'Name':<20} {'Price':<15} {'Change':<8}")
    print("-" * 90)

    for i, pair in enumerate(unique_pairs[:15], 1):
        price = pair.get('current_price', 0)
        change = pair.get('price_change_percentage_24h', 0)
        market_cap = pair.get('market_cap', 0)

        price_str = f"{price:,.8f}" if price < 0.01 else f"{price:,.2f}"
        change_str = f"{change:+.2f}%" if change is not None else "N/A"
        mcap_str = f"${market_cap:,.0f}" if market_cap > 0 else "N/A"

        print(f"{i:<4} {pair['coingecko_symbol']:<8} {pair['binance_symbol']:<12} {pair['coingecko_name'][:20]:<20} ${price_str:<14} {change_str:<8} {mcap_str:<15}")

    # Create JSON file with unique pairs
    unique_data = []
    for i, pair in enumerate(unique_pairs, 1):
        # Format price for display
        price = pair.get('current_price', 0)
        if price < 0.01:
            price_str = f"{price:,.8f}"
        else:
            price_str = f"{price:,.2f}"

        unique_data.append({
            'rank': i,
            'symbol': pair['coingecko_symbol'],
            'name': pair['coingecko_name'],
            'binance_symbol': pair['binance_symbol'],  # Add Binance trading pair directly
            'price': price_str,
            'change_24h': f"{pair.get('price_change_percentage_24h', 0):+.2f}%" if pair.get('price_change_percentage_24h') is not None else "N/A",
            'market_cap': f"${pair.get('market_cap', 0):,.0f}" if pair.get('market_cap') > 0 else "N/A",
            'volume_24h': "N/A",  # Not fetching volume to simplify
            'circulating_supply': "N/A",  # Not fetching supply to simplify
            'coingecko_id': '',  # Not fetching ID to simplify
            'image': ''  # Not fetching image to simplify
        })

    result = {
        'fetched_at': datetime.now().isoformat() + 'Z',
        'source': 'CoinGecko API',
        'count': len(unique_data),
        'ranking_by': 'market_cap',
        'data': unique_data
    }

    with open('top_crypto.json', 'w') as f:
        json.dump(result, f, indent=2)

    print(f"\n💾 Saved {len(unique_data)} unique trading pairs to top_crypto.json")
    print(f"🔗 Binance trading pairs are now included directly in top_crypto.json (binance_mapping.json no longer needed)")

    # Validate Binance pairs with multiple dates
    print(f"\n🧪 Running Binance Vision API validation with multiple dates...")
    valid_pairs, invalid_pairs = validate_binance_pairs_parallel(unique_pairs)

    # If we have invalid pairs, update the data to only include valid ones
    if invalid_pairs:
        print(f"\n🔧 Filtering out invalid pairs...")
        # Filter unique_pairs to only include valid ones
        valid_binance_symbols = {pair['binance_symbol'] for pair in valid_pairs}
        unique_pairs = [pair for pair in unique_pairs if pair['binance_symbol'] in valid_binance_symbols]

        # Rebuild unique_data with only valid pairs
        unique_data = []
        for i, pair in enumerate(unique_pairs, 1):
            # Format price for display
            price = pair.get('current_price', 0)
            if price < 0.01:
                price_str = f"{price:,.8f}"
            else:
                price_str = f"{price:,.2f}"

            unique_data.append({
                'rank': i,
                'symbol': pair['coingecko_symbol'],
                'name': pair['coingecko_name'],
                'binance_symbol': pair['binance_symbol'],  # Add Binance trading pair directly
                'price': price_str,
                'change_24h': f"{pair.get('price_change_percentage_24h', 0):+.2f}%" if pair.get('price_change_percentage_24h') is not None else "N/A",
                'market_cap': f"${pair.get('market_cap', 0):,.0f}" if pair.get('market_cap') > 0 else "N/A",
                'volume_24h': "N/A",  # Not fetching volume to simplify
                'circulating_supply': "N/A",  # Not fetching supply to simplify
                'coingecko_id': '',  # Not fetching ID to simplify
                'image': ''  # Not fetching image to simplify
            })

        # Update result with filtered data
        result['count'] = len(unique_data)
        result['data'] = unique_data

        # Save filtered data
        with open('top_crypto.json', 'w') as f:
            json.dump(result, f, indent=2)

        print(f"💾 Updated top_crypto.json with {len(unique_data)} validated pairs only")
    else:
        print(f"✅ All {len(unique_pairs)} pairs are valid!")

    # Show statistics
    total_market_cap = sum(pair.get('market_cap', 0) for pair in unique_pairs)
    top_10_market_cap = sum(pair.get('market_cap', 0) for pair in unique_pairs[:10])

    print(f"\n📈 Market Cap Statistics:")
    print(f"   Total market cap ({len(unique_pairs)} unique pairs): ${total_market_cap:,.0f}")
    print(f"   Top 10 market cap: ${top_10_market_cap:,.0f} ({top_10_market_cap/total_market_cap*100:.1f}%)")
    print(f"   Average per pair: ${total_market_cap/len(unique_pairs):,.0f}")

    # Show duplicates found
    original_count = min(fetch_limit, len(coingecko_data))
    duplicate_count = original_count - len(unique_pairs)
    if duplicate_count > 0:
        print(f"\n🔗 Deduplication:")
        print(f"   Processed {original_count} coins from CoinGecko")
        print(f"   Removed {duplicate_count} duplicate trading pairs")
        print(f"   Final unique trading pairs: {len(unique_pairs)}")

    print(f"\n✅ Analysis complete!")
    print(f"💡 top_crypto.json - Contains {len(unique_data)} unique trading pairs by market cap with binance_symbol field")
    print(f"🔗 binance_symbol field - Direct Binance trading pair mapping (binance_mapping.json no longer needed)")

if __name__ == "__main__":
    main()