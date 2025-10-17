"""Prompt-injection neutraliser for untrusted text (builder B; ADR 0006). Used by the normaliser, the agents,
the LLM prompt builder and A's notice drafting.

Contract (tests in tests/safety/): `neutralise()` never raises, is idempotent, removes C0/C1 control and
zero-width/bidi characters, collapses whitespace, caps length (default 2,000 chars), and flags (never deletes)
instruction-like content: imperative phrases aimed at an assistant ("ignore previous instructions", "you are
now", "as the system"), role markers ("system:", "<|im_start|>"), tool-call syntax ("call disable_account",
JSON with "tool"/"function" keys), markdown links/images to external hosts and base64 blobs > 200 chars.
"""

from __future__ import annotations

from nightjar.contracts import UntrustedText


def neutralise(value: object, *, field: str, max_len: int = 2000) -> UntrustedText:
    raise NotImplementedError


def render_for_prompt(items: dict[str, UntrustedText]) -> str:
    """Quoted data block: each item as `<untrusted field="..." flags="...">…</untrusted>` with angle brackets
    in the content escaped, preceded by the standing instruction that untrusted blocks are evidence only."""
    raise NotImplementedError
