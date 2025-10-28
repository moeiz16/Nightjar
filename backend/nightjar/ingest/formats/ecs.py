"""Elastic Common Schema documents (`ecs`) and Nightjar's own flat event shape (`nightjar`, the normalised
document produced by another Nightjar instance or a test harness, camelCase or snake_case)."""

from __future__ import annotations

from typing import Any

from nightjar.contracts import CloudInfo, EmailInfo, Endpoint, HostRef, ProcessInfo, UserRef
from nightjar.ingest.common import Draft, clean_ip, country, dig, first, kebab, outcome_of, parse_ts, short_host


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value)
    return (str(value),)


def _endpoint(d: Any) -> Endpoint | None:
    if not isinstance(d, dict):
        return None
    ip = clean_ip(d.get("ip"))
    if not ip:
        return None
    return Endpoint(
        ip=ip,
        port=d.get("port"),
        geo_country=country(dig(d, "geo.country_iso_code") or d.get("geoCountry") or d.get("geo_country")),
        as_org=dig(d, "as.organization.name") or d.get("asOrg") or d.get("as_org"),
    )


def parse_ecs(p: dict[str, Any]) -> Draft:
    ev = p.get("event") or {}
    ts = parse_ts(first(p, "@timestamp") or ev.get("created"))
    dataset = str(ev.get("dataset") or "nightjar.synthetic")
    action = kebab(str(ev.get("action") or ev.get("code") or "event"))
    host = (
        HostRef(name=short_host(dig(p, "host.name")) or "", ip=_tuple(dig(p, "host.ip")))
        if dig(p, "host.name")
        else None
    )
    user = None
    if dig(p, "user.name"):
        user = UserRef(name=str(dig(p, "user.name")).lower(), domain=dig(p, "user.domain"), email=dig(p, "user.email"))
    proc = None
    if isinstance(p.get("process"), dict):
        pr = p["process"]
        proc = ProcessInfo(
            name=pr.get("name"),
            executable=pr.get("executable"),
            pid=pr.get("pid"),
            command_line=pr.get("command_line"),
            parent_name=dig(pr, "parent.name"),
            parent_executable=dig(pr, "parent.executable"),
            hash_sha256=dig(pr, "hash.sha256"),
        )
    email = None
    if isinstance(p.get("email"), dict):
        em = p["email"]
        email = EmailInfo(
            sender=dig(em, "from.address") or em.get("sender"),
            recipients=_tuple(dig(em, "to.address") or em.get("recipients")),
            subject=em.get("subject"),
            attachment_names=tuple(a.get("file", {}).get("name", "") for a in em.get("attachments", []) or []),
            urls=_tuple(em.get("urls")),
        )
    cloud = None
    if isinstance(p.get("cloud"), dict):
        c = p["cloud"]
        cloud = CloudInfo(
            provider=c.get("provider"),
            account_id=dig(c, "account.id"),
            region=c.get("region"),
            service=dig(c, "service.name"),
        )
    draft = Draft(
        timestamp=ts,
        dataset=dataset,
        kind=str(ev.get("kind") or "event") if ev.get("kind") in ("event", "alert", "signal") else "event",
        action=action,
        category=_tuple(ev.get("category")) or ("host",),
        type=_tuple(ev.get("type")) or ("info",),
        outcome=outcome_of(ev.get("outcome")),
        message=str(p.get("message") or action),
        severity=ev.get("severity") if isinstance(ev.get("severity"), int) else None,
        host=host,
        user=user,
        source=_endpoint(p.get("source")),
        destination=_endpoint(p.get("destination")),
        process=proc,
        cloud=cloud,
        email=email,
        url=dig(p, "url.full") or dig(p, "url.original"),
        file_path=dig(p, "file.path"),
        file_hash_sha256=dig(p, "file.hash.sha256"),
    )
    if email and email.subject:
        draft.free_text["email.subject"] = email.subject
    return draft


def _g(d: dict[str, Any], snake: str) -> Any:
    from nightjar.contracts import _camel

    return d.get(snake, d.get(_camel(snake)))


def parse_nightjar(p: dict[str, Any]) -> Draft:
    if not _g(p, "dataset") or not _g(p, "action"):
        raise ValueError("not a Nightjar event (dataset/action missing)")
    ts = parse_ts(_g(p, "timestamp") or p.get("@timestamp"))
    h = _g(p, "host") or {}
    u = _g(p, "user") or {}
    pr = _g(p, "process") or {}
    em = _g(p, "email") or {}
    cl = _g(p, "cloud") or {}
    msg = _g(p, "message")
    if isinstance(msg, dict):
        msg = msg.get("text")
    draft = Draft(
        timestamp=ts,
        dataset=str(_g(p, "dataset")),
        kind=str(_g(p, "kind") or "event"),
        action=str(_g(p, "action")),
        category=_tuple(_g(p, "category")) or ("host",),
        type=_tuple(_g(p, "type")) or ("info",),
        outcome=str(_g(p, "outcome") or "unknown"),
        message=str(msg or _g(p, "action")),
        severity=_g(p, "severity"),
        host=HostRef(name=short_host(h.get("name")) or "", ip=_tuple(h.get("ip")), os=h.get("os"))
        if h.get("name")
        else None,
        user=UserRef(name=str(u["name"]).lower(), domain=u.get("domain"), email=u.get("email"))
        if u.get("name")
        else None,
        source=_endpoint(_g(p, "source")),
        destination=_endpoint(_g(p, "destination")),
        process=ProcessInfo(
            name=pr.get("name"),
            executable=pr.get("executable"),
            pid=pr.get("pid"),
            command_line=_g(pr, "command_line"),
            parent_name=_g(pr, "parent_name"),
            parent_executable=_g(pr, "parent_executable"),
            hash_sha256=_g(pr, "hash_sha256"),
        )
        if pr
        else None,
        cloud=CloudInfo(
            provider=cl.get("provider"),
            account_id=_g(cl, "account_id"),
            region=cl.get("region"),
            service=cl.get("service"),
            api=cl.get("api"),
            access_key_id=_g(cl, "access_key_id"),
        )
        if cl
        else None,
        email=EmailInfo(
            sender=em.get("sender"),
            recipients=_tuple(em.get("recipients")),
            subject=em.get("subject"),
            attachment_names=_tuple(_g(em, "attachment_names")),
            urls=_tuple(em.get("urls")),
            verdict=em.get("verdict"),
        )
        if em
        else None,
        url=_g(p, "url"),
        file_path=_g(p, "file_path"),
        file_hash_sha256=_g(p, "file_hash_sha256"),
    )
    if em.get("subject"):
        draft.free_text["email.subject"] = str(em["subject"])
    return draft
