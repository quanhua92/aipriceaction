"""
PriceDancing Proof-of-Work Token Miner

Reverse-engineered from the PriceDancing chart website (pricedancing.com).
The API uses a proof-of-work token system:
  1. Request without token -> get 403 with a challenge string
  2. Mine a nonce such that SHA-256(challenge + nonce_padded)[:4] == "0000"
  3. Use the concatenation of challenge + nonce_padded as a Bearer token

Difficulty: n=4 (first 4 hex chars of SHA-256 must be "0000", ~1 in 65536 odds)
Average mining time: ~0.07s in Python

Token format: challenge (32 chars) + nonce_padded (32 chars) = 64 chars total
"""

import hashlib
import json
import random
import time
import urllib.request
import urllib.error


def get_challenge(chart_id="rAAvxj", period="forty-five-days"):
    """
    Step 1: Make a request to the PriceDancing API without a token.
    The server returns 403 with a challenge string in the 'data' field.

    Returns the challenge string, or raises on unexpected errors.
    """
    ts = int(time.time() * 1000)
    url = f"https://www.pricedancing.com/api/chart/{chart_id}?style=technical&period={period}&_t={ts}"

    req = urllib.request.Request(url)
    req.add_header("Content-Type", "application/json")
    req.add_header(
        "Referer",
        f"https://www.pricedancing.com/vi/SJC-SJC.1L-VND-chart-{chart_id}",
    )
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body.decode()


def mine_token(challenge, difficulty=4, max_nonce=200000):
    """
    Step 2: Proof-of-work mine.

    Algorithm (mirrors the JS in app.js):
      - Generate a random pad character from SHA-256(random_uint32)[:32][0]
      - Find an integer nonce where SHA-256(challenge + nonce_padded)[:difficulty] == "0" * difficulty
      - nonce_padded = str(nonce).rjust(32, pad_char)
      - token = challenge + nonce_padded

    Returns (token, nonce, hash_result, attempts, elapsed_seconds).
    """
    # Generate random salt / pad character
    # JS: o = pe(F().toString()).substring(0,32)
    # F() = crypto.getRandomValues(Uint32Array(1))[0]  (random uint32)
    # pe = sha256
    rand_val = random.randint(0, 0xFFFFFFFF)
    salt = hashlib.sha256(str(rand_val).encode()).hexdigest()[:32]
    pad_char = salt[0]  # JS padStart with string uses the first character

    print(f"Mining... (difficulty={difficulty}, pad_char='{pad_char}')")
    start_time = time.time()

    a = 0
    while a < max_nonce:
        nonce_str = str(a).rjust(32, pad_char)
        combined = challenge + nonce_str
        hash_result = hashlib.sha256(combined.encode()).hexdigest()
        if hash_result[:difficulty] == "0" * difficulty:
            elapsed = time.time() - start_time
            token = combined
            return token, a, hash_result, a, elapsed
        a += 1

    raise RuntimeError(f"Mining failed after {max_nonce} attempts")


def api_request(url, token=None):
    """Make an authenticated (or unauthenticated) request to PriceDancing API."""
    req = urllib.request.Request(url)
    req.add_header("Content-Type", "application/json")
    req.add_header(
        "Referer",
        "https://www.pricedancing.com/vi/SJC-SJC.1L-VND-chart-rAAvxj",
    )
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body.decode()


def fetch_chart(chart_id="rAAvxj", period="forty-five-days"):
    """
    Full end-to-end flow:
      1. Get challenge (403 response)
      2. Mine token
      3. Make authenticated request
      4. Return the chart data
    """
    # Step 1: Get challenge
    ts = int(time.time() * 1000)
    base_url = f"https://www.pricedancing.com/api/chart/{chart_id}?style=technical&period={period}&_t={ts}"

    status, data = api_request(base_url)
    print(f"Step 1 - Status: {status}, Challenge: {data.get('data', 'N/A')}")

    if status != 403 or "data" not in data:
        raise RuntimeError(f"Expected 403 with challenge, got {status}: {data}")

    challenge = data["data"]

    # Step 2: Mine token
    token, nonce, hash_result, attempts, elapsed = mine_token(challenge)
    print(f"Step 2 - Found! nonce={nonce}, attempts={attempts}, time={elapsed:.2f}s")
    print(f"  Hash: {hash_result}")

    # Step 3: Use token immediately
    ts2 = int(time.time() * 1000)
    base_url2 = f"https://www.pricedancing.com/api/chart/{chart_id}?style=technical&period={period}&_t={ts2}"
    status2, data2 = api_request(base_url2, token)
    print(f"Step 3 - Status: {status2}")

    if status2 == 200 and isinstance(data2, dict) and data2.get("status") == "ok":
        candles = data2["data"]["data"]
        print(f"  SUCCESS! Got {len(candles)} candles")
        print(f"  First: {candles[0]['t']}, Last: {candles[-1]['t']}")
        return data2
    else:
        print(f"  Response: {data2}")
        return data2


# Valid periods for the PriceDancing chart API
VALID_PERIODS = {
    "twenty-four-hours": {"interval": "30m", "description": "~10 hours"},
    "one-week": {"interval": "4h", "description": "~3 days"},
    "forty-five-days": {"interval": "1D", "description": "45 trading days"},
    "one-year": {"interval": "1W", "description": "1 year"},
    "four-years": {"interval": "1M", "description": "4 years"},
    "all-time": {"interval": "4M", "description": "Since 2009"},
}


if __name__ == "__main__":
    import sys

    chart_id = sys.argv[1] if len(sys.argv) > 1 else "rAAvxj"
    period = sys.argv[2] if len(sys.argv) > 2 else "forty-five-days"

    print(f"Fetching chart: {chart_id}, period: {period}")
    print(f"Valid periods: {', '.join(VALID_PERIODS.keys())}")
    print()

    result = fetch_chart(chart_id, period)
