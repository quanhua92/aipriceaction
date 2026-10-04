import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.storage import Repository
from scripts import review_vn_historical_price_basis as review
from scripts.compare_vn_feeds import FEEDS


def row(day, *, ratio=1, volume=100, offset=0):
    return dict(
        time=date_bounds(day) + 3 * 3600 + offset,
        open=100 * ratio,
        high=110 * ratio,
        low=90 * ratio,
        close=100 * ratio,
        volume=volume,
    )


def test_all_observed_dates_are_classified_and_basis_transition_is_preserved():
    dates = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]
    local = [row(day) for day in dates]
    source = [row(day, ratio=0.9 if day < "2026-09-28" else 1) for day in dates]
    witnesses = {"native_daily": {day: source[index] for index, day in enumerate(dates)}}
    detail = review.timeline(local, source, witnesses)
    assert [r["observed_dates"] for r in detail["observed_regimes"]] == [2, 2]
    assert detail["observed_regimes"][0]["observed_ratio_rounded_6dp"] == 0.9
    assert detail["observed_regimes"][0]["last_observed_date"] == "2026-09-25"
    assert detail["observed_regimes"][1]["first_observed_date"] == "2026-09-28"
    old = detail["days"][0]["daily_witnesses"]["native_daily"]["minute_comparisons"]
    assert old["vci_minutes"]["maximum_price_difference_vnd"] == 0
    assert old["sqlite_minutes"]["maximum_price_difference_vnd"] == 11
    assert sum(r["shared_rows"] for r in detail["observed_regimes"]) == len(local)


def test_partial_and_nonuniform_observations_cannot_become_uniform_regimes():
    local = [row("2026-09-28"), row("2026-09-28", offset=60), row("2026-09-29")]
    source = [row("2026-09-28", ratio=0.9), row("2026-09-28", offset=60)]
    source[1]["close"] = 101
    detail = review.timeline(local, source, {})
    assert detail["days"][0]["class"] == "mixed"
    assert detail["days"][0]["price_classes"] == {
        "uniform_price_ratio": 1,
        "nonuniform_price_difference": 1,
    }
    assert detail["days"][1]["class"] == "no_shared_rows"
    assert detail["days"][1]["sqlite_only_rows"] == 1
    assert detail["days"][1]["observed_minute_aggregates"]["vci_minutes"] is None
    with pytest.raises(DataError, match="Duplicate"):
        review.timeline(local + local[:1], source, {})


def test_saved_legacy_agreement_cannot_supply_a_second_native_volume_witness():
    local = [row("2026-09-28")]
    source = [row("2026-09-28", ratio=0.9)]
    daily = {
        feed: {
            "2026-09-28": row(
                "2026-09-28", ratio=0.9, volume=100 if feed in ("legacy", "dnse") else 101
            )
        }
        for feed in FEEDS
    }
    detail = review.timeline(local, source, daily)
    summary = review.summarize(detail["days"])
    assert summary["two_exact_native_volume_witness_days"] == 0
    assert summary["native_volume_witness_exceptions"][0]["matching_native_feeds"] == ["dnse"]
    assert summary["daily_comparisons"]["dnse"]["vci_minutes"]["price_within_1_vnd_days"] == 1
    assert not summary["publication_license"]


def test_compact_review_counts_all_days_and_limits_samples_without_using_legacy():
    dates = [(date(2026, 3, 1) + timedelta(days=i)).isoformat() for i in range(32)]
    local = [row(day, volume=101) for day in dates]
    source = [row(day, ratio=0.9) for day in dates]
    daily = {
        feed: {
            day: row(
                day, ratio=1 if feed == "legacy" else 0.9, volume=101 if feed == "legacy" else 100
            )
            for day in dates
        }
        for feed in ("legacy", "vndirect", "dnse")
    }
    result = review.compact_coherence(review.timeline(local, source, daily))
    assert result["observed_day_count"] == 32
    assert result["price_classes"] == {"uniform_price_ratio": 32}
    assert result["daily_coherence_counts"]["sqlite_minutes"] == {
        "observed_days": 32,
        "two_native_price_within_1_vnd_days": 0,
        "two_exact_native_volume_days": 0,
    }
    assert result["daily_coherence_counts"]["vci_minutes"] == {
        "observed_days": 32,
        "two_native_price_within_1_vnd_days": 32,
        "two_exact_native_volume_days": 32,
    }
    assert len(result["daily_coherence_exception_samples"]["sqlite_minutes"]) == 20
    assert not result["daily_coherence_exception_samples"]["vci_minutes"]
    assert not result["publication_license"] and "days" not in result


