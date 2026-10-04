import hashlib
import json
import shutil

import pytest

from scripts.compact_validation_snapshots import compact, restore, source_path


def plan(tmp_path):
    files = []
    for name in ("before", "after"):
        path = tmp_path / name / "snapshot.sqlite3"
        path.parent.mkdir()
        path.write_bytes(b"SQLite format 3\0" + b"validation data" * 1000)
        stat = path.stat()
        files.append(
            {
                "path": str(path.relative_to(tmp_path)),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"root": str(tmp_path), "files": files}))
    return path


@pytest.mark.skipif(not shutil.which("zstd"), reason="Optional maintenance executable")
def test_lossless_dedup_and_restore(tmp_path):
    manifest = plan(tmp_path)
    live = tmp_path / "live.sqlite3"
    original = (tmp_path / "before/snapshot.sqlite3").read_bytes()
    assert compact(manifest, live)["packed"] == 2
    assert (tmp_path / "before/snapshot.sqlite3").exists()
    assert compact(manifest, live, execute=True)["packed"] == 2
    assert len(list((tmp_path / "validation-snapshots").glob("*.zst"))) == 1
    assert not (tmp_path / "before/snapshot.sqlite3").exists()
    receipt = tmp_path / "before/snapshot.sqlite3.packed.json"
    dest = tmp_path / "restored.sqlite3"
    restore(tmp_path, receipt, dest)
    assert dest.read_bytes() == original
    with pytest.raises(FileExistsError):
        restore(tmp_path, receipt, dest)
    assert compact(manifest, live, execute=True)["packed"] == 0


def test_reject_live_symlink_and_traversal(tmp_path):
    live = tmp_path / "active/live.sqlite3"
    live.parent.mkdir()
    live.write_bytes(b"live")
    for relative in ("active/live.sqlite3", "../escape.sqlite3"):
        with pytest.raises(ValueError):
            source_path(tmp_path, relative, live)
    alias = tmp_path / "alias"
    alias.symlink_to(live.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        source_path(tmp_path, "alias/live.sqlite3", tmp_path / "other.sqlite3")


def test_reject_modified_snapshot_and_outstanding_wal(tmp_path):
    manifest = plan(tmp_path)
    path = tmp_path / "before/snapshot.sqlite3"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        compact(manifest, tmp_path / "live.sqlite3", execute=True)
    wal = path.with_name(path.name + "-wal")
    wal.write_bytes(b"outstanding transaction")
    with pytest.raises(ValueError, match="journal"):
        source_path(tmp_path, "before/snapshot.sqlite3", tmp_path / "live.sqlite3")
