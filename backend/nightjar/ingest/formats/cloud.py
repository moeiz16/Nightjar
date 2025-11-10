"""AWS CloudTrail records and S3 server-access rows (OTRF honey-bucket CSV)."""

from __future__ import annotations

import ast
from typing import Any

from nightjar.contracts import CloudInfo, Endpoint, UserRef
from nightjar.ingest.common import Draft, clean_ip, first, kebab, parse_ts

_IAM_WRITE = {"CreateUser", "CreateAccessKey", "AttachUserPolicy", "PutUserPolicy", "CreateLoginProfile"}


def parse_cloudtrail(p: dict[str, Any]) -> Draft:
    name = first(p, "eventName")
    src_service = str(first(p, "eventSource") or "")
    if not name or not src_service:
        raise ValueError("not a CloudTrail record (eventName/eventSource missing)")
    service = src_service.split(".")[0]
    ts = parse_ts(first(p, "eventTime", "@timestamp"))
    ident = p.get("userIdentity") or {}
    uname = first(ident, "userName") or str(first(ident, "arn") or "").split("/")[-1] or first(ident, "principalId")
    error = first(p, "errorCode")
    outcome = "failure" if error else "success"
    if name == "ConsoleLogin":
        resp = (p.get("responseElements") or {}).get("ConsoleLogin")
        outcome = "success" if resp == "Success" else "failure"
    if service == "signin" or name == "ConsoleLogin":
        cat, typ = ("authentication",), ("start",)
    elif service in ("iam", "sts"):
        cat = ("iam",)
        typ = ("creation",) if name.startswith("Create") else ("change",) if name in _IAM_WRITE else ("info",)
    elif service == "s3":
        cat = ("file",)
        typ = ("access",) if name.startswith(("Get", "List", "Head")) else ("change",)
    else:
        cat, typ = ("configuration",), ("info",) if name.startswith(("Describe", "List", "Get")) else ("change",)
    if error:
        typ = (*typ, "denied")  # type: ignore[assignment]
    req = p.get("requestParameters") or {}
    bucket = req.get("bucketName") if isinstance(req, dict) else None
    key = req.get("key") if isinstance(req, dict) else None
    src_ip = clean_ip(first(p, "sourceIPAddress"))
    account = first(p, "recipientAccountId") or first(ident, "accountId")
    message = f"{name} on {service}" + (f" ({error})" if error else "")
    draft = Draft(
        timestamp=ts,
        dataset="aws.cloudtrail",
        action=f"{service}-{kebab(name)}",
        category=cat,
        type=typ,
        outcome=outcome,
        message=str(first(p, "errorMessage") or message),
        user=UserRef(name=str(uname).lower()) if uname else None,
        source=Endpoint(ip=src_ip) if src_ip else None,
        cloud=CloudInfo(
            provider="aws",
            account_id=str(account) if account else None,
            region=first(p, "awsRegion"),
            service=service,
            api=f"{service}:{name}",
            access_key_id=first(ident, "accessKeyId"),
        ),
        file_path=f"s3://{bucket}/{key}" if bucket and key else (f"s3://{bucket}" if bucket else None),
    )
    if first(p, "userAgent"):
        draft.free_text["user_agent.original"] = str(first(p, "userAgent"))
    if isinstance(req, dict) and req.get("userName") and str(req["userName"]).lower() != str(uname).lower():
        draft.related_users.append(str(req["userName"]).lower())
    return draft


def parse_s3_access(p: dict[str, Any]) -> Draft:
    """OTRF `aws_honeybucket.csv`: Event Name, Source IP, Request Parameters (python-dict text), ..."""
    name = first(p, "Event Name", "operation")
    if not name:
        raise ValueError("not an S3 access row")
    ts = parse_ts(first(p, "Event DateTime", "time", "@timestamp"))
    params: dict[str, Any] = {}
    raw_params = first(p, "Request Parameters")
    if isinstance(raw_params, str):
        try:
            parsed = ast.literal_eval(raw_params)
            params = parsed if isinstance(parsed, dict) else {}
        except (ValueError, SyntaxError):
            params = {}
    bucket = params.get("bucketName") or first(p, "bucket")
    src_ip = clean_ip(first(p, "Source IP", "remote_ip"))
    draft = Draft(
        timestamp=ts,
        dataset="aws.s3access",
        action=f"s3-{kebab(str(name))}",
        category=("file", "web"),
        type=("access",),
        outcome="success",
        message=f"{name} on bucket {bucket}" if bucket else str(name),
        source=Endpoint(ip=src_ip) if src_ip else None,
        cloud=CloudInfo(provider="aws", service="s3", api=f"s3:{name}"),
        file_path=f"s3://{bucket}" if bucket else None,
    )
    if first(p, "Request User Agent"):
        draft.free_text["user_agent.original"] = str(first(p, "Request User Agent"))
    return draft
