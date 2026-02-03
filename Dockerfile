# Multi-stage production Docker build
# Optimized for minimal size, security, and fast builds with cargo-chef
# Uses BuildKit cache mounts for maximum cache efficiency

# Stage 1: Build planner for cargo-chef
FROM rust:1.91-alpine AS chef
USER root

# Install build dependencies for Alpine
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

# Install cargo-chef with cache mount to speed up installation
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo install cargo-chef --locked
WORKDIR /app

FROM chef AS rust-planner
WORKDIR /app

# Copy only manifest files for cargo-chef to analyze
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src && echo "fn main() {}" > src/main.rs
RUN cargo chef prepare --recipe-path recipe.json

# Stage 2: Cache dependencies
FROM chef AS rust-cacher
WORKDIR /app
ARG TARGETPLATFORM

# Add Alpine build dependencies for this stage
RUN apk add --no-cache \
    musl-dev \
    ca-certificates \
    gcc \
    g++ \
    make \
    curl-dev \
    openssl-dev \
    nghttp2-dev \
    libssh2

COPY --from=rust-planner /app/recipe.json recipe.json

# Use native musl target for the current architecture
# Cache cargo registry and git checkouts for faster dependency downloads
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    echo "Building for native musl target" && \
    cargo chef cook --release --recipe-path recipe.json

# Stage 3: Build the application
FROM chef AS rust-builder
WORKDIR /app
ARG TARGETPLATFORM

# Add Alpine build dependencies for this stage
RUN apk add --no-cache \
    musl-dev \
    ca-certificates \
    gcc \
    g++ \
    make \
    curl-dev \
    openssl-dev \
    nghttp2-dev \
    libssh2

# Copy cached dependencies and workspace structure
COPY --from=rust-cacher /app/target target
COPY ./Cargo.lock ./Cargo.lock
COPY ./Cargo.toml ./Cargo.toml
COPY ./src ./src

# Build for native musl target with cache mounts for faster incremental builds
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    echo "Building binary for native musl target" && \
    cargo build --release && \
    cp target/release/aipriceaction /app/aipriceaction-bin

# Stage 4: Create the final, minimal production image
FROM alpine:3.22 AS final-image
WORKDIR /app

# Accept build arguments
ARG BUILD_DATE
ARG GIT_COMMIT

# Install runtime libraries for isahc (libcurl) and curl for health checks
RUN apk add --no-cache ca-certificates curl libcurl nghttp2 libssh2

# Create non-root user for security
RUN addgroup -S appgroup && adduser -S -G appgroup appuser

# Set default environment variables
ENV RUST_LOG="info"
ENV BUILD_DATE="${BUILD_DATE}"
ENV GIT_COMMIT="${GIT_COMMIT}"
ENV PORT=3000
ENV MARKET_DATA_DIR="/app/market_data"
ENV CRYPTO_DATA_DIR="/app/crypto_data"
ENV PUBLIC_DIR="/app/public"

# Copy the compiled binary from rust-builder stage
COPY --from=rust-builder /app/aipriceaction-bin ./aipriceaction

# Copy ticker group configuration file
COPY ./ticker_group.json ./ticker_group.json

# Copy crypto top 100 list
COPY ./crypto_top_100.json ./crypto_top_100.json

# Copy public directory for static files
COPY ./public ./public

# Create market_data directory for CSV storage
RUN mkdir -p /app/market_data && chown -R appuser:appgroup /app/market_data

# Create crypto_data directory for cryptocurrency CSV storage
RUN mkdir -p /app/crypto_data && chown -R appuser:appgroup /app/crypto_data

# Change ownership to non-root user
RUN chown -R appuser:appgroup /app

# Use non-root user
USER appuser

# Expose port (default 3000, configurable via PORT env var)
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-3000}/health || exit 1

# Default command - serve mode
CMD ["./aipriceaction", "serve", "--port", "3000"]
