import gzip
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_peer_volume import peer_proof_from_capture
from aipriceaction_api.vci_volume import apply_volume_proof, digest, validate_volume_proof


def fixture(name):
    return gzip.decompress(
        (Path(__file__).parent / f"fixtures/vci_gas_peer_volume_{name}.json.gz").read_bytes()
    )


def proof():
    return json.loads(fixture("proof"))


def test_actual_peer_proof_reconciles_existing_target_without_synthesizing_gap(tmp_path):
    p = proof()
    rebuilt = peer_proof_from_capture(
        fixture("capture"),
        p["daily_witnesses"],
        p["minute_witnesses"],
        p["target_time"],
        p["verified_at_ns"],
        p["witness_capture_sha256"],
    )
    assert rebuilt == p
    corrected = validate_volume_proof(p)
    original = next(r for r in p["source_rows"] if r["time"] == corrected.time)
    raw = Candle(**{k: v for k, v in original.items() if k != "cumulative_volume"})
    assert apply_volume_proof(raw, p) == replace(raw, volume=1300)
    assert corrected.time == 1783664400
    assert 1783664460 not in {r["time"] for r in p["source_rows"]}
    assert sum(r["volume"] for r in p["source_rows"]) + 200 == 512700
    repo = Repository(tmp_path / "db")
    repo.initialize()
    repo.record_volume_proofs([p])
    with repo.connect() as con:
        stored = con.execute(
            "SELECT detail,resolved FROM quality WHERE kind='verified_vci_volume'"
        ).fetchone()
        assert json.loads(stored["detail"]) == p and stored["resolved"] == 1


@pytest.mark.parametrize(
    "defect",
    (
        "checksum",
        "hash",
        "witness_hash",
        "schema_bool",
        "future",
        "prefix",
        "duplicate",
        "target",
        "missing_peer",
        "peer_volume",
        "peer_price",
        "another_cumulative",
        "gap",
        "daily_volume",
        "daily_peer",
        "extra_change",
    ),
)
def test_peer_proof_rejects_partial_changed_or_uncorroborated_evidence(defect):
    p = deepcopy(proof())
    rows = p["source_rows"]
    i = next(i for i, r in enumerate(rows) if r["time"] == p["target_time"])
    if defect == "checksum":
        p["source_rows_checksum"] = "0" * 64
    elif defect == "hash":
        p["source_capture_sha256"] = "bad"
    elif defect == "witness_hash":
        p["witness_capture_sha256"].pop()
    elif defect == "schema_bool":
        p["schema_version"] = True
    elif defect == "future":
        p["verified_at_ns"] = (p["day"] + 3600) * 1000000000
    elif defect == "prefix":
        rows[0]["cumulative_volume"] += 100
    elif defect == "duplicate":
        rows.insert(1, deepcopy(rows[0]))
    elif defect == "target":
        p["target_time"] += 60
    elif defect == "missing_peer":
        p["minute_witnesses"]["dnse"].pop()
    elif defect == "peer_volume":
        p["minute_witnesses"]["dnse"][i]["volume"] += 100
    elif defect == "peer_price":
        p["minute_witnesses"]["vndirect"][i]["open"] -= 1
    elif defect == "another_cumulative":
        rows[1]["cumulative_volume"] += 100
    elif defect == "gap":
        rows[i + 1]["cumulative_volume"] -= 100
    elif defect == "daily_volume":
        p["daily_witnesses"][0]["volume"] += 100
    elif defect == "daily_peer":
        p["daily_witnesses"][0]["provider"] = "dnse"
    elif defect == "extra_change":
        for peer in p["minute_witnesses"].values():
            peer[0]["volume"] += 100
    if defect != "checksum":
        p["source_rows_checksum"] = digest(rows)
    p["minute_witnesses_checksum"] = digest(p["minute_witnesses"])
    with pytest.raises(DataError):
        validate_volume_proof(p)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", (None, "target_volume", "neighbor_cumulative", "missing_cumulative", "new_timestamp")
)
async def test_provider_replays_peer_proof_and_rejects_revised_native_observations(change):
    p = proof()
    body = json.loads(fixture("capture"))
    native = body[0]
    i = next(i for i, t in enumerate(native["t"]) if int(t) == p["target_time"])
    if change == "target_volume":
        native["v"][i] += 100
    elif change == "neighbor_cumulative":
        native["accumulatedVolume"][i + 1] += 100
    elif change == "missing_cumulative":
        native.pop("accumulatedVolume")
    elif change == "new_timestamp":
        native["t"][i + 1] = str(p["target_time"] + 60)
    providers = Providers(
        replace(Settings(), vci_history_fallback=True),
        httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        vci_volume_proofs=[p],
    )
    try:
        if change:
            with pytest.raises(DataError):
                await providers.page(
                    "vn", "GAS", "1m", p["day"] + 86400, count=500, start=p["day"], provider="vci"
                )
        else:
            page = await providers.page(
                "vn", "GAS", "1m", p["day"] + 86400, count=500, start=p["day"], provider="vci"
            )
            assert len(page.rows) == 181 and sum(r.volume for r in page.rows) == 512700
            assert next(r for r in page.rows if r.time == p["target_time"]).volume == 1300
            assert page.volume_proofs == (p,)
    finally:
        await providers.close()
