"""Shared helpers for allowlisted list sorting and filtered counts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, asc, desc, func, select


def apply_allowlisted_sort(
    query: Select[Any],
    *,
    sort_by: str | None,
    sort_order: str | None,
    columns: dict[str, Any],
    default: Any,
) -> Select[Any]:
    """Apply ORDER BY using an allowlisted column map."""
    column = columns.get(sort_by or "", default)
    order = (sort_order or "desc").lower()
    if order == "asc":
        return query.order_by(asc(column))
    return query.order_by(desc(column))


async def count_filtered(session, query: Select[Any]) -> int:
    """Count rows for a filtered select (strips ORDER BY / LIMIT / OFFSET)."""
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    result = await session.execute(count_query)
    return int(result.scalar_one())
