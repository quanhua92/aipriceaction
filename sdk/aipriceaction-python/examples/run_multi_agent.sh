#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -n "${1:-}" ]; then
    echo "Resuming session: $1"
    PYTHONUNBUFFERED=1 uv run python examples/multi_agent.py "$1" 2>&1 | tee "examples/multi_agent_$(date +%Y-%m-%d_%H-%M)_resume.md"
else
    PYTHONUNBUFFERED=1 uv run python examples/multi_agent.py 2>&1 | tee "examples/multi_agent_$(date +%Y-%m-%d_%H-%M).md"
fi
