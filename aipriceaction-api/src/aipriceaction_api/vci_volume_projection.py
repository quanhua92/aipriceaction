"""Stage a volume-only candidate on a complete frozen legacy day.

This does not license publication, provider adoption or a price-basis change.
The caller must preserve the original snapshot and establish recovery separately.
"""

from dataclasses import replace

from .domain import DataError
from .vci_volume import validate_volume_proof


def project_legacy_volume(snapshot, proof):
    """Preserve every field except one verified volume on matching day observations."""
    corrected = validate_volume_proof(proof)
    if proof["kind"] != "vci_cumulative_volume" or proof["schema_version"] != 1:
        raise DataError("Legacy projection requires one cumulative-volume correction")
    rows = list(snapshot)
    source = {row["time"]: row for row in proof["source_rows"]}
    if not rows or [row.time for row in rows] != sorted(source):
        raise DataError("Legacy projection requires the exact complete observed source day")
    if len({row.revision for row in rows}) != 1 or not rows[0].revision:
        raise DataError("Legacy projection requires one frozen revision")
    for row in rows:
        row.validate()
        if (
            (row.source, row.symbol, row.interval, row.provider)
            != ("vn", corrected.symbol, "1m", "legacy-api")
            or not 0 < row.updated_at <= proof["verified_at_ns"]
            or row.volume != source[row.time]["volume"]
        ):
            raise DataError("Legacy projection differs from frozen source identity or volumes")
    result = [
        replace(row, volume=corrected.volume) if row.time == corrected.time else row for row in rows
    ]
    if sum(row.volume for row in result) != proof["daily_witnesses"][0]["volume"]:
        raise DataError("Legacy projection does not reconcile the verified daily volume")
    return result
