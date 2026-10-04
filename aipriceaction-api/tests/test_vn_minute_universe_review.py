import json
from types import SimpleNamespace

import pytest

from aipriceaction_api.domain import DataError
from scripts import review_vn_minute_universe


def test_compact_report_preserves_all_counts_and_bounds_samples():
    values = list(range(100))
    row = {
        "symbol": "FPT",
        "interval": "1m",
        "sqlite_rows": 100,
        "volume_correction_receipts": 1,
        "providers": {
            "vci": {
                "shared": 100,
                "provider_only": values,
                "sqlite_only": [],
                "price_disagreements": values,
                "volume_disagreements": values,
            }
        },
        "unanimous_provider_conflicts": values,
        "missing_unanimous_provider_timestamps": [],
    }
    compact = review_vn_minute_universe.compact_local(row)
    assert compact["providers"]["vci"]["counts"]["volume_disagreements"] == 100
    assert len(compact["providers"]["vci"]["samples"]["volume_disagreements"]) == 20
    assert compact["unanimous_provider_conflicts"]["count"] == 100
    assert len(compact["unanimous_provider_conflicts"]["samples"]) == 20
    assert len(row["unanimous_provider_conflicts"]) == 100


@pytest.mark.parametrize("partial", [True, False])
def test_partial_and_misdeclared_complete_collections_are_rejected(tmp_path, partial):
    summary = {"completed": not partial, "symbols": ["FPT"], "batches": []}
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(DataError, match="incomplete|missing selected"):
        review_vn_minute_universe.run(
            SimpleNamespace(audit=tmp_path, output=tmp_path / "review", allow_partial=False)
        )
    assert not (tmp_path / "review").exists()
