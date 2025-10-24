"""Pagination helper for list endpoints marked *(paged)* (API contract §1.1)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class Page:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200, alias="pageSize")) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def wrap(self, items: Sequence[Any], total: int) -> dict:
        return {
            "items": list(items),
            "page": self.page,
            "pageSize": self.page_size,
            "total": total,
            "totalPages": (total + self.page_size - 1) // self.page_size,
        }

    def slice(self, items: Sequence[Any]) -> dict:
        return self.wrap(items[self.offset : self.offset + self.page_size], len(items))


async def count(db: AsyncSession, stmt: Select) -> int:
    return int((await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one())
