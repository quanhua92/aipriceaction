"""Opt-in Docker integration test for a rolling Nginx config update."""

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    os.environ.get("AIPA_RUN_DOCKER_TESTS") != "1",
    reason="Set AIPA_RUN_DOCKER_TESTS=1 to run the isolated Docker reload test",
)
def test_nginx_reload_handles_replaced_bind_mount_and_rejects_bad_config():
    project = "aipa-nginx-test-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=".nginx-reload-test-", dir=ROOT) as directory:
        root = Path(directory)
        config = root / "default.conf"
        config.write_text("server { listen 8899; location / { return 200 'old'; } }\n")
        (root / "compose.yaml").write_text(
            "services:\n  nginx:\n    image: nginx:1.28-alpine\n"
            "    volumes:\n      - ./default.conf:/etc/nginx/conf.d/default.conf:ro\n"
        )
        env = os.environ | {"COMPOSE_PROJECT_NAME": project, "AIPA_NGINX_CONFIG": str(config)}

        def run(*args, check=True):
            return subprocess.run(
                args, cwd=root, env=env, text=True, capture_output=True, check=check, timeout=90
            )

        def body():
            return run("docker", "compose", "exec", "-T", "nginx", "wget", "-qO-", "http://127.0.0.1:8899").stdout

        def wait_body(expected):
            for _ in range(50):
                try:
                    if body() == expected:
                        return
                except subprocess.CalledProcessError:
                    pass
                time.sleep(0.1)
            pytest.fail(f"Nginx did not serve {expected!r}")

        try:
            run("docker", "compose", "up", "-d")
            wait_body("old")
            container = run("docker", "compose", "ps", "-q", "nginx").stdout.strip()
            replacement = root / "replacement.conf"
            replacement.write_text("server { listen 8899; location / { return 200 'new'; } }\n")
            replacement.replace(config)  # Git-style replacement leaves the mounted inode stale.
            assert body() == "old"
            run(str(ROOT / "scripts/reload-nginx-config.sh"))
            wait_body("new")
            assert run("docker", "compose", "ps", "-q", "nginx").stdout.strip() == container

            config.write_text("server { this is not valid nginx syntax; }\n")
            assert run(str(ROOT / "scripts/reload-nginx-config.sh"), check=False).returncode != 0
            assert body() == "new"

            config.write_text("server { listen 8899; location / { return 200 'newer'; } }\n")
            run(str(ROOT / "scripts/reload-nginx-config.sh"))
            wait_body("newer")
            assert run("docker", "compose", "ps", "-q", "nginx").stdout.strip() == container
        finally:
            run("docker", "compose", "down", "--remove-orphans", check=False)
