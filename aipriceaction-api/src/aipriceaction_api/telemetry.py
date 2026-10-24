"""Optional OpenTelemetry tracing export to OpenObserve."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

log = logging.getLogger(__name__)


def is_enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "false") in {"true", "1"}


def _endpoint() -> str:
    return os.getenv("OTEL_ENDPOINT", "http://localhost:5080").rstrip("/")


def health_configuration(*, exporter_initialized: bool = False) -> dict[str, Any]:
    """Return safe tracing settings; never expose the endpoint or auth value."""
    return {
        "enabled": is_enabled(),
        "endpoint_configured": bool(os.getenv("OTEL_ENDPOINT", "").strip()),
        "service_name": os.getenv("OTEL_SERVICE_NAME", "aipriceaction"),
        "auth_configured": bool(os.getenv("OTEL_AUTH_HEADER", "")),
        # This reports local SDK setup only, not remote collector reachability.
        "exporter_initialized": exporter_initialized,
    }


class OpenTelemetryASGIMiddleware:
    """Create safe HTTP spans without recording query strings or exception text."""

    def __init__(self, app, telemetry):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("aipa.original_method", scope.get("method", ""))
        attributes = {
            "http.request.method": method,
            "method": method,
        }
        scheme = scope.get("scheme")
        if scheme:
            attributes["url.scheme"] = scheme

        server = scope.get("server")
        if server:
            attributes["server.address"] = server[0]
            if server[1] is not None:
                attributes["server.port"] = server[1]

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }
        parent_context = TraceContextTextMapPropagator().extract(headers)
        started = time.perf_counter()
        status_code = None

        tracer = self.telemetry.tracer
        if tracer is None:
            await self.app(scope, receive, send)
            return

        with tracer.start_as_current_span(
            "http_request",
            context=parent_context,
            kind=SpanKind.SERVER,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:

            async def send_traced(message):
                nonlocal status_code
                if message.get("type") == "http.response.start":
                    code = message.get("status")
                    if isinstance(code, int):
                        status_code = code
                        self._record_status(span, code)
                await send(message)

            try:
                await self.app(scope, receive, send_traced)
            except Exception:
                # Do not record exception messages or stack traces: callers may
                # include credentials from the request URL in an error string.
                status_code = status_code or 500
                self._record_status(span, status_code)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route = scope.get("route")
                route_path = getattr(route, "path", None)
                if route_path:
                    span.set_attribute("http.route", route_path)
                    span.set_attribute("url.path", route_path)
                    span.set_attribute("http.target", route_path)
                span.set_attribute(
                    "latency_ms", max(0, round((time.perf_counter() - started) * 1000))
                )

    @staticmethod
    def _record_status(span, status_code):
        span.set_attribute("http.response.status_code", status_code)
        span.set_attribute("http_status", status_code)
        if status_code >= 500:
            span.set_status(Status(StatusCode.ERROR))


@dataclass(slots=True)
class Telemetry:
    provider: TracerProvider | None = None

    @property
    def tracer(self):
        return self.provider.get_tracer("aipriceaction") if self.provider else None


def initialize(app: Any) -> Telemetry | None:
    """Attach tracing middleware when enabled; create exporters at app startup."""
    if not is_enabled():
        return None

    telemetry = Telemetry()
    # Register during app construction, before Starlette builds its stack.
    app.add_middleware(OpenTelemetryASGIMiddleware, telemetry=telemetry)
    return telemetry


def start(telemetry: Telemetry | None) -> bool:
    """Start a fresh exporter for this application lifespan."""
    if telemetry is None:
        return False
    if telemetry.provider is not None:
        return True

    endpoint = _endpoint()
    service_name = os.getenv("OTEL_SERVICE_NAME", "aipriceaction")
    headers = {}
    auth_header = os.getenv("OTEL_AUTH_HEADER", "")
    if auth_header:
        headers["Authorization"] = auth_header

    provider = None
    try:
        exporter = OTLPSpanExporter(
            endpoint=f"{endpoint}/api/default/v1/traces",
            headers=headers,
            timeout=10,
        )
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        telemetry.provider = provider
        tracer = telemetry.tracer
        with tracer.start_as_current_span("server.startup") as span:
            span.set_attribute("event", "otel_initialized")
        log.info("OpenTelemetry tracing enabled service_name=%s", service_name)
        return True
    except Exception:
        log.exception("OpenTelemetry tracing initialization failed; continuing without traces")
        telemetry.provider = None
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                log.debug(
                    "OpenTelemetry cleanup after initialization failure failed", exc_info=True
                )
        return False


def shutdown(telemetry: Telemetry | None) -> None:
    """Flush queued spans and stop the exporter during API shutdown."""
    if telemetry is None:
        return

    provider = telemetry.provider
    telemetry.provider = None
    if provider is None:
        return

    try:
        if not provider.force_flush(timeout_millis=5000):
            log.warning("OpenTelemetry span flush did not complete before timeout")
        provider.shutdown()
        log.info("OpenTelemetry tracer provider shut down")
    except Exception:
        log.warning("OpenTelemetry tracer provider shutdown failed", exc_info=True)
