"""Sigma detection with pySigma's native matcher (ADR 0003).

* `RuleBook` loads the curated SigmaHQ set (`rules/sigmahq`, DRL 1.1) and Nightjar's own rules
  (`rules/nightjar`), keeps per-rule metadata (ATT&CK refs from tags, level, status, author, YAML) and exposes
  `RuleInfo` for the API.
* `Detector` holds one compiled matcher per organisation (correlation state must never mix tenants), applies the
  ECS field pipeline, caches the logsource dispatch, turns matches into `Detection`s, de-duplicates correlation
  re-fires per group key and window, and honours per-org rule switches.
* Base rules at level `informational` that only feed correlation rules (single failed sign-ins, single refused
  MFA prompts) never become detections on their own.
* Events of kind `alert` from other security tools become `source="external"` detections.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sigma.collection import SigmaCollection
from sigma.correlations import SigmaCorrelationRule
from sigma.matching import Event, EventLogSource, SigmaMatcher
from sigma.processing.pipeline import ProcessingItem, ProcessingPipeline
from sigma.processing.transformations import FieldMappingTransformation

from nightjar.contracts import AttackRef, Detection, Entities, EventSnapshot, NormalizedEvent, RuleInfo
from nightjar.detect import fastmatch
from nightjar.detect.flatten import UNTRUSTED_LOGSOURCE, flatten, logsource
from nightjar.intel.api import attack_technique, normalise_technique_id
from nightjar.safety import neutralise

fastmatch.install()

RULES_DIR = Path(__file__).resolve().parent / "rules"
NS_DETECTION = uuid.UUID("6f1c2c1e-6e6a-4d1c-9d1a-0de7ec7105a1")
SIGMAHQ_ORIGIN = "SigmaHQ r2025-11-01 (DRL 1.1)"

# Sigma (Windows/Sysmon) field names → Nightjar's ECS-like names. Fields not listed are matched against the
# original payload's native names, which the flattener keeps.
FIELD_MAP: dict[str, str] = {
    "Image": "process.executable",
    "OriginalFileName": "process.pe.original_file_name",
    "CommandLine": "process.command_line",
    "ParentImage": "process.parent.executable",
    "ParentCommandLine": "process.parent.command_line",
    "TargetImage": "target.process.executable",
    "SourceImage": "process.executable",
    "EventID": "event.code",
    "DestinationIp": "destination.ip",
    "DestinationPort": "destination.port",
    "DestinationHostname": "destination.domain",
    "SourceIp": "source.ip",
}
LEVELS = {"informational": "info", "low": "low", "medium": "medium", "high": "high", "critical": "critical"}
_LEVEL_CONF = {"info": 0.2, "low": 0.4, "medium": 0.6, "high": 0.8, "critical": 0.9}
_STATUS_ADJ = {"stable": 0.05, "test": 0.0, "experimental": -0.1, "deprecated": -0.2}
EXTERNAL_ALERTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # action → (title, severity, techniques)
    "secret-exposed": ("Cloud access key exposed in a public code repository", "high", ("T1552.001",)),
    "shadow-copy-deletion-blocked": ("Endpoint protection blocked deletion of recovery copies", "critical", ("T1490",)),
    "ransomware-behaviour-blocked": ("Endpoint protection blocked ransomware-like behaviour", "critical", ("T1486",)),
    "malware-detected": ("Endpoint protection found malicious software", "high", ("T1204.002",)),
    "credential-theft-blocked": ("Endpoint protection blocked a credential theft attempt", "high", ("T1003.001",)),
}
_NOT_USERS = {"system", "local service", "network service", "anonymous logon", "-", "n/a", "dwm-1", "umfd-0"}
EVIDENCE_FIELDS = (
    "event.code",
    "process.executable",
    "process.command_line",
    "process.parent.executable",
    "target.process.executable",
    "GrantedAccess",
    "user.name",
    "source.ip",
    "source.geo.country_iso_code",
    "destination.ip",
    "destination.port",
    "destination.domain",
    "url.original",
    "email.from",
    "email.subject",
    "cloud.api",
    "cloud.access_key_id",
    "file.path",
    "network.bytes_out",
    "m365.operation_count",
    "threat.indicator.verdict",
    "LogonType",
    "TaskName",
    "ServiceName",
    "ShareName",
)


def pipeline() -> ProcessingPipeline:
    return ProcessingPipeline(
        name="nightjar-ecs",
        priority=20,
        items=[ProcessingItem(identifier="nightjar-ecs-fields", transformation=FieldMappingTransformation(FIELD_MAP))],
    )


@dataclass(frozen=True, slots=True)
class RuleMeta:
    id: str
    name: str | None
    title: str
    level: str
    status: str
    logsource: str
    attack: tuple[AttackRef, ...]
    source: str  # sigma | nightjar
    origin: str
    author: str
    yaml_text: str
    correlation: bool
    references: tuple[str, ...] = ()
    description: str = ""


def _attack_refs(tags: Iterable[Any]) -> tuple[AttackRef, ...]:
    refs: list[AttackRef] = []
    for t in tags or ():
        ns = getattr(t, "namespace", None)
        name = getattr(t, "name", str(t))
        if ns not in (None, "attack"):
            continue
        tid = normalise_technique_id(name)
        if tid:
            ref = attack_technique(tid)
            if ref and ref not in refs:
                refs.append(ref)
    return tuple(refs)


def _split_docs(text: str) -> list[str]:
    return [d for d in text.split("\n---") if d.strip()]


class RuleBook:
    """All active rules with metadata. Built once per process (`rulebook()`)."""

    def __init__(self, dirs: Iterable[Path] | None = None) -> None:
        self.dirs = list(dirs or (RULES_DIR / "sigmahq", RULES_DIR / "nightjar"))
        self.meta: dict[str, RuleMeta] = {}
        self.sources: list[tuple[str, str]] = []  # (yaml text, source)
        self.hidden: set[str] = set()  # informational base rules that only feed correlations
        for d in self.dirs:
            src = "nightjar" if d.name == "nightjar" else "sigma"
            for f in sorted(d.glob("*.yml")):
                self._load(f.read_text(), src)
        referenced: set[str] = set()
        for m in self.meta.values():
            if m.correlation:
                doc = next((yaml.safe_load(t) for t in _split_docs(m.yaml_text)), {}) or {}
                referenced.update((doc.get("correlation") or {}).get("rules") or [])
        for m in self.meta.values():
            if m.level == "info" and m.name and m.name in referenced:
                self.hidden.add(m.id)

    def _load(self, text: str, source: str) -> None:
        coll = SigmaCollection.from_yaml(text)
        docs = _split_docs(text)
        for i, rule in enumerate(coll.rules):
            rid = str(rule.id) if rule.id else hashlib.sha256(rule.title.encode()).hexdigest()[:32]
            level = LEVELS.get(str(rule.level.name).lower() if rule.level else "medium", "medium")
            ls = getattr(rule, "logsource", None)
            ls_text = (
                "/".join(x for x in (ls.product, ls.category or ls.service) if x) if ls is not None else "correlation"
            )
            self.meta[rid] = RuleMeta(
                id=rid,
                name=getattr(rule, "name", None),
                title=rule.title,
                level=level,
                status=str(rule.status.name).lower() if rule.status else "test",
                logsource=ls_text or "correlation",
                attack=_attack_refs(rule.tags),
                source=source,
                origin=SIGMAHQ_ORIGIN if source == "sigma" else "Nightjar",
                author=str(getattr(rule, "author", "") or ""),
                yaml_text=docs[i] if len(docs) == len(coll.rules) else text,
                correlation=isinstance(rule, SigmaCorrelationRule),
                references=tuple(str(r) for r in (getattr(rule, "references", None) or ())),
                description=str(getattr(rule, "description", "") or ""),
            )
        self.sources.append((text, source))

    def collection(self) -> SigmaCollection:
        """A fresh parse (the pipeline mutates rules, so every matcher gets its own copy)."""
        rules: list[Any] = []
        for text, _ in self.sources:
            rules += SigmaCollection.from_yaml(text).rules
        return SigmaCollection(rules)

    def info(self, rule_id: str, *, enabled: bool = True, hits_7d: int = 0, precision: float | None = None) -> RuleInfo:
        m = self.meta[rule_id]
        return RuleInfo(
            id=m.id,
            title=m.title,
            source="nightjar" if m.source == "nightjar" else "sigma",
            level=m.level,  # type: ignore[arg-type]
            status=m.status,
            logsource=m.logsource,
            attack=m.attack,
            enabled=enabled,
            hits_7d=hits_7d,
            precision_estimate=precision,
            origin=m.origin,
        )


@lru_cache(maxsize=1)
def rulebook() -> RuleBook:
    return RuleBook()


class _FastMatcher(SigmaMatcher):
    """SigmaMatcher with the logsource dispatch cached per (category, product, service)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ls_cache: dict[tuple[str | None, str | None, str | None], list[Any]] = {}

    def _get_matchers(self, logsource: EventLogSource):  # type: ignore[override]
        key = logsource.as_tuple()
        hit = self._ls_cache.get(key)
        if hit is None:
            hit = super()._get_matchers(logsource)
            self._ls_cache[key] = hit
        return hit


