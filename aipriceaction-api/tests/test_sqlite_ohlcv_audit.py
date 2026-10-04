import json

from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.storage import Repository
from scripts.audit_sqlite_ohlcv import audit


def repository(tmp_path):
    repo = Repository(tmp_path / "live.sqlite3")
    repo.initialize()
    repo.put(
        [
            Candle("vn", "FPT", "1D", parse_time("2026-01-02"), 10, 11, 9, 10, 100),
            Candle("sjc", "SJC", "1D", parse_time("2026-01-02"), 100, 90, 70, 80, 0),
            Candle("yahoo", "GC=F", "1D", parse_time("2026-01-02"), 100, 110, 90, 120, 100),
            Candle(
                "yahoo",
                "GC=F",
                "1m",
                parse_time("2026-01-02T03:10:06Z"),
                100,
                100,
                100,
                100,
                0,
                "legacy-api",
            ),
        ]
    )
    return repo


def test_full_scan_preserves_valid_quote_semantics_and_database_bytes(tmp_path):
    repo = repository(tmp_path)
    before = repo.path.read_bytes()
    report = audit(repo.path)
    assert report["checked_rows"] == 4 and report["structural_validity_passed"]
    assert report["invalid_rows"] == 0 and report["perfect_data_proven"] is False
    assert repo.path.read_bytes() == before
    assert list(tmp_path.glob("*.sqlite3")) == [repo.path]


def test_corrupt_rows_are_bounded_and_nonfinite_values_remain_reportable(tmp_path):
    repo = repository(tmp_path)
    with repo.connect() as con:
        con.execute("UPDATE candles SET high=? WHERE source='vn'", (float("inf"),))
        con.execute("UPDATE candles SET volume=0.5 WHERE source='sjc'")
        con.execute("UPDATE candles SET time=time+60 WHERE source='yahoo' AND interval='1D'")
    report = audit(repo.path, sample_limit=1)
    assert report["checked_rows"] == 4 and report["invalid_rows"] == 3
    assert not report["structural_validity_passed"]
    assert len(report["samples"]) == 1 and report["samples_truncated"]
    assert report["samples"][0]["row"]["volume"] == 0.5
    expanded = audit(repo.path, sample_limit=10)
    assert any(s["row"]["high"] == "inf" for s in expanded["samples"])
    json.dumps(expanded, allow_nan=False)
