#!/usr/bin/env bash
set -euo pipefail

api_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$api_root/.venv/bin/pytest" ]]; then
  pytest_bin="$api_root/.venv/bin/pytest"
else
  pytest_bin="$(command -v pytest || true)"
  if [[ -z "$pytest_bin" ]]; then
    echo "pytest is unavailable; install the API development dependencies first" >&2
    exit 1
  fi
fi

export AIPA_RUN_RUST_DIFFERENTIAL=1
export AIPA_REQUIRE_RUST_DIFFERENTIAL=1
export AIPA_USE_DOCKER_POSTGRES=1
unset AIPA_USE_LIVE_REDIS_DIFFERENTIAL
unset AIPA_USE_UNREACHABLE_REDIS_DIFFERENTIAL
unset AIPA_USE_REDIS_PERMISSION_ERROR_DIFFERENTIAL

cd "$api_root"
"$pytest_bin" -q -r s --show-capture=no tests/test_rust_python_differential.py

export AIPA_USE_LIVE_REDIS_DIFFERENTIAL=1
"$pytest_bin" -q -r s --show-capture=no tests/test_rust_python_differential.py -k shared_fixture

unset AIPA_USE_LIVE_REDIS_DIFFERENTIAL
export AIPA_USE_UNREACHABLE_REDIS_DIFFERENTIAL=1
"$pytest_bin" -q -r s --show-capture=no tests/test_rust_python_differential.py -k shared_fixture

unset AIPA_USE_UNREACHABLE_REDIS_DIFFERENTIAL
export AIPA_USE_REDIS_PERMISSION_ERROR_DIFFERENTIAL=1
exec "$pytest_bin" -q -r s --show-capture=no tests/test_rust_python_differential.py -k shared_fixture
