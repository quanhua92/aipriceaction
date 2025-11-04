# Data Sync Scripts

Convenient shell scripts for syncing market data. Run from the project root directory.

## Available Scripts

### 📊 pull_daily.sh
Syncs daily data for all 290 tickers (last 3 days)
```bash
./scripts/pull_daily.sh
```
**Expected time**: ~15 seconds
**Data volume**: 3 records/ticker = very light

### 📊 pull_hourly.sh
Syncs hourly data for all 290 tickers (last 5 days)
```bash
./scripts/pull_hourly.sh
```
**Expected time**: ~20-30 seconds
**Data volume**: ~30 records/ticker = moderate

### 📊 pull_minute.sh
Syncs minute data for all 290 tickers (last 2 days)
```bash
./scripts/pull_minute.sh
```
**Expected time**: ~1-2 minutes
**Data volume**: ~720 records/ticker = heavy (optimized to avoid API overload)

### 📊 pull_all.sh
Syncs all intervals (daily, hourly, minute) in sequence
```bash
./scripts/pull_all.sh
```
**Expected time**: ~2-3 minutes total
**Uses optimized resume days per interval**

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

The scripts use interval-specific resume days to balance data freshness with API load:

| Interval | Resume Days | Records/Ticker | Rationale |
|----------|-------------|----------------|-----------|
| **Daily** | 3 | 3 | Very light data, safe buffer |
| **Hourly** | 5 | ~30 | Moderate data, optimal for batch API |
| **Minute** | 2 | ~720 | Heavy data, prevents API overload |

**Why different values?**
- Minute data has ~360 records per day
- 7 days of minute data = 2,520 records/ticker × 10 tickers/batch = 25,200 records
- This can timeout or fail the API
- 2 days = 720 records/ticker is much safer

## Notes

- Scripts use resume mode by default (incremental updates)
- Resume days are optimized per interval (see table above)
- All scripts require the project to be built: `cargo build --release`
- Run daily for best results (keeps data fresh without overload)
