import gzip
import json
from pathlib import Path

import pytest

from aipriceaction_api.domain import DataError
from scripts.dnse_precision_witnesses import binary32


def test_all_actual_shb_representation_differences_are_exact_binary32_results():
    evidence = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/shb_dnse_precision_evidence.json.gz").read_bytes()
        )
    )
    assert evidence["provider_storage_type_verified"] is False
    assert len(evidence["dates"]) == 48
    for row in evidence["dates"]:
        assert binary32(row["exact_volume"]) == row["reported_volume"]
        assert row["exact_volume"] == row["cumulative_total"]
        assert row["exact_volume"] != row["reported_volume"]
    assert max(abs(r["reported_volume"] - r["exact_volume"]) for r in evidence["dates"]) == 8


@pytest.mark.parametrize("value", (True, False, 0, -1, 2**24, 2**53, 83370900.0, "83370900"))
def test_precision_equivalence_never_introduces_a_generic_numeric_tolerance(value):
    with pytest.raises(DataError):
        binary32(value)
