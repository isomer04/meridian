"""Run the Meridian FastAPI backend and Next.js frontend together."""

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
NPM = "npm.cmd" if os.name == "nt" else "npm"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Enable the local-only auto-approval demonstration control.",
    )
    return parser.parse_args()


def require_free_port(port: int) -> None:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"port {port} is already in use") from exc


def wait_for(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        if process.poll() is not None:
            raise RuntimeError(
                f"service on port {port} exited early with {process.returncode}"
            )
        time.sleep(0.25)
    raise TimeoutError(f"service on port {port} did not become ready within 60 seconds")


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=15)


def main() -> int:
    options = parse_args()
    try:
        require_free_port(options.api_port)
        require_free_port(options.frontend_port)
    except RuntimeError as exc:
        print(f"Cannot start Meridian: {exc}.", file=sys.stderr)
        print(
            "Stop the process using that port, or choose different ports, for example:\n"
            "  npm run dev:stack -- --api-port 8010 --frontend-port 3010",
            file=sys.stderr,
        )
        return 2

    api_origin = f"http://127.0.0.1:{options.api_port}"
    api_env = dict(os.environ)
    if options.demo:
        api_env["MERIDIAN_DEMO_MODE"] = "1"
    frontend_env = {
        **os.environ,
        "MERIDIAN_API_ORIGIN": api_origin,
        "NEXT_PUBLIC_MERIDIAN_API_ORIGIN": api_origin,
    }

    api: subprocess.Popen[bytes] | None = None
    frontend: subprocess.Popen[bytes] | None = None
    try:
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "meridian.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(options.api_port),
            ],
            cwd=ROOT,
            env=api_env,
            start_new_session=(os.name != "nt"),
        )
        frontend = subprocess.Popen(
            [NPM, "run", "dev", "--", "--port", str(options.frontend_port)],
            cwd=FRONTEND,
            env=frontend_env,
            start_new_session=(os.name != "nt"),
        )
        wait_for(options.api_port, api)
        wait_for(options.frontend_port, frontend)
        print(f"\nMeridian is ready at http://127.0.0.1:{options.frontend_port}")
        print(f"API: {api_origin}")
        print("Press Ctrl+C to stop both services.\n")

        while True:
            if api.poll() is not None:
                return api.returncode or 1
            if frontend.poll() is not None:
                return frontend.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Meridian…")
        return 0
    finally:
        stop(frontend)
        stop(api)


if __name__ == "__main__":
    raise SystemExit(main())
