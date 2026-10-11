"""FastAPI dependencies: database session, current staff / parent, role checks, media auth."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db.models import Parent, StaffRole, StaffUser
from app.db.session import get_session
from app.services.auth import TokenError, decode_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Требуется авторизация",
    headers={"WWW-Authenticate": "Bearer"},
)


def _bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    return credentials.credentials


async def _load_staff(session: AsyncSession, token: str) -> StaffUser:
    try:
        claims = decode_token(token, expected_type="staff")
    except TokenError as exc:
        raise _UNAUTHORIZED from exc
    user = await session.get(StaffUser, uuid.UUID(claims["sub"]), options=[selectinload(StaffUser.center)])
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return user


async def _load_parent(session: AsyncSession, token: str) -> Parent:
    try:
        claims = decode_token(token, expected_type="parent")
    except TokenError as exc:
        raise _UNAUTHORIZED from exc
    parent = await session.get(Parent, uuid.UUID(claims["sub"]))
    if parent is None:
        raise _UNAUTHORIZED
    return parent


async def current_staff(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> StaffUser:
    """Resolve the staff user from ``Authorization: Bearer`` (401 otherwise). ``user.center`` is loaded."""
    token = _bearer_token(credentials)
    if token is None:
        raise _UNAUTHORIZED
    return await _load_staff(session, token)


CurrentStaff = Annotated[StaffUser, Depends(current_staff)]


def require_role(*roles: StaffRole) -> Callable[..., Coroutine[Any, Any, StaffUser]]:
    """Dependency factory: the current staff user must have one of ``roles`` (403 otherwise)."""

    async def _dep(user: CurrentStaff) -> StaffUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
        return user

    return _dep


ManagerStaff = Annotated[StaffUser, Depends(require_role(StaffRole.owner, StaffRole.methodist))]
OwnerStaff = Annotated[StaffUser, Depends(require_role(StaffRole.owner))]


async def current_parent(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Parent:
    """Resolve the parent from a parent JWT (401 otherwise)."""
    token = _bearer_token(credentials)
    if token is None:
        raise _UNAUTHORIZED
    return await _load_parent(session, token)


CurrentParent = Annotated[Parent, Depends(current_parent)]


@dataclass(slots=True)
class MediaPrincipal:
    """Either a staff user or a parent; used by media routes that accept a query-string token."""

    staff: StaffUser | None = None
    parent: Parent | None = None

    @property
    def center_id(self) -> uuid.UUID:
        principal = self.staff or self.parent
        assert principal is not None
        return principal.center_id


async def media_principal(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    access_token: Annotated[str | None, Query(description="JWT for <img>/<video> tags that cannot set headers")] = None,
) -> MediaPrincipal:
    """Media-only auth: the JWT may come from the Authorization header OR ``?access_token=``.

    Works for both staff and parent tokens; the route decides whether the principal may see the file.
    """
    token = _bearer_token(credentials) or access_token
    if not token:
        raise _UNAUTHORIZED
    try:
        claims = decode_token(token, expected_type="staff")
        typ = "staff"
    except TokenError:
        try:
            claims = decode_token(token, expected_type="parent")
            typ = "parent"
        except TokenError as exc:
            raise _UNAUTHORIZED from exc
    del claims
    if typ == "staff":
        return MediaPrincipal(staff=await _load_staff(session, token))
    return MediaPrincipal(parent=await _load_parent(session, token))


CurrentMediaPrincipal = Annotated[MediaPrincipal, Depends(media_principal)]


@dataclass(slots=True)
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def pagination(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Pagination:
    """Common ``?page=&page_size=`` query parameters (page_size ≤ 100)."""
    return Pagination(page=page, page_size=page_size)


PageParams = Annotated[Pagination, Depends(pagination)]


def not_found(entity: str = "Объект") -> HTTPException:
    """404 used both for missing and cross-tenant entities (existence is not revealed)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity} не найден")


def is_teacher(user: StaffUser) -> bool:
    """True for the ``teacher`` role, which only sees its own lessons."""
    return user.role == StaffRole.teacher
