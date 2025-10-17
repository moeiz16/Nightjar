"""Public interface for generated text (builder B). Used by agents and by A's notice drafts and weekly brief.

`LLM_FAKE=true` (default) returns deterministic, template-based output with the same schema, so every flow
works offline and evals are reproducible. Untrusted inputs are passed only through `untrusted=` and are
wrapped as quoted data by the prompt builder (ADR 0006); the result is validated against `schema`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from nightjar.contracts import UntrustedText


@dataclass(frozen=True, slots=True)
class LlmResult:
    output: dict[str, Any]  # validated against the schema
    model: str  # "offline-fake" or the cloud model id
    input_hash: str  # sha256 of the rendered prompt (stored on the agent step)
    fallback_used: bool


async def generate(
    task: str,
    *,
    instructions: str,
    trusted: dict[str, Any],
    untrusted: dict[str, UntrustedText],
    schema: type[BaseModel],
) -> LlmResult:
    raise NotImplementedError