@dataclass
class _OrgState:
    matcher: _FastMatcher
    fired: dict[tuple[str, tuple[Any, ...]], datetime] = field(default_factory=dict)


def _ts(dt: datetime) -> str:
    return dt.isoformat()


def detection_id(org_id: uuid.UUID, rule_id: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NS_DETECTION, f"{org_id}|{rule_id}|{key}")


def entities_of(events: Iterable[NormalizedEvent]) -> Entities:
    hosts: list[str] = []
    users: list[str] = []
    ips: list[str] = []
    accounts: list[str] = []
    keys: list[str] = []
    hashes: list[str] = []
    emails: list[str] = []

    def add(lst: list[str], v: Any) -> None:
        if v and str(v) not in lst:
            lst.append(str(v))

    for e in events:
        for h in e.related_hosts:
            add(hosts, h)
        for u in e.related_users:
            if u.lower() not in _NOT_USERS and not u.endswith("$"):
                add(users, u)
        for ip in e.related_ips:
            add(ips, ip)
        if e.cloud:
            add(accounts, e.cloud.account_id)
            if e.cloud.access_key_id and str(e.cloud.access_key_id).upper().startswith(("AKIA", "ASIA")):
                add(keys, e.cloud.access_key_id)
        add(hashes, e.file_hash_sha256)
        if e.process:
            add(hashes, e.process.hash_sha256)
        if e.email:
            add(emails, e.email.sender)
            for r in e.email.recipients:
                add(emails, r)
            mid = (e.raw or {}).get("message_id") or (e.raw or {}).get("MessageId")
            add(emails, mid)
    return Entities(
        hosts=tuple(hosts),
        users=tuple(users),
        ips=tuple(ips),
        cloud_accounts=tuple(accounts),
        access_keys=tuple(keys),
        hashes=tuple(hashes),
        emails=tuple(emails),
    )


