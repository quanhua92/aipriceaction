# Optional Linux systemd deployment examples

These units are prepared examples for running the Python API and worker directly on a Linux host. They have not been installed or accepted in production. Docker Compose is the maintained local workflow and starts API, worker, initializer, and RustFS together.

The sample service expects the project under `/srv/aipriceaction-api`, a configured `.env`, an existing SQLite database, and an S3-compatible archive. Adapt all paths and storage credentials before use. The API defaults to loopback port `8899`; do not expose it until CORS, secrets, proxy rules, file permissions, and rollback are configured.

The sample worker scopes and unit settings are deployment examples, not the current Compose worker's market scope. Update the worker command to match the selected catalog/universe and verify it ingests VN, crypto, Yahoo, and SJC as intended. There is no systemd in the current macOS workspace.

Before enabling these units, run `systemd-analyze verify`, stop competing API/worker processes, check `/health`, and exercise restart/resume, archive reads, and a real retention boundary. The full production migration and rollback sequence is in [`../MIGRATION.md`](../../MIGRATION.md).
