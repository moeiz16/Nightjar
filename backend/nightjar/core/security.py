"""Credential primitives: Argon2id passwords, opaque session tokens, CSRF, link tokens, ingest keys and the
signed human-approval tokens that gate destructive tools (ADR 0006).

Session and approval timers use the real clock (`clock.real_now()`), never the demo clock.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from nightjar import clock
from nightjar.core.config import get_settings
from nightjar.core.errors import AppError

_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)  # OWASP Argon2id minimums
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")
_COMMON = {
    "password1234",
    "123456789012",
    "qwertyuiopas",
    "letmein12345",
    "nightjar1234",
    "passwordpassword",
    "welcome12345",
}


# ── Passwords ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Always runs one Argon2 verification, even for unknown users (no user-enumeration timing)."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str, *, email: str | None = None) -> None:
    s = get_settings()
    problems = []
    if len(password) < s.password_min_length:
        problems.append(f"Use at least {s.password_min_length} characters.")
    if len(password) > 256:
        problems.append("Use at most 256 characters.")
    if len(set(password)) < 5:
        problems.append("Use a less repetitive password.")
    if password.lower() in _COMMON:
        problems.append("This password is too common.")
    if email and email.split("@")[0].lower() in password.lower():
        problems.append("Don't include your email name in the password.")
    if problems:
        raise AppError(
            422,
            "weak_password",
            "Password does not meet requirements",
            " ".join(problems),
            errors=[{"field": "password", "message": p} for p in problems],
        )


# ── Opaque tokens (stored as HMAC: a database leak reveals no usable token) ──
def hash_token(token: str) -> str:
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_link_token() -> str:
    """Opaque URL token for invitations: 32 random bytes."""
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def new_ingest_key() -> tuple[str, str]:
    """(plaintext shown once, prefix kept for display). Store `hash_token(plaintext)`."""
    raw = "njk_" + secrets.token_urlsafe(30)
    return raw, raw[:10]


# ── Human-approval tokens (ADR 0006) ─────────────────────────────────────────
@dataclass(frozen=True)
class ApprovalClaims:
    approval_id: str
    org_id: str
    action: str
    target: str
    nonce: str
    expires_at: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _approval_key() -> bytes:
    return hmac.new(get_settings().secret_key.get_secret_value().encode(), b"approval-v1", hashlib.sha256).digest()


def sign_approval_token(
    *, approval_id: str, org_id: str, action: str, target: str, ttl: timedelta | None = None
) -> tuple[str, ApprovalClaims]:
    """Issued by the API when the required humans approve an action. The response tool server verifies it,
    checks that `action`/`target` match the call exactly, and consumes the nonce once (single use)."""
    s = get_settings()
    expires = clock.real_now() + (ttl or timedelta(minutes=s.approval_ttl_minutes))
    claims = ApprovalClaims(approval_id, org_id, action, target, secrets.token_hex(12), expires)
    body = json.dumps(
        {
            "aid": approval_id,
            "org": org_id,
            "act": action,
            "tgt": target,
            "nonce": claims.nonce,
            "exp": int(expires.timestamp()),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    sig = hmac.new(_approval_key(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(sig)}", claims


def verify_approval_token(token: str, *, action: str, target: str, org_id: str) -> ApprovalClaims | None:
    """Signature, expiry and exact action/target/org match. Nonce single-use is enforced by the caller."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _unb64(body_b64)
        if not hmac.compare_digest(_unb64(sig_b64), hmac.new(_approval_key(), body, hashlib.sha256).digest()):
            return None
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return None
    expires = datetime.fromtimestamp(int(data["exp"]), tz=clock.real_now().tzinfo)
    if expires < clock.real_now():
        return None
    if data.get("act") != action or data.get("tgt") != target or data.get("org") != org_id:
        return None
    return ApprovalClaims(data["aid"], data["org"], data["act"], data["tgt"], data["nonce"], expires)
