# Data Sync Scripts

Convenient shell scripts for syncing market data. Run from the project root directory.

## Available Scripts

### 📊 pull_daily.sh
Syncs daily data for all 283 tickers (last 1 day)
```bash
./scripts/pull_daily.sh
```
**Expected time**: ~3 seconds (50-ticker batches)
**Data volume**: 1 record/ticker = very light

### 📊 pull_hourly.sh
Syncs hourly data for all 283 tickers (last 5 days)
```bash
./scripts/pull_hourly.sh
```
**Expected time**: ~4 seconds (20-ticker batches)
**Data volume**: ~30 records/ticker = moderate

### 📊 pull_minute.sh
Syncs minute data for all 283 tickers (last 2 days)
```bash
./scripts/pull_minute.sh
```
**Expected time**: ~5 minutes (3-ticker batches)
**Data volume**: ~400 records/ticker = heavy (optimized to avoid API overload)

### 📊 pull_all.sh
Syncs all intervals (daily, hourly, minute) in sequence
```bash
./scripts/pull_all.sh
```
**Expected time**: ~5-6 minutes total
**Uses smart interval-specific defaults (no parameters needed)**
**Breakdown**: Daily (3s) + Hourly (4s) + Minute (5min) = ~5min 7s

## Usage

All scripts must be run from the project root directory:

```bash
# Daily sync (recommended for daily cron jobs)
./scripts/pull_daily.sh

# Hourly sync (run daily or weekly)
./scripts/pull_hourly.sh

# Minute sync (run daily or weekly)
./scripts/pull_minute.sh

# Full sync (all intervals)
./scripts/pull_all.sh
```

## Features

- ✅ Shows start/end timestamps
- ✅ Displays execution time
- ✅ Exit codes for automation
- ✅ Clear success/failure messages

## Scheduling with Cron

Example crontab entries:

```bash
# Daily sync at 6 PM (after market closes at 3:45 PM Vietnam time)
0 18 * * 1-5 cd /path/to/aipriceaction && ./scripts/pull_daily.sh >> logs/daily.log 2>&1

# Weekly full sync on Sunday at 2 AM
0 2 * * 0 cd /path/to/aipriceaction && ./scripts/pull_all.sh >> logs/weekly.log 2>&1
```

## Smart Resume Days Strategy

The scripts use interval-specific resume days and batch sizes, all configured automatically in the Rust code:

| Interval | Resume Days | Batch Size | Records/Batch | Performance |
|----------|-------------|------------|---------------|-------------|
| **Daily** | 1 | 50 tickers | 50 records | ~3s for 283 tickers |
| **Hourly** | 5 | 20 tickers | 600 records | ~4s for 283 tickers |
| **Minute** | 2 | 3 tickers | 1200 records | ~5min for 283 tickers |

**Why different batch sizes?**
- Daily data is very light (1 record/ticker), large batches for maximum speed
- Hourly data is moderate (~30 records/ticker), 20 tickers/batch balances speed and reliability
- Minute data is heavy (~400 records/ticker), only 3 tickers/batch to avoid API overload
- All intervals now use batch API for maximum performance!
- Note: 7 tickers without daily/hourly data removed from ticker list (BCG, BTN, FID, LTG, PXI, SSH, TCD)

## Notes

- Scripts use resume mode by default (incremental updates)
- Resume days are optimized per interval (see table above)
- All scripts require the project to be built: `cargo build --release`
- Run daily for best results (keeps data fresh without overload)
