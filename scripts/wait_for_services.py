#!/usr/bin/env python3
"""Block until Postgres and Redis answer, or give up loudly."""

from __future__ import annotations

import socket
import sys
import time
from urllib.parse import urlparse


def wait(name: str, host: str, port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"{name:<9} ready on {host}:{port}")
                return True
        except OSError:
            time.sleep(1)
    print(f"{name:<9} NOT ready on {host}:{port} after {timeout:.0f}s", file=sys.stderr)
    return False


def _hostport(url: str, default_port: int) -> tuple[str, int]:
    p = urlparse(url)
    return p.hostname or "localhost", p.port or default_port


def main() -> int:
    import os
    from pathlib import Path

    env = {}
    envfile = Path(".env") if Path(".env").exists() else Path(".env.example")
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()

    db = os.environ.get("DATABASE_URL", env.get("DATABASE_URL", "postgresql://x@localhost:5433/x"))
    rd = os.environ.get("REDIS_URL", env.get("REDIS_URL", "redis://localhost:6380/0"))

    ok = wait("postgres", *_hostport(db, 5432))
    ok = wait("redis", *_hostport(rd, 6379)) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
