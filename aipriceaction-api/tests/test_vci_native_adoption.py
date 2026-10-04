import gzip
import json
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_adoption import adopt_native_snapshot, validate_native_adoption
from aipriceaction_api.workers import Worker


@pytest.fixture
async def system(tmp_path):
    raw = gzip.decompress((Path(__file__).parent / "fixtures/vci_fpt_recent.json.gz").read_bytes())
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        vci_history_fallback=True,
        requests_per_minute=100000,
    )
    providers = Providers(
        settings, httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
    )
    repo = Repository(settings.database)
    repo.initialize()
    page = await providers.page("vn", "FPT", "1m", count=2000, provider="vci")
    repo.put([replace(r, revision="native-snapshot") for r in page.rows])
    try:
        yield repo, providers, settings
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_native_handoff_replays_source_and_licenses_fresh_same_provider_updates(system):
    repo, providers, settings = system
    original = repo.read("vn", "FPT", "1m")
    preview = await adopt_native_snapshot(repo, providers, "FPT")
    assert preview["dry_run"] and len(repo.adoptions()) == 0
    assert repo.read("vn", "FPT", "1m") == original
    result = await adopt_native_snapshot(repo, providers, "FPT", execute=True)
    assert not result["dry_run"] and result["evidence"]["matched_rows"] == 2000
    assert repo.read("vn", "FPT", "1m") == original
    worker = Worker(repo, settings, providers=providers)
    assert await worker.sync({"source": "vn", "symbol": "FPT", "intervals": ["1m"]}, "1m") == 40
    current = repo.read("vn", "FPT", "1m")
    repo.validate_basis(current)
    assert all(
        (a.time, a.open, a.high, a.low, a.close, a.volume, a.provider, a.revision)
        == (b.time, b.open, b.high, b.low, b.close, b.volume, b.provider, b.revision)
        for a, b in zip(original, current, strict=True)
    )
    assert max(r.updated_at for r in current) > result["evidence"]["verified_at_ns"]
    archive = Archive(repo, settings)
    obj = archive.publish(current[-40:], require_current=True)
    restored = Repository(settings.database.parent / "restored")
    restored.initialize()
    remote = Archive(restored, replace(settings, database=restored.path))
    assert remote.restore_index() == 1
    restored.validate_basis(remote.read(obj, refresh=True))
    assert len(restored.adoptions()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("price", "volume", "tail", "hole", "provider"))
async def test_native_handoff_rejects_source_disagreement_or_missing_observations(system, defect):
    repo, providers, _ = system
    page = providers.page

    async def changed(*args, **kwargs):
        result = await page(*args, **kwargs)
        if defect == "price":
            result.rows[0] = replace(result.rows[0], close=result.rows[0].close + 0.01)
        elif defect == "volume":
            result.rows[0] = replace(result.rows[0], volume=result.rows[0].volume + 1)
        elif defect == "tail":
            result.rows.pop()
        elif defect == "hole":
            result.rows.pop(100)
        else:
            result.provider = "vps"
        return result

    providers.page = changed
    with pytest.raises(DataError):
        await adopt_native_snapshot(repo, providers, "FPT", execute=True)
    assert not repo.adoptions()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ("kind", "provider", "snapshot_provider", "symbol", "checksum", "native", "time")
)
async def test_native_handoff_certificate_rejects_tampering(system, defect):
    repo, providers, _ = system
    await adopt_native_snapshot(repo, providers, "FPT", execute=True)
    record = deepcopy(repo.adoptions()[0])
    evidence = json.loads(record["evidence"])
    if defect == "kind":
        evidence["kind"] = "exact_snapshot_overlap"
    elif defect in ("provider", "snapshot_provider", "symbol"):
        record[defect] = "legacy-api"
    elif defect == "checksum":
        evidence["overlap_checksum"] = "0" * 64
    elif defect == "native":
        evidence["native_overlap"][0]["volume"] += 1
    else:
        evidence["snapshot_overlap"][0]["updated_at"] = time.time_ns() + 10**12
    record["evidence"] = json.dumps(evidence)
    with pytest.raises(DataError):
        validate_native_adoption(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ("mutation", "lease"))
async def test_native_handoff_guards_concurrent_snapshot_changes_and_workers(system, race):
    repo, providers, _ = system
    page = providers.page

    async def changed(*args, **kwargs):
        result = await page(*args, **kwargs)
        if race == "mutation":
            row = repo.read("vn", "FPT", "1m", limit=1)[0]
            repo.put([replace(row, volume=row.volume + 1)])
        else:
            assert repo.live_claim("vn", "FPT", "1m", "other", lease=300)
        return result

    providers.page = changed
    with pytest.raises(DataError):
        await adopt_native_snapshot(repo, providers, "FPT", execute=True)
    assert not repo.adoptions()


@pytest.mark.asyncio
async def test_native_handoff_preserves_normalization_proof_scope(system):
    repo, providers, _ = system
    await adopt_native_snapshot(repo, providers, "FPT", execute=True)
    record = repo.adoptions()[0]
    evidence = json.loads(record["evidence"])
    proof = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/vci_fpt_volume_proof.json.gz").read_bytes()
        )
    )
    evidence["volume_proofs"] = [proof]
    record["evidence"] = json.dumps(evidence)
    with pytest.raises(DataError, match="normalized witness"):
        validate_native_adoption(record)


@pytest.mark.asyncio
async def test_native_handoff_requires_vci_enablement_and_a_native_source_snapshot(system):
    repo, providers, settings = system
    providers.settings = replace(settings, vci_history_fallback=False)
    with pytest.raises(DataError, match="explicit fallback"):
        await adopt_native_snapshot(repo, providers, "FPT", execute=True)
    providers.settings = settings
    with repo.connect() as con:
        con.execute("UPDATE series SET provider='legacy-api' WHERE symbol='FPT'")
    with pytest.raises(DataError, match="ready populated VCI"):
        await adopt_native_snapshot(repo, providers, "FPT", execute=True)
    assert not repo.adoptions()
