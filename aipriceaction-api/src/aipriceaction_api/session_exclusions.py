"""Reviewed historical exceptions; never infer closures from absent bars.

Scope is deliberately explicit per ticker/year. Current ticker exchange metadata
cannot establish an asset's historical venue, and zero volume alone does not
prove a market closure. References remain in every recovery receipt.
"""

import csv
import io
import math

from .domain import DataError, parse_time

REVIEWED_VENUES_2018 = {
    "FPT": None,  # Preserve the existing FPT receipt references unchanged.
    "VCB": "https://vietcombank.com.vn/vi-VN/Ve-Vietcombank",
    "MBB": "https://news.mbbank.com.vn/news/thuong-tuong-le-huu-djuc-vi-tuong-dji-tu-chien-truong-djen-thuong-truong-1703061927",
    "VIC": "https://ircdn.vingroup.net/storage/Uploads/0_Quan%20he%20co%20dong/0_Vingroup_2022/TP/121004/2.%20BCB%20Niem%20yet%20VICB2124001%20-%2015.06.2022.pdf",
    "HPG": "https://www.hoaphat.com.vn/tin-tuc/hoa-phat-duoc-chap-canh-boi-ttck.html",
}


def validate_placeholder(values):
    if (
        len(values) != 5
        or not all(math.isfinite(value) for value in values)
        or not all(value > 0 for value in values[:4])
        or values[4] != 0
        or len(set(values[:4])) != 1
    ):
        raise DataError("Closure recovery requires flat zero-volume original placeholders")


def verified_exclusions(symbol, year, raw):
    if year != 2018 or symbol not in REVIEWED_VENUES_2018:
        raise DataError("No reviewed session exclusions for this ticker/year", 400)
    dates = ["2018-01-23", "2018-01-24"]
    times = {parse_time(day) for day in dates}
    found = set()
    for row in csv.reader(io.StringIO(raw.decode("utf-8-sig"))):
        if parse_time(row[0]) not in times:
            continue
        # These particular legacy rows are demonstrably flat, zero-volume
        # placeholders on documented closure dates. Reject changed evidence.
        try:
            values = [float(value) for value in row[1:]]
        except ValueError as exc:
            raise DataError("Invalid original closure placeholder") from exc
        validate_placeholder(values)
        found.add(parse_time(row[0]))
    if found != times:
        raise DataError("Original snapshot does not contain the reviewed closure placeholders")
    references = [
        "https://www.vndirect.com.vn/vndirect-thong-bao-ve-viec-tam-ngung-giao-dich-tren-so-giao-dich-chung-khoan-thanh-pho-ho-chi-minh-ngay-24-01-2018/",
        "https://www.vietnamholding.com/media/b5qf5kcm/annual-report-30-june-2018.pdf",
    ]
    if symbol != "FPT":
        references.append(REVIEWED_VENUES_2018[symbol])
    return {
        "event": f"hose-2018-technical-closure-{symbol.lower()}",
        "symbol": symbol,
        "dates": dates,
        "reason": "HOSE trading closed; original flat zero-volume placeholders are not trades",
        "references": references,
    }
