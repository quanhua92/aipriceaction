import gzip
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page, Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_volume import (
    apply_volume_proof,
    digest,
    proof_from_capture,
    validate_volume_proof,
)
from aipriceaction_api.workers import Worker


def excerpt(proof):
    stamp = validate_volume_proof(proof).time
    rows = [r for r in proof["source_rows"] if r["time"] in (stamp - 60, stamp)]
    fields = {
        "t": "time",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "accumulatedVolume": "cumulative_volume",
    }
    return {"symbol": proof["symbol"]} | {
        key: [r[field] for r in rows] for key, field in fields.items()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ("fpt", "tpb"))
async def test_explicit_proof_corrects_only_exact_source_volume_and_preserves_evidence(
    tmp_path, symbol
):
    proof = captured(symbol)
    corrected = validate_volume_proof(proof)
    path = tmp_path / "proofs.json"
    path.write_text(json.dumps([proof]))
    body = excerpt(proof)
    providers = Providers(
        replace(Settings(), vci_history_fallback=True, vci_volume_proofs=path),
        httpx.MockTransport(lambda request: httpx.Response(200, json=[body])),
    )
    try:
        page = await providers.page(
            "vn", corrected.symbol, "1m", corrected.time + 60, provider="vci"
        )
        actual = page.rows[-1]
        assert actual.volume == corrected.volume
        assert (actual.time, actual.open, actual.high, actual.low, actual.close) == (
            corrected.time,
            corrected.open,
            corrected.high,
            corrected.low,
            corrected.close,
        )
        assert page.volume_proofs == (proof,)
        body["accumulatedVolume"][1] += 100
        with pytest.raises(DataError, match="changed since"):
            await providers.page("vn", corrected.symbol, "1m", corrected.time + 60, provider="vci")
    finally:
        await providers.close()


def test_proof_file_does_not_enable_vci_and_duplicate_proofs_are_rejected(tmp_path):
    proof = captured()
    path = tmp_path / "proofs.json"
    path.write_text(json.dumps([proof]))
    with pytest.raises(DataError, match="explicit fallback"):
        Providers(replace(Settings(), vci_volume_proofs=path))
    path.write_text(json.dumps([proof, proof]))
    with pytest.raises(DataError, match="Duplicate"):
        Providers(replace(Settings(), vci_history_fallback=True, vci_volume_proofs=path))


@pytest.mark.asyncio
async def test_volume_normalization_never_hides_conflicting_native_duplicates():
    proof = captured()
    body = excerpt(proof)
    for field in ("t", "o", "h", "l", "c", "v", "accumulatedVolume"):
        body[field].append(body[field][-1])
    body["v"][-1] = validate_volume_proof(proof).volume
    providers = Providers(
        replace(Settings(), vci_history_fallback=True),
        httpx.MockTransport(lambda request: httpx.Response(200, json=[body])),
        vci_volume_proofs=[proof],
    )
    try:
        with pytest.raises(DataError, match="conflicting source"):
            await providers.page("vn", "FPT", "1m", 1758855360, provider="vci")
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_worker_persists_replayed_proof_without_licensing_publication(tmp_path):
    proof = captured()
    corrected = validate_volume_proof(proof)
    repo = Repository(tmp_path / "db")
    repo.initialize()
    repo.queue("vn", "FPT", "1m", "bootstrap", corrected.time, "vci")

    class Source:
        async def page(self, *args, **kwargs):
            return Page([corrected], "vci", cursor=corrected.time, volume_proofs=(proof,))

    worker = Worker(repo, replace(Settings(), vci_history_fallback=True), providers=Source())
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 1
    assert repo.read("vn", "FPT", "1m") == []
    with repo.connect() as con:
        stored = con.execute(
            "SELECT detail,resolved FROM quality WHERE kind='verified_vci_volume'"
        ).fetchone()
        assert json.loads(stored["detail"]) == proof and stored["resolved"] == 1
        assert con.execute("SELECT COUNT(*) FROM snapshot_adoptions").fetchone()[0] == 0
    assert any(r["kind"] == "vci_verification_pending" for r in repo.findings())


def captured(symbol="fpt"):
    return json.loads(
        gzip.decompress(
            (Path(__file__).parent / f"fixtures/vci_{symbol}_volume_proof.json.gz").read_bytes()
        )
    )


