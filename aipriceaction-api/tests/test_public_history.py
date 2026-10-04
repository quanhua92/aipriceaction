import json
from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.importing import csv_rows
from aipriceaction_api.public_history import partition_public_year, recover_public_year
from aipriceaction_api.storage import Repository


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    recent = Candle("vn", "VND", "1D", parse_time("2026-01-01"), 100, 101, 99, 100, 10)
    repo.put([recent])
    records = [
        dict(time=day, open=100, high=101, low=99, close=close, volume=10)
        for day, close in [
            ("2020-01-02", 100),
            ("2020-01-03", 100),
            ("2020-02-19", 102),
            ("2020-12-30", 100),
            ("2020-12-31", 100),
        ]
    ]
    public = tmp_path / "public.json"
    public.write_text(json.dumps({"VND": records}))
    original = tmp_path / "original.csv"
    original.write_text(
        "\n".join(
            ",".join(str(r[f]) for f in ("time", "open", "high", "low", "close", "volume"))
            for r in records
        )
        + "\n"
    )
    with pytest.raises(DataError) as failure:
        csv_rows(original.read_text(), "vn", "VND", "1D")
    repo.record_history_gap(
        "vn",
        "VND",
        "1D",
        parse_time("2020-01-01"),
        parse_time("2021-01-01") - 1,
        str(failure.value),
        {"kind": "legacy_daily_import", "year": 2020},
    )
    return repo, archive, History(repo, archive, settings), public, original


def apply(system, execute=True):
    repo, archive, _, public, original = system
    return recover_public_year(
        repo,
        archive,
        public,
        original,
        "VND",
        2020,
        "frozen-year",
        parse_time("2021-01-02") * 1_000_000_000,
        execute,
    )


def test_valid_dates_become_available_without_healing_invalid_date_or_context(system):
    repo, archive, history, _, _ = system
    primary = repo.read("vn", "VND", "1D")
    with pytest.raises(DataError, match="unavailable"):
        history.read(
            "vn", "VND", "1D", start=parse_time("2020-12-30"), end=parse_time("2020-12-31")
        )
    result = apply(system)
    assert result["valid_rows"] == 4
    assert repo.read("vn", "VND", "1D") == primary
    assert len(repo.history_gaps()) == 2
    assert repo.history_gaps()[1]["start"] == parse_time("2020-02-19")
    assert (
        len(
            history.query(
                "vn",
                "VND",
                "1D",
                start=parse_time("2020-12-31"),
                end=parse_time("2020-12-31"),
                ma=False,
            )
        )
        == 1
    )
    with pytest.raises(DataError, match="unavailable"):
        history.read(
            "vn", "VND", "1D", start=parse_time("2020-02-19"), end=parse_time("2020-02-19")
        )
    with pytest.raises(DataError, match="unavailable"):
        history.query(
            "vn", "VND", "1D", start=parse_time("2020-12-31"), end=parse_time("2020-12-31"), ma=True
        )
    assert len(archive.read(result["object"])) == 4


def test_public_partition_manifest_restores_invalid_record_evidence(system, tmp_path):
    _, archive, _, _, _ = system
    result = apply(system)
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, archive.settings)
    assert restored.restore_index() == 1
    gaps = fresh.history_gaps()
    assert gaps == result["remaining_gaps"]
    assert gaps[1]["evidence"]["invalid_record"]["close"] == 102
    for key in gaps[1]["evidence"]["evidence_keys"]:
        assert archive.store.read(key)


def test_public_partition_dry_run_has_no_publication_or_gap_mutation(system):
    repo, _, _, _, _ = system
    before = repo.history_gaps()
    result = apply(system, False)
    assert not result["execute"]
    assert repo.archives() == []
    assert repo.history_gaps() == before


@pytest.mark.parametrize(
    "problem", ["missing", "changed", "duplicate", "wrong_symbol", "future_capture"]
)
def test_public_partition_rejects_unproven_original_coverage(system, problem):
    _, _, _, public, original = system
    payload = json.loads(public.read_text())
    captured = parse_time("2021-01-02") * 1_000_000_000
    if problem == "missing":
        payload["VND"].pop()
    if problem == "changed":
        payload["VND"][0]["volume"] = 20
    if problem == "duplicate":
        payload["VND"][-1] = payload["VND"][0]
    if problem == "wrong_symbol":
        payload["VND"][0]["symbol"] = "FPT"
    if problem == "future_capture":
        captured = 9_999_999_999_999_999_999
    with pytest.raises(DataError):
        partition_public_year(
            json.dumps(payload).encode(),
            original.read_bytes(),
            "VND",
            2020,
            "frozen-year",
            captured,
        )