def snapshot(event: NormalizedEvent, flat: dict[str, Any] | None = None) -> EventSnapshot:
    flat = flat if flat is not None else flatten(event)
    fields = {k: flat[k] for k in EVIDENCE_FIELDS if k in flat}
    text = event.message.text
    if event.process and event.process.command_line:
        text = event.process.command_line
    return EventSnapshot(
        event_id=event.event_id,
        timestamp=event.timestamp,
        dataset=event.dataset,
        action=event.action,
        summary=neutralise(text, field="process.command_line" if event.process else "message", max_len=500),
        host=event.host.name if event.host else None,
        user=event.user.name if event.user else None,
        fields=fields,
    )


def summary_for(title: str, ent: Entities) -> str:
    subject = ent.hosts[0] if ent.hosts else ent.users[0] if ent.users else ent.ips[0] if ent.ips else None
    who = f" ({ent.users[0]})" if ent.hosts and ent.users else ""
    return f"{title} on {subject}{who}" if subject else title


class Detector:
    """Per-process detector; `detect()` is synchronous and CPU-bound (call it from a worker thread or loop)."""

    def __init__(self, book: RuleBook | None = None) -> None:
        self.book = book or rulebook()
        self._orgs: dict[uuid.UUID, _OrgState] = {}
        self.disabled: dict[uuid.UUID, set[str]] = {}
        self.events_seen = 0
        self.seconds = 0.0
        self._recent: OrderedDict[str, NormalizedEvent] = OrderedDict()  # evidence for correlation detections

    def _state(self, org_id: uuid.UUID) -> _OrgState:
        st = self._orgs.get(org_id)
        if st is None:
            st = _OrgState(_FastMatcher(self.book.collection(), pipeline=pipeline()))
            self._orgs[org_id] = st
        return st

    def set_disabled(self, org_id: uuid.UUID, rule_ids: Iterable[str]) -> None:
        self.disabled[org_id] = set(rule_ids)

    def detect(self, event: NormalizedEvent) -> list[Detection]:
        started = time.perf_counter()
        st = self._state(event.org_id)
        flat = flatten(event)
        flat["__event_id"] = event.event_id
        ls = logsource(event)
        out: list[Detection] = []
        matches = st.matcher.match(Event(timestamp=event.timestamp, logsource=EventLogSource(*ls), data=flat))
        if event.untrusted_flags:
            matches += st.matcher.match(
                Event(timestamp=event.timestamp, logsource=EventLogSource(*UNTRUSTED_LOGSOURCE), data=flat)
            )
        disabled = self.disabled.get(event.org_id, set())
        for m in matches:
            rid = str(m.rule.id) if m.rule.id else ""
            meta = self.book.meta.get(rid)
            if meta is None or rid in disabled or rid in self.book.hidden:
                continue
            if meta.correlation:
                self._remember(event)
                d = self._correlation_detection(st, event, meta, m)
                if d:
                    out.append(d)
                continue
            ent = entities_of([event])
            out.append(
                Detection(
                    id=detection_id(event.org_id, rid, event.event_id),
                    org_id=event.org_id,
                    source="sigma",
                    rule_id=rid,
                    rule_title=meta.title,
                    severity=meta.level,  # type: ignore[arg-type]
                    confidence=round(
                        min(0.99, max(0.05, _LEVEL_CONF[meta.level] + _STATUS_ADJ.get(meta.status, 0))), 2
                    ),
                    attack=meta.attack,
                    entities=ent,
                    first_seen=event.timestamp,
                    last_seen=event.timestamp,
                    event_ids=(event.event_id,),
                    evidence=(snapshot(event, flat),),
                    summary=summary_for(meta.title, ent),
                    fields={**{k: flat[k] for k in EVIDENCE_FIELDS if k in flat}, "ruleAuthor": meta.author},
                )
            )
        if matches:
            self._remember(event)
        if event.kind == "alert" and event.dataset in ("edr.alert", "nightjar.synthetic"):
            ext = self._external(event, flat)
            if ext:
                out.append(ext)
        self.events_seen += 1
        self.seconds += time.perf_counter() - started
        return out

    def _correlation_detection(
        self,
        st: _OrgState,
        event: NormalizedEvent,
        meta: RuleMeta,
        match: Any,
    ) -> Detection | None:
        rule = match.rule
        span = timedelta(seconds=rule.timespan.seconds) if getattr(rule, "timespan", None) else timedelta(minutes=10)
        group = tuple(sorted((match.group_values or {}).items())) if match.group_values else ()
        key = (meta.id, group)
        first_fired = st.fired.get(key)
        if first_fired is not None and event.timestamp - first_fired < span:
            return None  # the correlation re-fires on every event past the threshold: keep the first
        st.fired[key] = event.timestamp
        if len(st.fired) > 20_000:
            cutoff = event.timestamp - timedelta(hours=6)
            st.fired = {k: v for k, v in st.fired.items() if v >= cutoff}
        ids = [e.data.get("__event_id") for e in match.events]
        event_ids = tuple(i for i in ids if i) or (event.event_id,)
        evid = [self._recent[i] for i in event_ids if i in self._recent][:20]
        if not evid:
            evid = [event]
        ent = entities_of(evid)
        if group:
            extra_ips = [str(v) for _, v in group if isinstance(v, str) and v.count(".") == 3]
            if extra_ips:
                ent = Entities(**{**_ent_dict(ent), "ips": tuple(dict.fromkeys([*ent.ips, *extra_ips]))})
        first_ts = min((e.timestamp for e in match.events), default=event.timestamp)
        group_text = ", ".join(f"{k}={v}" for k, v in group)
        return Detection(
            id=detection_id(event.org_id, meta.id, f"{group_text}|{_ts(first_ts)}"),
            org_id=event.org_id,
            source="sigma",
            rule_id=meta.id,
            rule_title=meta.title,
            severity=meta.level,  # type: ignore[arg-type]
            confidence=round(min(0.99, _LEVEL_CONF[meta.level] + 0.05), 2),
            attack=meta.attack,
            entities=ent,
            first_seen=first_ts,
            last_seen=event.timestamp,
            event_ids=event_ids[:500],
            evidence=tuple(snapshot(e) for e in evid[:20]),
            summary=summary_for(meta.title, ent) + (f" ({len(event_ids)} events)" if len(event_ids) > 1 else ""),
            fields={"group": group_text, "eventCount": len(event_ids), "ruleAuthor": meta.author},
        )

    def _external(self, event: NormalizedEvent, flat: dict[str, Any]) -> Detection | None:
        title, severity, techs = EXTERNAL_ALERTS.get(
            event.action, (f"Alert from another security tool: {event.action.replace('-', ' ')}", "medium", ())
        )
        attack = tuple(r for r in (attack_technique(t) for t in techs) if r)
        ent = entities_of([event])
        rid = f"external:{event.dataset}:{event.action}"
        return Detection(
            id=detection_id(event.org_id, rid, event.event_id),
            org_id=event.org_id,
            source="external",
            rule_id=rid,
            rule_title=title,
            severity=severity,  # type: ignore[arg-type]
            confidence=0.75,
            attack=attack,
            entities=ent,
            first_seen=event.timestamp,
            last_seen=event.timestamp,
            event_ids=(event.event_id,),
            evidence=(snapshot(event, flat),),
            summary=summary_for(title, ent),
            fields={k: flat[k] for k in EVIDENCE_FIELDS if k in flat},
        )

    def _remember(self, event: NormalizedEvent) -> None:
        self._recent[event.event_id] = event
        self._recent.move_to_end(event.event_id)
        while len(self._recent) > 5000:
            self._recent.popitem(last=False)

    def throughput(self) -> float:
        return self.events_seen / self.seconds if self.seconds else 0.0


def _ent_dict(e: Entities) -> dict[str, Any]:
    return {
        "hosts": e.hosts,
        "users": e.users,
        "ips": e.ips,
        "cloud_accounts": e.cloud_accounts,
        "access_keys": e.access_keys,
        "hashes": e.hashes,
        "emails": e.emails,
    }