def test_actual_ctg_day_requires_explicit_multi_correction_and_reconciles_both():
    proof = captured("ctg")
    keys = {
        "t": "time",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "accumulatedVolume": "cumulative_volume",
    }
    body = {key: [row[field] for row in proof["source_rows"]] for key, field in keys.items()}
    body["symbol"] = "CTG"
    raw = json.dumps([body]).encode()
    with pytest.raises(DataError, match="exactly one"):
        proof_from_capture(
            raw, proof["daily_witnesses"], proof["target_time"], proof["verified_at_ns"]
        )
    total = sum(r["volume"] for r in proof["source_rows"])
    for stamp, expected in zip(proof["correction_times"], (11400, 27000), strict=True):
        rebuilt = proof_from_capture(
            raw, proof["daily_witnesses"], stamp, proof["verified_at_ns"], allow_multiple=True
        )
        corrected = validate_volume_proof(rebuilt)
        original = next(r for r in proof["source_rows"] if r["time"] == stamp)
        row = Candle(**{k: v for k, v in original.items() if k != "cumulative_volume"})
        assert apply_volume_proof(row, rebuilt) == replace(row, volume=expected)
        other = next(t for t in proof["correction_times"] if t != stamp)
        other_raw = next(r for r in proof["source_rows"] if r["time"] == other)
        with pytest.raises(DataError, match="exact original source"):
            apply_volume_proof(
                Candle(**{k: v for k, v in other_raw.items() if k != "cumulative_volume"}), rebuilt
            )
        total += corrected.volume - row.volume
    assert total == 4955000 == proof["source_rows"][-1]["cumulative_volume"]
    assert all(w["volume"] == total for w in proof["daily_witnesses"])


@pytest.mark.parametrize(
    "defect",
    (
        "single_schema",
        "missing",
        "reversed",
        "duplicate",
        "unknown_target",
        "bool_target",
        "bool_schema",
        "bool_time",
        "third_contradiction",
        "gap",
        "peer_volume",
    ),
)
def test_multi_correction_rejects_incomplete_declaration_or_unproven_totals(defect):
    proof = deepcopy(captured("ctg"))
    rows = proof["source_rows"]
    if defect == "single_schema":
        proof["schema_version"] = 1
    elif defect == "missing":
        proof["correction_times"].pop()
    elif defect == "reversed":
        proof["correction_times"].reverse()
    elif defect == "duplicate":
        proof["correction_times"].append(proof["correction_times"][-1])
    elif defect == "unknown_target":
        proof["target_time"] += 60
    elif defect == "bool_target":
        proof["target_time"] = True
    elif defect == "bool_schema":
        proof["schema_version"] = True
    elif defect == "bool_time":
        proof["correction_times"][0] = True
    elif defect == "third_contradiction":
        rows[1]["volume"] += 100
    elif defect == "gap":
        index = next(i for i, r in enumerate(rows) if r["time"] == proof["target_time"])
        rows.pop(index - 1)
    elif defect == "peer_volume":
        proof["daily_witnesses"][0]["volume"] += 100
    proof["source_rows_checksum"] = digest(rows)
    with pytest.raises(DataError):
        validate_volume_proof(proof)


@pytest.mark.asyncio
async def test_multi_day_provider_requires_a_separate_proof_for_each_exact_target():
    first = captured("ctg")
    second = deepcopy(first)
    second["target_time"] = next(t for t in first["correction_times"] if t != first["target_time"])
    fields = {
        "t": "time",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "accumulatedVolume": "cumulative_volume",
    }
    body = {"symbol": "CTG"} | {k: [r[f] for r in first["source_rows"]] for k, f in fields.items()}
    for proofs in ([first], [first, second]):
        providers = Providers(
            replace(Settings(), vci_history_fallback=True),
            httpx.MockTransport(lambda request: httpx.Response(200, json=[body])),
            vci_volume_proofs=proofs,
        )
        try:
            if len(proofs) == 1:
                with pytest.raises(DataError):
                    await providers.page("vn", "CTG", "1m", body["t"][-1] + 60, provider="vci")
            else:
                page = await providers.page("vn", "CTG", "1m", body["t"][-1] + 60, provider="vci")
                assert sum(r.volume for r in page.rows) == 4955000
                assert len(page.volume_proofs) == 2
        finally:
            await providers.close()


