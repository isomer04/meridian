"""Run cross-stack Playwright tests with isolated services and reliable cleanup."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
ROOT = FRONTEND.parent
DATABASE = ROOT / ".eval_work" / "phase3-4-playwright.db"
NPM = "npm.cmd" if os.name == "nt" else "npm"

args = argparse.ArgumentParser(description=__doc__)
args.add_argument(
    "--config",
    default=None,
    help="Playwright config to use, relative to frontend/ (defaults to playwright.config.ts).",
)
options, _extra = args.parse_known_args()


def wait_for(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        if process.poll() is not None:
            raise RuntimeError(f"service on port {port} exited early with {process.returncode}")
        time.sleep(0.25)
    raise TimeoutError(f"service on port {port} did not become ready within 60 seconds")


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
        process.wait(timeout=15)
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=15)


DATABASE.unlink(missing_ok=True)
for suffix in ("-shm", "-wal"):
    DATABASE.with_name(DATABASE.name + suffix).unlink(missing_ok=True)

api: subprocess.Popen[bytes] | None = None
next_server: subprocess.Popen[bytes] | None = None
try:
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "meridian.api.app:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        env={
            **os.environ,
            "MERIDIAN_DB": str(DATABASE),
            "MERIDIAN_DEMO_MODE": "1",
        },
        start_new_session=(os.name != "nt"),
    )
    next_server = subprocess.Popen(
        [NPM, "run", "dev"],
        cwd=FRONTEND,
        env={
            **os.environ,
            "MERIDIAN_API_ORIGIN": "http://127.0.0.1:8000",
            "NEXT_PUBLIC_MERIDIAN_API_ORIGIN": "http://127.0.0.1:8000",
        },
        start_new_session=(os.name != "nt"),
    )
    assert api is not None
    assert next_server is not None
    wait_for(8000, api)
    wait_for(3000, next_server)
    playwright_args = [NPM, "exec", "playwright", "--", "test"]
    if options.config:
        playwright_args += ["--config", options.config]
    result = subprocess.run(
        playwright_args,
        cwd=FRONTEND,
        env={**os.environ, "CI": "1"},
        timeout=180,
    )
    raise SystemExit(result.returncode)
finally:
    stop(next_server)
    stop(api)
