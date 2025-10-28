"""Streams, subjects and durable consumers (API contract Part 4). Created idempotently by
`python -m nightjar.workers setup` and on worker start; the memory bus uses the same definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MB = 1024 * 1024
DAY = 86_400


@dataclass(frozen=True, slots=True)
class StreamSpec:
    name: str
    subjects: tuple[str, ...]
    max_age_s: int
    max_bytes: int = -1
    discard: Literal["old", "new"] = "old"
    retention: Literal["limits", "workqueue"] = "limits"
    duplicate_window_s: int = 120


@dataclass(frozen=True, slots=True)
class ConsumerSpec:
    name: str
    stream: str
    filter_subjects: tuple[str, ...]
    ack_wait_s: float
    max_deliver: int
    max_ack_pending: int
    backoff_s: tuple[float, ...] = field(default=())
    process: Literal["workers", "api"] = "workers"

    def delay_for(self, num_delivered: int) -> float:
        """Back-off before the next delivery after `num_delivered` failed attempts."""
        if not self.backoff_s:
            return 1.0
        return self.backoff_s[min(max(num_delivered, 1), len(self.backoff_s)) - 1]


def raw_subject(org: object, source: str) -> str:
    return f"nj.raw.{org}.{source}"


def events_subject(org: object, dataset: str) -> str:
    return f"nj.events.normalized.{org}.{dataset.replace('.', '_')}"


def detections_raw_subject(org: object, source: str) -> str:
    return f"nj.detections.raw.{org}.{source}"


def detections_scored_subject(org: object) -> str:
    return f"nj.detections.scored.{org}"


def incidents_subject(org: object, what: Literal["opened", "updated"]) -> str:
    return f"nj.incidents.{org}.{what}"


def agent_tasks_subject(org: object) -> str:
    return f"nj.agents.tasks.{org}"


def approved_actions_subject(org: object) -> str:
    return f"nj.actions.approved.{org}"


def jobs_subject(kind: str) -> str:
    return f"nj.jobs.{kind}"


def dlq_subject(stream: str, consumer: str) -> str:
    return f"nj.dlq.{stream}.{consumer}"


CONTROL_RULES = "nj.control.rules"
CONTROL_MODELS = "nj.control.models"


def streams(raw_max_bytes: int = 256 * MB) -> tuple[StreamSpec, ...]:
    return (
        StreamSpec("NJ_RAW", ("nj.raw.>",), DAY, raw_max_bytes, "new"),
        StreamSpec("NJ_EVENTS", ("nj.events.normalized.>",), 7 * DAY, 1024 * MB, "old"),
        StreamSpec("NJ_DETECTIONS", ("nj.detections.raw.>", "nj.detections.scored.>"), 30 * DAY),
        StreamSpec("NJ_INCIDENTS", ("nj.incidents.>",), 30 * DAY),
        StreamSpec("NJ_WORK", ("nj.agents.tasks.>", "nj.actions.approved.>", "nj.jobs.>"), DAY, retention="workqueue"),
        StreamSpec("NJ_DLQ", ("nj.dlq.>",), 30 * DAY),
    )


CONSUMERS: tuple[ConsumerSpec, ...] = (
    ConsumerSpec("normalizer", "NJ_RAW", ("nj.raw.>",), 30, 5, 1000, (1, 5, 30, 120)),
    ConsumerSpec("store-writer", "NJ_EVENTS", ("nj.events.normalized.>",), 60, 10, 5000, (1, 5, 30), "api"),
    ConsumerSpec("detector", "NJ_EVENTS", ("nj.events.normalized.>",), 30, 5, 2000, (1, 5, 30, 120)),
    ConsumerSpec("ueba", "NJ_EVENTS", ("nj.events.normalized.>",), 60, 5, 2000, (5, 30)),
    ConsumerSpec("scorer", "NJ_DETECTIONS", ("nj.detections.raw.>",), 30, 5, 500, (1, 5, 30)),
    ConsumerSpec("correlator", "NJ_DETECTIONS", ("nj.detections.scored.>",), 30, 5, 200, (1, 5, 30)),
    ConsumerSpec("dispatcher", "NJ_INCIDENTS", ("nj.incidents.*.opened",), 30, 5, 50, (5, 30)),
    ConsumerSpec("orchestrator", "NJ_WORK", ("nj.agents.tasks.>",), 180, 3, 4, (10, 60)),
    ConsumerSpec("responder", "NJ_WORK", ("nj.actions.approved.>",), 60, 3, 10, (5, 30)),
    ConsumerSpec("jobs", "NJ_WORK", ("nj.jobs.>",), 600, 2, 1, (30,)),
)
CONSUMER_BY_NAME = {c.name: c for c in CONSUMERS}

# Normalised datasets the UEBA consumer reads (auth, process and network activity).
UEBA_DATASETS = frozenset(
    {"windows_security", "windows_sysmon", "okta_system", "m365_audit", "vpn_syslog", "aws_cloudtrail"}
)


def subject_matches(pattern: str, subject: str) -> bool:
    """NATS wildcard semantics: `*` one token, `>` one or more trailing tokens."""
    p, s = pattern.split("."), subject.split(".")
    for i, tok in enumerate(p):
        if tok == ">":
            return len(s) > i
        if i >= len(s):
            return False
        if tok != "*" and tok != s[i]:
            return False
    return len(p) == len(s)
