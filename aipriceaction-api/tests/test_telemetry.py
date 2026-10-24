from dataclasses import replace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from aipriceaction_api import telemetry
from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings


def test_tracing_disabled_by_default_does_not_build_exporter(monkeypatch):
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    monkeypatch.setattr(
        telemetry,
        "OTLPSpanExporter",
        lambda **kwargs: pytest.fail("exporter should not be built when tracing is disabled"),
    )

    assert telemetry.initialize(FastAPI()) is None


@pytest.mark.parametrize("enabled_value", ["true", "1"])
def test_enabled_tracing_exports_safe_http_spans(monkeypatch, enabled_value):
    monkeypatch.setenv("OTEL_ENABLED", enabled_value)
    monkeypatch.setenv("OTEL_ENDPOINT", "http://collector:5080/")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "aipriceaction-test")
    monkeypatch.setenv("OTEL_AUTH_HEADER", "Basic test-token")
    global_provider = trace.get_tracer_provider()

    exporter = InMemorySpanExporter()
    exporter_config = {}

    def build_exporter(**kwargs):
        exporter_config.update(kwargs)
        return exporter

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", build_exporter)

    app = FastAPI()

    @app.get("/telemetry-test")
    def telemetry_test():
        return {"ok": True}

    @app.get("/telemetry-error")
    async def telemetry_error(request: Request):
        raise RuntimeError(request.query_params["secret"])

    @app.get("/telemetry-key/{secret_key}")
    def telemetry_key(secret_key: str):
        return {"ok": bool(secret_key)}

    runtime = telemetry.initialize(app)
    assert runtime is not None
    assert runtime.provider is None
    assert telemetry.start(runtime) is True
    assert trace.get_tracer_provider() is global_provider
    assert exporter_config == {
        "endpoint": "http://collector:5080/api/default/v1/traces",
        "headers": {"Authorization": "Basic test-token"},
        "timeout": 10,
    }
    assert runtime.provider.resource.attributes["service.name"] == "aipriceaction-test"

    trace_id = "0123456789abcdef0123456789abcdef"
    parent_span_id = "0123456789abcdef"
    secret = "query-secret-must-not-be-exported"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/telemetry-test?secret={secret}",
            headers={"traceparent": f"00-{trace_id}-{parent_span_id}-01"},
        )
        failed_response = client.get(f"/telemetry-error?secret={secret}")
        path_secret = "path-secret-must-not-be-exported"
        path_response = client.get(f"/telemetry-key/{path_secret}")
    assert response.status_code == 200
    assert failed_response.status_code == 500
    assert path_response.status_code == 200

    telemetry.shutdown(runtime)
    spans = exporter.get_finished_spans()
    assert any(span.name == "server.startup" for span in spans)
    request_span = next(
        span
        for span in spans
        if span.kind is SpanKind.SERVER and span.attributes.get("http.route") == "/telemetry-test"
    )
    assert request_span.parent is not None
    assert request_span.parent.is_remote
    assert f"{request_span.context.trace_id:032x}" == trace_id
    assert f"{request_span.parent.span_id:016x}" == parent_span_id
    error_span = next(
        span
        for span in spans
        if span.kind is SpanKind.SERVER and span.attributes.get("http.route") == "/telemetry-error"
    )
    assert error_span.status.description is None
    path_span = next(
        span
        for span in spans
        if span.kind is SpanKind.SERVER
        and span.attributes.get("http.route") == "/telemetry-key/{secret_key}"
    )
    assert path_span.attributes.get("url.path") == "/telemetry-key/{secret_key}"
    assert secret not in repr([(span.name, span.attributes, span.events) for span in spans])
    assert path_secret not in repr([(span.name, span.attributes, span.events) for span in spans])


def test_api_lifespan_exports_and_reports_safe_otel_status(monkeypatch, tmp_path):
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_ENDPOINT", "http://private-collector:5080")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "aipriceaction-api-test")
    monkeypatch.setenv("OTEL_AUTH_HEADER", "Basic private-test-secret")
    exporters = []

    def build_exporter(**kwargs):
        exporter = InMemorySpanExporter()
        exporters.append(exporter)
        return exporter

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", build_exporter)

    settings = replace(
        Settings(),
        database=tmp_path / "api.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    app = create_app(settings)
    for _ in range(2):
        with TestClient(app) as client:
            response = client.get("/health?secret=api-query-secret")
            head_response = client.head("/health")
            assert response.status_code == 200
            assert head_response.status_code == 200
            config = response.json()["configuration"]["opentelemetry"]
            assert config == {
                "enabled": True,
                "endpoint_configured": True,
                "service_name": "aipriceaction-api-test",
                "auth_configured": True,
                "exporter_initialized": True,
            }
            assert "private-collector" not in response.text
            assert "private-test-secret" not in response.text
    assert len(exporters) == 2
    for exporter in exporters:
        spans = exporter.get_finished_spans()
        health_spans = [span for span in spans if span.attributes.get("http.route") == "/health"]
        assert {span.attributes["http.request.method"] for span in health_spans} == {"GET", "HEAD"}
        assert "api-query-secret" not in repr(
            [(span.name, span.attributes, span.events) for span in spans]
        )


def test_exporter_configuration_error_does_not_break_app_startup(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_ENABLED", "true")

    def fail_exporter(**kwargs):
        raise ValueError("bad endpoint")

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", fail_exporter)

    runtime = telemetry.initialize(FastAPI())
    assert runtime is not None
    assert telemetry.start(runtime) is False
    assert "continuing without traces" in caplog.text


def test_endpoint_is_not_reported_configured_when_using_default(monkeypatch):
    monkeypatch.delenv("OTEL_ENDPOINT", raising=False)
    assert telemetry.health_configuration()["endpoint_configured"] is False
