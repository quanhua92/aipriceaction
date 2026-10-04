"""Losslessly pack explicitly inventoried, inactive SQLite validation images.

Requires the optional local zstd executable, not an API runtime dependency.
Never run against a database in use. A plan records relative paths, size,
mtime_ns and SHA256; execute validates these and decompressed bytes before
removing each original. Receipts retain every original identity for restoration.
"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from aipriceaction_api.config import Settings


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack_digest(blob):
    with subprocess.Popen(["zstd", "-q", "-d", "-c", str(blob)], stdout=subprocess.PIPE) as proc:
        value = hashlib.file_digest(proc.stdout, "sha256").hexdigest()
        if proc.wait():
            raise ValueError("Snapshot decompression failed")
    return value


def source_path(root, relative, live):
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Snapshot must have a relative path inside the data directory")
    if path.resolve() != path.absolute() or path.resolve() == live.resolve():
        raise ValueError("Live database and symlink paths cannot be packed")
    if path.suffix != ".sqlite3" or (
        path.parent == root and not path.name.startswith("restored-rehearsal-")
    ):
        raise ValueError("Only explicitly selected validation snapshots can be packed")
    if any(
        (Path(str(path) + suffix).exists() and Path(str(path) + suffix).stat().st_size)
        for suffix in ("-wal", "-journal")
    ):
        raise ValueError("Snapshot has an outstanding SQLite journal")
    return path


def compact(plan_path, live, execute=False):
    plan = json.loads(plan_path.read_text())
    root = Path(plan["root"]).resolve()
    store = root / "validation-snapshots"
    if store.is_symlink():
        raise ValueError("Snapshot store cannot be a symlink")
    verified = set()
    result = {"packed": 0, "original_bytes": 0, "new_blob_bytes": 0}
    for entry in plan["files"]:
        path = source_path(root, entry["path"], live)
        identity = entry["sha256"]
        if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Invalid snapshot checksum")
        receipt = Path(str(path) + ".packed.json")
        blob = store / (identity + ".sqlite3.zst")
        if not path.exists() and receipt.exists():
            if json.loads(receipt.read_text())["sha256"] != identity or not blob.is_file():
                raise ValueError("Incomplete existing snapshot receipt")
            continue
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (entry["bytes"], entry["mtime_ns"]):
            raise ValueError(f"Snapshot changed: {path}")
        if not execute:
            result["packed"] += 1
            result["original_bytes"] += stat.st_size
            continue
        if digest(path) != identity:
            raise ValueError(f"Snapshot checksum changed: {path}")
        store.mkdir(exist_ok=True)
        if not blob.exists():
            temp = blob.with_suffix(".tmp")
            # A previous interrupted run may have left an incomplete compressed
            # stream. The original is still present until verification succeeds.
            temp.unlink(missing_ok=True)
            with temp.open("xb") as out:
                subprocess.run(["zstd", "-q", "-3", "-c", str(path)], stdout=out, check=True)
                out.flush()
                os.fsync(out.fileno())
            if unpack_digest(temp) != identity:
                raise ValueError("Packed snapshot checksum differs")
            temp.rename(blob)
            result["new_blob_bytes"] += blob.stat().st_size
            verified.add(identity)
        if identity not in verified:
            if unpack_digest(blob) != identity:
                raise ValueError("Existing packed snapshot checksum differs")
            verified.add(identity)
        current = path.stat()
        if (current.st_size, current.st_mtime_ns, current.st_ino) != (
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ino,
        ):
            raise ValueError("Snapshot changed during compaction")
        payload = {**entry, "blob": str(blob.relative_to(root)), "codec": "zstd"}
        with receipt.open("x") as out:
            out.write(json.dumps(payload, indent=2) + "\n")
            out.flush()
            os.fsync(out.fileno())
        path.unlink()
        result["packed"] += 1
        result["original_bytes"] += stat.st_size
        print(json.dumps({"packed": entry["path"], **result}), flush=True)
    return result


def restore(root, receipt, destination):
    entry = json.loads(receipt.read_text())
    blob = root.resolve() / entry["blob"]
    if not blob.resolve().is_relative_to(root.resolve()) or blob.is_symlink():
        raise ValueError("Packed snapshot must remain inside the data directory")
    # Exclusive create prevents replacing a live or previously restored database.
    with destination.open("xb") as out:
        subprocess.run(["zstd", "-q", "-d", "-c", str(blob)], stdout=out, check=True)
        out.flush()
        os.fsync(out.fileno())
    if destination.stat().st_size != entry["bytes"] or digest(destination) != entry["sha256"]:
        raise ValueError("Restored snapshot checksum differs; do not use this output")
    return {"restored": str(destination), "sha256": entry["sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("compact")
    pack.add_argument("--plan", type=Path, required=True)
    pack.add_argument("--execute", action="store_true")
    unpack = commands.add_parser("restore")
    unpack.add_argument("--root", type=Path, required=True)
    unpack.add_argument("--receipt", type=Path, required=True)
    unpack.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "compact":
        result = compact(args.plan, Settings.from_env().database, args.execute)
    else:
        result = restore(args.root, args.receipt, args.destination)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
