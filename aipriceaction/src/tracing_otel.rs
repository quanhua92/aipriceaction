use std::collections::HashMap;
use std::sync::Arc;
use opentelemetry::trace::TracerProvider;
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_otlp::WithHttpConfig;
use opentelemetry_sdk::trace::SdkTracerProvider;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::Layer;
use tracing_subscriber::EnvFilter;

/// Check if OpenTelemetry is enabled via `OTEL_ENABLED` env var.
pub fn is_enabled() -> bool {
    std::env::var("OTEL_ENABLED")
        .map(|v| v == "true" || v == "1")
        .unwrap_or(false)
}

/// Initialize the tracing subscriber.
///
/// When `OTEL_ENABLED=true`, sets up a dual-layer subscriber:
/// - **fmt layer**: all events to stdout (controlled by `RUST_LOG`)
/// - **OTel layer**: exports traces via OTLP HTTP, filtered to exclude noisy modules
///
/// When disabled, falls back to a simple fmt subscriber.
///
/// Returns `Some(SdkTracerProvider)` when OTel is enabled (caller must call `shutdown_tracer_provider`),
/// or `None` when OTel is disabled.
///
/// Must be called inside a tokio runtime.
pub fn init() -> Option<Arc<SdkTracerProvider>> {
    let env_filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info"));

    if !is_enabled() {
        // Simple fmt-only subscriber
        tracing_subscriber::fmt()
            .with_env_filter(env_filter)
            .with_target(false)
            .init();
        return None;
    }

    // Build OTel exporter
    let endpoint = std::env::var("OTEL_ENDPOINT")
        .unwrap_or_else(|_| "http://localhost:5080".to_string());
    let service_name = std::env::var("OTEL_SERVICE_NAME")
        .unwrap_or_else(|_| "aipriceaction".to_string());

    let mut headers = HashMap::new();
    if let Ok(auth) = std::env::var("OTEL_AUTH_HEADER") {
        if !auth.is_empty() {
            headers.insert("Authorization".to_string(), auth);
        }
    }

    let exporter = opentelemetry_otlp::SpanExporter::builder()
        .with_http()
        .with_endpoint(format!("{endpoint}/api/default/v1/traces"))
        .with_headers(headers)
        .build()
        .expect("Failed to build OTel span exporter");

    let resource = opentelemetry_sdk::Resource::builder()
        .with_service_name(service_name)
        .build();

    let tracer_provider = SdkTracerProvider::builder()
        .with_batch_exporter(exporter)
        .with_resource(resource)
        .build();

    let tracer_provider = Arc::new(tracer_provider);

    // OTel layer with filter to exclude noisy modules
    let otel_filter = EnvFilter::new("info")
        .add_directive("hyper=off".parse().unwrap())
        .add_directive("reqwest=off".parse().unwrap())
        .add_directive("sqlx=off".parse().unwrap())
        .add_directive("tokio=off".parse().unwrap())
        .add_directive("fred=off".parse().unwrap())
        .add_directive("aipriceaction::workers=off".parse().unwrap());

    let tracer = tracer_provider.tracer("aipriceaction");
    let otel_layer = tracing_opentelemetry::layer()
        .with_tracer(tracer)
        .with_filter(otel_filter);

    // Dual-layer subscriber: fmt (all events) + OTel (filtered)
    let fmt_layer = tracing_subscriber::fmt::layer()
        .with_target(false);

    tracing_subscriber::registry()
        .with(env_filter)
        .with(fmt_layer)
        .with(otel_layer)
        .init();

    tracing::info!("OpenTelemetry tracing enabled (endpoint={endpoint})");

    // Emit a startup span to verify the OTel pipeline works end-to-end
    {
        let tracer = tracer_provider.tracer("aipriceaction");
        use opentelemetry::trace::{TraceContextExt, Tracer};
        tracer.in_span("server.startup", |cx| {
            let span = cx.span();
            span.set_attribute(opentelemetry::KeyValue::new("event", "otel_initialized"));
            tracing::info!("OTel startup span emitted");
        });
    }

    Some(tracer_provider)
}

/// Gracefully shut down the tracer provider, flushing buffered spans.
pub fn shutdown_tracer_provider(provider: Arc<SdkTracerProvider>) {
    if let Err(e) = provider.shutdown() {
        tracing::warn!("OTel tracer provider shutdown error: {e}");
    } else {
        tracing::info!("OTel tracer provider shut down successfully");
    }
}
