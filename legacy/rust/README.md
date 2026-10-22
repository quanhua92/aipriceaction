# Deprecated Rust backend archive

The retired Rust API project has moved to [`aipriceaction/`](aipriceaction/).
It is kept for historical source and compatibility investigations and is not
the supported server to deploy. The former Rust image publishing workflow is
archived in [`workflows/`](workflows/); it is no longer under
`.github/workflows/` and will not run automatically.

The old Rust documentation is in [`docs/rust/`](docs/rust/README.md), including the original project README at [`docs/rust/README-previous.md`](docs/rust/README-previous.md). It records
the former PostgreSQL/Redis-first design and provider experiments. These claims
do not describe the Python API. In particular, RustFS is still used by the
Python service as an S3-compatible object store; it is not part of the retired
API server.

Use the [current Python API guide](../../aipriceaction-api/README.md) and its
[cutover runbook](../../aipriceaction-api/MIGRATION.md). The last recorded
production baseline still showed the Rust service; verify deployment evidence
before treating cutover as complete.