@pytest.mark.parametrize("symbol,expected", (("fpt", 5100), ("tpb", 15200)))
def test_actual_full_day_volume_proofs_reconcile_exactly_one_candle(symbol, expected):
    proof = captured(symbol)
    corrected = validate_volume_proof(proof)
    assert corrected.volume == expected
    raw = next(r for r in proof["source_rows"] if r["time"] == corrected.time)
    original = Candle(**{k: v for k, v in raw.items() if k != "cumulative_volume"})
    assert apply_volume_proof(original, proof) == replace(original, volume=expected)
    total = sum(r["volume"] for r in proof["source_rows"]) - original.volume + expected
    assert total == proof["source_rows"][-1]["cumulative_volume"]
    assert all(r["volume"] == total for r in proof["daily_witnesses"])


@pytest.mark.parametrize(
    "defect",
    (
        "checksum",
        "prefix",
        "tail",
        "duplicate",
        "gap",
        "second_contradiction",
        "decreasing",
        "peer_volume",
        "same_peer",
        "peer_day",
        "peer_symbol",
        "uncompleted",
        "hash",
    ),
)
def test_volume_proof_rejects_incomplete_or_uncorroborated_evidence(defect):
    proof = deepcopy(captured())
    rows = proof["source_rows"]
    if defect == "checksum":
        proof["source_rows_checksum"] = "0" * 64
    elif defect == "prefix":
        rows[0]["cumulative_volume"] += 100
    elif defect == "tail":
        rows[-1]["cumulative_volume"] += 100
    elif defect == "duplicate":
        rows.insert(1, deepcopy(rows[0]))
    elif defect == "gap":
        index = next(i for i, row in enumerate(rows) if row["time"] == 1758855300)
        rows.pop(index - 1)
    elif defect == "second_contradiction":
        rows[1]["volume"] += 100
    elif defect == "decreasing":
        rows[1]["cumulative_volume"] = 0
    elif defect == "peer_volume":
        proof["daily_witnesses"][0]["volume"] += 100
    elif defect == "same_peer":
        proof["daily_witnesses"][0]["provider"] = proof["daily_witnesses"][1]["provider"]
    elif defect == "peer_day":
        proof["daily_witnesses"][0]["time"] += 86400
    elif defect == "peer_symbol":
        proof["daily_witnesses"][0]["symbol"] = "TPB"
    elif defect == "uncompleted":
        proof["verified_at_ns"] = (proof["day"] + 3600) * 1_000_000_000
    else:
        proof["source_capture_sha256"] = "not-a-capture-hash"
    if defect != "checksum":
        proof["source_rows_checksum"] = digest(rows)
    with pytest.raises(DataError):
        validate_volume_proof(proof)


@pytest.mark.parametrize(
    "field,value",
    (
        ("symbol", "TPB"),
        ("time", 1758855360),
        ("close", 86800),
        ("volume", 5000),
        ("provider", "legacy-api"),
    ),
)
def test_volume_proof_cannot_correct_a_different_or_revised_source_candle(field, value):
    proof = captured()
    corrected = validate_volume_proof(proof)
    raw = next(r for r in proof["source_rows"] if r["time"] == corrected.time)
    original = Candle(**{k: v for k, v in raw.items() if k != "cumulative_volume"})
    with pytest.raises(DataError, match="exact original source"):
        apply_volume_proof(replace(original, **{field: value}), proof)


def test_capture_builder_requires_the_requested_contradiction_and_equal_arrays():
    proof = captured()
    keys = {
        "t": "time",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "accumulatedVolume": "cumulative_volume",
    }
    body = {key: [row[field] for row in proof["source_rows"]] for key, field in keys.items()}
    body["symbol"] = "FPT"
    rebuilt = proof_from_capture(
        json.dumps([body]).encode(), proof["daily_witnesses"], 1758855300, proof["verified_at_ns"]
    )
    assert validate_volume_proof(rebuilt).volume == 5100
    with pytest.raises(DataError, match="different requested minute"):
        proof_from_capture(
            json.dumps([body]).encode(),
            proof["daily_witnesses"],
            1758855360,
            proof["verified_at_ns"],
        )
    body["v"].pop()
    with pytest.raises(DataError, match="arrays"):
        proof_from_capture(
            json.dumps([body]).encode(),
            proof["daily_witnesses"],
            1758855300,
            proof["verified_at_ns"],
        )
