import asyncio
import json
import signal
import subprocess
import sys
import time
from argparse import Namespace
from dataclasses import replace

import pytest

from aipriceaction_api import cli
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, cutoff
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def system(tmp_path):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"vn": [{"symbol": "FPT", "intervals": ["1D"]}]}))
    settings = replace(
        Settings(), database=tmp_path / "db", archive_backend="filesystem", watchlist=watchlist
    )
    repo = Repository(settings.database)
    repo.initialize()
    return repo, settings


def test_release_preserves_other_owners_sentinels_and_job_progress(system):
    repo, _ = system
    repo.queue("vn", "FPT", "1D", "bootstrap", cutoff(3), "vps")
    repo.queue("vn", "VCB", "1D", "bootstrap", cutoff(3), "vps")
    job = repo.claim_job("exiting", allowed=[("vn", "FPT", "1D")])
    other = repo.claim_job("peer", allowed=[("vn", "VCB", "1D")])
    assert repo.live_claim("vn", "FPT", "1D", "exiting")
    assert repo.live_claim("vn", "VCB", "1D", "peer")
    assert repo.live_claim("vn", "FPT", "sentinel", "exiting-sentinel", lease=86400)
    repo.release_worker_leases("exiting")
    with repo.connect() as con:
        released = dict(con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
        assert (
            dict(con.execute("SELECT * FROM jobs WHERE id=?", (other["id"],)).fetchone()) == other
        )
        assert {r[0] for r in con.execute("SELECT owner FROM live_leases")} == {
            "peer",
            "exiting-sentinel",
        }
    assert released == job | {"status": "pending", "lease_owner": None, "lease_until": 0}
    assert repo.claim_job("replacement", allowed=[("vn", "FPT", "1D")])["id"] == job["id"]


def test_catalog_sized_allowed_set_does_not_exceed_sqlite_expression_depth(system):
    repo, _ = system
    repo.queue("vn", "FPT", "1D", "bootstrap", cutoff(3), "vps")
    allowed = [("vn", f"S{i:04d}", "1D") for i in range(1500)]
    allowed.append(("vn", "FPT", "1D"))
    claimed = repo.claim_job("catalog-worker", allowed=allowed)
    assert (claimed["source"], claimed["symbol"], claimed["interval"]) == (
        "vn",
        "FPT",
        "1D",
    )


def test_allowed_order_prioritizes_curated_jobs_over_catalog_tail(system):
    repo, _ = system
    repo.queue("vn", "AAA", "1D", "bootstrap", cutoff(3), "vps")
    repo.queue("vn", "VCB", "1D", "bootstrap", cutoff(3), "vps")
    claimed = repo.claim_job("catalog-worker", allowed=[("vn", "VCB", "1D"), ("vn", "AAA", "1D")])
    assert claimed["symbol"] == "VCB"


def test_catalog_claims_every_unstarted_tail_before_resuming_deep_history(system):
    repo, _ = system
    first = repo.queue("vn", "VCB", "1m", "bootstrap", cutoff(1), "vps")
    second = repo.queue("vn", "FPT", "1m", "bootstrap", cutoff(1), "vps")
    job = repo.claim_job("worker", allowed=[("vn", "VCB", "1m"), ("vn", "FPT", "1m")])
    assert job["id"] == first
    stamp = int(time.time()) // 60 * 60
    row = Candle("vn", "VCB", "1m", stamp, 100, 101, 99, 100, 10, "vps", job["revision"])
    repo.stage(job, [row], stamp, "vps")

    next_job = repo.claim_job(
        "worker", allowed=[("vn", "VCB", "1m"), ("vn", "FPT", "1m")]
    )
    assert next_job["id"] == second


@pytest.mark.asyncio
async def test_cancelled_worker_closes_provider_and_immediately_resumes_staged_repair(system):
    repo, settings = system
    stamp = int(time.time()) // 86400 * 86400 - 3 * 86400
    original = Candle("vn", "FPT", "1D", stamp, 100, 101, 99, 100, 10, "vps", "original")
    repo.put([original])
    published = repo.read("vn", "FPT", "1D")
    repo.queue("vn", "FPT", "1D", "repair", cutoff(3), "vps")
    prepared = repo.claim_job("preparer")
    repo.stage(prepared, [replace(original, revision=prepared["revision"])], stamp, "vps")
    repo.schedule("vn", "FPT", "1D", int(time.time()) + 3600)
    with repo.connect() as con:
        before = dict(con.execute("SELECT * FROM jobs WHERE id=?", (prepared["id"],)).fetchone())
        staging = [tuple(r) for r in con.execute("SELECT * FROM staging")]
    entered = asyncio.Event()

    class BlockedProvider:
        closed = False

        async def page(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    provider = BlockedProvider()
    worker = Worker(repo, settings, provider)
    task = asyncio.create_task(worker.run(source="vn"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.closed and repo.read("vn", "FPT", "1D") == published
    with repo.connect() as con:
        after = dict(con.execute("SELECT * FROM jobs WHERE id=?", (prepared["id"],)).fetchone())
        assert [tuple(r) for r in con.execute("SELECT * FROM staging")] == staging
    assert after == before
    resumed = repo.claim_job("replacement", allowed=[("vn", "FPT", "1D")])
    assert resumed["id"] == prepared["id"] and resumed["cursor"] == stamp


@pytest.mark.asyncio
async def test_provider_close_failure_still_releases_worker_claims(system, monkeypatch):
    repo, settings = system
    repo.queue("vn", "FPT", "1D", "bootstrap", cutoff(3), "vps")

    class BrokenClose:
        async def close(self):
            raise RuntimeError("close failed")

    worker = Worker(repo, settings, BrokenClose())

    async def claim_only():
        assert repo.claim_job(worker.owner)

    monkeypatch.setattr(worker, "cycle", claim_only)
    with pytest.raises(RuntimeError, match="close failed"):
        await worker.run(once=True, source="vn")
    assert repo.claim_job("replacement")["lease_owner"] == "replacement"


@pytest.mark.asyncio
async def test_sigterm_requests_one_cancellation_and_restores_previous_handler(monkeypatch):
    entered, cleaned = asyncio.Event(), asyncio.Event()
    previous = signal.getsignal(signal.SIGTERM)

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    monkeypatch.setattr(cli, "execute", blocked)
    task = asyncio.create_task(cli.execute_with_shutdown(Namespace(command="worker"), Settings()))
    await entered.wait()
    handler = signal.getsignal(signal.SIGTERM)
    handler(signal.SIGTERM, None)
    handler(signal.SIGTERM, None)
    await task
    assert cleaned.is_set() and signal.getsignal(signal.SIGTERM) == previous


@pytest.mark.asyncio
async def test_unrelated_cancellation_is_not_swallowed(monkeypatch):
    entered = asyncio.Event()
    previous = signal.getsignal(signal.SIGTERM)

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(cli, "execute", blocked)
    task = asyncio.create_task(cli.execute_with_shutdown(Namespace(command="worker"), Settings()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signal.getsignal(signal.SIGTERM) == previous


def test_real_cli_sigterm_releases_a_blocked_bootstrap_job(tmp_path):
    database, entered, closed = (tmp_path / name for name in ("db", "entered", "closed"))
    script = tmp_path / "worker.py"
    script.write_text(
        "import asyncio,sys\n"
        "from pathlib import Path\n"
        "from aipriceaction_api import cli\n"
        "from aipriceaction_api.providers import Providers\n"
        "async def blocked(self,*args,**kwargs):\n"
        " Path(sys.argv[2]).write_text('entered')\n"
        " await asyncio.Event().wait()\n"
        "original_close=Providers.close\n"
        "async def close(self):\n"
        " await original_close(self)\n"
        " Path(sys.argv[3]).write_text('closed')\n"
        "Providers.page=blocked\nProviders.close=close\n"
        "sys.exit(cli.main(['--database',sys.argv[1],'--archive-backend','filesystem',"
        "'worker','--source','crypto','--symbol','BTCUSDT','--interval','1m']))\n"
    )
    process = subprocess.Popen(
        [sys.executable, str(script), str(database), str(entered), str(closed)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not entered.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.exists()
        repo = Repository(database)
        with repo.connect() as con:
            job = dict(con.execute("SELECT * FROM jobs WHERE status='running'").fetchone())
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert closed.exists() and "Traceback" not in stderr
        with repo.connect() as con:
            released = dict(con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
        assert released == job | {"status": "pending", "lease_owner": None, "lease_until": 0}
        assert repo.claim_job("replacement")["id"] == job["id"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_real_cli_sigterm_waits_for_verified_archive_transfer_before_exiting(tmp_path):
    database, entered, closed, release = (
        tmp_path / name for name in ("db", "entered", "closed", "release")
    )
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"crypto": [{"symbol": "BTCUSDT", "intervals": ["1D"]}]}))
    repo = Repository(database)
    repo.initialize()
    repo.put(
        [Candle("crypto", "BTCUSDT", "1D", cutoff(3) - 86400, 100, 101, 99, 100, 10, "binance")]
    )
    repo.schedule("crypto", "BTCUSDT", "1D", int(time.time()) + 3600)
    original = repo.read("crypto", "BTCUSDT", "1D")
    script = tmp_path / "archive_worker.py"
    script.write_text(
        "import sys,time\nfrom dataclasses import replace\nfrom pathlib import Path\n"
        "from aipriceaction_api import cli\n"
        "from aipriceaction_api.archive import FileStore\n"
        "from aipriceaction_api.config import Settings\n"
        "from aipriceaction_api.providers import Providers\n"
        "root=Path(sys.argv[1])\n"
        "settings=replace(Settings(),database=root/'db',archive_backend='filesystem',"
        "object_dir=root/'objects',cache_dir=root/'cache',watchlist=root/'watchlist.json')\n"
        "Settings.from_env=classmethod(lambda cls:settings)\n"
        "put=FileStore.put\n"
        "def blocked_put(self,key,path):\n"
        " if key.endswith('.parquet'):\n"
        "  (root/'entered').write_text('entered')\n"
        "  while not (root/'release').exists(): time.sleep(0.01)\n"
        " return put(self,key,path)\n"
        "FileStore.put=blocked_put\n"
        "original_close=Providers.close\n"
        "async def close(self):\n"
        " await original_close(self)\n"
        " (root/'closed').write_text('closed')\n"
        "Providers.close=close\n"
        "sys.exit(cli.main(['worker','--source','crypto','--interval','1D','--archive-daily']))\n"
    )
    process = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not entered.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.exists()
        process.terminate()
        deadline = time.monotonic() + 10
        while not closed.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert closed.exists() and process.poll() is None
        process.send_signal(signal.SIGTERM)
        time.sleep(0.05)
        assert process.poll() is None
        assert repo.read("crypto", "BTCUSDT", "1D") == original
        release.write_text("resume transfer")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert not repo.read("crypto", "BTCUSDT", "1D")
        from aipriceaction_api.archive import Archive

        settings = replace(
            Settings(),
            database=database,
            archive_backend="filesystem",
            object_dir=tmp_path / "objects",
            cache_dir=tmp_path / "cache",
        )
        archive = Archive(repo, settings)
        assert len(repo.archives()) == 1
        assert archive.read(repo.archives()[0], refresh=True) == original
        with repo.connect() as con:
            assert not con.execute(
                "SELECT 1 FROM live_leases WHERE symbol='__ARCHIVE_WRITER__'"
            ).fetchone()
    finally:
        release.touch()
        if process.poll() is None:
            process.kill()
            process.communicate()