def test_public_partition_changed_parent_rolls_back_archive_index(system, monkeypatch):
    repo, archive, _, _, _ = system
    original = repo.publish_partitioned_history

    def race(obj, parent, gaps):
        with repo.connect() as con:
            con.execute("UPDATE quality SET resolved=1 WHERE kind='history_unavailable'")
        return original(obj, parent, gaps)

    monkeypatch.setattr(repo, "publish_partitioned_history", race)
    with pytest.raises(DataError, match="marker changed"):
        apply(system)
    assert repo.archives() == []


def test_public_partition_failed_readback_preserves_coarse_marker(system, monkeypatch):
    repo, archive, _, _, _ = system
    before = repo.history_gaps()
    monkeypatch.setattr(archive.store, "read", lambda key: b"corrupt")
    with pytest.raises(DataError, match="readback"):
        apply(system)
    assert repo.history_gaps() == before
    assert repo.archives() == []


def test_public_partition_manifest_retry_does_not_lose_invalid_gap(system, monkeypatch, tmp_path):
    repo, archive, _, _, _ = system
    manifest = archive.manifest

    def unavailable(objects):
        raise OSError("object store unavailable")

    monkeypatch.setattr(archive, "manifest", unavailable)
    with pytest.raises(OSError):
        apply(system)
    assert len(repo.archives()) == 1
    assert repo.history_gaps()[1]["start"] == parse_time("2020-02-19")
    monkeypatch.setattr(archive, "manifest", manifest)
    archive.publish_metadata()
    restored = Repository(tmp_path / "restored")
    restored.initialize()
    assert Archive(restored, archive.settings).restore_index() == 1
    assert restored.history_gaps() == repo.history_gaps()


def test_public_partition_cannot_relabel_current_frame(system):
    repo, archive, _, public, original = system
    before = repo.history_gaps()
    with pytest.raises(DataError, match="active revision"):
        recover_public_year(
            repo,
            archive,
            public,
            original,
            "VND",
            2020,
            "initial",
            parse_time("2021-01-02") * 1_000_000_000,
            True,
        )
    assert repo.history_gaps() == before
    assert repo.archives() == []


def test_public_partition_cannot_clear_an_unrelated_unavailability_reason(system):
    repo, _, _, _, _ = system
    with repo.connect() as con:
        con.execute(
            "UPDATE quality SET detail=json_set(detail,'$.evidence.kind','provider_outage')"
        )
    before = repo.history_gaps()
    with pytest.raises(DataError, match="legacy daily import"):
        apply(system)
    assert repo.history_gaps() == before


def test_public_partition_requires_capture_after_original_dates(system):
    _, _, _, public, original = system
    with pytest.raises(DataError, match="capture version"):
        partition_public_year(
            public.read_bytes(),
            original.read_bytes(),
            "VND",
            2020,
            "frozen",
            parse_time("2019-01-01") * 1_000_000_000,
        )


def test_public_partition_preserves_primary_and_cross_retention_guards(system):
    _, _, history, _, _ = system
    apply(system)
    with pytest.raises(DataError, match="unavailable"):
        history.read(
            "vn", "VND", "1D", start=parse_time("2020-12-30"), end=parse_time("2026-01-01")
        )
    with pytest.raises(DataError, match="unavailable"):
        history.read(
            "vn",
            "VND",
            "1D",
            start=parse_time("2020-12-30"),
            end=parse_time("2020-12-31"),
            revision="initial",
        )


def test_public_partition_cannot_waive_a_gate_with_a_different_snapshot_id(system):
    repo, _, history, _, _ = system
    apply(system)
    with repo.connect() as con:
        con.execute(
            "UPDATE quality SET detail=json_set(detail,'$.evidence.snapshot_id','wrong') WHERE json_extract(detail,'$.evidence.kind')='public_year_partition_basis'"
        )
    with pytest.raises(DataError, match="unavailable"):
        history.read(
            "vn", "VND", "1D", start=parse_time("2020-12-30"), end=parse_time("2020-12-31")
        )


def test_public_partition_cli_defaults_to_a_validated_plan(system, monkeypatch, capsys):
    from aipriceaction_api.cli import main

    repo, archive, _, public, original = system
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: archive.settings))
    assert (
        main(
            [
                "recover-public-year",
                str(public),
                "--original",
                str(original),
                "--symbol",
                "vnd",
                "--year",
                "2020",
                "--revision",
                "public-year",
                "--captured-at",
                "2021-01-02T00:00:00Z",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["valid_rows"] == 4 and len(result["invalid_rows"]) == 1
    assert not result["execute"] and repo.archives() == []
