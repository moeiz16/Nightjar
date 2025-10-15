"""Role → permission map (RBAC). Single source of truth for the API, the agents' human-approval rules and the
web app (`/auth/me` returns the caller's permission list). Mirrored in docs/API_CONTRACT.md §1.3.

Separation of duties:
* `admin` runs the platform (members, ingest keys, settings) but cannot approve response actions or notices.
* `responder` approves containment actions; actions on crown-jewel assets also need an `executive` approval
  (two-person rule, ADR 0006).
* `executive` (CISO / executive) owns regulatory determinations (significance, materiality) and notice sign-off.
* `auditor` is read-only everywhere, including the audit trail.
"""

from __future__ import annotations

ROLES: tuple[str, ...] = ("admin", "executive", "responder", "analyst", "auditor")
ROLE_LABELS = {
    "admin": "Administrator",
    "executive": "CISO / executive",
    "responder": "Incident responder",
    "analyst": "Security analyst",
    "auditor": "Auditor",
}

_ALL = set(ROLES)
_SEC = {"analyst", "responder"}
_READERS = _SEC | {"admin", "auditor"}

PERMISSIONS: dict[str, set[str]] = {
    # the watch floor, executive posture and weekly brief
    "watch:read": _ALL,
    "exec:read": _ALL,
    "reports:generate": {"executive", "admin"},
    # events, detections, rules
    "events:read": _READERS | {"executive"},
    "events:ingest": {"admin", "analyst"},
    "detections:read": _READERS,
    "rules:read": _READERS,
    "rules:manage": {"analyst"},
    # incidents and investigations
    "incidents:read": _ALL,
    "incidents:write": _SEC,
    "investigations:read": _ALL,
    "investigations:run": _SEC,
    # response actions (ADR 0006)
    "actions:read": _ALL,
    "actions:propose": _SEC,
    "actions:approve": {"responder"},
    "actions:approve_crown_jewel": {"executive"},
    # incident clock and notices
    "reporting:read": _ALL,
    "reporting:assess": {"executive"},
    "notices:draft": _SEC | {"executive"},
    "notices:approve": {"executive"},
    "notices:submit": {"executive"},
    # hunting and models
    "hunting:read": _READERS,
    "hunting:write": _SEC,
    "ueba:read": _READERS,
    "models:read": _READERS | {"executive"},
    "models:promote": {"executive"},
    # platform
    "settings:read": _ALL,
    "settings:manage": {"admin", "executive"},
    "ingest_keys:manage": {"admin"},
    "members:read": _ALL,
    "members:manage": {"admin"},
    "org:admin": {"admin"},
    "audit:read": {"admin", "auditor", "executive"},
}

ALL_PERMISSIONS: tuple[str, ...] = tuple(PERMISSIONS)


def permissions_for(role: str | None) -> list[str]:
    if not role:
        return []
    return [p for p, roles in PERMISSIONS.items() if role in roles]


def role_has(role: str | None, perm: str) -> bool:
    return bool(role) and role in PERMISSIONS.get(perm, set())


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", (role or "").replace("_", " ").capitalize())
