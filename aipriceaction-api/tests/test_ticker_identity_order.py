from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle
from aipriceaction_api.history import History, load_rust_ticker_identity_order
from aipriceaction_api.storage import Repository


def _identity_export(path, identities):
    path.write_text(
        "id,source,symbol\n"
        + "".join(f"{ticker_id},{source},{symbol}\n" for ticker_id, source, symbol in identities),
        encoding="utf-8",
    )
    return path


def _history(tmp_path, identities):
    identity_csv = _identity_export(tmp_path / "rust-ticker-identities.csv", identities)
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        rust_ticker_identity_order=identity_csv,
    )
    repo = Repository(settings.database)
    repo.initialize()
    history = History(repo, Archive(repo, settings), settings)
    origin = datetime(2024, 1, 2, tzinfo=UTC)
    rows = []
    for source, base in (("sjc", 200.0), ("yahoo", 100.0)):
        rows.extend(
            Candle(
                source,
                "COLL",
                "1D",
                int((origin + timedelta(days=index)).timestamp()),
                base + index,
                base + index + 1,
                base + index - 1,
                base + index,
                1000 + index,
                "fixture",
                "fixture-v1",
            )
            for index in range(3)
        )
    repo.put(rows)
    return settings, history


@pytest.mark.parametrize(
    ("identities", "expected_latest_first_context"),
    [
        ([(1, "sjc", "COLL"), (2, "yahoo", "COLL")], [102.0, 200.0, 201.0, 202.0]),
        ([(1, "yahoo", "COLL"), (2, "sjc", "COLL")], [202.0, 100.0, 101.0, 102.0]),
    ],
    ids=["rust-sjc-first", "rust-yahoo-first"],
)
def test_native_merged_query_uses_exported_identity_order(
    tmp_path, identities, expected_latest_first_context
):
    _, history = _history(tmp_path, identities)

    result = history.query_merged_native_sources(("sjc", "yahoo"), "COLL", "1D", limit=4, ma=False)

    assert [row["close"] for row in result] == expected_latest_first_context


@pytest.mark.parametrize(
    ("identities", "expected_open", "expected_close"),
    [
        ([(1, "sjc", "COLL"), (2, "yahoo", "COLL")], 200.0, 102.0),
        ([(1, "yahoo", "COLL"), (2, "sjc", "COLL")], 100.0, 202.0),
    ],
    ids=["rust-sjc-first", "rust-yahoo-first"],
)
def test_aggregated_merged_query_uses_exported_identity_order(
    tmp_path, identities, expected_open, expected_close
):
    _, history = _history(tmp_path, identities)

    result = history.query_merged_aggregated_sources(
        ("sjc", "yahoo"), "COLL", "1W", limit=1, ma=False
    )

    assert len(result) == 1
    assert result[0]["open"] == expected_open
    assert result[0]["close"] == expected_close


def test_identity_order_falls_back_to_caller_order_when_export_is_absent(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    history = History(repo, Archive(repo, settings), settings)

    assert history.ordered_merged_sources(("sjc", "yahoo"), "COLL") == ("sjc", "yahoo")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("source,id,symbol\n1,sjc,COLL\n", "header"),
        ("id,source,symbol\n1,sjc,COLL\n1,yahoo,COLL\n", "increasing"),
        ("id,source,symbol\n1,sjc,COLL\n2,sjc,COLL\n", "duplicates"),
        ("id,source,symbol\n1,unknown,COLL\n", "unsupported source"),
        ("id,source,symbol\n1,sjc,\n", "empty symbol"),
        ("id,source,symbol\n2,sjc,COLL\n1,yahoo,COLL\n", "increasing"),
    ],
)
def test_invalid_identity_export_is_rejected(tmp_path, content, message):
    path = tmp_path / "identities.csv"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_rust_ticker_identity_order(path)


def test_identity_order_path_can_be_configured_from_environment(monkeypatch, tmp_path):
    identity_csv = tmp_path / "identities.csv"
    _identity_export(identity_csv, [(1, "sjc", "COLL"), (2, "yahoo", "COLL")])
    monkeypatch.setenv("RUST_TICKER_IDENTITY_ORDER_PATH", str(identity_csv))

    assert Settings.from_env().rust_ticker_identity_order == identity_csv
