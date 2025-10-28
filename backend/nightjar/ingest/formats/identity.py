"""Okta System Log events and Microsoft 365 unified audit records."""

from __future__ import annotations

from typing import Any

from nightjar.contracts import CloudInfo, EmailInfo, Endpoint, UserRef
from nightjar.ingest.common import Draft, clean_ip, country, dig, first, kebab, outcome_of, parse_ts, split_user

OKTA_ACTIONS = {
    "user.session.start": "logon",
    "user.authentication.sso": "sso-logon",
    "user.authentication.auth_via_mfa": "mfa-verified",
    "system.push.send_factor_verify_push": "mfa-push-sent",
    "user.mfa.okta_verify.deny_push": "mfa-push-denied",
    "user.mfa.okta_verify.deny_push_upgrade_needed": "mfa-push-denied",
    "user.authentication.auth_via_mfa.push": "mfa-push-approved",
    "user.account.lock": "account-locked",
    "user.session.end": "logoff",
    "user.account.reset_password": "password-reset",
    "user.mfa.factor.update": "mfa-factor-changed",
    "user.account.update_password": "password-changed",
}


def parse_okta(p: dict[str, Any]) -> Draft:
    event_type = first(p, "eventType")
    if not event_type:
        raise ValueError("not an Okta event (eventType missing)")
    ts = parse_ts(first(p, "published", "@timestamp"))
    alt = dig(p, "actor.alternateId") or dig(p, "actor.displayName")
    uname, _ = split_user(alt)
    result = dig(p, "outcome.result")
    outcome = outcome_of(result)
    action = OKTA_ACTIONS.get(str(event_type), kebab(str(event_type).replace(".", "-")))
    if action == "logon" and outcome == "failure":
        action = "logon-failed"
    if event_type == "user.authentication.auth_via_mfa" and outcome == "failure":
        action = "mfa-push-denied"
    if action == "mfa-verified" and outcome == "success":
        action = "mfa-push-approved"
    cat = (
        ("authentication",)
        if "auth" in str(event_type) or "session" in str(event_type) or "mfa" in action
        else ("iam",)
    )
    src_ip = clean_ip(dig(p, "client.ipAddress"))
    geo = country(dig(p, "client.geographicalContext.country"))
    as_org = dig(p, "securityContext.asOrg")
    draft = Draft(
        timestamp=ts,
        dataset="okta.system",
        action=action,
        category=cat,
        type=("start",) if "logon" in action else ("info",),
        outcome=outcome,
        message=str(first(p, "displayMessage") or event_type),
        user=UserRef(name=uname, email=str(alt) if alt and "@" in str(alt) else None) if uname else None,
        source=Endpoint(ip=src_ip, geo_country=geo, as_org=as_org) if src_ip else None,
        cloud=CloudInfo(provider="okta", service="okta", api=str(event_type)),
    )
    ua = dig(p, "client.userAgent.rawUserAgent")
    if ua:
        draft.free_text["user_agent.original"] = str(ua)
    reason = dig(p, "outcome.reason")
    if reason:
        draft.free_text["event.reason"] = str(reason)
    return draft


M365_ACTIONS = {
    "UserLoggedIn": ("logon", ("authentication",)),
    "UserLoginFailed": ("logon-failed", ("authentication",)),
    "New-InboxRule": ("inbox-rule-created", ("email", "configuration")),
    "Set-InboxRule": ("inbox-rule-created", ("email", "configuration")),
    "UpdateInboxRules": ("inbox-rule-created", ("email", "configuration")),
    "Set-Mailbox": ("mailbox-settings-changed", ("email", "configuration")),
    "MailItemsAccessed": ("mailbox-items-accessed", ("email",)),
    "Send": ("email-sent", ("email",)),
    "FileDownloaded": ("file-downloaded", ("file",)),
    "FileAccessed": ("file-accessed", ("file",)),
    "FileSyncDownloadedFull": ("file-downloaded", ("file",)),
    "Disable Strong Authentication.": ("mfa-disabled", ("iam",)),
}


def parse_m365(p: dict[str, Any]) -> Draft:
    op = first(p, "Operation")
    if not op:
        raise ValueError("not a Microsoft 365 audit record (Operation missing)")
    ts = parse_ts(first(p, "CreationTime", "@timestamp"))
    action, cat = M365_ACTIONS.get(str(op), (kebab(str(op)), ("configuration",)))
    uid = first(p, "UserId")
    uname, _ = split_user(uid)
    status = first(p, "ResultStatus")
    outcome = outcome_of(status) if status else "success"
    if action == "logon-failed":
        outcome = "failure"
    src_ip = clean_ip(first(p, "ClientIP", "ClientIPAddress", "ActorIpAddress"))
    params = {str(x.get("Name")): x.get("Value") for x in (p.get("Parameters") or []) if isinstance(x, dict)}
    recipients: list[str] = []
    for key in ("ForwardTo", "ForwardAsAttachmentTo", "RedirectTo", "ForwardingSmtpAddress"):
        if params.get(key):
            recipients += [r.strip() for r in str(params[key]).replace(";", ",").split(",") if r.strip()]
    email = EmailInfo(recipients=tuple(recipients)) if recipients else None
    count = p.get("OperationCount") or (p.get("Folders") and sum(len(f.get("FolderItems", [])) for f in p["Folders"]))
    msg = f"{op} by {uid}" + (f" ({count} items)" if count else "")
    draft = Draft(
        timestamp=ts,
        dataset="m365.audit",
        action=action,
        category=cat,
        type=("creation",) if "created" in action else ("access",) if "accessed" in action else ("info",),
        outcome=outcome,
        message=msg,
        user=UserRef(name=uname, email=str(uid) if uid and "@" in str(uid) else None) if uname else None,
        source=Endpoint(ip=src_ip, geo_country=country(first(p, "ClientCountry"))) if src_ip else None,
        cloud=CloudInfo(provider="m365", service=str(first(p, "Workload") or "Exchange").lower(), api=str(op)),
        email=email,
        file_path=first(p, "ObjectId", "SourceFileName"),
    )
    if params.get("Name"):
        draft.free_text["m365.rule_name"] = str(params["Name"])
    if first(p, "ClientInfoString", "UserAgent"):
        draft.free_text["user_agent.original"] = str(first(p, "ClientInfoString", "UserAgent"))
    return draft
