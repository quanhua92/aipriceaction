"""Verbose timing logger for CLI performance debugging."""

from __future__ import annotations

import sys
import time
from datetime import datetime

_verbose: bool = False
_t0: float = 0.0


def set_verbose(enabled: bool) -> None:
    """Enable or disable verbose logging. Resets the epoch timer on enable."""
    global _verbose, _t0
    _verbose = enabled
    if enabled:
        _t0 = time.time()


def verbose_log(msg: str) -> None:
    """Print a verbose timing message to stderr if verbose mode is enabled.

    Format: [VERBOSE HH:MM:SS.mmm +X.XXXs] message
    """
    if not _verbose:
        return
    now = datetime.now()
    elapsed = time.time() - _t0
    ts = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
    print(f"[VERBOSE {ts} +{elapsed:.3f}s] {msg}", file=sys.stderr, flush=True)
