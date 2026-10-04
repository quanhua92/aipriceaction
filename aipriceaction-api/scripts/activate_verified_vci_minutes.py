"""Activate rehearsed/reviewed local VN stock minutes; preview unless --execute.

Stop the local VN worker and API before execution. Other source workers may
continue. Keep the SQLite backup and S3 before-images for recovery. This script
does not deploy production or certify exchange-calendar completeness.
"""

import argparse
import asyncio
import hashlib
import json
import tempfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.adoption import checksum
from aipriceaction_api.archive import Archive
from aipriceaction_api.coherent_snapshot import capture, publish
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, cutoff, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_adoption import adopt_native_snapshot
from aipriceaction_api.workers import Worker
from scripts.captured_vci_activation_inputs import captured_inputs, http_controls
from scripts.stage_yahoo_daily_history import RecordingTransport
from scripts.vci_activation_inputs import reviewed_inputs


class MinuteVerificationHistory(History):
    """Exercise minute-derived storage buckets without changing API routing."""

    def native_interval(self, source, symbol, iv):
        return "1m" if iv == "1h" else super().native_interval(source, symbol, iv)


def daily_check(rows, price_reference, volume_references):
    """Check every observed minute session without scaling any source values."""
    groups = defaultdict(list)
    for row in rows:
        groups[row.time // 86400 * 86400].append(row)
    observed = [
        replace(
            bars[0],
            time=day,
            interval="1D",
            high=max(r.high for r in bars),
            low=min(r.low for r in bars),
            close=bars[-1].close,
            volume=sum(r.volume for r in bars),
        )
        for day, bars in sorted(groups.items())
    ]
    checks = []
    for provider, candles in {
        "retained_native_prices": price_reference,
        **volume_references,
    }.items():
        reference = {r.time: r for r in candles}
        missing = [r.time for r in observed if r.time not in reference]
        prices = [
            abs(getattr(r, field) - getattr(reference[r.time], field))
            for r in observed
            if r.time in reference
            for field in ("open", "high", "low", "close")
        ]
        volume_mismatches = [
            r.time for r in observed if r.time in reference and r.volume != reference[r.time].volume
        ]
        price_gate = provider == "retained_native_prices"
        if missing or not prices or (max(prices) > 1 if price_gate else volume_mismatches):
            raise DataError(f"{rows[0].symbol} minute/daily {provider} witness failed")
        checks.append(
            {
                "provider": provider,
                "dates": len(observed),
                "max_price_difference_vnd": max(prices),
                "checked_prices": price_gate,
                "checked_volume": not price_gate,
                "volume_mismatches": len(volume_mismatches),
            }
        )
    return checks


async def run(args):
    with tempfile.TemporaryDirectory(prefix="aipa-vci-activation-") as temporary:
        return await _run(args, Path(temporary))


async def _run(args, temporary):
    base = Settings.from_env()
    if base.s3_endpoint != "http://127.0.0.1:9100" or base.archive_backend != "s3":
        raise ValueError("Activation is restricted to the local RustFS environment")
    generic = bool(getattr(args, "review", None))
    captured_mode = bool(getattr(args, "captured_extension", None))
    if captured_mode:
        if (
            not args.captured_rehearsal
            or not args.daily
            or generic
            or args.candidates
            or args.rehearsal
            or args.tpb_daily
        ):
            raise DataError(
                "Captured activation requires source/rehearsal/daily inputs and no other candidate mode"
            )
    elif generic:
        if not args.candidates or not args.daily or args.rehearsal or args.tpb_daily:
            raise DataError("Reviewed activation requires candidates/daily and no rehearsal inputs")
    elif not args.rehearsal or not args.tpb_daily:
        raise DataError("Choose a complete review or the FPT/TPB rehearsal inputs")
    if args.resume and not args.execute:
        raise ValueError("Resume requires --execute and an existing incomplete report")
    args.output.mkdir(parents=True, exist_ok=args.resume)
    settings = replace(
        base,
        vci_history_fallback=True,
        vci_volume_proofs=args.volume_proofs.resolve(),
        allow_direct=True,
        proxies=(),
    )
    main = Repository(settings.database)
    archive = Archive(main, settings)
    report = {
        "execute": args.execute,
        "passed": False,
        "symbols": [],
        "database": str(main.path),
        "volume_proofs_file": str(args.volume_proofs.resolve()),
    }
    path = args.output / "report.json"
    if args.resume:
        report = json.loads(path.read_text())
        if report["passed"] or not report["execute"] or report["database"] != str(main.path):
            raise DataError("Resume requires the incomplete execution report for this database")

    def save():
        path.write_text(json.dumps(report, indent=2) + "\n")

    save()
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    worker = Worker(main, settings, providers=providers, archive=archive)
    floor = cutoff(settings.minute_years)
    plans = []
    try:
        artifacts = {}
        if captured_mode:
            inputs, artifacts = await captured_inputs(args, settings, main, temporary, providers)
        elif generic:
            inputs, artifacts = await reviewed_inputs(args, settings, main)
        else:
            rehearsal = json.loads((args.rehearsal / "report.json").read_text())
            if not rehearsal["passed"] or rehearsal["main_publication"]:
                raise DataError("A successful isolated storage rehearsal is required")
            candidate = Repository(Path(rehearsal["candidate_database"]))
            candidate_settings = replace(
                settings, database=candidate.path, s3_prefix=rehearsal["prefix"]
            )
            candidate_history = History(
                candidate, Archive(candidate, candidate_settings), candidate_settings
            )
            witnesses = json.loads(args.tpb_daily.read_text())
            tpb_references = {
                r["feed"]: [Candle(**c) for c in r["rows"]]
                for r in witnesses
                if r["feed"] in ("vndirect", "dnse")
            }
            if set(tpb_references) != {"vndirect", "dnse"}:
                raise DataError("Both TPB daily peers are required")
            inputs = []
            for symbol in ("FPT", "TPB"):
                candidate.validate_adoption(
                    candidate.snapshot_adoption(candidate.state("vn", symbol, "1m"))
                )
                rows = candidate_history.read("vn", symbol, "1m")
                price_reference = main.read("vn", symbol, "1D")
                expected_provider = "vndirect" if symbol == "FPT" else "vps"
                if {r.provider for r in price_reference} != {expected_provider}:
                    raise DataError("Retained daily price witness provider changed")
                references = (
                    tpb_references if symbol == "TPB" else {"retained_vndirect": price_reference}
                )
                inputs.append(
                    (symbol, candidate, rows, daily_check(rows, price_reference, references))
                )
        # Preflight every selected symbol before any canonical mutation.
        for symbol, candidate, rows, checks in inputs:
            native = await adopt_native_snapshot(candidate, providers, symbol)
            original = capture(main, archive, symbol)
            replacement = [
                replace(r, revision="verified-vci-" + symbol.lower() + "-" + args.output.name)
                for r in rows
            ]
            previous = (
                next((e for e in report["symbols"] if e["symbol"] == symbol), None)
                if args.resume
                else None
            )
            if previous and previous.get("activation", {}).get("published"):
                if (original.state["provider"], original.state["revision"]) != (
                    "vci",
                    replacement[0].revision,
                ):
                    raise DataError("Activated checkpoint changed before resume")
                plan = previous["plan"]
            else:
                plan = publish(main, archive, original, replacement, floor)
            entry = previous or {
                "symbol": symbol,
                "plan": plan,
                "daily_checks": checks,
                "fresh_native_overlap": native,
                "candidate_checksum": checksum(rows),
            }
            if not previous:
                report["symbols"].append(entry)
            plans.append((entry, original, replacement))
            save()
        if args.resume and [e["symbol"] for e in report["symbols"]] != [i[0] for i in inputs]:
            raise DataError("Resume must preserve the complete original activation selection")
        if args.execute and not args.resume:
            backup = args.output / "before.sqlite3"
            main.backup(backup)
            report["backup"] = str(backup)
            # Keep the exact previous manifest pointer as well as immutable
            # candle objects. Do not restore it over unrelated worker progress.
            (args.output / "before-LATEST.json").write_bytes(
                archive.store.read(settings.s3_prefix + "/LATEST.json")
            )
            save()
        if args.execute:
            selected = {i[0] for i in inputs}
            with main.connect() as con:
                selected.update(
                    r[0]
                    for r in con.execute(
                        "SELECT symbol FROM series WHERE source='vn' AND interval='1m' AND provider='vci'"
                    )
                )
            active_proofs = [
                p for p in providers.vci_volume_proofs.values() if p["symbol"] in selected
            ]
            catalog_path = args.output / "active-volume-proofs.json"
            catalog_raw = (json.dumps(active_proofs, indent=2, allow_nan=False) + "\n").encode()
            catalog_path.write_bytes(catalog_raw)
            digest = hashlib.sha256(catalog_raw).hexdigest()
            artifacts[digest] = {"path": str(catalog_path)}
            # Preserve original raw responses behind the active correction
            # proofs as well as the full candidate traversal.
            for proof in active_proofs:
                for digest in [
                    proof["source_capture_sha256"],
                    *proof.get("witness_capture_sha256", []),
                ]:
                    matches = list(settings.database.parent.rglob("native-" + digest + ".json"))
                    if not matches or hashlib.sha256(matches[0].read_bytes()).hexdigest() != digest:
                        raise DataError("Active volume proof original capture is missing")
                    artifacts[digest] = {"path": str(matches[0])}
            report["active_volume_proofs_file"] = str(catalog_path)
            report["active_volume_proofs_count"] = len(active_proofs)
            # Full immutable inputs survive loss of this workstation. Preview
            # does not upload evidence or change any canonical pointer.
            report["source_evidence"] = []
            for digest, artifact in artifacts.items():
                key = settings.s3_prefix + "/evidence/verified-vci-inputs/" + digest + ".json"
                source = Path(artifact["path"])
                archive.store.put(key, source)
                if hashlib.sha256(archive.store.read(key)).hexdigest() != digest:
                    raise DataError("Immutable source-evidence readback differs")
                report["source_evidence"].append({"key": key, "sha256": digest})
            save()
        for entry, original, replacement in plans:
            symbol = entry["symbol"]
            if not args.execute:
                continue
            if not entry.get("activation", {}).get("published"):
                entry["activation"] = publish(
                    main, archive, original, replacement, floor, execute=True
                )
            else:
                current = History(main, archive, settings).read("vn", symbol, "1m")
                if [(r.time, r.open, r.high, r.low, r.close, r.volume) for r in current] != [
                    (r.time, r.open, r.high, r.low, r.close, r.volume) for r in replacement
                ]:
                    raise DataError("Activated checkpoint OHLCV changed before resume")
            save()
            entry["handoff"] = await adopt_native_snapshot(main, providers, symbol, execute=True)
            main.record_volume_proofs(
                [
                    proof
                    for proof in providers.vci_volume_proofs.values()
                    if proof["symbol"] == symbol
                ]
            )
            archive.publish_metadata()
            save()
            entry["refreshed_rows"] = await worker.sync({"source": "vn", "symbol": symbol}, "1m")
            if entry["refreshed_rows"] < 1:
                raise DataError("Activated native refresh did not succeed")
            actual = History(main, archive, settings).read("vn", symbol, "1m")
            if [(r.time, r.open, r.high, r.low, r.close, r.volume) for r in actual] != [
                (r.time, r.open, r.high, r.low, r.close, r.volume) for r in replacement
            ]:
                raise DataError("Canonical OHLCV differs after refresh")
            golden = Repository(temporary / (symbol + "-golden.sqlite3"))
            golden.initialize()
            golden.put(replacement)
            reference = History(golden, Archive(golden, settings), settings)
            history = MinuteVerificationHistory(main, archive, settings)
            cases = []
            for interval in ("1m", "15m", "1h"):
                limit = 3000 if interval == "1m" else 200
                for start in (None, floor - 7 * 86400):
                    for ma, ema in ((False, False), (True, False), (True, True)):
                        result = history.query(
                            "vn", symbol, interval, start, floor + 7 * 86400, limit, ma, ema
                        )
                        if result != reference.query(
                            "vn", symbol, interval, start, floor + 7 * 86400, limit, ma, ema
                        ):
                            raise DataError(
                                "Canonical boundary query differs from all-SQLite reference"
                            )
                        times = [parse_time(r["time"]) for r in result]
                        if not times or not min(times) < floor <= max(times):
                            raise DataError("Boundary check did not cross SQLite/S3")
                        cases.append(
                            {
                                "interval": interval,
                                "start": start,
                                "ma": ma,
                                "ema": ema,
                                "rows": len(result),
                            }
                        )
            entry["boundary_queries"] = cases
            entry["readback_checksum"] = checksum(actual)
            if captured_mode:
                entry["http_routes"] = http_controls(settings, symbol)
            save()
            print(
                json.dumps(
                    {
                        "symbol": symbol,
                        "activated_rows": len(actual),
                        "refreshed_rows": entry["refreshed_rows"],
                        "boundary_queries": len(cases),
                    }
                ),
                flush=True,
            )
        if args.execute:
            archive.publish_metadata()
        report["captures"] = transport.captures
        report["passed"] = True
        save()
    finally:
        report["captures"] = transport.captures
        save()
        await providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearsal", type=Path)
    parser.add_argument("--tpb-daily", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--captured-extension", type=Path)
    parser.add_argument("--captured-rehearsal", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--daily", type=Path)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--volume-proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue an incomplete execution checkpoint without republishing activated snapshots",
    )
    asyncio.run(run(parser.parse_args()))
