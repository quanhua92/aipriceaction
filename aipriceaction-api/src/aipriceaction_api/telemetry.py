"""Optional OpenTelemetry tracing export to OpenObserve."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Telemetry:
    app: Any
    provider: TracerProvider


def initialize(app: Any) -> Telemetry | None:
    """Set up FastAPI request tracing when OTEL_ENABLED is true or 1."""
    if os.getenv("OTEL_ENABLED", "false") not in {"true", "1"}:
        return None

    endpoint = os.getenv("OTEL_ENDPOINT", "http://localhost:5080").rstrip("/")
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
        trace.set_tracer_provider(provider)
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

        tracer = provider.get_tracer("aipriceaction")
        with tracer.start_as_current_span("server.startup") as span:
            span.set_attribute("event", "otel_initialized")
        log.info("OpenTelemetry tracing enabled endpoint=%s service_name=%s", endpoint, service_name)
        return Telemetry(app=app, provider=provider)
    except Exception:
        log.exception(
            "OpenTelemetry tracing initialization failed; continuing without exported traces endpoint=%s",
            endpoint,
        )
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                log.debug("OpenTelemetry cleanup after initialization failure failed", exc_info=True)
        return None


def shutdown(telemetry: Telemetry | None) -> None:
    """Flush queued spans and stop the exporter during API shutdown."""
    if telemetry is None:
        return

    try:
        FastAPIInstrumentor.uninstrument_app(telemetry.app)
    except Exception:
        log.warning("OpenTelemetry FastAPI uninstrumentation failed", exc_info=True)

    try:
        if not telemetry.provider.force_flush(timeout_millis=5000):
            log.warning("OpenTelemetry span flush did not complete before timeout")
        telemetry.provider.shutdown()
        log.info("OpenTelemetry tracer provider shut down")
    except Exception:
        log.warning("OpenTelemetry tracer provider shutdown failed", exc_info=True)
