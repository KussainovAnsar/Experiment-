"""Audit log writes. Only ids are stored in ``meta`` — never children's names."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActorType, AuditLog


async def audit(
    session: AsyncSession,
    *,
    center_id: uuid.UUID | None,
    actor_type: ActorType,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str | None = None,
    entity_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an ``audit_log`` row (flushed, not committed) and return it."""
    row = AuditLog(
        center_id=center_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        meta=meta or {},
    )
    session.add(row)
    await session.flush()
    return row