def test_compact_review_retains_source_absence_and_every_volume_shortage_count():
    dates = [(date(2026, 3, 1) + timedelta(days=i)).isoformat() for i in range(32)]
    local = [row(day) for day in dates]
    result = review.compact_coherence(review.timeline(local, [], {}))
    assert result["daily_coherence_counts"]["vci_minutes"]["observed_days"] == 0
    assert result["summary"]["native_volume_witness_exception_count"] == 32
    assert len(result["summary"]["native_volume_witness_exception_samples"]) == 20


@pytest.mark.parametrize("changed_proof", [False, True])
@pytest.mark.parametrize("all_series", [False, True])
def test_automatic_exception_selection_is_read_only_and_binds_proof_catalog(
    tmp_path, monkeypatch, changed_proof, all_series
):
    settings = replace(Settings(), database=tmp_path / "live.sqlite3")
    repository = Repository(settings.database)
    repository.initialize()
    original = row("2026-09-28")
    repository.put([Candle("vn", "FPT", "1m", **original)])
    before = settings.database.read_bytes()
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    combined, daily = tmp_path / "combined", tmp_path / "daily"
    combined.mkdir()
    daily.mkdir()
    proofs = tmp_path / "proofs.json"
    proofs.write_text("[]")
    (combined / "report.json").write_text(
        json.dumps(
            {
                "completed": True,
                "proofs_sha256": hashlib.sha256(proofs.read_bytes()).hexdigest(),
                "series": [
                    {
                        "symbol": symbol,
                        "sqlite_comparison": {
                            "providers": {"vci": {"diagnostics": {"price_classes": classes}}}
                        },
                    }
                    for symbol, classes in [
                        ("FPT", {"uniform_price_ratio": 1}),
                        ("VCB", {"nonuniform_price_difference": 1}),
                    ]
                ],
            }
        )
    )
    record = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    (combined / "FPT-record.json").write_text(json.dumps(record))
    (combined / "VCB-record.json").write_text(json.dumps(record))
    (daily / "report.json").write_text(
        json.dumps(
            {
                "feeds": list(FEEDS),
                "intervals": ["1D"],
                "symbols": ["FPT", "VCB"],
                "daily_start": "2026-09-01",
                "end_date": "2026-09-30",
                "canonical_publication": False,
                "requests": 8,
            }
        )
    )
    for feed in FEEDS:
        (daily / feed).mkdir()
        (daily / feed / "FPT-1D.json").write_text(
            json.dumps(
                {
                    "rows": [row("2026-09-28", ratio=0.9)],
                    "error": "retained older error" if feed == "legacy" else None,
                }
            )
        )
        (daily / feed / "VCB-1D.json").write_text(
            json.dumps({"rows": [row("2026-09-28", ratio=0.9)]})
        )
    if changed_proof:
        proofs.write_text("[1]")
    calls = []

    async def replay(config, symbol, captured, retain_rows):
        calls.append(symbol)
        assert retain_rows and config.vci_volume_proofs == proofs and captured == record
        return {
            "rows": [Candle("vn", symbol, "1m", **row("2026-09-28", ratio=0.9))],
            "captured_pages_passed": True,
        }

    monkeypatch.setattr(review, "replay_record", replay)
    args = SimpleNamespace(
        review=combined,
        daily=daily,
        proofs=proofs,
        output=tmp_path / "result",
        all_series=all_series,
    )
    if changed_proof:
        with pytest.raises(DataError, match="exact proof catalog"):
            asyncio.run(review.run(args))
        assert not calls and not args.output.exists()
    else:
        result = asyncio.run(review.run(args))
        assert calls == result["selected_symbols"] == (["FPT", "VCB"] if all_series else ["FPT"])
        assert result["compact_diagnostic_only"] == all_series
        assert ("days" in result["series"][0]) != all_series
        assert result["completed"] and not result["canonical_publication"]
        assert result["series"][0]["daily_observations"]["legacy"]["original_error"]
        assert not list(args.output.rglob("*.sqlite3*"))
    assert settings.database.read_bytes() == before
