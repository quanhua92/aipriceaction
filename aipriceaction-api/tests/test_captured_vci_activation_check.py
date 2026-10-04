import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from scripts import check_captured_vci_activation as check


@pytest.mark.parametrize("failure", [None, "price", "missing", "status"])
def test_live_http_requires_every_verified_value_and_success_status(failure):
    expected = [{"time": "2025-10-03T03:00:00Z", "close": 100}]

    class Reference:
        def query(self, *args):
            return expected

    def response(request):
        assert request.url.path == "/tickers"
        assert request.url.params["cache"] == request.url.params["snap"] == "false"
        rows = (
            []
            if failure == "missing"
            else [{**expected[0], "close": 101 if failure == "price" else 100}]
        )
        return httpx.Response(500 if failure == "status" else 200, json={"VCB": rows})

    with httpx.Client(
        base_url="http://127.0.0.1:3001", transport=httpx.MockTransport(response)
    ) as client:
        if failure:
            with pytest.raises(DataError):
                check.verify_http(client, Reference(), "VCB", date_bounds("2025-10-04"))
        else:
            assert (
                len(check.verify_http(client, Reference(), "VCB", date_bounds("2025-10-04"))) == 6
            )


def test_failed_full_index_restoration_removes_temporary_databases(tmp_path, monkeypatch):
    settings = replace(Settings(), archive_backend="s3", s3_endpoint="http://127.0.0.1:9100")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    backup = tmp_path / "operational-before.sqlite3"
    with sqlite3.connect(backup) as con:
        con.execute("CREATE TABLE existing(value)")
    activation = tmp_path / "activation.json"
    activation.write_text(
        json.dumps(
            {
                "passed": True,
                "execute": True,
                "backup": str(backup),
                "active_volume_proofs_file": str(tmp_path / "catalog.json"),
                "symbols": [{"symbol": "VCB", "activation": {"published": True}, "handoff": True}],
            }
        )
    )
    (tmp_path / "catalog.json").write_text("[]")

    class BrokenArchive:
        def __init__(self, *args, **kwargs):
            pass

        def restore_index(self):
            raise DataError("Injected archive failure")

    monkeypatch.setattr(check, "Archive", BrokenArchive)
    dirs = []
    temporary_directory = check.tempfile.TemporaryDirectory

    def temporary(**kwargs):
        kwargs.setdefault("dir", tmp_path)
        result = temporary_directory(**kwargs)
        dirs.append(Path(result.name))
        return result

    monkeypatch.setattr(check.tempfile, "TemporaryDirectory", temporary)
    args = SimpleNamespace(
        activation=activation,
        extension=tmp_path,
        output=tmp_path / "result",
        base_url="http://127.0.0.1:3001",
    )
    with pytest.raises(DataError, match="Injected"):
        asyncio.run(check.run(args))
    receipt = json.loads((args.output / "report.json").read_text())
    assert receipt["temporary_storage_removed"] and not receipt["completed"]
    assert dirs and not any(root.exists() for root in dirs)
    assert not list(args.output.rglob("*.sqlite3*"))
    assert backup.exists()
