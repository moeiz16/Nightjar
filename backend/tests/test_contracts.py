"""Shared contract helpers (A↔B): camelCase serialisation and the destructive-action set."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from nightjar.contracts import (
    DESTRUCTIVE_ACTIONS,
    AttackRef,
    Entities,
    ProposedAction,
    UntrustedText,
    to_doc,
)


def test_to_doc_is_camel_case_and_keeps_raw_keys() -> None:
    entities = Entities(hosts=("ant-wms-01",), access_keys=("AKIA…",))
    doc = to_doc(
        {
            "attack_refs": [AttackRef("T1003.001", "LSASS Memory", ("credential-access",))],
            "entities": entities,
            "raw": {"Event_ID": 10},
            "when": datetime(2025, 11, 20, tzinfo=UTC),
        }
    )
    assert doc["attackRefs"][0]["techniqueId"] == "T1003.001"
    assert doc["entities"]["accessKeys"] == ["AKIA…"]
    assert doc["raw"] == {"Event_ID": 10}
    assert doc["when"] == "2025-11-20T00:00:00+00:00"


def test_destructive_actions_need_approval_and_reversals_too() -> None:
    assert ProposedAction("isolate_host", "ant-wms-01", "host", "r", "e", True).destructive
    assert ProposedAction("release_host", "ant-wms-01", "host", "r", "e", True).destructive
    assert not ProposedAction("open_ticket", "NJ-1", "ticket", "r", "e", True).destructive
    assert {"disable_account", "revoke_access_key", "block_ip"} <= DESTRUCTIVE_ACTIONS


def test_untrusted_text_is_frozen() -> None:
    t = UntrustedText("hello", field="message", flags=("role_marker",))
    try:
        t.text = "x"  # type: ignore[misc]
    except AttributeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("UntrustedText must be immutable")
    assert uuid.UUID(int=1)


def test_raw_event_payload_crosses_the_bus_verbatim_and_event_ids_are_stable() -> None:
    from nightjar.contracts import RawEvent
    from nightjar.ingest.normalizer import normalize
    from nightjar.stream import codec

    payload = {
        "@timestamp": "2025-11-20T03:12:04Z",
        "event_type": "user.session.start",
        "EventData": {"SubjectUserName": "j.okafor", "logon_type": 3},
        "host": {"name": "ant-ws-014"},
        "message": "Sign-in from a new country",
    }
    raw = RawEvent(uuid.UUID(int=7), "webhook", datetime(2025, 11, 20, 3, 12, 5, tzinfo=UTC), payload, format="ecs")
    doc = to_doc(raw)
    assert doc["payload"] == payload  # keys as written, nested ones too
    assert doc["receivedAt"] == "2025-11-20T03:12:05+00:00"
    again = codec.decode(RawEvent, codec.dumps(raw))
    assert again.payload == payload
    first, second = normalize(raw), normalize(again)
    assert first.event_id == second.event_id
    assert normalize(again).event_id == first.event_id  # deterministic


def test_robust_z_keeps_feature_ids() -> None:
    assert to_doc({"robust_z": {"bytes_out": 3.5}})["robustZ"] == {"bytes_out": 3.5}
