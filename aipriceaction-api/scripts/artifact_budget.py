"""Bound audit artifacts, including temporary atomic-write bytes and resume files."""

import tempfile
from pathlib import Path


class ArtifactBudgetExceeded(RuntimeError):
    pass


class ArtifactBudget:
    def __init__(self, root, limit):
        self.root = Path(root).resolve()
        if type(limit) is not int or limit <= 0:
            raise ValueError("Choose a positive artifact byte budget")
        self.limit = limit
        self.sizes = {
            path.resolve(): path.stat().st_size for path in self.root.rglob("*") if path.is_file()
        }
        self.used = sum(self.sizes.values())
        if self.used > limit:
            raise ArtifactBudgetExceeded("Existing artifacts exceed the requested byte budget")

    def write(self, path, data):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Artifact outside the budget root")
        if type(data) is not bytes:
            raise TypeError("Artifact data must be encoded bytes")
        if path.exists() and path.read_bytes() == data:
            return
        # Atomic replacement briefly keeps both versions; budget that peak too.
        if self.used + len(data) > self.limit:
            raise ArtifactBudgetExceeded(
                "Audit artifact byte budget exhausted; completed checkpoints are preserved"
            )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
            temporary.replace(path)
            self.used += len(data) - self.sizes.get(path, 0)
            self.sizes[path] = len(data)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
