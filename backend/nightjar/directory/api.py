"""Public interface of the asset inventory and identity directory (builder A).

Lookups are read by B's tool servers (assets, identity) and agents. The containment effectors at the bottom are
called ONLY by the response tool server after it has verified and consumed a human approval token (ADR 0006);
they change inventory state (containment is simulated: no real endpoint is touched) and raise KeyError when the
target does not exist in that organisation.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import AssetRecord, IdentityRecord
from nightjar.db.models.soc import AccessKey, Asset, Identity, IpBlock, QuarantinedMessage, SignInHistory
from nightjar.db.session import session_scope


# ── Converters ───────────────────────────────────────────────────────────────
def to_asset_record(a: Asset) -> AssetRecord:
    return AssetRecord(
        id=a.id,
        name=a.name,
        kind=a.kind,  # type: ignore[arg-type]
        ips=tuple(a.ips or ()),
        os=a.os,
        site=a.site,
        jurisdiction=a.jurisdiction,  # type: ignore[arg-type]
        owner=a.owner_username,
        business_service=a.business_service,
        criticality=a.criticality,  # type: ignore[arg-type]
        tags=tuple(a.tags or ()),
        isolated=a.isolated,
        vulnerabilities=tuple(a.vulnerabilities or ()),
    )


def to_identity_record(i: Identity) -> IdentityRecord:
    return IdentityRecord(
        id=i.id,
        username=i.username,
        display_name=i.display_name,
        email=i.email,
        department=i.department,
        title=i.title,
        jurisdiction=i.jurisdiction,  # type: ignore[arg-type]
        manager=i.manager,
        privileged=i.privileged,
        mfa=i.mfa,  # type: ignore[arg-type]
        usual_countries=tuple(i.usual_countries or ()),
        disabled=i.disabled,
        kind=i.kind,  # type: ignore[arg-type]
    )


def asset_doc(a: Asset, kev: list[dict] | None = None) -> dict:
    """`AssetDoc` (§1.8). `kev` is the list of KevHitDoc for the asset's CVEs (from B's intel; may be empty)."""
    return {
        "id": str(a.id),
        "name": a.name,
        "kind": a.kind,
        "ips": list(a.ips or []),
        "os": a.os,
        "site": a.site,
        "jurisdiction": a.jurisdiction,
        "owner": a.owner_username,
        "businessService": a.business_service,
        "criticality": a.criticality,
        "tags": list(a.tags or []),
        "isolated": a.isolated,
        "vulnerabilities": kev if kev is not None else [{"cveId": c} for c in a.vulnerabilities or []],
    }


def identity_doc(i: Identity) -> dict:
    return {
        "id": str(i.id),
        "username": i.username,
        "displayName": i.display_name,
        "email": i.email,
        "department": i.department,
        "title": i.title,
        "jurisdiction": i.jurisdiction,
        "manager": i.manager,
        "privileged": i.privileged,
        "mfa": i.mfa,
        "usualCountries": list(i.usual_countries or []),
        "disabled": i.disabled,
        "kind": i.kind,
    }


def is_crown_jewel(asset: Asset, crown_tags: list[str] | tuple[str, ...] = ()) -> bool:
    return asset.criticality == "crown_jewel" or bool(set(asset.tags or ()) & set(crown_tags))


# ── Session-level helpers (used inside other A services' transactions) ───────
async def assets_by_names_or_ips(db: AsyncSession, org_id: uuid.UUID, keys: list[str]) -> list[Asset]:
    if not keys:
        return []
    names = {k.lower() for k in keys}
    rows = (await db.execute(select(Asset).where(Asset.org_id == org_id))).scalars().all()
    return [a for a in rows if a.name.lower() in names or names & {ip.lower() for ip in a.ips or ()}]


async def identities_by_keys(db: AsyncSession, org_id: uuid.UUID, keys: list[str]) -> list[Identity]:
    if not keys:
        return []
    norm = {_bare_username(k) for k in keys} | {k.lower() for k in keys}
    rows = (await db.execute(select(Identity).where(Identity.org_id == org_id))).scalars().all()
    return [i for i in rows if i.username.lower() in norm or (i.email or "").lower() in norm]


def _bare_username(value: str) -> str:
    """`HALDEN\\j.okafor` → `j.okafor`; emails are kept whole."""
    return value.split("\\")[-1].lower()


async def _find_asset(db: AsyncSession, org_id: uuid.UUID, name_or_ip: str) -> Asset | None:
    key = name_or_ip.strip().lower()
    row = (
        await db.execute(select(Asset).where(Asset.org_id == org_id, func.lower(Asset.name) == key))
    ).scalar_one_or_none()
    if row:
        return row
    try:
        ipaddress.ip_address(key)
    except ValueError:
        return None
    for a in (await db.execute(select(Asset).where(Asset.org_id == org_id))).scalars():
        if key in {ip.lower() for ip in a.ips or ()}:
            return a
    return None


async def _find_identity(db: AsyncSession, org_id: uuid.UUID, username_or_email: str) -> Identity | None:
    key = _bare_username(username_or_email.strip())
    return (
        await db.execute(
            select(Identity).where(
                Identity.org_id == org_id,
                or_(func.lower(Identity.username) == key, func.lower(Identity.email) == key),
            )
        )
    ).scalar_one_or_none()


# ── Lookups (B's tool servers and agents) ────────────────────────────────────
async def find_asset(org_id: uuid.UUID, name_or_ip: str) -> AssetRecord | None:
    async with session_scope() as db:
        a = await _find_asset(db, org_id, name_or_ip)
        return to_asset_record(a) if a else None


async def search_assets(org_id: uuid.UUID, query: str, *, limit: int = 20) -> list[AssetRecord]:
    q = f"%{query.strip().lower()}%"
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(Asset)
                    .where(
                        Asset.org_id == org_id,
                        or_(
                            func.lower(Asset.name).like(q),
                            func.lower(func.coalesce(Asset.business_service, "")).like(q),
                            func.lower(Asset.site).like(q),
                        ),
                    )
                    .order_by(Asset.name)
                    .limit(max(1, min(limit, 100)))
                )
            )
            .scalars()
            .all()
        )
        return [to_asset_record(a) for a in rows]


async def find_identity(org_id: uuid.UUID, username_or_email: str) -> IdentityRecord | None:
    async with session_scope() as db:
        i = await _find_identity(db, org_id, username_or_email)
        return to_identity_record(i) if i else None


async def search_identities(org_id: uuid.UUID, query: str, *, limit: int = 20) -> list[IdentityRecord]:
    q = f"%{query.strip().lower()}%"
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(Identity)
                    .where(
                        Identity.org_id == org_id,
                        or_(
                            func.lower(Identity.username).like(q),
                            func.lower(Identity.display_name).like(q),
                            func.lower(Identity.email).like(q),
                            func.lower(Identity.department).like(q),
                        ),
                    )
                    .order_by(Identity.username)
                    .limit(max(1, min(limit, 100)))
                )
            )
            .scalars()
            .all()
        )
        return [to_identity_record(i) for i in rows]


async def recent_sign_in_countries(org_id: uuid.UUID, username: str, *, days: int = 90) -> list[str]:
    """Countries seen in the directory's sign-in history, most frequent first (not the hunting store)."""
    since = clock.today() - timedelta(days=max(1, min(days, 365)))
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(SignInHistory.country, func.sum(SignInHistory.count))
                .where(
                    SignInHistory.org_id == org_id,
                    func.lower(SignInHistory.username) == _bare_username(username),
                    SignInHistory.day >= since,
                )
                .group_by(SignInHistory.country)
                .order_by(func.sum(SignInHistory.count).desc(), SignInHistory.country)
            )
        ).all()
        return [c for c, _n in rows]


