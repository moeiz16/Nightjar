"""Public interface of the ticket store (builder A)."""

from __future__ import annotations

import uuid
from typing import Any, Literal


async def create_ticket(
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    title: str,
    body: str,
    priority: Literal["p1", "p2", "p3", "p4"],
    assignee_team: str,
) -> dict[str, Any]:
    """Returns `Ticket` (§1.11). Idempotent per (incident, title)."""
    raise NotImplementedError


async def add_comment(org_id: uuid.UUID, ticket_id: uuid.UUID, *, body: str, author: str) -> dict[str, Any]:
    raise NotImplementedError


async def get_ticket(org_id: uuid.UUID, ticket_id: uuid.UUID) -> dict[str, Any] | None:
    raise NotImplementedError
