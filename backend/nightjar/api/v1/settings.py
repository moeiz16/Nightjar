"""Settings: regulator contacts, the regulatory profile and preferences (API contract §1.16)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends
from pydantic import EmailStr, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import validation_error
from nightjar.db.models.platform import Membership, User
from nightjar.schemas.base import CamelIn
from nightjar.services.audit import audit
from nightjar.services.auth import DEFAULT_ORG_SETTINGS

router = APIRouter(prefix="/settings", tags=["settings"])

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")]
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class ContactIn(CamelIn):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    role: Short = ""
    email: EmailStr | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class OwnerIn(CamelIn):
    user_id: str | None = None
    name: Short = ""


class RegimeContactIn(CamelIn):
    jurisdiction: Literal["EU", "US", "AU"]
    authority_name: Short = ""
    portal_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300, pattern=r"^(https://.*)?$")] = (
        ""
    )
    internal_owner: OwnerIn | None = None
    contacts: list[ContactIn] = Field(default_factory=list, max_length=10)


class RegimeContactsIn(CamelIn):
    nis2: RegimeContactIn | None = None
    sec_8k: RegimeContactIn | None = None
    soci: RegimeContactIn | None = None
    gdpr_art33: RegimeContactIn | None = None


class Nis2In(CamelIn):
    applies: bool
    sector: Short
    subsector: Short | None = None
    entity_type: Literal["essential", "important"]
    member_state: Annotated[str, StringConstraints(min_length=2, max_length=2)]
    authority: Short
    authority_name: Short


class SecIn(CamelIn):
    registrant: bool
    filer_type: Literal["domestic"] = "domestic"
    cik: Short | None = None
    ticker: Short | None = None
    disclosure_committee: list[Short] = Field(default_factory=list, max_length=12)


class SociIn(CamelIn):
    responsible_entity: bool
    asset_classes: list[Short] = Field(default_factory=list, max_length=12)
    assets: list[Short] = Field(default_factory=list, max_length=24)


class GdprIn(CamelIn):
    controller: bool
    lead_authority: Short
    lead_authority_name: Short


class LegalEntityIn(CamelIn):
    id: Id
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    jurisdiction: Literal["EU", "US", "AU"]
    country: Annotated[str, StringConstraints(min_length=2, max_length=2)]
    timezone: str
    nis2: Nis2In | None = None
    sec: SecIn | None = None
    soci: SociIn | None = None
    gdpr: GdprIn | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown time zone.") from exc
        return v


class ProfileIn(CamelIn):
    entities: list[LegalEntityIn] = Field(max_length=20)
    crown_jewel_tags: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]] = (
        Field(default_factory=list, max_length=20)
    )


class PreferencesIn(CamelIn):
    auto_investigate: bool | None = None
    crown_jewel_asset_tags: (
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]] | None
    ) = None
    weekly_brief_day: Weekday | None = None


def _settings(ctx: RequestContext) -> dict:
    assert ctx.org is not None
    return {**DEFAULT_ORG_SETTINGS, **(ctx.org.settings or {})}


@router.get("/regulators")
async def regulators(ctx: RequestContext = Depends(require_perm("settings:read"))) -> dict:
    return _settings(ctx).get("regimeContacts") or {}


@router.put("/regulators")
async def put_regulators(
    body: RegimeContactsIn,
    ctx: RequestContext = Depends(require_perm("settings:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    assert ctx.org is not None
    doc = body.model_dump(by_alias=True, exclude_none=True, mode="json")
    for regime, entry in doc.items():
        owner = entry.get("internalOwner") or {}
        if owner.get("userId"):
            try:
                uid = uuid.UUID(owner["userId"])
            except ValueError:
                uid = None
            row = (
                (
                    await db.execute(
                        select(User.full_name)
                        .join(Membership, Membership.user_id == User.id)
                        .where(
                            Membership.org_id == ctx.org_id, Membership.user_id == uid, Membership.status == "active"
                        )
                    )
                ).scalar_one_or_none()
                if uid
                else None
            )
            if row is None:
                raise validation_error(
                    "Choose a member as the internal owner.",
                    [{"field": f"{regime}.internalOwner.userId", "message": "Not a member."}],
                )
            owner["name"] = row
    ctx.org.settings = {**_settings(ctx), "regimeContacts": doc}
    await audit(
        db,
        ctx,
        action="settings.contacts_updated",
        resource_type="org",
        resource_id=ctx.org.id,
        metadata={"regimes": sorted(doc)},
    )
    await db.commit()
    return doc


@router.get("/profile")
async def profile(ctx: RequestContext = Depends(require_perm("settings:read"))) -> dict:
    assert ctx.org is not None
    p = ctx.org.profile or {}
    return {"entities": p.get("entities") or [], "crownJewelTags": p.get("crownJewelTags") or []}


@router.put("/profile")
async def put_profile(
    body: ProfileIn, ctx: RequestContext = Depends(require_perm("settings:manage")), db: AsyncSession = Depends(get_db)
) -> dict:
    assert ctx.org is not None
    ids = [e.id for e in body.entities]
    if len(ids) != len(set(ids)):
        raise validation_error("Legal entity ids must be unique.", [{"field": "entities", "message": "Duplicate id."}])
    doc = body.model_dump(by_alias=True, exclude_none=True, mode="json")
    ctx.org.profile = doc
    await audit(
        db,
        ctx,
        action="settings.profile_updated",
        resource_type="org",
        resource_id=ctx.org.id,
        metadata={"entities": ids},
    )
    await db.commit()
    return doc


@router.patch("/preferences")
async def preferences(
    body: PreferencesIn,
    ctx: RequestContext = Depends(require_perm("settings:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    assert ctx.org is not None
    changes = body.model_dump(by_alias=True, exclude_none=True)
    ctx.org.settings = {**_settings(ctx), **changes}
    await audit(
        db,
        ctx,
        action="settings.preferences_updated",
        resource_type="org",
        resource_id=ctx.org.id,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return _settings(ctx)