# ── Containment effectors (response tool server only, after a consumed approval token) ──
async def set_host_isolation(org_id: uuid.UUID, asset_name: str, *, isolated: bool, approval_id: uuid.UUID) -> None:
    async with session_scope() as db:
        a = await _find_asset(db, org_id, asset_name)
        if a is None:
            raise KeyError(f"unknown asset {asset_name!r}")
        a.isolated = isolated
        a.isolated_by_approval_id = approval_id if isolated else None


async def set_account_disabled(org_id: uuid.UUID, username: str, *, disabled: bool, approval_id: uuid.UUID) -> None:
    async with session_scope() as db:
        i = await _find_identity(db, org_id, username)
        if i is None:
            raise KeyError(f"unknown identity {username!r}")
        i.disabled = disabled
        i.disabled_by_approval_id = approval_id if disabled else None


async def revoke_identity_sessions(org_id: uuid.UUID, username: str, *, approval_id: uuid.UUID) -> None:
    async with session_scope() as db:
        i = await _find_identity(db, org_id, username)
        if i is None:
            raise KeyError(f"unknown identity {username!r}")
        i.sessions_revoked_at = clock.now()


async def reset_identity_password(org_id: uuid.UUID, username: str, *, approval_id: uuid.UUID) -> None:
    async with session_scope() as db:
        i = await _find_identity(db, org_id, username)
        if i is None:
            raise KeyError(f"unknown identity {username!r}")
        i.password_reset_at = clock.now()


async def revoke_access_key(org_id: uuid.UUID, access_key_id: str, *, approval_id: uuid.UUID) -> None:
    async with session_scope() as db:
        k = (
            await db.execute(select(AccessKey).where(AccessKey.org_id == org_id, AccessKey.key_id == access_key_id))
        ).scalar_one_or_none()
        if k is None:
            raise KeyError(f"unknown access key {access_key_id!r}")
        k.status = "revoked"
        k.revoked_by_approval_id = approval_id


async def set_ip_block(org_id: uuid.UUID, ip: str, *, blocked: bool, approval_id: uuid.UUID) -> None:
    try:
        ip = str(ipaddress.ip_address(ip.strip()))
    except ValueError as exc:
        raise KeyError(f"not an internet address: {ip!r}") from exc
    async with session_scope() as db:
        row = (await db.execute(select(IpBlock).where(IpBlock.org_id == org_id, IpBlock.ip == ip))).scalar_one_or_none()
        if row is None:
            db.add(IpBlock(org_id=org_id, ip=ip, blocked=blocked, approval_id=approval_id))
        else:
            row.blocked, row.approval_id = blocked, approval_id


async def quarantine_message(org_id: uuid.UUID, message_id: str, *, approval_id: uuid.UUID) -> None:
    if not message_id.strip():
        raise KeyError("empty message id")
    async with session_scope() as db:
        exists = (
            await db.execute(
                select(QuarantinedMessage).where(
                    QuarantinedMessage.org_id == org_id, QuarantinedMessage.message_id == message_id
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(QuarantinedMessage(org_id=org_id, message_id=message_id, approval_id=approval_id))
