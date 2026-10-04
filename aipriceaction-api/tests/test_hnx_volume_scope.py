import hashlib
import re
from pathlib import Path

import pytest

from scripts.review_hnx_volume_scope import ENDPOINT, review_capture


@pytest.fixture
def capture():
    raw = (Path(__file__).parent / "fixtures/hnx_shs_scale.html").read_bytes()
    request = {
        "endpoint": ENDPOINT,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "symbol": "SHS",
        "date": "2026-09-28",
        "default_date": "2026-10-02",
        "form": {
            "p_keysearch": "28/09/2026|0|0|'SHS'|0|02/10/2026",
            "pColOrder": "SYMBOL",
            "pOrderType": "ASC",
            "pCurrentPage": "1",
            "pRecordOnPage": "100",
            "pIsSearch": "1",
        },
    }
    return raw, request


def test_official_categories_remain_unreconciled(capture):
    result = review_capture(*capture)
    assert result["volumes"]["total"] == 13_716_282
    assert result["component_sum"] == 13_718_082
    assert result["total_minus_components"] == -1800
    assert result["diagnostic_only"]
    assert not result["canonical_publication"]
    assert not result["response_date_verified"]
    assert not result["additive_scope_verified"]


def test_empty_query_is_absent_evidence(capture):
    raw, request = capture
    raw = re.sub(rb"<tbody>.*?</tbody>", b"<tbody></tbody>", raw, flags=re.S)
    raw = raw.replace("Tổng số 1".encode(), "Tổng số 0".encode())
    request["sha256"] = hashlib.sha256(raw).hexdigest()
    result = review_capture(raw, request)
    assert result["record_count"] == 0
    assert "volumes" not in result
    assert not result["canonical_publication"]


@pytest.mark.parametrize("mutation", ["hash", "endpoint", "date", "symbol", "scope"])
def test_query_binding_rejected(capture, mutation):
    raw, request = capture
    if mutation == "scope":
        request["form"]["pCurrentPage"] = "2"
    else:
        request[
            {"hash": "sha256", "endpoint": "endpoint", "date": "date", "symbol": "symbol"}[mutation]
        ] = {
            "hash": "0" * 64,
            "endpoint": "https://example.com/",
            "date": "2026-09-29",
            "symbol": "VIX",
        }[mutation]
    with pytest.raises(ValueError):
        review_capture(raw, request)


@pytest.mark.parametrize(
    "old,new",
    [
        (b"STT", b"Changed"),
        (b"13.716.282", b"13,716,282"),
        (b">SHS<", b">VIX<"),
        ("Tổng số 1".encode(), "Tổng số 2".encode()),
        (b"13.716.282", b"13.716.283"),
    ],
)
def test_changed_response_rejected(capture, old, new):
    raw, request = capture
    raw = raw.replace(old, new, 1)
    request["sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        review_capture(raw, request)
