"""Run the sibling web app against Python without modifying its repository."""

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configured_api_url():
    explicit_url = os.environ.get("AIPA_LOCAL_API_URL") or os.environ.get("VITE_API_PROXY_TARGET")
    if explicit_url:
        return explicit_url
    values = dotenv_values(PROJECT_ROOT / ".env")
    port = os.environ.get("AIPA_API_PORT") or values.get("AIPA_API_PORT") or "8899"
    try:
        if not 1 <= int(port) <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("AIPA_API_PORT must be between 1 and 65535") from None
    port = str(int(port))
    return f"http://127.0.0.1:{port}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--web-dir", type=Path, default=Path("/Users/quan/data-workspace/aipriceaction-web")
    )
    parser.add_argument("--api")
    parser.add_argument("--port", default="5173")
    args = parser.parse_args()
    args.api = args.api or configured_api_url()
    source = args.web_dir.resolve()
    vite = source / "node_modules/vite/bin/vite.js"
    if not vite.exists():
        parser.error(f"Installed Vite not found: {vite}")
    with tempfile.TemporaryDirectory(prefix="aipa-local-web-") as directory:
        root = Path(directory) / "web"
        shutil.copytree(
            source,
            root,
            ignore=shutil.ignore_patterns(
                "node_modules", ".git", "dist", ".env", ".env.*", ".vercel", ".tanstack"
            ),
        )
        (root / "node_modules").symlink_to(source / "node_modules", target_is_directory=True)
        (root / "vite.local.config.ts").write_text("""import original from './vite.config'
export default async (env) => {
  const config = typeof original === 'function' ? await original(env) : original;
  return { ...config, cacheDir: './.local-vite-cache' };
};
""")
        print(f"Web: http://localhost:{args.port} -> Python: {args.api}", flush=True)
        subprocess.run(
            [
                "node",
                str(vite),
                "--config",
                "vite.local.config.ts",
                "--configLoader",
                "runner",
                "--host",
                "0.0.0.0",
                "--port",
                args.port,
                "--strictPort",
            ],
            cwd=root,
            env={**os.environ, "VITE_API_PROXY_TARGET": args.api},
            check=True,
        )


if __name__ == "__main__":
    main()
