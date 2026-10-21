import math

import pytest

from aipriceaction_api.responses import RustJSONResponse, _serde_float_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.40028008402801e-5, "0.0000140028008402801"),
        (1e-5, "0.00001"),
        (9.999e-6, "9.999e-6"),
        (1e-6, "1e-6"),
        (1e15, "1000000000000000.0"),
        (1e16, "1e+16"),
        (1.0, "1.0"),
        (-0.0, "-0.0"),
    ],
)
def test_serde_float_notation_matches_serde_json(value, expected):
    assert _serde_float_text(value) == expected


def test_json_response_emits_compact_utf8_json_and_preserves_field_order():
    response = RustJSONResponse(
        {"first": "cổ phiếu", "ratio": 1.40028008402801e-5, "enabled": True}
    )

    assert response.body == (
        '{"first":"cổ phiếu","ratio":0.0000140028008402801,"enabled":true}'
    ).encode()
    assert response.headers["content-type"] == "application/json"
    assert int(response.headers["content-length"]) == len(response.body)


def test_json_response_rejects_non_finite_floats():
    with pytest.raises(ValueError, match="not JSON compliant"):
        RustJSONResponse({"value": math.nan})
