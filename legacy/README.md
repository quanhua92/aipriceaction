# Legacy archive

This tree preserves superseded Rust backend code and older architecture documents.
Nothing under `legacy/` is the supported API runtime or an active deployment
workflow.

- [`rust/`](rust/README.md): the complete retired Rust project, its former
  Docker image publishing workflow, and its archived guides.
- [`docs/root/`](docs/root/README.md): previous root-level architecture and
  contributor documentation.
- [Current Python API](../aipriceaction-api/README.md) and
  [production migration runbook](../aipriceaction-api/MIGRATION.md).

The Rust API source, local files, and ignored build output were moved intact;
RustFS remains a separate S3-compatible object-store service used by the Python
Compose stack.
