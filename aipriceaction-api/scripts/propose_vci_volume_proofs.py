"""Propose replayable closed-day proofs from captures; never publish candles."""

import argparse
import json
import time
from pathlib import Path

from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.vci_volume import proof_from_capture, validate_volume_proof


def contradictions(raw):
    payload = json.loads(raw)
    payload = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        return None, []
    body = payload[0]
    if not all(key in body for key in ("symbol", "t", "v", "accumulatedVolume")):
        return None, []
    rows = sorted(
        (int(t), int(v), int(a))
        for t, v, a in zip(body["t"], body["v"], body["accumulatedVolume"], strict=True)
    )
    return body["symbol"], [
        stamp
        for (previous, _, before), (stamp, volume, after) in zip(rows, rows[1:], strict=False)
        if stamp == previous + 60
        and stamp // 86400 == previous // 86400
        and after - before != volume
    ]


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    proofs = json.loads(args.existing.read_text())
    known = {(p["symbol"], validate_volume_proof(p).time): p for p in proofs}
    report = {"main_publication": False, "proposals": [], "blocked": []}
    daily = {}
    for path in args.captures.rglob("native-*.json"):
        raw = path.read_bytes()
        symbol, stamps = contradictions(raw)
        for stamp in stamps:
            if (symbol, stamp) in known:
                continue
            day = stamp // 86400 * 86400
            witnesses = []
            try:
                for feed in ("vndirect", "dnse"):
                    key = (symbol, feed)
                    if key not in daily:
                        record = json.loads((args.daily / feed / f"{symbol}-1D.json").read_text())
                        daily[key] = {r["time"]: r for r in record.get("rows", [])}
                    if day not in daily[key]:
                        raise DataError(f"Missing verified {feed} daily witness")
                    witnesses.append(
                        Candle("vn", symbol, "1D", provider=feed, **daily[key][day]).record()
                    )
                proof = proof_from_capture(raw, witnesses, stamp, time.time_ns())
                corrected = validate_volume_proof(proof)
            except DataError as exc:
                report["blocked"].append(
                    {"symbol": symbol, "time": stamp, "capture": str(path), "error": str(exc)}
                )
                continue
            proofs.append(proof)
            known[symbol, stamp] = proof
            original = next(r for r in proof["source_rows"] if r["time"] == stamp)
            report["proposals"].append(
                {
                    "symbol": symbol,
                    "time": stamp,
                    "capture": str(path),
                    "source_rows": len(proof["source_rows"]),
                    "original_volume": original["volume"],
                    "corrected_volume": corrected.volume,
                    "daily_volume": witnesses[0]["volume"],
                }
            )
    (args.output / "proofs.json").write_text(json.dumps(proofs, indent=2) + "\n")
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
