import json
from dataclasses import replace

from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.quality import audit
from aipriceaction_api.storage import Repository


def bar(source, symbol, iv, stamp):
    return Candle(source, symbol, iv, stamp, 100, 101, 99, 100, 1000)


def test_sql_audit_resolves_fixed_gaps_without_loading_price_series(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    repo.register("crypto", "BTCUSDT", enabled=True)
    repo.register("crypto", "INACTIVE", enabled=False)
    first = parse_time("2026-01-01")
    repo.put([bar("crypto", "BTCUSDT", "1m", first + i) for i in (0, 120)])
    repo.put([bar("crypto", "INACTIVE", "1m", first + i) for i in (0, 120)])
    repo.finding("crypto", "BTCUSDT", "1m", "historical_revision", "Independent repair")
    monkeypatch.setattr(
        repo, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Unbounded read"))
    )
    findings = audit(repo, now=first + 600)
    assert {r["kind"] for r in findings} == {"audit_gap", "audit_stale", "historical_revision"}
    gap = json.loads(next(r["detail"] for r in findings if r["kind"] == "audit_gap"))
    assert gap["from"] == first and gap["to"] == first + 120 and gap["continuous"]
    repo.put([bar("crypto", "BTCUSDT", "1m", first + i) for i in range(0, 601, 60)])
    assert [r["kind"] for r in audit(repo, now=first + 601)] == ["historical_revision"]


def test_observed_sessions_respect_listing_floor_and_exclude_unfinished_day(tmp_path):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    for symbol in ("VNINDEX", "FPT", "NEW"):
        repo.register("vn", symbol, enabled=True)
    dates = [parse_time(x) for x in ("2026-01-02", "2026-01-05", "2026-01-06")]
    repo.put([bar("vn", "VNINDEX", "1D", t) for t in dates])
    repo.put([bar("vn", "FPT", "1D", dates[0]), bar("vn", "FPT", "1D", dates[2])])
    repo.put([bar("vn", "FPT", "1h", dates[0] + 7200)])
    repo.put([bar("vn", "NEW", "1D", t) for t in dates[1:]])
    old = repo.queue("vn", "NEW", "1D", "bootstrap", dates[0])
    with repo.connect() as con:
        con.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (old,))
    repo.queue("vn", "NEW", "1D", "repair", dates[1])
    # Jan 6 is still in progress at 07:00 UTC. Saturday/Sunday have no
    # observed index bars and must not become invented missing sessions.
    early = audit(repo, now=dates[2] + 7 * 3600)
    observed = [r for r in early if r["kind"] == "audit_observed_sessions"]
    assert len(observed) == 1 and observed[0]["symbol"] == "FPT" and observed[0]["interval"] == "1D"
    assert json.loads(observed[0]["detail"])["dates"] == ["2026-01-05"]
    # When that real session finishes, the missing intraday date is reviewable.
    late = audit(repo, now=dates[2] + 9 * 3600)
    hourly = next(r for r in late if r["symbol"] == "FPT" and r["interval"] == "1h")
    assert json.loads(hourly["detail"])["dates"] == ["2026-01-06"]
    assert not any(r["symbol"] == "NEW" for r in late)


def test_weekend_index_observations_are_reviewed_without_inventing_missing_sessions(tmp_path):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    for symbol in ("VNINDEX", "FPT"):
        repo.register("vn", symbol, enabled=True)
    fri, sun, mon = map(parse_time, ("2026-01-02", "2026-01-04", "2026-01-05"))
    repo.put([bar("vn", "VNINDEX", "1D", t) for t in (fri, sun, mon)])
    repo.put([bar("vn", "FPT", "1D", t) for t in (fri, mon)])
    findings = audit(repo, now=mon + 9 * 3600)
    assert len(findings) == 1 and findings[0]["kind"] == "audit_non_session_day"
    assert findings[0]["symbol"] == "VNINDEX"
    assert json.loads(findings[0]["detail"])["dates"] == ["2026-01-04"]
    assert len(repo.read("vn", "VNINDEX", "1D")) == 3


def test_completed_interval_basis_disagreements_remain_review_only(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    for symbol in ("FPT", "INACTIVE"):
        repo.register("vn", symbol, enabled=symbol == "FPT")
    finished, unfinished = map(parse_time, ("2026-01-02", "2026-01-05"))
    for symbol in ("FPT", "INACTIVE"):
        repo.put([bar("vn", symbol, "1D", stamp) for stamp in (finished, unfinished)])
        repo.put(
            [
                replace(
                    bar("vn", symbol, "1m", stamp + 7200 + offset),
                    open=50,
                    high=51,
                    low=49,
                    close=50,
                )
                for stamp in (finished, unfinished)
                for offset in (0, 60)
            ]
        )
    originals = repo.read("vn", "FPT", "1m")
    state = repo.state("vn", "FPT", "1m")
    monkeypatch.setattr(
        repo, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Unbounded read"))
    )
    findings = audit(repo, now=unfinished + 7 * 3600)
    basis = [row for row in findings if row["kind"] == "audit_interval_basis"]
    assert len(basis) == 1 and basis[0]["symbol"] == "FPT"
    detail = json.loads(basis[0]["detail"])
    assert detail["relative_threshold"] == 0.01
    assert [r["date"] for r in detail["sessions"]] == ["2026-01-02"]
    assert detail["sessions"][0]["minute_ohlc"] == [50, 51, 49, 50]
    assert detail["sessions"][0]["daily_ohlc"] == [100, 101, 99, 100]
    assert repo.status()["jobs"] == []
    assert repo.state("vn", "FPT", "1m") == state
    monkeypatch.undo()
    assert repo.read("vn", "FPT", "1m") == originals


def test_fixed_interval_basis_and_rounding_resolve_only_the_audit_finding(tmp_path):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    repo.register("vn", "FPT", enabled=True)
    day = parse_time("2026-01-02")
    repo.put([bar("vn", "FPT", "1D", day)])
    repo.put([replace(bar("vn", "FPT", "1m", day + 7200), open=95, high=96, low=94, close=95)])
    repo.finding("vn", "FPT", "1m", "historical_revision", "Independent provider evidence")
    assert any(row["kind"] == "audit_interval_basis" for row in audit(repo, day + 9 * 3600))
    repo.put([replace(bar("vn", "FPT", "1m", day + 7200), close=100.00001)])
    assert {row["kind"] for row in audit(repo, day + 9 * 3600)} == {"historical_revision"}


def test_high_low_basis_disagreement_is_visible_even_with_matching_closes(tmp_path):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    repo.register("vn", "VNINDEX", enabled=True)
    day = parse_time("2026-01-02")
    repo.put([bar("vn", "VNINDEX", "1D", day)])
    repo.put([replace(bar("vn", "VNINDEX", "1m", day + 7200), high=105)])
    findings = audit(repo, day + 9 * 3600)
    basis = next(row for row in findings if row["kind"] == "audit_interval_basis")
    session = json.loads(basis["detail"])["sessions"][0]
    assert session["minute_ohlc"][-1] == session["daily_ohlc"][-1] == 100
    assert session["max_relative_difference"] > 0.01
