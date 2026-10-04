import json
from dataclasses import asdict, replace

import pytest

from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.storage import Repository
from scripts.rehearse_vci_minute_storage import load_proposal


@pytest.mark.parametrize(
    "defect", (None, "price", "timestamp", "missing", "two_volumes", "receipt_time", "published")
)
def test_rehearsal_requires_preserved_prices_timestamps_and_exact_volume_receipt(tmp_path, defect):
    row = Candle("vn", "FPT", "1m", 1758855300, 100, 110, 90, 100, 4900, "vci")
    original = [row, replace(row, time=row.time + 60)]
    proposed = [replace(row, volume=5100), original[1]]
    if defect == "price":
        proposed[0] = replace(proposed[0], close=101)
    elif defect == "timestamp":
        proposed[0] = replace(proposed[0], time=row.time - 60)
    elif defect == "missing":
        proposed.pop()
    elif defect == "two_volumes":
        proposed[1] = replace(proposed[1], volume=5100)
    receipt = {
        "main_publication": defect == "published",
        "original": asdict(row),
        "proposed": asdict(replace(row, volume=5100)),
    }
    if defect == "receipt_time":
        receipt["proposed"]["time"] += 60
    before = Repository(tmp_path / "candidate.sqlite3")
    after = Repository(tmp_path / "volume-repair-proposal/candidate.sqlite3")
    before.initialize()
    after.initialize()
    before.put(original)
    after.put(proposed)
    (tmp_path / "volume-repair-proposal/receipt.json").write_text(json.dumps(receipt))
    if defect:
        with pytest.raises(DataError):
            load_proposal(tmp_path, "FPT")
    else:
        accepted = load_proposal(tmp_path, "FPT")
        assert [r.time for r in accepted] == [r.time for r in original]
        assert [r.volume for r in accepted] == [5100, 4900]
