import hashlib
import json
from datetime import date
from types import SimpleNamespace

import pytest

from aipriceaction_api.domain import DataError
from scripts.audit_vn_provider_dates import expected_dates, run


def catalog(tmp_path):
    sources = []
    for exchange in ("HOSE", "HNX"):
        pdf = tmp_path / f"{exchange}.pdf"
        pdf.write_bytes(b"%PDF-1.4 calendar fixture")
        declaration = {
            "schema": 1,
            "exchange": exchange,
            "year": 2026,
            "weekdays": [0, 1, 2, 3, 4],
            "symbols": ["VNINDEX", "VN30"] if exchange == "HOSE" else [],
            "closed_ranges": [["2026-01-01", "2026-01-01"]],
            "explicit_closed_weekends": [],
            "source": {
                "bytes": pdf.stat().st_size,
                "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            },
        }
        path = tmp_path / f"{exchange}.json"
        path.write_text(json.dumps(declaration))
        sources.append(
            {"exchange": exchange, "year": 2026, "calendar": str(path), "source": str(pdf)}
        )
    return {"sources": sources}


@pytest.mark.parametrize("defect", [None, "source", "different_schedule", "duplicate"])
def test_source_verified_equal_schedules_required(tmp_path, defect):
    evidence = catalog(tmp_path)
    if defect == "source":
        (tmp_path / "HOSE.pdf").write_bytes(b"%PDF-1.4 changed")
    elif defect == "different_schedule":
        path = tmp_path / "HNX.json"
        declaration = json.loads(path.read_text())
        declaration["closed_ranges"].append(["2026-01-02", "2026-01-02"])
        path.write_text(json.dumps(declaration))
    elif defect == "duplicate":
        evidence["sources"].append(evidence["sources"][0])
    if defect:
        with pytest.raises(DataError):
            expected_dates(evidence, date(2026, 1, 1), date(2026, 1, 5))
    else:
        assert expected_dates(evidence, date(2026, 1, 1), date(2026, 1, 5)) == {
            "2026-01-02",
            "2026-01-05",
        }


def test_partial_comparison_and_empty_provider_cannot_prove_complete_history(tmp_path):
    evidence = catalog(tmp_path)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(evidence))
    audit = tmp_path / "audit"
    batch = audit / "batch-000"
    batch.mkdir(parents=True)
    (audit / "request.json").write_text(
        json.dumps(
            {"symbols": ["FPT", "MWG"], "start_date": "2026-01-01", "end_date": "2026-01-05"}
        )
    )
    (batch / "report.json").write_text(
        json.dumps(
            {
                "intraday_start": "2026-01-01",
                "end_date": "2026-01-05",
                "intervals": ["1m"],
                "feeds": ["vps", "vci"],
                "comparisons": [
                    {
                        "symbol": "FPT",
                        "date_coverage": {
                            "providers": {"vps": {"rows_by_date": {"2026-01-02": 1}}}
                        },
                    }
                ],
            }
        )
    )
    report = run(
        SimpleNamespace(audit=audit, calendar_catalog=path, output=tmp_path / "review.json")
    )
    assert not report["comparison_collection_complete"]
    assert report["checked_symbols"] == 1
    providers = report["series"][0]["providers"]
    assert providers["vps"]["missing_scheduled_date_candidates"] == ["2026-01-05"]
    assert not providers["vci"]["usable_observations"]
    assert len(providers["vci"]["missing_scheduled_date_candidates"]) == 2
