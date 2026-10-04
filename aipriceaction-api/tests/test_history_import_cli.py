import json
from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.cli import main
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


@pytest.fixture
def system(tmp_path, monkeypatch):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    floor = parse_time("2021-01-01")
    monkeypatch.setattr("aipriceaction_api.archive.cutoff", lambda years, now=None: floor)
    monkeypatch.setattr("aipriceaction_api.history.cutoff", lambda years, now=None: floor)
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    cold = Candle("vn", "FPT", "1D", floor - 86400, 100, 101, 99, 100, 1000)
    original = archive.publish([cold])
    repo.put([replace(cold, time=floor)])
    return settings, repo, archive, original


def arguments(path, format="json", revision="frozen-public"):
    return [
        "import-history",
        str(path),
        "--source",
        "vn",
        "--symbol",
        "fpt",
        "--format",
        format,
        "--revision",
        revision,
        "--captured-at",
        "2021-01-02T00:00:00Z",
    ]


def export(path, format="json", date="2020-12-31", close=200):
    if format == "json":
        path.write_text(
            json.dumps(
                {
                    "FPT": [
                        {
                            "time": date,
                            "open": close,
                            "high": close + 1,
                            "low": close - 1,
                            "close": close,
                            "volume": 1000,
                        }
                    ]
                }
            )
        )
    else:
        path.write_text(f"{date},{close},{close + 1},{close - 1},{close},1000\n")


@pytest.mark.parametrize("format", ["json", "csv"])
@pytest.mark.parametrize("execute", [False, True])
def test_cli_frozen_history_preserves_primary_versions_and_restores(
    system, tmp_path, capsys, format, execute
):
    settings, repo, archive, original = system
    path = tmp_path / "capture"
    export(path, format)
    before = repo.read("vn", "FPT", "1D")
    args = arguments(path, format) + (["--execute"] if execute else [])
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["rows"] == 1
    assert result["execute"] == execute
    assert repo.read("vn", "FPT", "1D") == before
    assert original in repo.archives()
    assert len(repo.archives()) == (2 if execute else 1)
    history = History(repo, archive, settings)
    historical = history.read("vn", "FPT", "1D", end=parse_time("2020-12-31"))
    assert historical[0].close == (200 if execute else 100)
    if execute:
        assert historical[0].updated_at == parse_time("2021-01-02") * 1_000_000_000
        restored = Repository(tmp_path / "restored")
        restored.initialize()
        cold_archive = Archive(restored, settings)
        assert cold_archive.restore_index() == 2
        assert (
            History(restored, cold_archive, settings).read(
                "vn", "FPT", "1D", end=parse_time("2020-12-31")
            )
            == historical
        )


@pytest.mark.parametrize("problem", ["recent", "active_revision", "future_capture", "empty"])
def test_cli_rejects_invalid_public_snapshot_before_upload(system, tmp_path, capsys, problem):
    _, repo, _, original = system
    path = tmp_path / "capture"
    export(path, date="2021-01-01" if problem == "recent" else "2020-12-31")
    if problem == "empty":
        path.write_text('{"FPT": []}')
    args = arguments(path, revision="initial" if problem == "active_revision" else "frozen-public")
    if problem == "future_capture":
        args[-1] = "2099-01-01"
    before = repo.read("vn", "FPT", "1D")
    assert main(args + ["--execute"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert repo.archives() == [original]
    assert repo.read("vn", "FPT", "1D") == before


@pytest.mark.parametrize("problem", ["changed_capture", "changed_values", "primary_revision"])
def test_cli_cannot_combine_distinct_captures_or_reuse_primary_basis(
    system, tmp_path, capsys, problem
):
    _, repo, archive, _ = system
    path = tmp_path / "capture"
    export(path)
    args = arguments(path) + ["--execute"]
    if problem == "primary_revision":
        # Keep an old primary revision distinct from the current local state.
        original = archive.publish(
            [
                replace(
                    repo.read("vn", "FPT", "1D")[0],
                    time=parse_time("2019-01-01"),
                    revision="old-primary",
                )
            ]
        )
        args[args.index("--revision") + 1] = original["revision"]
    else:
        assert main(args) == 0
        capsys.readouterr()
        if problem == "changed_capture":
            args[args.index("--captured-at") + 1] = "2021-01-03"
        else:
            export(path, close=300)
    before = repo.archives()
    assert main(args) == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert repo.archives() == before


def test_cli_same_capture_republication_is_idempotent(system, tmp_path, capsys):
    _, repo, _, _ = system
    path = tmp_path / "capture"
    export(path)
    args = arguments(path) + ["--execute"]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)["object"]
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)["object"]
    assert first["id"] == second["id"]
    assert len(repo.archives()) == 2
