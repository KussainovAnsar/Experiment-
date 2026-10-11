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
from app.services.auth import TokenError, decode_token, parent_tokens_revoked_after, token_issued_at

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


async def _staff_by_id(session: AsyncSession, staff_id: uuid.UUID) -> StaffUser:
    user = await session.get(StaffUser, staff_id, options=[selectinload(StaffUser.center)])
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return user


async def _parent_by_claims(session: AsyncSession, parent_id: uuid.UUID, claims: dict[str, Any]) -> Parent:
    parent = await session.get(Parent, parent_id)
    if parent is None:
        raise _UNAUTHORIZED
    revoked_at = await parent_tokens_revoked_after(session, parent.id)
    if revoked_at is not None and token_issued_at(claims) < revoked_at:
        raise _UNAUTHORIZED  # issued before the staff revocation («Отозвать доступ»)
    return parent


async def _load_staff(session: AsyncSession, token: str) -> StaffUser:
    try:
        claims = decode_token(token, expected_type="staff")
    except TokenError as exc:
        raise _UNAUTHORIZED from exc
    return await _staff_by_id(session, uuid.UUID(claims["sub"]))


async def _load_parent(session: AsyncSession, token: str) -> Parent:
    try:
        claims = decode_token(token, expected_type="parent")
    except TokenError as exc:
        raise _UNAUTHORIZED from exc
    return await _parent_by_claims(session, uuid.UUID(claims["sub"]), claims)


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
    """Resolve the parent from a parent JWT (401 otherwise; also after a staff revocation)."""
    token = _bearer_token(credentials)
    if token is None:
        raise _UNAUTHORIZED
    return await _load_parent(session, token)


CurrentParent = Annotated[Parent, Depends(current_parent)]


@dataclass(slots=True)
class MediaPrincipal:
    """Either a staff user or a parent; used by media routes.

    ``lesson_id`` is set when the request was authorised by a media token, which is valid for that lesson only.
    """

    staff: StaffUser | None = None
    parent: Parent | None = None
    lesson_id: uuid.UUID | None = None

    @property
    def center_id(self) -> uuid.UUID:
        principal = self.staff or self.parent
        assert principal is not None
        return principal.center_id

    def allows_lesson(self, lesson_id: uuid.UUID) -> bool:
        """False when a lesson-scoped media token is used for another lesson."""
        return self.lesson_id is None or self.lesson_id == lesson_id


async def media_principal(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    access_token: Annotated[
        str | None, Query(description="Короткоживущий media-токен урока для <img>/<video> (GET …/media-token)")
    ] = None,
) -> MediaPrincipal:
    """Media-only auth.

    ``Authorization: Bearer`` accepts staff and parent session tokens as everywhere else. The ``?access_token=``
    query string accepts **only** ``typ=media`` tokens (short-lived, scoped to one lesson) — a session token in a
    URL would leak into proxy logs and browser history. The route still decides whether the principal may see
    the file.
    """
    header_token = _bearer_token(credentials)
    if header_token:
        try:
            decode_token(header_token, expected_type="staff")
        except TokenError:
            return MediaPrincipal(parent=await _load_parent(session, header_token))
        return MediaPrincipal(staff=await _load_staff(session, header_token))
    if not access_token:
        raise _UNAUTHORIZED
    try:
        claims = decode_token(access_token, expected_type="media")
    except TokenError as exc:
        raise _UNAUTHORIZED from exc
    lesson_id = uuid.UUID(str(claims["lesson_id"]))
    subject = uuid.UUID(claims["sub"])
    if claims["ptyp"] == "staff":
        return MediaPrincipal(staff=await _staff_by_id(session, subject), lesson_id=lesson_id)
    return MediaPrincipal(parent=await _parent_by_claims(session, subject, claims), lesson_id=lesson_id)


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
