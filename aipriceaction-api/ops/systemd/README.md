# Prepared Linux process supervision

These units supervise the existing API and workers on a Linux host. Compose
still runs RustFS alone. SQLite, DuckDB and the Python processes remain on the
host; no scheduler or message broker is added.

The examples assume an existing `aipriceaction` user/group and a project at
`/srv/aipriceaction-api`, with its installed `.venv`, `.env`, configured watchlist
and verified database. Adapt both unit paths and `AIPA_API_HOME` together. The
service user needs access to `.env`, SQLite's directory (including WAL/SHM files),
archive cache and temporary storage. Python loads the project `.env` itself.
Do not replace the current database with an empty initialization.

The API listens on loopback port 3001. Worker instances are `vn`, `crypto` and
`yahoo`; each requires its checked-in scope file. VN's explicit 57-stock list
matches the verified local worker and excludes both indices. Yahoo's watchlist
contains only the licensed native intervals; preserve that configuration.
There is no SJC worker example while its upstream remains unavailable. Review
the direct VN flag against the target host's configured proxy policy.

`Type=exec` reports an executable/user setup failure at startup. `Restart=always`
resumes an unexpectedly exited process, including a clean SIGTERM exit sent
outside the manager; an explicit manager stop remains stopped. Restarts wait
ten seconds. `$WORKER_GLOBAL_ARGS` and `$WORKER_SYMBOL_ARGS` expand into separate
arguments using systemd's command syntax, without a shell. See the upstream
[service documentation](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml).

Stop uses SIGTERM and allows up to 300 seconds for cooperative cleanup and
verified archival work. Increase that timeout if measured archive transfers
need longer. A forced kill still depends on durable lease expiry and retry;
process restart is not proof of archive completion or data quality.

Before activation on a Linux staging host:

1. Check the adapted units with `systemd-analyze verify` and confirm the required
   source files expand to the intended CLI arguments.
2. Stop the matching manually started processes and confirm they are terminal.
   Run one worker per source scope and one API process against this database.
3. Verify loopback `/health`, fresh successful `source_checks` for every eligible
   minute series, protected data and archive readback. Use consistent backup
   images and `scripts/check_worker_restart.py` for the data comparison.
4. Exercise manager stop/start and one unexpected process exit. Confirm a new
   PID resumes ingestion, inspect manager logs, and check data again.
5. Observe a real UTC-day transition, verified retention maintenance and cold
   historical reads before declaring unattended operation accepted.

These files are prepared examples. They have not been installed or started;
the current macOS workspace has no systemd. Linux verification, real supervisor
recovery, multi-day uptime and production routing remain acceptance gates.
