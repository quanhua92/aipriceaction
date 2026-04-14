# Multi-stage production Docker build for aipriceaction (PostgreSQL backend)
# Uses cargo-chef for dependency caching and BuildKit cache mounts

# Stage 1: Build planner for cargo-chef
FROM rust:1.94-alpine AS chef
USER root

RUN apk add --no-cache \
    musl-dev \
    ca-certificates \
    gcc \
    g++ \
    make \
    curl-dev \
    openssl-dev \
    openssl-libs-static \
    nghttp2-dev \
    libssh2 \
    zlib-static

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo install cargo-chef --locked
WORKDIR /app

FROM chef AS rust-planner
WORKDIR /app

COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src && echo "fn main() {}" > src/main.rs
RUN cargo chef prepare --recipe-path recipe.json

# Stage 2: Cache dependencies
FROM chef AS rust-cacher
WORKDIR /app

RUN apk add --no-cache musl-dev gcc g++ make curl-dev openssl-dev nghttp2-dev libssh2

COPY --from=rust-planner /app/recipe.json recipe.json

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo chef cook --release --recipe-path recipe.json

# Stage 3: Build the application
FROM chef AS rust-builder
WORKDIR /app

RUN apk add --no-cache musl-dev gcc g++ make curl-dev openssl-dev nghttp2-dev libssh2

COPY --from=rust-cacher /app/target target
COPY Cargo.lock Cargo.toml ./
COPY src/ src/
COPY migrations/ migrations/
COPY .sqlx/ .sqlx/

ENV SQLX_OFFLINE=true

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --release && \
    cp target/release/aipriceaction /app/aipriceaction-bin

# Stage 4: Minimal production image
FROM alpine:3.23.3 AS final-image
WORKDIR /app

ARG BUILD_DATE
ARG GIT_COMMIT

RUN apk update && apk upgrade --no-cache \
 && apk add --no-cache ca-certificates curl libcurl nghttp2 libssh2 \
 && rm -rf /var/cache/apk/*

RUN addgroup -S appgroup && adduser -S -G appgroup appuser

ENV RUST_LOG="info"
ENV BUILD_DATE="${BUILD_DATE}"
ENV GIT_COMMIT="${GIT_COMMIT}"
ENV PORT=3000

COPY --from=rust-builder /app/aipriceaction-bin ./aipriceaction

COPY ticker_group.json ./ticker_group.json
COPY binance_tickers.json ./binance_tickers.json
COPY global_tickers.json ./global_tickers.json
COPY company_info.json ./company_info.json
COPY vn.csv ./vn.csv
COPY sjc_tickers.json ./sjc_tickers.json
COPY scripts/helpers/sjc-batch.csv ./sjc-batch.csv
COPY public/ ./public

RUN mkdir -p /app/uploads && chown -R appuser:appgroup /app && chown 1000:1000 /app/uploads
USER appuser

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-3000}/health || exit 1

CMD ["./aipriceaction", "serve", "--port", "3000"]
