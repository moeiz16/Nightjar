"""Behaviour anomalies and the models behind triage and UEBA (API contract §1.15)."""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.contracts import to_doc
from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import AppError, conflict, unavailable
from nightjar.core.paging import Page, count
from nightjar.db.models.soc import AnomalyRow, Asset, DetectionRow, Identity, ModelPromotion
from nightjar.directory.api import asset_doc, identity_doc
from nightjar.schemas.base import CamelIn
from nightjar.services.audit import audit

router = APIRouter(tags=["ueba"])


def anomaly_doc(a: AnomalyRow, incident_id: str | None) -> dict:
    return {
        "id": str(a.id),
        "entityType": a.entity_type,
        "entityId": a.entity_id,
        "windowStart": a.window_start.isoformat(),
        "windowEnd": a.window_end.isoformat(),
        "score": a.score,
        "reasons": a.reasons or [],
        "features": a.features or {},
        "modelVersion": a.model_version,
        "detectionId": str(a.detection_id) if a.detection_id else None,
        "incidentId": incident_id,
    }


async def _incident_map(db: AsyncSession, rows: list[AnomalyRow]) -> dict:
    det_ids = [a.detection_id for a in rows if a.detection_id]
    if not det_ids:
        return {}
    return {
        d: str(i)
        for d, i in (
            await db.execute(select(DetectionRow.id, DetectionRow.incident_id).where(DetectionRow.id.in_(det_ids)))
        ).all()
        if i
    }


@router.get("/ueba/anomalies")
async def anomalies(
    page: Page = Depends(),
    entity_type: str | None = Query(None, alias="entityType"),
    min_score: float | None = Query(None, alias="minScore", ge=0, le=1),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    ctx: RequestContext = Depends(require_perm("ueba:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(AnomalyRow).where(AnomalyRow.org_id == ctx.org_id)
    if entity_type:
        stmt = stmt.where(AnomalyRow.entity_type == entity_type)
    if min_score is not None:
        stmt = stmt.where(AnomalyRow.score >= min_score)
    if date_from:
        stmt = stmt.where(AnomalyRow.window_end >= date_from)
    if date_to:
        stmt = stmt.where(AnomalyRow.window_start <= date_to)
    total = await count(db, stmt)
    rows = list(
        (await db.execute(stmt.order_by(AnomalyRow.score.desc()).offset(page.offset).limit(page.page_size))).scalars()
    )
    links = await _incident_map(db, rows)
    return page.wrap([anomaly_doc(a, links.get(a.detection_id)) for a in rows], total)


FEATURE_LABELS = {
    "logins": "Sign-ins",
    "failed_logins": "Failed sign-ins",
    "distinct_hosts": "Different computers used",
    "distinct_shares": "Different file shares opened",
    "bytes_out": "Data sent out (bytes)",
    "off_hours": "Activity outside usual hours",
    "new_countries": "Sign-ins from new countries",
    "processes": "Programs started",
}


@router.get("/ueba/entities/{entity_type}/{entity_id}")
async def entity(
    entity_type: str,
    entity_id: str,
    ctx: RequestContext = Depends(require_perm("ueba:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if entity_type not in ("user", "host"):
        raise AppError(404, "not_found", "Not found", "Unknown entity type.")
    if entity_type == "user":
        ent = (
            await db.execute(select(Identity).where(Identity.org_id == ctx.org_id, Identity.username == entity_id))
        ).scalar_one_or_none()
        ent_doc = identity_doc(ent) if ent else None
    else:
        a = (
            await db.execute(select(Asset).where(Asset.org_id == ctx.org_id, Asset.name == entity_id))
        ).scalar_one_or_none()
        ent_doc = asset_doc(a) if a else None
    rows = list(
        (
            await db.execute(
                select(AnomalyRow)
                .where(
                    AnomalyRow.org_id == ctx.org_id,
                    AnomalyRow.entity_type == entity_type,
                    AnomalyRow.entity_id == entity_id,
                )
                .order_by(AnomalyRow.window_start.desc())
                .limit(50)
            )
        ).scalars()
    )
    if ent_doc is None and not rows:
        raise AppError(404, "not_found", "Not found", "Entity not found.")
    baseline: list[dict] = []
    try:
        from nightjar.ml import api as ml

        fn = getattr(ml, "entity_baseline", None)
        if fn is not None:
            baseline = [dict(b) for b in fn(ctx.org_id, entity_type, entity_id)]
    except Exception:
        baseline = []
    return {
        "entity": ent_doc or {"id": entity_id},
        "baseline": baseline,
        "recent": [
            {"windowStart": a.window_start.isoformat(), "features": a.features or {}, "score": a.score} for a in rows
        ],
        "featureLabels": FEATURE_LABELS,
    }


def _ml():
    from nightjar.ml import api as ml

    return ml


@router.get("/models")
async def models(ctx: RequestContext = Depends(require_perm("models:read"))) -> list[dict]:
    try:
        infos = _ml().list_models()
    except NotImplementedError as exc:
        raise unavailable("Model information is not available yet.") from exc
    out = []
    for m in infos:
        doc = to_doc(m)
        doc.pop("cardMarkdown", None)
        out.append(doc)
    return out


@router.get("/models/{name}")
async def model(name: str, ctx: RequestContext = Depends(require_perm("models:read"))) -> dict:
    try:
        infos = [m for m in _ml().list_models() if m.name == name]
    except NotImplementedError as exc:
        raise unavailable("Model information is not available yet.") from exc
    if not infos:
        raise AppError(404, "not_found", "Model not found", "Model not found.")
    champion = next((m for m in infos if m.stage == "champion"), infos[0])
    doc = to_doc(champion)
    extra = getattr(_ml(), "model_detail", None)
    if extra is not None:
        with contextlib.suppress(Exception):  # extras are optional: calibration bins, drift, fairness
            doc.update(extra(name) or {})
    doc["versions"] = [{k: v for k, v in to_doc(m).items() if k != "cardMarkdown"} for m in infos]
    return doc


class PromoteIn(CamelIn):
    version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


@router.post("/models/{name}/promote")
async def promote(
    name: str,
    body: PromoteIn,
    ctx: RequestContext = Depends(require_perm("models:promote")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        info = _ml().promote(name, body.version, promoted_by=ctx.user.id)
    except NotImplementedError as exc:
        raise unavailable("Model promotion is not available yet.") from exc
    except KeyError as exc:
        raise AppError(404, "not_found", "Model not found", "Model or version not found.") from exc
    except ValueError as exc:
        raise conflict("conflict", str(exc) or "This version did not pass its quality gates.") from exc
    db.add(
        ModelPromotion(
            org_id=ctx.org_id,
            model_name=name,
            version=body.version,
            stage="champion",
            promoted_by=ctx.user.id,
            note=body.note,
        )
    )
    await audit(
        db,
        ctx,
        action="model.promoted",
        resource_type="model",
        resource_id=name,
        metadata={
            "modelLabel": "Triage model" if name == "triage" else "Behaviour model",
            "version": body.version,
            "note": body.note,
        },
    )
    await db.commit()
    return to_doc(info)
