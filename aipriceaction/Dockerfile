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
    make

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

RUN apk add --no-cache musl-dev gcc g++ make

COPY --from=rust-planner /app/recipe.json recipe.json

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo chef cook --release --recipe-path recipe.json

# Stage 3: Build the application
FROM chef AS rust-builder
WORKDIR /app

RUN apk add --no-cache musl-dev gcc g++ make

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
FROM alpine:3.22 AS final-image
WORKDIR /app

ARG BUILD_DATE
ARG GIT_COMMIT

RUN apk add --no-cache ca-certificates curl

RUN addgroup -S appgroup && adduser -S -G appgroup appuser

ENV RUST_LOG="info"
ENV BUILD_DATE="${BUILD_DATE}"
ENV GIT_COMMIT="${GIT_COMMIT}"
ENV PORT=3000

COPY --from=rust-builder /app/aipriceaction-bin ./aipriceaction

COPY ticker_group.json ./ticker_group.json
COPY crypto_top_100.json ./crypto_top_100.json
COPY public/ ./public

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-3000}/health || exit 1

CMD ["./aipriceaction", "serve", "--port", "3000"]
