"""Response action catalogue: what each kind targets, its plain-language label and effect, and the tool and
directory effector that carry it out. Shared by the response agent and the response tool server."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActionSpec:
    kind: str
    target_type: str
    label: str
    effect: str
    reversible: bool
    tool: str | None  # response tool server tool name (None: non-destructive, handled by the agent)
    effector: str | None  # nightjar.directory.api function


ACTIONS: dict[str, ActionSpec] = {
    a.kind: a
    for a in (
        ActionSpec(
            "isolate_host",
            "host",
            "Isolate this computer from the network",
            "Stops the computer talking to anything except the security tools",
            True,
            "isolate_host",
            "set_host_isolation",
        ),
        ActionSpec(
            "release_host",
            "host",
            "Reconnect this computer to the network",
            "Returns the computer to normal network access",
            True,
            "release_host",
            "set_host_isolation",
        ),
        ActionSpec(
            "disable_account",
            "user",
            "Turn off this account",
            "The account can no longer sign in anywhere until it is turned back on",
            True,
            "disable_account",
            "set_account_disabled",
        ),
        ActionSpec(
            "enable_account",
            "user",
            "Turn this account back on",
            "The person can sign in again",
            True,
            "enable_account",
            "set_account_disabled",
        ),
        ActionSpec(
            "revoke_sessions",
            "user",
            "Sign this person out everywhere",
            "Ends every open session; the person signs in again with their password and prompt",
            True,
            "revoke_sessions",
            "revoke_identity_sessions",
        ),
        ActionSpec(
            "reset_password",
            "user",
            "Reset this person's password",
            "The current password stops working; the person sets a new one with the service desk",
            False,
            "reset_password",
            "reset_identity_password",
        ),
        ActionSpec(
            "revoke_access_key",
            "access_key",
            "Withdraw this cloud access key",
            "Anything using this key (including automated deployments) stops working until a new key is issued",
            False,
            "revoke_access_key",
            "revoke_access_key",
        ),
        ActionSpec(
            "block_ip",
            "ip",
            "Block this internet address",
            "The gateways and firewalls drop traffic to and from this address",
            True,
            "block_ip",
            "set_ip_block",
        ),
        ActionSpec(
            "unblock_ip",
            "ip",
            "Unblock this internet address",
            "Traffic to and from the address is allowed again",
            True,
            "unblock_ip",
            "set_ip_block",
        ),
        ActionSpec(
            "quarantine_email",
            "email",
            "Move this email out of every mailbox",
            "The message is held in quarantine where nobody can open it",
            True,
            "quarantine_email",
            "quarantine_message",
        ),
        ActionSpec(
            "open_ticket",
            "ticket",
            "Open a service-desk ticket",
            "Gives the right team a tracked task",
            True,
            None,
            None,
        ),
        ActionSpec(
            "notify_owner", "user", "Tell the owner", "The system or account owner is informed", True, None, None
        ),
        ActionSpec(
            "collect_triage_package",
            "host",
            "Collect a triage package",
            "Copies logs and a process list from the computer for investigation",
            True,
            None,
            None,
        ),
        ActionSpec(
            "add_to_watchlist", "ip", "Watch this address", "Future activity from it is highlighted", True, None, None
        ),
    )
}
TOOL_TO_KIND = {a.tool: a.kind for a in ACTIONS.values() if a.tool}
