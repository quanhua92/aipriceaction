#!/bin/sh
set -e
cd "$(dirname "$0")/.."
TS=$(date +%Y-%m-%d_%H-%M-%S)
PYTHONUNBUFFERED=1 uv run python examples/multi_agent.py ${1:+$1} 2>&1 | tee "examples/multi_agent_${TS}.md"
exit 0
