import asyncio
import gzip
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.vci_volume import proof_from_capture, validate_volume_proof
from scripts.replay_vci_candidate_pages import replay_record


def inputs(tmp_path, licensed):
    saved = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/vci_fpt_volume_proof.json.gz").read_bytes()
        )
    )
    fields = {
        "t": "time",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "accumulatedVolume": "cumulative_volume",
    }
    body = {
        "symbol": saved["symbol"],
        **{key: [row[field] for row in saved["source_rows"]] for key, field in fields.items()},
    }
    raw = json.dumps([body]).encode()
    proof = proof_from_capture(
        raw, saved["daily_witnesses"], validate_volume_proof(saved).time, saved["verified_at_ns"]
    )
    path = tmp_path / "raw.json"
    path.write_bytes(raw)
    catalog = tmp_path / "proofs.json"
    catalog.write_text(json.dumps([proof] if licensed else []))
    day = datetime.fromtimestamp(proof["day"], UTC).strftime("%Y-%m-%d")
    record = {
        "start_date": day,
        "end_date": day,
        "error": "Original volume contradiction",
        "window": {"pages": []},
        "captures": [
            {
                "path": str(path),
                "status": 200,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    settings = replace(Settings(), vci_history_fallback=True, vci_volume_proofs=catalog)
    return proof, settings, record, path


@pytest.mark.parametrize("licensed", [True, False])
def test_captured_page_replay_requires_proof_and_does_not_certify_requested_history(
    tmp_path, licensed
):
    proof, settings, record, path = inputs(tmp_path, licensed)
    before = path.read_bytes()
    result = asyncio.run(replay_record(settings, proof["symbol"], record))
    assert result["captured_pages_passed"] is licensed
    assert not result["requested_year_proven"]
    if licensed:
        assert result["pages"][0]["applied_proofs"] == 1
        assert result["pages"][0]["rows"] == len(proof["source_rows"])
        assert result["next_cursor"] == proof["source_rows"][0]["time"]
    else:
        assert "volume contradicts" in result["error"]
    assert path.read_bytes() == before


def test_changed_capture_is_rejected_before_parser_replay(tmp_path):
    proof, settings, record, path = inputs(tmp_path, True)
    path.write_bytes(b"[]")
    with pytest.raises(DataError, match="identity changed"):
        asyncio.run(replay_record(settings, proof["symbol"], record))
