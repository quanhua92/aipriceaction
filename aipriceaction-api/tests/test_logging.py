import logging
from dataclasses import replace

from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings
from aipriceaction_api.logging_config import UTCFormatter


def test_log_formatter_emits_utc_timestamp():
    formatter = UTCFormatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "problem", (), None)
    output = formatter.format(record)
    assert output.endswith("Z WARNING problem")
    assert "T" in output


def test_http_requests_log_status_latency_and_safe_path(tmp_path, caplog):
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    with caplog.at_level(logging.INFO, logger="aipriceaction_api.app"):
        with TestClient(create_app(settings)) as client:
            response = client.get("/health?secret=must-not-be-logged")

    records = [
        record for record in caplog.records if record.getMessage().startswith("http response")
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "method=GET" in message
    assert "path=/health" in message
    assert "status=200" in message
    assert "latency_ms=" in message
    assert "must-not-be-logged" not in message
    assert response.headers["x-request-id"]


def test_client_errors_log_at_warning(tmp_path, caplog):
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    with caplog.at_level(logging.INFO, logger="aipriceaction_api.app"):
        with TestClient(create_app(settings)) as client:
            assert client.get("/missing").status_code == 404

    record = next(record for record in caplog.records if "path=/missing" in record.getMessage())
    assert record.levelno == logging.WARNING
