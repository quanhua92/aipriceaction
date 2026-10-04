"""Check selected local VN minute activations through HTTP and S3 recovery."""

import argparse
import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.adoption import checksum
from aipriceaction_api.archive import Archive
from aipriceaction_api.calculations import volume_profile
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import cutoff, date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.storage import COLUMNS, Repository


async def run(args):
    root = args.activation
    report = json.loads((root / "report.json").read_text())
    if not report["passed"] or not report["execute"]:
        raise ValueError("Require a completed local activation")
    symbols = [item["symbol"] for item in report["symbols"]]
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("Activation report must contain a unique explicit selection")
    placeholders = ",".join("?" for _ in symbols)
    settings = Settings.from_env()
    if settings.s3_endpoint != "http://127.0.0.1:9100":
        raise ValueError("Require local RustFS")
    args.output.mkdir(parents=True, exist_ok=False)
    repo = Repository(settings.database)
    archive = Archive(repo, settings)
    history = History(repo, archive, settings)
    before = Repository(Path(report["backup"]))
    before_history = History(before, Archive(before, settings), settings)
    restored = Repository(args.output / "restored.sqlite3")
    restored.initialize()
    restore_settings = replace(settings, database=restored.path, cache_dir=args.output / "cache")
    remote = Archive(restored, restore_settings)
    result = {
        "scope": "Selected local VN minutes; scoped recovery, not whole deployment",
        "symbols": symbols,
        "http_cases": [],
        "restoration": [],
        "passed": False,
    }
    pointer = json.loads(archive.store.read(settings.s3_prefix + "/LATEST.json"))
    raw = archive.store.read(pointer["key"])
    assert hashlib.sha256(raw).hexdigest() == pointer["checksum"]
    manifest = json.loads(raw)
    records = [
        r
        for r in manifest["adoptions"]
        if r["source"] == "vn"
        and r["symbol"] in symbols
        and r["interval"] == "1m"
        and r["revision"] == repo.state("vn", r["symbol"], "1m")["revision"]
    ]
    assert len(records) == len(symbols)
    restored.restore_adoptions(records)

    def values(rows):
        return [
            (r.time, r.open, r.high, r.low, r.close, r.volume, r.provider, r.revision) for r in rows
        ]

    for item in report["symbols"]:
        symbol, activation = item["symbol"], item["activation"]
        state = repo.state("vn", symbol, "1m")
        images = remote.read(activation["replacement_hot_image"], refresh=True)
        assert checksum(images) == activation["replacement_hot_checksum"]
        for obj in activation["replacement_archives"]:
            assert any(o["id"] == obj["id"] for o in manifest["objects"])
            remote.read(obj, refresh=True)
            restored.publish_archive(obj)
        restored.register("vn", symbol)
        with restored.connect() as con:
            columns = tuple(state)
            con.execute(
                f"INSERT INTO series({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(state.values()),
            )
            con.executemany(
                f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                [tuple(r.record()[key] for key in COLUMNS) for r in images],
            )
        restored.validate_basis(images)
        old_hot = [
            r for obj in activation["before_hot_images"] for r in remote.read(obj, refresh=True)
        ]
        assert (
            checksum(sorted(old_hot, key=lambda r: r.time)) == activation["original_hot_checksum"]
        )
        for obj in activation["original_archives"]:
            remote.read(obj, refresh=True)
        revived = History(restored, remote, restore_settings).read("vn", symbol, "1m")
        assert values(revived) == values(history.read("vn", symbol, "1m"))
        result["restoration"].append(
            {
                "symbol": symbol,
                "rows": len(revived),
                "cold_objects": len(activation["replacement_archives"]),
                "exact_hot_versions": True,
                "before_images_readable": True,
                "native_certificate_valid": True,
            }
        )
    with repo.connect() as con:
        con.execute("ATTACH DATABASE ? AS original", (str(before.path),))
        fields = "source,symbol,interval,time,open,high,low,close,volume,provider,revision"
        clause = f"source='vn' AND NOT (symbol IN ({placeholders}) AND interval='1m')"
        for left, right in (("main", "original"), ("original", "main")):
            assert (
                con.execute(
                    f"SELECT COUNT(*) FROM (SELECT {fields} FROM {left}.candles WHERE {clause} EXCEPT SELECT {fields} FROM {right}.candles WHERE {clause})",
                    symbols + symbols,
                ).fetchone()[0]
                == 0
            )
        result["other_vn_ohlcv_and_basis_unchanged"] = True
        result["source_checks"] = [
            dict(r)
            for r in con.execute(
                f"SELECT * FROM source_checks WHERE source='vn' AND symbol IN ({placeholders}) AND interval='1m'",
                symbols,
            )
        ]
        assert len(result["source_checks"]) == len(symbols) and all(
            r["outcome"] == "succeeded" for r in result["source_checks"]
        )
    floor = cutoff(settings.minute_years)

    def date_string(stamp):
        return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(base_url="http://127.0.0.1:3001", timeout=60) as client:
        for item in report["symbols"]:
            symbol = item["symbol"]
            golden = Repository(root / (symbol + "-golden.sqlite3"))
            reference = History(golden, Archive(golden, settings), settings)
            for interval in ("1m", "15m", "1h"):
                for first, last, label in (
                    (None, floor + 7 * 86400, "boundary"),
                    (floor - 7 * 86400, floor + 7 * 86400, "dated-boundary"),
                    (None, None, "recent"),
                ):
                    for ma, ema in ((False, False), (True, False), (True, True)):
                        limit = 3000 if interval == "1m" and last else 200 if last else 50
                        params = dict(
                            symbol=symbol,
                            interval=interval,
                            limit=limit,
                            ma=str(ma).lower(),
                            ema=str(ema).lower(),
                            cache="false",
                        )
                        if first is not None:
                            params["start_date"] = date_string(first)
                        if last is not None:
                            params["end_date"] = date_string(last)
                        end = date_bounds(params["end_date"], True) if last is not None else None
                        selected = before_history if interval == "1h" else reference
                        expected = selected.query(
                            "vn", symbol, interval, first, end, limit, ma, ema
                        )
                        response = await client.get("/tickers", params=params)
                        assert response.status_code == 200, (
                            symbol,
                            interval,
                            label,
                            response.status_code,
                        )
                        assert response.json()[symbol] == expected, (
                            symbol,
                            interval,
                            label,
                            ma,
                            ema,
                        )
                        result["http_cases"].append(
                            {
                                "symbol": symbol,
                                "interval": interval,
                                "window": label,
                                "ma": ma,
                                "ema": ema,
                                "rows": len(expected),
                                "reference": "unchanged native hourly"
                                if interval == "1h"
                                else "corrected all-SQLite minute reference",
                            }
                        )
            proof_path = report.get("volume_proofs_file")
            proofs = json.loads(Path(proof_path).read_text()) if proof_path else []
            days = sorted({p["day"] for p in proofs if p["symbol"] == symbol})
            date = (
                date_string(days[0])
                if days
                else (
                    "2025-09-26"
                    if symbol == "FPT"
                    else "2026-06-23"
                    if symbol == "TPB"
                    else date_string(golden.read("vn", symbol, "1m", limit=1)[0].time)
                )
            )
            response = await client.get(
                "/analysis/volume-profile", params=dict(symbol=symbol, date=date)
            )
            assert response.status_code == 200
            expected = volume_profile(
                reference.read("vn", symbol, "1m", date_bounds(date), date_bounds(date, True)),
                symbol,
                "vn",
                50,
                70,
            )
            assert response.json()["data"] == expected
            result["http_cases"].append(
                {"symbol": symbol, "endpoint": "volume-profile", "date": date, "verified": True}
            )
    result["passed"] = True
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "http_cases": len(result["http_cases"]),
                "restoration": result["restoration"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
