"""/api/auth/* — staff login, self-service centre registration, current user."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentStaff, DbSession
from app.db.models import ActorType, Center, StaffRole, StaffUser
from app.logging import get_logger
from app.schemas.api import LoginRequest, RegisterCenterRequest, StaffUserOut, TokenResponse
from app.services.audit import audit
from app.services.auth import create_staff_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = get_logger(__name__)


def staff_to_dto(user: StaffUser, center: Center) -> StaffUserOut:
    """Build the ``user`` DTO returned by login / me."""
    return StaffUserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        center_id=user.center_id,
        center_name=center.name,
        teacher_id=user.teacher_id,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: DbSession) -> TokenResponse:
    """Exchange email + password for a staff JWT."""
    email = body.email.lower().strip()
    result = await session.execute(select(StaffUser).where(func.lower(StaffUser.email) == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль")
    center = await session.get(Center, user.center_id)
    assert center is not None
    await audit(session, center_id=center.id, actor_type=ActorType.staff, actor_id=user.id, action="login")
    await session.commit()
    log.info("staff_login", staff_id=str(user.id), center_id=str(center.id))
    return TokenResponse(access_token=create_staff_token(user), user=staff_to_dto(user, center))


@router.post("/register-center", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_center(body: RegisterCenterRequest, session: DbSession) -> TokenResponse:
    """Self-service registration: creates a centre and its owner account, returns a token."""
    email = body.email.lower().strip()
    exists = await session.execute(select(StaffUser.id).where(func.lower(StaffUser.email) == email))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Пользователь с таким email уже существует")

    center = Center(name=body.center_name.strip(), city=(body.city or "").strip() or None)
    session.add(center)
    await session.flush()
    user = StaffUser(
        center_id=center.id,
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.owner_name.strip(),
        role=StaffRole.owner,
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Пользователь с таким email уже существует"
        ) from exc
    await audit(
        session,
        center_id=center.id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="register_center",
        entity="center",
        entity_id=center.id,
    )
    await session.commit()
    log.info("center_registered", center_id=str(center.id), staff_id=str(user.id))
    return TokenResponse(access_token=create_staff_token(user), user=staff_to_dto(user, center))


@router.get("/me", response_model=StaffUserOut)
async def me(user: CurrentStaff) -> StaffUserOut:
    """Return the authenticated staff user."""
    return staff_to_dto(user, user.center)
