"""A faster string compiler for pySigma's native matcher (ADR 0003 throughput note).

pySigma compiles every wildcard value to a case-insensitive regex such as `.*delete.*`; `re.search` with a
leading greedy `.*` backtracks quadratically on long command lines (encoded PowerShell launchers run to
several kilobytes), which made 557 process-creation rules cost ~4 ms per event. This subclass compiles the
common Sigma shapes to plain string operations on a per-event lower-case cache:

* `*x*` (contains)      → `x in value`
* `x*`  (startswith)    → `value.startswith(x)`
* `*x`  (endswith)      → `value.endswith(x)`
* `x`   (equals)        → `value == x`
* anything else         → regex with redundant leading/trailing `.*` removed (same semantics under search)

Semantics are unchanged (case-insensitive unless the rule uses `|cased`); tests compare both compilers on the
smoke-check cases and the replay.
"""

from __future__ import annotations

import re
from typing import Any

import sigma.matching.matcher as _matcher_module
from sigma.matching.compiler import MatchCompiler
from sigma.types import SigmaExpansion, SigmaString, SpecialChars

_LC_KEY = "\x00lc"
_RAW_KEY = "\x00raw"


def _low(data: dict[str, Any], field: str) -> str:
    cache = data.get(_LC_KEY)
    if cache is None:
        cache = {}
        data[_LC_KEY] = cache
    hit = cache.get(field)
    if hit is None:
        v = data.get(field, "")
        hit = (v if isinstance(v, str) else str(v)).lower()
        cache[field] = hit
    return hit


def _lraw(data: dict[str, Any], raw: str | None) -> str:
    hit = data.get(_RAW_KEY)
    if hit is None:
        hit = (raw or "").lower()
        data[_RAW_KEY] = hit
    return hit


def _plain_parts(value: SigmaString) -> tuple[bool, str, bool] | None:
    """(leading *, literal, trailing *) when the only specials are a leading and/or trailing `*`."""
    items = list(value.s)
    lead = bool(items) and items[0] == SpecialChars.WILDCARD_MULTI
    trail = len(items) > int(lead) and items[-1] == SpecialChars.WILDCARD_MULTI
    core = items[int(lead) : len(items) - int(trail)]
    if not all(isinstance(i, str) for i in core):
        return None
    return lead, "".join(str(c) for c in core), trail


class FastMatchCompiler(MatchCompiler):
    def _compile_string_match(
        self, field_name: str, value: SigmaString, closures: dict[str, Any], case_sensitive: bool
    ) -> str:
        parts = _plain_parts(value)
        closures["_low"] = _low
        if parts is not None and not case_sensitive:
            lead, lit, trail = parts
            lit = lit.lower()
            getter = f"_low(data, {field_name!r})"
            if lead and trail:
                return f"({lit!r} in {getter})"
            if trail:
                return f"{getter}.startswith({lit!r})"
            if lead:
                return f"{getter}.endswith({lit!r})"
            return f"({getter} == {lit!r})"
        if not value.contains_special():
            return super()._compile_string_match(field_name, value, closures, case_sensitive)
        regex = self._sigma_string_to_regex(value)
        if regex.startswith("^.*"):
            regex = regex[3:]
        if regex.endswith(".*$"):
            regex = regex[:-3]
        elif regex.endswith(".*"):
            regex = regex[:-2]
        if regex.startswith(".*"):
            regex = regex[2:]
        var = f"_pat_{len(closures)}"
        closures[var] = re.compile(regex, 0 if case_sensitive else re.IGNORECASE)
        return f"({var}.search(str(data.get({field_name!r}, ''))) is not None)"

    def _compile_keyword(self, value: Any, closures: dict[str, Any]) -> str:
        if isinstance(value, SigmaString) and not value.contains_special():
            closures["_lraw"] = _lraw
            return f"({str(value).lower()!r} in _lraw(data, raw))"
        if isinstance(value, SigmaExpansion):
            return "(" + " or ".join(self._compile_keyword(v, closures) for v in value.values) + ")"
        return super()._compile_keyword(value, closures)


def install() -> None:
    """Make every SigmaMatcher built afterwards use the fast compiler."""
    _matcher_module.MatchCompiler = FastMatchCompiler  # type: ignore[misc]
