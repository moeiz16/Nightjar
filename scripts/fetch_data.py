"""Download the optional public datasets into `data/` (git-ignored). See docs/DATA.md for licences.

Everything Nightjar SOC needs to run is bundled (synthetic replay, ATT&CK/KEV subsets, curated rules);
these downloads add real attack telemetry for evaluation and the full rule/intel catalogues.

    python scripts/fetch_data.py            # all sources (~55 MB transferred, ~110 MB on disk)
    python scripts/fetch_data.py otrf kev   # selected sources
    python scripts/fetch_data.py --check    # HEAD-check every URL, download nothing

Idempotent: files already present with the expected checksum are skipped. Pinned artefacts carry a
SHA-256; the KEV feed is a live catalogue and is not pinned (the intel tools filter it by `dateAdded`).
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
UA = {"User-Agent": "nightjar-soc-data"}
MAX_FILE_MB = 50

OTRF_BASE = "https://raw.githubusercontent.com/OTRF/Security-Datasets/master/datasets/atomic"
SIGMA_RELEASE = "r2025-11-01"
ATTACK_VERSION = "18.1"


@dataclass(frozen=True)
class Source:
    key: str
    group: str
    url: str
    dest: str
    sha256: str | None = None
    note: str = ""


# OTRF Security Datasets (MIT): small host/cloud scenarios. `attack` = labels from OTRF metadata.
OTRF = [
    ("lsass_mimikatz", "windows/credential_access/host/empire_mimikatz_logonpasswords.zip", "T1003.001"),
    ("lsass_comsvcs", "windows/credential_access/host/psh_lsass_memory_dump_comsvcs.zip", "T1003.001"),
    ("lsass_dumpert", "windows/credential_access/host/cmd_lsass_memory_dumpert_syscalls.zip", "T1003.001"),
    (
        "ntds_shadow_copy",
        "windows/credential_access/host/cmd_dumping_ntds_dit_file_volume_shadow_copy.zip",
        "T1003.003",
    ),
    ("psexec_lateral", "windows/lateral_movement/host/empire_psexec_dcerpc_tcp_svcctl.zip", "T1021.002"),
    ("schtask_remote", "windows/lateral_movement/host/schtask_create.zip", "T1053.005"),
    ("psh_http_listener", "windows/execution/host/psh_powershell_httplistener.zip", "T1059.001"),
    ("aws_s3_exfil", "aws/collection/ec2_proxy_s3_exfiltration.zip", "T1078.004,T1530"),
    ("aws_honeybucket", "aws/discovery/aws_s3_honeybucketlogs.zip", "T1580"),
]

SOURCES: list[Source] = [
    *[Source(key, "otrf", f"{OTRF_BASE}/{path}", f"otrf/{key}.zip", note=attack) for key, path, attack in OTRF],
    Source(
        "sigma_core",
        "sigma",
        f"https://github.com/SigmaHQ/sigma/releases/download/{SIGMA_RELEASE}/sigma_core.zip",
        "sigma/sigma_core.zip",
        note="DRL 1.1",
    ),
    Source(
        "attack_enterprise",
        "attack",
        f"https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/"
        f"enterprise-attack-{ATTACK_VERSION}.json",
        f"attack/enterprise-attack-{ATTACK_VERSION}.json",
        note="ATT&CK Terms of Use",
    ),
    Source(
        "kev",
        "kev",
        "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
        "kev/known_exploited_vulnerabilities.json",
        note="CC0 1.0 (live feed, not pinned)",
    ),
]

# Sigma rule folders kept from sigma_core.zip (everything else is discarded).
SIGMA_KEEP = (
    "rules/windows/process_creation/",
    "rules/windows/process_access/",
    "rules/windows/builtin/security/",
    "rules/windows/powershell/powershell_script/",
    "rules/windows/network_connection/",
    "rules/cloud/aws/",
    "rules/cloud/azure/signin_logs/",
    "rules/cloud/m365/",
)

PINS_FILE = ROOT / "scripts" / "data_pins.json"


def _pins() -> dict[str, str]:
    return json.loads(PINS_FILE.read_text()) if PINS_FILE.exists() else {}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _head(url: str) -> tuple[int, int | None]:
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        length = resp.headers.get("Content-Length")
        return resp.status, int(length) if length else None


def _extract_otrf(zip_path: Path) -> Path:
    """Unzip the log in an OTRF archive next to it: JSON lines (`<key>.json`) or, for S3 access logs, CSV."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.filename.startswith("__MACOSX") and not m.is_dir()]
        logs = [m for m in members if m.filename.endswith(".json")] or [
            m for m in members if m.filename.endswith(".csv")
        ]
        if not logs:
            raise SystemExit(f"{zip_path.name}: no JSON or CSV log inside")
        total = sum(m.file_size for m in logs)
        if total > MAX_FILE_MB * 1024 * 1024:
            raise SystemExit(f"{zip_path.name}: {total / 1e6:.0f} MB uncompressed exceeds the {MAX_FILE_MB} MB cap")
        out = zip_path.with_suffix(Path(logs[0].filename).suffix)
        with out.open("wb") as fh:
            for m in logs:
                fh.write(zf.read(m))
    return out


