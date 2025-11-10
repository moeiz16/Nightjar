"""Regenerate the bundled `halden-night` replay and its ground truth (deterministic):

    python scripts/build_replay.py

* backend/nightjar/workers/replay/halden-night.jsonl.gz   events (segment, offset from night, format, payload)
* eval/scenarios/halden-night.labels.json                 per line: scenario, malicious, technique; expected incidents
Labels never travel with the events (API contract Part 6).
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

from nightjar.workers.replay.generator import SEGMENT_INFO, halden_night

INCIDENT_KIND = {
    "ransomware_precursor": "ransomware_precursor",
    "phishing_credential_misuse": "credential_misuse",
    "cloud_credential_exposure": "cloud_credential_exposure",
    "benign_backup_rollout": "benign_activity",
    "password_spray": "brute_force",
    "data_exfiltration": "data_exfiltration",
}
INCIDENT_KIND = {
    "ransomware_precursor": "ransomware_precursor",
    "phishing_credential_misuse": "credential_misuse",
    "cloud_credential_exposure": "cloud_credential_exposure",
    "benign_backup_rollout": "benign_activity",
    "password_spray": "brute_force",
    "data_exfiltration": "data_exfiltration",
}
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "nightjar" / "workers" / "replay" / "halden-night.jsonl.gz"
LABELS = ROOT / "eval" / "scenarios" / "halden-night.labels.json"


def main() -> None:
    evs = halden_night()
    with gzip.GzipFile(OUT, "wb", mtime=0) as gz:
        for i, e in enumerate(evs):
            line = {"seq": i, "segment": e.segment, "t": round(e.t, 3), "format": e.fmt, "payload": e.payload}
            gz.write((json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n").encode())
    labels = {
        "scenario": "halden-night",
        "keyedBy": "seq (line number in the bundle; event ids depend on the organisation and replay time)",
        "expectedIncidents": [
            {
                "segment": seg,
                "reference": ref,
                "kind": INCIDENT_KIND[kind],
                "verdict": {"s4": "benign", "s6": "needs_review"}.get(seg, "true_positive"),
            }
            for seg, (kind, ref) in SEGMENT_INFO.items()
        ],
        "events": [
            {"seq": i, "scenario": e.scenario, "malicious": e.malicious, "technique": e.technique}
            for i, e in enumerate(evs)
            if e.segment != "bg" or e.scenario != "background"
        ],
    }
    LABELS.write_text(json.dumps(labels, indent=1) + "\n")
    print(
        f"{len(evs)} events -> {OUT.name} ({OUT.stat().st_size // 1024} KB); labels for {len(labels['events'])} "
        f"scenario events; {Counter(e.segment for e in evs)}"
    )


if __name__ == "__main__":
    main()
