# One-bucket archive layout

The API and terminal use one S3-compatible bucket and one set of writer
credentials. The object keys separate the two formats:

```text
meta/tickers.json
ohlcv/{source}/{ticker}/{interval}/{ticker}-{interval}-{YYYY-MM-DD}.csv
ohlcv/{source}/{ticker}/yearly/{ticker}-{interval}-{YYYY}.csv
parquet/ohlcv/{source}/{ticker}/{interval}/{start}-{end}-{sha256}.parquet
parquet/manifests/{sha256}.json
parquet/LATEST.json
```

The CSV keys remain at the bucket root because the existing terminal and SDK
read those paths directly over anonymous HTTP. The Python API's immutable
Parquet objects and manifests use `S3_PREFIX=parquet` in that same bucket.
There is no second bucket or second credential namespace.

The source segment keeps the existing market directories: `vn`, `crypto`,
`sjc`, and `yahoo`. Daily candle objects are written under the interval
directory for all native intervals (`1D`, `1h`, and `1m`). Yearly aggregates
follow Rust's existing layout for `1D` and `1h`; minute history remains in
per-UTC-day objects under `1m/`.

The configured bucket is public, as requested. That makes **every object**
public, including `parquet/` objects and their manifests. Apply the policy in
[`public-bucket-policy.template.json`](public-bucket-policy.template.json) and
disable the applicable S3 Block Public Access settings only after replacing
`REPLACE_WITH_BUCKET` with the actual bucket name.

Set these application values in the API `.env`:

```dotenv
S3_BUCKET=your-public-bucket
S3_PREFIX=parquet
PUBLIC_EXPORT_ENABLED=true
```

`S3_ENDPOINT`, credentials, and region are shared by both formats. The worker
publishes `meta/tickers.json` when it starts and after catalog refreshes. After
each successful ingestion it updates affected per-day CSV files; 1D and 1h
updates also refresh the corresponding yearly file. It uses Rust's CSV field
layout and S3 `content-hash` fingerprint, so unchanged existing objects are
skipped and existing keys keep their current paths.

The read-only probe of the live ACB daily and yearly objects, including body
checksums and the matching Rust metadata fingerprints, is recorded in
[`reports/public-s3-format-probe-2026-10-23.json`](../../reports/public-s3-format-probe-2026-10-23.json).

For a historical backfill or a one-off repair, preview first:

```sh
aipa-api export-public --catalog
aipa-api export-public --source vn --symbol VCB --interval 1D --year 2025
aipa-api export-public --source vn --symbol VCB --interval 1D --day 2026-10-21
```

Add `--execute` to upload the selected object. These commands read through the
canonical history layer, which merges SQLite rows with verified Parquet and
refuses unresolved history gaps. They do not initialize a bucket or migrate
existing objects.

Changing an existing deployment from `archive-v2` to `parquet` is a separate
copy-and-verify migration. Keep its current prefix until all referenced objects
and the `LATEST.json` index have been copied and verified. The code does not
delete or silently rename existing archive objects.
