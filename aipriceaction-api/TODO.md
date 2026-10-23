# Current API migration gates

This is a short current checklist. Dated implementation logs and detailed
historical evidence are preserved in [`legacy/2026-10-22/`](legacy/README.md).

- [ ] Verify the live production deployment and record the exact Rust image,
  data freshness, origin routing, and rollback configuration.
- [ ] Freeze the Python release and resolve all full-suite test failures; the
  latest recorded full run had two schema-version expectation failures.
- [ ] Rehearse either an empty production start or a matched SQLite + Parquet
  archive transfer. Do not transfer SQLite without its indexed archive objects.
- [ ] Verify production S3 permissions, secrets, CORS, provider access, and
  bundled ticker metadata.
- [ ] Start Python API and the all-source worker side by side; verify current
  VN, crypto, Yahoo, and SJC ingestion after restart.
- [x] Run the exact 19-request home-page benchmark; the retained local report
  is [`reports/homepage-19-2026-10-21.json`](reports/homepage-19-2026-10-21.json).
- [ ] Verify every supported web route/parameter, SDK/CLI reads, Notes flows,
  and auth boundaries against production-equivalent data.
- [ ] Resolve the Notes SSR origin integration and confirm rendered web charts
  against the Python endpoint.
- [ ] Cut over ingress only after data freshness and route acceptance pass;
  keep the Rust rollback snapshot until the observation period ends.
