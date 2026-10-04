"""Offline diagnostics for captured HNX scale queries; never a publication proof."""

import argparse
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from scripts.artifact_budget import ArtifactBudget

ENDPOINT = "https://www.hnx.vn/ModuleReportStockETFs/Report_MD_TradingScale/ListData_Listed"
HEADERS = [
    "STT",
    "Mã CK",
    "Mã ISIN",
    "Đặt mua",
    "Đặt bán",
    "Khớp lệnh liên tục",
    "Khớp lệnh định kỳ đóng cửa",
    "Phiên sau giờ",
    "Thỏa thuận",
    "Toàn thị trường",
]
SUBHEADERS = ["Số lệnh", "Khối lượng", "Giá trị (Đồng)"] * 2 + [
    "Khối lượng",
    "Giá trị (Đồng)",
] * 5


class ScaleTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.tables = 0
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table" and dict(attrs).get("id") == "_tableDatas":
            self.active = True
            self.tables += 1
        if self.active and tag == "tr":
            self.row = []
        if self.active and tag in ("td", "th"):
            self.cell = ""

    def handle_data(self, text):
        if self.active and self.cell is not None:
            self.cell += text

    def handle_endtag(self, tag):
        if self.active and tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join(self.cell.split()))
            self.cell = None
        if self.active and tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag == "table":
            self.active = False


def integer(value):
    if not re.fullmatch(r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:\.[0-9]{3})+)", value):
        raise ValueError("Unexpected HNX integer format")
    return int(value.replace(".", ""))


def review_capture(raw, request):
    """Bind bytes and query; the response itself does not certify its date."""
    if request["endpoint"] != ENDPOINT or request["sha256"] != hashlib.sha256(raw).hexdigest():
        raise ValueError("HNX endpoint/capture binding differs")
    symbol = request["symbol"]
    if not re.fullmatch(r"[A-Z0-9]+", symbol):
        raise ValueError("Invalid requested symbol")
    date = datetime.strptime(request["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    default = datetime.strptime(request["default_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    expected = {
        "p_keysearch": f"{date}|0|0|'{symbol}'|0|{default}",
        "pColOrder": "SYMBOL",
        "pOrderType": "ASC",
        "pCurrentPage": "1",
        "pRecordOnPage": "100",
        "pIsSearch": "1",
    }
    if request["form"] != expected:
        raise ValueError("Unexpected HNX query scope")
    text = raw.decode("utf-8")
    parser = ScaleTable()
    parser.feed(text)
    if parser.tables != 1 or parser.rows[:2] != [HEADERS, SUBHEADERS]:
        raise ValueError("HNX table layout changed")
    count = re.findall(r"Tổng số ([0-9]+) bản ghi", text)
    if len(count) != 1 or count[0] not in ("0", "1"):
        raise ValueError("Ambiguous HNX record count")
    rows = parser.rows[2:]
    result = {
        "request": request,
        "diagnostic_only": True,
        "canonical_publication": False,
        "response_date_verified": False,
        "record_count": int(count[0]),
    }
    if count[0] == "0":
        if rows:
            raise ValueError("Empty HNX query contains rows")
        return result
    if len(rows) != 2 or len(rows[0]) != 19 or rows[0][1] != symbol or rows[0][0] != "1":
        raise ValueError("HNX symbol/row count differs")
    if rows[1] != ["Tổng", *rows[0][3:]]:
        raise ValueError("HNX summary differs from the sole symbol")
    values = [integer(value) for value in rows[0][3:]]
    volumes = dict(
        zip(
            ("continuous", "closing_auction", "after_hours", "negotiated", "total"),
            (values[6], values[8], values[10], values[12], values[14]),
            strict=True,
        )
    )
    component_sum = sum(value for key, value in volumes.items() if key != "total")
    result.update(
        volumes=volumes,
        component_sum=component_sum,
        total_minus_components=volumes["total"] - component_sum,
        additive_scope_verified=False,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    manifest = json.loads((root / "requests.json").read_text())
    reviews = []
    for request in manifest:
        path = (root / request["file"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Capture outside evidence directory")
        reviews.append(review_capture(path.read_bytes(), request))
    report = {"diagnostic_only": True, "canonical_publication": False, "captures": reviews}
    ArtifactBudget(root, 512 * 1024).write(
        root / "report.json", (json.dumps(report, indent=2) + "\n").encode()
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
