import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .domain import DataError

MODES = ("vn", "crypto", "yahoo")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_GROUPS = 500
MAX_SYMBOLS = 10_000


def normalize_groups(value, mode):
    if not isinstance(value, dict) or not value or len(value) > MAX_GROUPS:
        raise DataError(f"Live {mode} catalog is not a bounded group mapping")
    result = {}
    total = 0
    for group, symbols in value.items():
        if not isinstance(group, str) or not group or not isinstance(symbols, list):
            raise DataError(f"Live {mode} catalog contains an invalid group")
        normalized = []
        seen = set()
        for symbol in symbols:
            if (
                not isinstance(symbol, str)
                or not symbol
                or len(symbol) > 64
                or symbol != symbol.strip()
            ):
                raise DataError(f"Live {mode} catalog contains an invalid symbol")
            if symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        total += len(normalized)
        if total > MAX_SYMBOLS:
            raise DataError(f"Live {mode} catalog exceeds its symbol budget")
        result[group] = normalized
    return result


async def sync_catalog(base_url: str, destination: Path, transport=None):
    groups = {}
    async with httpx.AsyncClient(
        transport=transport,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "AIPriceAction-Python/0.1"},
    ) as client:
        for mode in MODES:
            try:
                response = await client.get(
                    base_url.rstrip("/") + "/tickers/group", params={"mode": mode}
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DataError(f"Live {mode} catalog request failed") from exc
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise DataError(f"Live {mode} catalog exceeds its byte budget")
            try:
                groups[mode] = normalize_groups(response.json(), mode)
            except ValueError as exc:
                raise DataError(f"Live {mode} catalog is not valid JSON") from exc
    counts = {
        mode: len({symbol for symbols in mapping.values() for symbol in symbols})
        for mode, mapping in groups.items()
    }
    if any(count == 0 for count in counts.values()):
        raise DataError("Live catalog contains an empty market universe")
    payload = {
        "schema": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "base_url": base_url.rstrip("/"),
        "groups": groups,
    }
    body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".catalog-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "counts": counts,
        "sha256": hashlib.sha256(body).hexdigest(),
    }
