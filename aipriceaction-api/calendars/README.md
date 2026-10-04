# Source-backed trading calendars

These declarations describe announced exchange schedules. They support read-only
date-coverage checks and never synthesize candles, rewrite OHLCV or prove that
every scheduled session actually took place.

`hose-2025.json` is manually transcribed from HOSE notice **2079/TB-SGDHCM**,
issued December 26, 2024. The downloaded signed PDF was rendered and checked
visually, including the explicit exclusion of the April 26 makeup Saturday.
The declaration pins the original URL, byte length and SHA-256. Source-byte
verification does not automatically verify the manual transcription.

Download the [official source PDF](https://staticfile.hsx.vn/Uploads/UploadDocuments/1737138/20241226_20242612_Thong%20bao%20ve%20lich%20nghi%202025.pdf)
to a local path, then run:

```sh
.venv/bin/python -m scripts.check_hose_scheduled_dates \
  --calendar calendars/hose-2025.json \
  --source-pdf data/hose-2025.pdf \
  --start-date 2025-01-01 --end-date 2025-12-31 \
  --output data/hose-2025-date-audit.json
```

The audit checks the PDF hash before opening SQLite in read-only mode and reads
both indices in one database snapshot. It reports missing scheduled dates,
unexpected holiday/weekend dates and non-midnight daily timestamps. A passing
date check does not certify prices, volumes, intraday coverage or exchange
operations. Existing output files cannot be overwritten.

Only VNINDEX and VN30, in the declared year, are licensed by this declaration.
Stock listing dates, suspensions and no-trade days require separate evidence.
Other exchanges and years need their own verified notices and amendments;
unknown windows are rejected rather than inferred from the legacy API.
