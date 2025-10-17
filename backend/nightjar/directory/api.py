"""Public interface of the asset inventory and identity directory (builder A)."""

from __future__ import annotations

import uuid

from nightjar.contracts import AssetRecord, IdentityRecord


async def find_asset(org_id: uuid.UUID, name_or_ip: str) -> AssetRecord | None:
    raise NotImplementedError


async def search_assets(org_id: uuid.UUID, query: str, *, limit: int = 20) -> list[AssetRecord]:
    raise NotImplementedError


async def find_identity(org_id: uuid.UUID, username_or_email: str) -> IdentityRecord | None:
    raise NotImplementedError


async def search_identities(org_id: uuid.UUID, query: str, *, limit: int = 20) -> list[IdentityRecord]:
    raise NotImplementedError


async def recent_sign_in_countries(org_id: uuid.UUID, username: str, *, days: int = 90) -> list[str]:
    """From the identity directory's sign-in history (seeded), not the hunting store."""
    raise NotImplementedError


# ── Containment effectors: ONLY the response tool server calls these, after verifying an approval token ──
async def set_host_isolation(org_id: uuid.UUID, asset_name: str, *, isolated: bool, approval_id: uuid.UUID) -> None:
    raise NotImplementedError


async def set_account_disabled(org_id: uuid.UUID, username: str, *, disabled: bool, approval_id: uuid.UUID) -> None:
    raise NotImplementedError


async def revoke_access_key(org_id: uuid.UUID, access_key_id: str, *, approval_id: uuid.UUID) -> None:
    raise NotImplementedError


async def set_ip_block(org_id: uuid.UUID, ip: str, *, blocked: bool, approval_id: uuid.UUID) -> None:
    raise NotImplementedError
