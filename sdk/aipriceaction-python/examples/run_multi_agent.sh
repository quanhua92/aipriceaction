#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python examples/multi_agent.py 2>&1 | tee "examples/multi_agent_$(date +%Y-%m-%d_%H-%M).md"
