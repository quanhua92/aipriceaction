from dataclasses import replace

import pytest

from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page
from scripts.audit_vci_archive_overlap import fetch_window

STAMP = 1758855300


def candle(stamp):
    return Candle("vn", "FPT", "1m", stamp, 100, 110, 90, 100, 1000, "vci")


class Source:
    def __init__(self, pages):
        self.pages, self.calls = iter(pages), []

    async def page(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return next(self.pages)


@pytest.mark.asyncio
async def test_archive_audit_pages_to_floor_without_inventing_missing_minutes():
    first, last = STAMP, STAMP + 240
    source = Source(
        [
            Page([candle(last), candle(last - 60)], "vci", cursor=last - 60),
            Page([candle(first)], "vci", cursor=first - 60),
        ]
    )
    rows = await fetch_window(source, "FPT", first, last)
    assert [row.time for row in rows] == [first, last - 60, last]
    assert [args[3] for args, _ in source.calls] == [last + 1, last - 60]
    assert all(kwargs["start"] == first for _, kwargs in source.calls)
    assert all(kwargs["provider"] == "vci" for _, kwargs in source.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("empty", "stalled", "provider", "outside", "bound"))
async def test_archive_audit_never_treats_partial_pagination_as_complete(defect):
    page = Page([candle(STAMP + 120)], "vci", cursor=STAMP + 120)
    if defect == "empty":
        page = Page([], "vci", no_data=True)
    elif defect == "stalled":
        page.cursor = STAMP + 181
    elif defect == "provider":
        page.provider = "vps"
    elif defect == "outside":
        page.rows = [candle(STAMP - 60)]
    with pytest.raises(DataError):
        await fetch_window(Source([page]), "FPT", STAMP, STAMP + 180, max_pages=1)


@pytest.mark.asyncio
async def test_archive_audit_rejects_source_changes_during_pagination():
    row = candle(STAMP + 60)
    source = Source(
        [
            Page([row], "vci", cursor=STAMP + 60),
            Page([replace(row, volume=2000)], "vci", cursor=STAMP),
        ]
    )
    with pytest.raises(DataError, match="changed a candle"):
        await fetch_window(source, "FPT", STAMP, STAMP + 120)
