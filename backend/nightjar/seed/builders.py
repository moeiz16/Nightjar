# ruff: noqa: S311  (seeded pseudo-random numbers make the demo data reproducible; nothing secret)
"""Building blocks for the demo seed (and test fixtures): organisations, members and inventory."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.db.models.platform import Membership, Organization, User
from nightjar.db.models.soc import AccessKey, Asset, Identity, SignInHistory
from nightjar.seed.data import DEMO_PASSWORD
from nightjar.services import auth as auth_svc


async def create_org(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    timezone: str,
    profile: dict,
    users: list[tuple[str, str, str, str]],
    is_sample: bool = True,
    settings: dict | None = None,
) -> tuple[Organization, dict[str, User]]:
    org = Organization(
        name=name,
        slug=slug,
        timezone=timezone,
        profile=profile,
        settings={**auth_svc.DEFAULT_ORG_SETTINGS, **(settings or {})},
        is_sample=is_sample,
    )
    db.add(org)
    await db.flush()
    people: dict[str, User] = {}
    for email, full_name, role, title in users:
        user = await auth_svc.get_user_by_email(db, email)
        if user is None:
            user = await auth_svc.create_user(db, email=email, password=DEMO_PASSWORD, full_name=full_name, title=title)
        db.add(Membership(org_id=org.id, user_id=user.id, role=role, status="active"))
        people[email] = user
    await db.flush()
    return org, people


async def add_inventory(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    assets: list[tuple],
    identities: list[tuple],
    domain: str,
    access_keys: list[tuple[str, str, str]] = (),  # type: ignore[assignment]
) -> None:
    for name, kind, ips, os_, site, juris, entity, owner, service, crit, tags, vulns in assets:
        db.add(
            Asset(
                org_id=org_id,
                name=name,
                kind=kind,
                ips=list(ips),
                os=os_,
                site=site,
                jurisdiction=juris,
                entity_id=entity,
                owner_username=owner,
                business_service=service,
                criticality=crit,
                tags=list(tags),
                vulnerabilities=list(vulns),
            )
        )
    for username, display, dept, title, juris, entity, manager, priv, mfa, countries, kind in identities:
        db.add(
            Identity(
                org_id=org_id,
                username=username,
                display_name=display,
                email=f"{username}@{domain}",
                department=dept,
                title=title,
                jurisdiction=juris,
                entity_id=entity,
                manager=manager,
                privileged=priv,
                mfa=mfa,
                usual_countries=list(countries),
                kind=kind,
            )
        )
    for key_id, identity, provider in access_keys:
        db.add(AccessKey(org_id=org_id, key_id=key_id, identity=identity, provider=provider, status="active"))
    await db.flush()


async def add_sign_in_history(
    db: AsyncSession, org_id: uuid.UUID, identities: list[tuple], *, days: int = 90, seed: int = 7
) -> int:
    """Daily sign-in counts per person and country for the last `days` days (directory look-ups)."""
    rng = random.Random(seed)
    today = clock.today()
    rows = 0
    for ident in identities:
        username, countries, kind = ident[0], ident[9], ident[10]
        for d in range(1, days + 1):
            day = today - timedelta(days=d)
            if kind != "service" and day.weekday() >= 5 and rng.random() < 0.8:
                continue
            for i, country in enumerate(countries):
                if i > 0 and rng.random() > 0.15:  # second countries (travel) are occasional
                    continue
                db.add(
                    SignInHistory(
                        org_id=org_id,
                        username=username,
                        day=day,
                        country=country,
                        count=rng.randint(2, 9) if kind != "service" else 24,
                    )
                )
                rows += 1
    await db.flush()
    return rows


async def org_by_slug(db: AsyncSession, slug: str) -> Organization | None:
    return (await db.execute(select(Organization).where(Organization.slug == slug))).scalar_one_or_none()
