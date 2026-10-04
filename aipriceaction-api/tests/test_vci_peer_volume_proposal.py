import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page
from scripts.propose_vci_peer_volume_corrections import proposal


def evidence():
    body = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/gas_peer_volume_controls.json.gz").read_bytes()
        )
    )
    pages = {
        r["feed"]: Page([Candle(**c) for c in r["rows"]], r["feed"], cursor=r["cursor"])
        for r in body["controls"]
    }
    daily = [Candle(**r) for r in body["daily"]]
    return pages, daily


def test_actual_native_peer_disagreement_proposes_only_an_existing_exact_target():
    pages, daily = evidence()
    before = [r.record() for r in pages["vci"].rows]
    result = proposal("GAS", "2026-07-10", pages, daily, 512700)
    assert result["target_time"] == 1783664400
    assert (result["original_volume"], result["proposed_volume"]) == (1100, 1300)
    assert result["corrected_daily_volume"] == 512700
    assert result["observed_rows"] == 181
    assert not result["runtime_licensed"] and not result["main_publication"]
    assert 1783664460 not in {r.time for r in pages["vci"].rows}
    assert [r.record() for r in pages["vci"].rows] == before


@pytest.mark.parametrize(
    "defect",
    (
        "missing_peer_minute",
        "different_peer_volume",
        "second_disagreement",
        "wrong_target_price",
        "partial_day",
        "wrong_daily_total",
        "duplicate_daily_peer",
        "cumulative_total",
        "wrong_symbol",
    ),
)
def test_proposal_rejects_unproven_attribution_and_incomplete_evidence(defect):
    pages, daily = evidence()
    index = next(i for i, r in enumerate(pages["vndirect"].rows) if r.time == 1783664400)
    cumulative = 512700
    if defect == "missing_peer_minute":
        pages["dnse"].rows.pop(index)
    elif defect == "different_peer_volume":
        pages["dnse"].rows[index] = replace(pages["dnse"].rows[index], volume=1200)
    elif defect == "second_disagreement":
        for feed in ("vndirect", "dnse"):
            pages[feed].rows[0] = replace(
                pages[feed].rows[0], volume=pages[feed].rows[0].volume + 100
            )
    elif defect == "wrong_target_price":
        pages["vndirect"].rows[index] = replace(
            pages["vndirect"].rows[index], open=pages["vndirect"].rows[index].open - 1
        )
    elif defect == "partial_day":
        pages["dnse"].cursor = 1783641600
    elif defect == "wrong_daily_total":
        daily[0] = replace(daily[0], volume=daily[0].volume + 100)
    elif defect == "duplicate_daily_peer":
        daily[0] = replace(daily[0], provider="dnse")
    elif defect == "cumulative_total":
        cumulative += 100
    elif defect == "wrong_symbol":
        pages["dnse"].rows[index] = replace(pages["dnse"].rows[index], symbol="MWG")
    with pytest.raises(DataError):
        proposal("GAS", "2026-07-10", pages, daily, cumulative)
