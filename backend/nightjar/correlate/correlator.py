"""Correlation: scored detections → incident clusters (time window + entity overlap) → `CorrelationUpdate`s for
A's `incidents.upsert_incident` (API contract Part 3, step 2).

Linking. A detection joins an open cluster when it falls within `window` of the cluster's activity and shares a
linking entity: a host, a user (only from detections naming at most 5 users, so a spray across 63 accounts does
not glue unrelated incidents together), an address (from detections with at most 5), an access key, or a
mail message/sender. A detection that links two clusters merges them (the younger incident is closed as merged).

Opening. A cluster becomes an incident when it holds a detection the triage model did not suppress at severity
high or above, two unsuppressed detections at medium or above, or a storm of `storm_size` detections (the
"false-positive storm": one incident, flagged likely benign, instead of 146 alerts). Isolated noise stays a
detection only.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from nightjar.contracts import (
    SEVERITY_ORDER,
    AssetRecord,
    AttackRef,
    CorrelationUpdate,
    Detection,
    Entities,
    IdentityRecord,
    IncidentKind,
    OpenIncidentIndexEntry,
)

MAX_LINK_USERS = 5
MAX_LINK_IPS = 5
_ORDER = ["info", "low", "medium", "high", "critical"]


@dataclass
class Cluster:
    key: str
    org_id: uuid.UUID
    first_seen: datetime
    last_seen: datetime
    links: set[str] = field(default_factory=set)
    detections: dict[uuid.UUID, Detection] = field(default_factory=dict)
    incident_id: uuid.UUID | None = None
    opened: bool = False
    restored_entities: Entities | None = None
    restored_tactics: tuple[str, ...] = ()

    def unsuppressed(self) -> list[Detection]:
        return [d for d in self.detections.values() if not (d.triage and d.triage.suppressed)]


def link_keys(d: Detection) -> set[str]:
    e = d.entities
    keys = {f"h:{h.lower()}" for h in e.hosts}
    if len(e.users) <= MAX_LINK_USERS:
        keys |= {f"u:{u.lower()}" for u in e.users}
    if len(e.ips) <= MAX_LINK_IPS:
        keys |= {f"i:{ip}" for ip in e.ips}
    keys |= {f"k:{k}" for k in e.access_keys}
    keys |= {
        f"m:{m.lower()}"
        for m in e.emails
        if m.startswith("<") or ("@" in m and not m.lower().endswith(".halden.example"))
    }
    return keys


def _entity_keys(e: Entities) -> set[str]:
    return (
        {f"h:{h.lower()}" for h in e.hosts}
        | {f"u:{u.lower()}" for u in e.users[:MAX_LINK_USERS]}
        | {f"i:{i}" for i in e.ips[:MAX_LINK_IPS]}
        | {f"k:{k}" for k in e.access_keys}
    )


def merge_entities(items: Iterable[Entities]) -> Entities:
    acc: dict[str, list[str]] = {
        k: [] for k in ("hosts", "users", "ips", "cloud_accounts", "access_keys", "hashes", "emails")
    }
    for e in items:
        for k in acc:
            for v in getattr(e, k):
                if v not in acc[k]:
                    acc[k].append(v)
    caps = {"users": 80, "ips": 40, "hosts": 40, "emails": 40, "hashes": 40, "access_keys": 20, "cloud_accounts": 10}
    return Entities(**{k: tuple(v[: caps[k]]) for k, v in acc.items()})


def classify(dets: list[Detection], *, all_noise: bool) -> IncidentKind:
    techs = {a.technique_id for d in dets for a in d.attack}
    roots = {t.split(".")[0] for t in techs}
    sources = {d.source for d in dets}
    if all_noise:
        return "benign_activity"
    if roots & {"T1490", "T1486"} or (roots & {"T1003"} and roots & {"T1021", "T1569", "T1570"}):
        return "ransomware_precursor"
    if roots & {"T1552", "T1530", "T1619"} or (
        any(d.entities.access_keys for d in dets) and roots & {"T1078", "T1136"}
    ):
        return "cloud_credential_exposure"
    if "T1566" in roots and roots & {"T1621", "T1078", "T1114"}:
        return "credential_misuse"
    if "T1566" in roots:
        return "phishing"
    if roots & {"T1621", "T1114"} or ("T1078" in roots and "ueba" in sources):
        return "credential_misuse"
    if "T1110" in roots:
        return "brute_force"
    if roots & {"T1567", "T1048", "T1039", "T1041"} or any(
        "bytesOutMb" in (d.fields or {}) for d in dets if d.source == "ueba"
    ):
        return "data_exfiltration"
    if roots & {"T1204", "T1059"} or "external" in sources:
        return "malware"
    if "ueba" in sources and len(sources) == 1:
        return "insider"
    return "other"


def _top(values: Iterable[str]) -> str | None:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


LINEAGE_NAMES = {
    "veeam": "Backup software update",
    "ccmexec": "Software deployment",
    "intune": "Device management update",
    "tanium": "Endpoint management task",
}


def _software(dets: list[Detection]) -> str | None:
    for d in dets:
        for s in d.evidence:
            parent = str((s.fields or {}).get("process.parent.executable", "")).lower()
            for key, name in LINEAGE_NAMES.items():
                if key in parent:
                    return name
    return None


def title_for(kind: IncidentKind, dets: list[Detection], ent: Entities, lookup: Lookup | None = None) -> str:
    host = _top(h for d in dets for h in d.entities.hosts) or (ent.hosts[0] if ent.hosts else None)
    user = _top(u for d in dets for u in d.entities.users if len(d.entities.users) <= MAX_LINK_USERS) or (
        ent.users[0] if ent.users else None
    )
    asset = lookup.asset(host) if (lookup and host) else None
    if kind == "ransomware_precursor":
        crown = [a for h in ent.hosts if lookup and (a := lookup.asset(h)) and a.criticality == "crown_jewel"]
        first_host = sorted(dets, key=lambda d: d.first_seen)
        beach = next(
            (
                h
                for d in first_host
                for h in d.entities.hosts
                if lookup and (a := lookup.asset(h)) and a.criticality == "crown_jewel"
            ),
            None,
        )
        target = (lookup.asset(beach) if (lookup and beach) else None) or (crown[0] if crown else None)
        if target and target.business_service:
            svc = target.business_service.split("(")[0].strip().lower()
            shared = "serves" in target.business_service.lower() or "shared_platform" in target.tags
            return f"Ransomware precursor on the {'shared ' if shared else ''}{svc}"
        return f"Ransomware precursor on {beach or host}"
    if kind == "credential_misuse":
        return f"Phishing → credential misuse for {user}" if user else "Phishing → credential misuse"
    if kind == "phishing":
        return f"Phishing email delivered to {user}" if user else "Phishing email delivered"
    if kind == "cloud_credential_exposure":
        key = ent.access_keys[0] if ent.access_keys else "a cloud access key"
        return f"Cloud access key {key} used from an unfamiliar network"
    if kind == "brute_force":
        if asset and asset.kind == "network":
            site = asset.site.split()[0] if asset.site else ""
            return f"Password spray against the {site} remote-access gateway".replace("the  ", "the ")
        return f"Password spray against {host}" if host else "Password spray"
    if kind == "data_exfiltration":
        ident = lookup.identity(user) if (lookup and user) else None
        who = f"contractor account {user}" if ident and ident.kind == "contractor" else user
        return f"Unusual data transfer by {who}" if user else "Unusual data transfer"
    if kind == "benign_activity":
        hosts = len(ent.hosts)
        software = _software(dets)
        if software:
            return f"{software} caused {len(dets)} alerts"
        who = f" by {user}" if user else ""
        return f"Burst of {len(dets)} alerts from routine activity{who} on {hosts} computer{'s' if hosts != 1 else ''}"
    if kind == "insider":
        return f"Unusual behaviour by {user}" if user else "Unusual behaviour"
    if kind == "malware":
        return f"Malicious software activity on {host}" if host else "Malicious software activity"
    return f"Suspicious activity on {host}" if host else "Suspicious activity"


def summary_for(dets: list[Detection], ent: Entities, attack: tuple[AttackRef, ...]) -> str:
    by_rule: dict[str, int] = {}
    for d in dets:
        by_rule[d.rule_title] = by_rule.get(d.rule_title, 0) + 1
    top = sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    parts = [f"{n} × {t}" if n > 1 else t for t, n in top]
    scope = []
    if ent.hosts:
        scope.append(f"{len(ent.hosts)} computer{'s' if len(ent.hosts) != 1 else ''}")
    if ent.users:
        scope.append(f"{len(ent.users)} account{'s' if len(ent.users) != 1 else ''}")
    head = f"{len(dets)} detection{'s' if len(dets) != 1 else ''}" + (f" across {' and '.join(scope)}" if scope else "")
    return f"{head}: " + "; ".join(parts) + ("." if parts else "")


class Lookup(Protocol):
    def asset(self, name: str) -> AssetRecord | None: ...

    def identity(self, username: str) -> IdentityRecord | None: ...


class Correlator:
    def __init__(
        self,
        *,
        window: timedelta = timedelta(minutes=60),
        storm_size: int = 20,
        lookup: Callable[[uuid.UUID], Lookup | None] | None = None,
    ) -> None:
        self.lookup = lookup
        self.window = window
        self.storm_size = storm_size
        self.clusters: dict[uuid.UUID, list[Cluster]] = {}

    # ── warm start ─────────────────────────────────────────────────────────
    def restore(self, org_id: uuid.UUID, entries: Iterable[OpenIncidentIndexEntry]) -> int:
        n = 0
        for e in entries:
            if e.status in ("resolved", "closed"):
                continue
            c = Cluster(
                key=e.correlation_key,
                org_id=org_id,
                first_seen=e.last_seen,
                last_seen=e.last_seen,
                links=_entity_keys(e.entities),
                incident_id=e.incident_id,
                opened=True,
                restored_entities=e.entities,
                restored_tactics=e.attack_tactics,
            )
            self.clusters.setdefault(org_id, []).append(c)
            n += 1
        return n

    # ── main entry ─────────────────────────────────────────────────────────
    def add(self, d: Detection) -> CorrelationUpdate | None:
        clusters = self.clusters.setdefault(d.org_id, [])
        keys = link_keys(d)
        lo, hi = d.first_seen - self.window, d.last_seen + self.window
        hits = [c for c in clusters if c.links & keys and c.last_seen >= lo and c.first_seen <= hi]
        for c in clusters:
            if d.id in c.detections:
                c.detections[d.id] = d  # redelivery or re-score: idempotent
                return self._update(c) if c.opened else None
        if not hits:
            cluster = Cluster(key=self._key(d), org_id=d.org_id, first_seen=d.first_seen, last_seen=d.last_seen)
            clusters.append(cluster)
            hits = [cluster]
        hits.sort(key=lambda c: (not c.opened, c.first_seen))
        main, others = hits[0], hits[1:]
        merged_ids: list[uuid.UUID] = []
        for o in others:
            main.detections.update(o.detections)
            main.links |= o.links
            main.first_seen = min(main.first_seen, o.first_seen)
            main.last_seen = max(main.last_seen, o.last_seen)
            if o.incident_id and o.incident_id != main.incident_id:
                merged_ids.append(o.incident_id)
            main.opened = main.opened or o.opened
            clusters.remove(o)
        main.detections[d.id] = d
        main.links |= keys
        main.first_seen = min(main.first_seen, d.first_seen)
        main.last_seen = max(main.last_seen, d.last_seen)
        if not main.opened and self._should_open(main):
            main.opened = True
        if not main.opened:
            return None
        return self._update(main, merged=tuple(merged_ids))

    def note_incident(self, org_id: uuid.UUID, correlation_key: str, incident_id: uuid.UUID) -> None:
        for c in self.clusters.get(org_id, []):
            if c.key == correlation_key:
                c.incident_id = incident_id

    def expire(self, now: datetime, *, keep: timedelta = timedelta(hours=12)) -> int:
        n = 0
        for org, cl in self.clusters.items():
            keep_list = [c for c in cl if c.last_seen >= now - keep]
            n += len(cl) - len(keep_list)
            self.clusters[org] = keep_list
        return n

    # ── internals ──────────────────────────────────────────────────────────
    def _key(self, d: Detection) -> str:
        seed = sorted(link_keys(d))[:3]
        bucket = d.first_seen.replace(minute=0, second=0, microsecond=0).isoformat()
        h = hashlib.sha256(f"{d.org_id}|{'|'.join(seed)}|{bucket}|{d.id}".encode()).hexdigest()[:24]
        return f"corr:{h}"

    def _should_open(self, c: Cluster) -> bool:
        if len(c.detections) >= self.storm_size:
            return True
        live = c.unsuppressed()
        if any(SEVERITY_ORDER[d.severity] >= SEVERITY_ORDER["high"] for d in live):
            return True
        return sum(1 for d in live if SEVERITY_ORDER[d.severity] >= SEVERITY_ORDER["medium"]) >= 2

    def _update(self, c: Cluster, merged: tuple[uuid.UUID, ...] = ()) -> CorrelationUpdate:
        dets = sorted(c.detections.values(), key=lambda d: (d.first_seen, str(d.id)))
        scored = [d.triage for d in dets if d.triage]
        all_noise = bool(dets) and len(scored) == len(dets) and all(t.band == "likely_noise" for t in scored)
        ent = merge_entities([*(d.entities for d in dets), *([c.restored_entities] if c.restored_entities else [])])
        attack: list[AttackRef] = []
        for d in dets:
            for a in d.attack:
                if a not in attack:
                    attack.append(a)
        live = [d for d in dets if not (d.triage and d.triage.suppressed)] or dets
        sev = max((d.severity for d in live), key=lambda s: SEVERITY_ORDER[s], default="low")
        if all_noise:
            sev = "low"
        kind = classify(dets, all_noise=all_noise)
        probs = [t.probability for t in scored]
        return CorrelationUpdate(
            org_id=c.org_id,
            correlation_key=c.key,
            detection_ids=tuple(d.id for d in dets),
            kind=kind,
            title=title_for(kind, dets, ent, self.lookup(c.org_id) if self.lookup else None),
            severity=sev,  # type: ignore[arg-type]
            attack=tuple(attack),
            entities=ent,
            first_seen=c.first_seen,
            last_seen=c.last_seen,
            summary=summary_for(dets, ent, tuple(attack)),
            max_triage_probability=max(probs) if probs else None,
            all_likely_noise=all_noise,
            merge_incident_ids=merged,
        )
