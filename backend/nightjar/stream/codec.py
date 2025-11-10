"""Bus message encoding: `to_doc(<contract dataclass>)` as orjson on the wire (API contract Part 4), and the
inverse `from_doc(cls, doc)` that rebuilds frozen contract dataclasses from camelCase docs.

The decoder is driven by the dataclass type hints, so a new contract field needs no codec change. It accepts
floats for int fields (A2A DataParts carry numbers as protobuf `Value` doubles) and ISO strings for datetimes.
"""

from __future__ import annotations

import dataclasses
import types
import typing
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal, get_args, get_origin

import orjson

from nightjar import contracts
from nightjar.contracts import _camel, to_doc

_KEEP = {"raw", "fields", "detail", "features", "params", "labels", "input", "output", "robust_z", "payload"}


@lru_cache(maxsize=256)
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls, vars(contracts) | {"Any": Any})


def _coerce(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        for a in args:
            try:
                return _coerce(a, value)
            except (TypeError, ValueError, KeyError):
                continue
        return value
    if origin is Literal:
        return value
    if origin is tuple:
        targs = get_args(tp)
        inner = targs[0] if targs else Any
        return tuple(_coerce(inner, v) for v in (value or ()))
    if origin is list:
        (inner,) = get_args(tp) or (Any,)
        return [_coerce(inner, v) for v in value]
    if origin is dict:
        dargs = get_args(tp)
        if len(dargs) == 2 and dargs[1] is float:
            return {k: float(v) for k, v in value.items()}
        return dict(value)
    if tp is Any:
        return value
    if tp is uuid.UUID:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if tp is datetime:
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if tp is int:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    if tp is float:
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            return str(value)
        return value
    if tp is bool:
        return bool(value)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        if isinstance(value, tp):
            return value
        return from_doc(tp, value)
    return value


def from_doc[T](cls: type[T], doc: dict[str, Any]) -> T:
    """camelCase (or snake_case) doc → frozen contract dataclass."""
    hints = _hints(cls)  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        key = _camel(f.name)
        if key in doc:
            raw = doc[key]
        elif f.name in doc:
            raw = doc[f.name]
        else:
            continue  # default applies
        if f.name in _KEEP and isinstance(raw, dict):
            kwargs[f.name] = dict(raw) if f.name != "robust_z" else {k: float(v) for k, v in raw.items()}
            continue
        kwargs[f.name] = _coerce(hints[f.name], raw)
    return cls(**kwargs)


def _default(obj: Any) -> Any:
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, tuple | set | frozenset):
        return list(obj)
    raise TypeError(type(obj).__name__)


# Fields whose dict keys are someone else's data and must cross the bus verbatim (`to_doc` keeps `raw`, `fields`,
# `detail`, … but not the raw intake payload).
_VERBATIM = ("payload",)


def dumps(obj: Any) -> bytes:
    """Contract dataclass or plain JSON-able value → bytes."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        keep = {f: getattr(obj, f) for f in _VERBATIM if hasattr(obj, f)}
        doc = to_doc(obj)
        doc.update(keep)
    else:
        doc = obj
    return orjson.dumps(doc, default=_default, option=orjson.OPT_NON_STR_KEYS)


def loads(data: bytes | str) -> Any:
    return orjson.loads(data)


def decode[T](cls: type[T], data: bytes | str) -> T:
    return from_doc(cls, loads(data))
