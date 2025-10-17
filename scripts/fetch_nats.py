"""Download the pinned `nats-server` binary into `.tools/` (git-ignored) and verify its SHA-256.

Single static binary, Apache-2.0, ~7 MB download, ~20 MB resident with JetStream on. No Docker, no installer.

    python scripts/fetch_nats.py            # idempotent; prints the binary path
    NATS_VERSION=v2.15.3 python scripts/fetch_nats.py
"""

from __future__ import annotations

import hashlib
import os
import platform
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = os.environ.get("NATS_VERSION", "v2.15.3")
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
BINARY = TOOLS / "nats-server"
BASE = f"https://github.com/nats-io/nats-server/releases/download/{VERSION}"


def _target() -> tuple[str, str]:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "AMD64": "amd64"}.get(platform.machine())
    if not system or not machine:
        sys.exit(f"Unsupported platform {platform.system()}/{platform.machine()}; download nats-server manually.")
    return system, machine


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "nightjar-soc-setup"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> int:
    system, machine = _target()
    asset = f"nats-server-{VERSION}-{system}-{machine}.tar.gz"
    version_file = TOOLS / "nats-server.version"
    if BINARY.exists() and version_file.exists() and version_file.read_text().strip() == VERSION:
        print(BINARY)
        return 0
    TOOLS.mkdir(exist_ok=True)
    sums = _get(f"{BASE}/SHA256SUMS").decode()
    expected = next((line.split()[0] for line in sums.splitlines() if line.strip().endswith(asset)), None)
    if not expected:
        sys.exit(f"{asset} is not listed in SHA256SUMS for {VERSION}")
    blob = _get(f"{BASE}/{asset}")
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        sys.exit(f"Checksum mismatch for {asset}: expected {expected}, got {actual}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset
        archive.write_bytes(blob)
        if asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                member = next(n for n in zf.namelist() if n.endswith("/nats-server"))
                BINARY.write_bytes(zf.read(member))
        else:
            with tarfile.open(archive) as tf:
                member = next(m for m in tf.getmembers() if m.name.endswith("/nats-server") and m.isfile())
                extracted = tf.extractfile(member)
                assert extracted is not None
                BINARY.write_bytes(extracted.read())
    BINARY.chmod(BINARY.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    version_file.write_text(VERSION + "\n")
    print(BINARY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