def _extract_sigma(zip_path: Path) -> int:
    target = zip_path.parent / "rules"
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for m in zf.infolist():
            name = m.filename
            if name.endswith(".yml") and any(k in name for k in SIGMA_KEEP):
                rel = name[name.index("rules/") :]
                dest = target / rel.removeprefix("rules/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(m))
                count += 1
    return count


def fetch(src: Source, pins: dict[str, str]) -> tuple[Path, str, bool]:
    dest = DATA / src.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = pins.get(src.key)
    if dest.exists() and expected and hashlib.sha256(dest.read_bytes()).hexdigest() == expected:
        return dest, expected, False
    blob = _get(src.url)
    digest = hashlib.sha256(blob).hexdigest()
    if expected and digest != expected and src.group != "kev":
        raise SystemExit(f"{src.key}: checksum mismatch (expected {expected}, got {digest})")
    if len(blob) > MAX_FILE_MB * 1024 * 1024:
        raise SystemExit(f"{src.key}: {len(blob) / 1e6:.0f} MB exceeds the {MAX_FILE_MB} MB cap")
    dest.write_bytes(blob)
    return dest, digest, True


def main(argv: list[str]) -> int:
    groups = {a for a in argv if not a.startswith("-")}
    selected = [s for s in SOURCES if not groups or s.group in groups or s.key in groups]
    if "--check" in argv:
        for s in selected:
            status, length = _head(s.url)
            print(f"{status} {(length or 0) / 1e6:7.2f} MB  {s.key:20s} {s.url}")
        return 0
    pins = _pins()
    new_pins = dict(pins)
    transferred = 0
    for s in selected:
        path, digest, downloaded = fetch(s, pins)
        if downloaded:
            transferred += path.stat().st_size
        if s.group != "kev":
            new_pins[s.key] = digest
        if s.group == "otrf":
            _extract_otrf(path)
        elif s.group == "sigma":
            print(f"  sigma: kept {_extract_sigma(path)} rules from {SIGMA_RELEASE}")
        print(f"{'fetched' if downloaded else 'cached '} {s.key:20s} {path.stat().st_size / 1e6:7.2f} MB")
    manifest = {s.key: {"url": s.url, "path": s.dest, "group": s.group, "note": s.note} for s in SOURCES}
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if new_pins != pins:
        PINS_FILE.write_text(json.dumps(dict(sorted(new_pins.items())), indent=2) + "\n")
    on_disk = sum(p.stat().st_size for p in DATA.rglob("*") if p.is_file())
    print(f"transferred {transferred / 1e6:.1f} MB · data/ now {on_disk / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
