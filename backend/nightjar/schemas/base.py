"""Pydantic base classes: camelCase on the wire, snake_case in Python (API contract §1.1)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class CamelIn(BaseModel):
    """Request bodies: accept camelCase (and snake_case), reject unknown fields."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", str_max_length=20_000)


class CamelOut(BaseModel):
    """Response bodies: serialise with camelCase aliases (`model_dump(by_alias=True)`)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
