"""Write backend/requirements.lock from a `pip install --dry-run --report` JSON (used by `make lock`).

Freezing a dry-run resolution of core + dev extras keeps optional extras (litellm, asyncpg) out of the lock even
when they are installed in the local venv.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HEADER = (
    "# Resolved backend dependency set (Python 3.13, macOS arm64 / Linux): core + dev extras.\n"
    "# Optional extras (ai: litellm, postgres: asyncpg) are installed separately. Regenerate with: make lock\n"
)


def main(report: str, out: str) -> int:
    data = json.loads(Path(report).read_text())
    pins = sorted(
        ((i["metadata"]["name"], i["metadata"]["version"]) for i in data["install"]),
        key=lambda p: p[0].lower(),
    )
    lines = [f"{name}=={ver}" for name, ver in pins if name != "nightjar-soc"]
    Path(out).write_text(HEADER + "\n".join(lines) + "\n")
    print(f"{out}: {len(lines)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
