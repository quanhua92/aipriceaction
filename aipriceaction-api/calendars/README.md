# Source-backed trading calendars

These declarations describe announced exchange schedules. They support read-only
date-coverage checks and never synthesize candles, rewrite OHLCV or prove that
every scheduled session actually took place.

The retained daily window uses declarations for 2023 through 2026. Notices were
downloaded, rendered and visually checked; mirrored documents are explicitly
identified in their source metadata.

| Year | Annual HOSE notice | Required amendment |
| --- | --- | --- |
| 2023 | 2208/TB-SGDHCM, signed notice image | None recorded |
| 2024 | 1943/TB-SGDHCM | 803/TB-SGDHCM: April 29 closure; no May 4 makeup trading |
| 2025 | 2079/TB-SGDHCM | None recorded |
| 2026 | 2294/TB-SGDHCM | 2410/TB-SGDHCM: January 2 closure; no January 10 makeup trading |

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

For an amended year, provide both the annual source and every amendment file,
in declaration order. Missing, additional, swapped or changed source files are
rejected. For example, with downloaded 2024 source files:

```sh
.venv/bin/python -m scripts.check_hose_scheduled_dates \
  --calendar calendars/hose-2024.json \
  --source-file data/hose-2024-base.pdf \
  --amendment-file data/hose-2024-amendment.pdf \
  --start-date 2024-01-01 --end-date 2024-12-31 \
  --output data/hose-2024-date-audit.json
```

`--source-pdf` remains an alias for `--source-file`; the 2023 source is a JPEG.
`--interval 1h` or `--interval 1m` checks observed intraday date partitions. A
single candle establishes date presence only, not full intraday coverage. Daily
timestamp validation remains specific to `1D`; intraday date bins do not change
stored timestamps. Console output shows counts; full findings remain in JSON.

The audit checks each source hash before opening SQLite in read-only mode and reads
both indices in one database snapshot. It reports missing scheduled dates,
unexpected holiday/weekend dates and non-midnight daily timestamps. A passing
date check does not certify prices, volumes, intraday coverage or exchange
operations. Existing output files cannot be overwritten.

Only VNINDEX and VN30, in the declared year, are licensed by this declaration.
Stock listing dates, suspensions and no-trade days require separate evidence.
Other exchanges and years need their own verified notices and amendments;
unknown windows are rejected rather than inferred from the legacy API.
SQLite-only absence must also be checked against the API's merged SQLite/S3
reader before it is described as absence from the served dataset.

## HNX reference evidence

`hnx-2025.json` records the closure table in signed HNX notice
**5386/TB-SGDHN**, dated December 23, 2024. The full-page PDF rendering was
visually checked; the document is distributed through a
[broker-hosted mirror](https://vietsc.vn/public/contents/vsc-ve-viec-cong-bo-lich-nghi-giao-dich-trong.pdf).
Its weekday closures match the verified 2025 HOSE table. Unlike the HOSE notice,
this copy mentions the April 26 employee makeup Saturday without explicitly
declaring its trading status, so it records no explicit weekend exclusion.

This is reference evidence only. Its empty symbol list licenses no stock audit,
and the HOSE index checker continues to reject it. Historical venue changes,
listing boundaries, suspensions and no-trade evidence must be established before
a stock's absent date can be called missing OHLCV. HNX notices and amendments for
the other retained years are now also recorded below.

| Year | Annual HNX notice | Required amendment |
| --- | --- | --- |
| 2023 | 3451/TB-SGDHN, December 14, 2022 | None recorded |
| 2024 | 5270/TB-SGDHN, December 8, 2023 | 1977/TB-SGDHN: April 29 closure; no May 4 makeup trading |
| 2025 | 5386/TB-SGDHN, December 23, 2024 | None recorded |
| 2026 | 5305/TB-SGDHN, December 3, 2025 | 5680/TB-SGDHN: January 2 closure; no January 10 makeup trading |

The 2023, 2024 and 2026 documents and both amendments were downloaded from
official HNX attachment links. Every page was visually checked. The 2026 annual
notice also explicitly excludes the August 22 makeup Saturday. All declarations
pin source URLs, exact bytes and SHA-256; their empty symbol lists do not certify
stock sessions.

## Stock date review

`scripts.check_vn_stock_scheduled_dates` verifies both exchange declarations and
every required amendment before reading local SQLite in one read-only transaction.
It requires identical scheduled weekdays in the requested year/window, avoiding
an assumption that today's exchange metadata proves a stock's historical venue.
Different schedules are rejected pending venue evidence. For example:

```sh
.venv/bin/python -m scripts.check_vn_stock_scheduled_dates \
  --hose-calendar calendars/hose-2025.json \
  --hose-source data/hose-calendar-primary-20261004/hose-2025.pdf \
  --hnx-calendar calendars/hnx-2025.json \
  --hnx-source data/hnx-calendar-primary-20261004/hnx-2025.pdf \
  --symbol FPT --symbol VPL --interval 1D \
  --start-date 2025-01-01 --end-date 2025-12-31 \
  --output data/stock-2025-date-review.json
```

Supply repeated `--hose-amendment` and `--hnx-amendment` source files for amended
years. Only explicit three-letter stock requests and native stored intervals are
accepted. Existing output is preserved. Daily shifted timestamps and unexpected
dates remain visible; intraday checks project date presence without changing any
timestamp.

`absent_shared_schedule_dates` are candidates for review, not licensed missing
stock sessions. The report makes no completeness pass claim, does not infer
listing or transfer dates, and cannot publish candles. Before treating SQLite
absence as served-data absence, verify the API's merged SQLite/S3 history reader.
