#!/bin/bash

# Start aipriceaction server on 0.0.0.0:8888
# Server binds to 0.0.0.0 by default (all interfaces)

set -e

PORT=8888

echo "🚀 Starting aipriceaction server..."
echo "📍 Host: 0.0.0.0 (all interfaces)"
echo "🔌 Port: ${PORT}"
echo ""

# Run the server
./target/release/aipriceaction serve --port "${PORT}"
