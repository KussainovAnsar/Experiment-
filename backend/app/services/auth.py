"""Passwords (bcrypt), JWT (HS256) for staff, parents and media, parent magic links, invite codes.

Token types (``typ`` claim):

* ``staff`` / ``parent`` — session tokens sent as ``Authorization: Bearer`` (7 / 30 days by default);
* ``media`` — a short-lived token scoped to **one lesson**, the only kind accepted from the ``?access_token=``
  query string of ``/api/media/*`` (``<img>``/``<video>`` tags cannot set headers; a long-lived session token
  in a URL would end up in proxy logs and browser history).

Parent access can be revoked by staff (:func:`revoke_parent_access`): outstanding magic links expire and every
parent JWT issued before the revocation is rejected (see :func:`parent_tokens_revoked_after`).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import MagicLink, Parent, StaffUser

JWT_ALGORITHM = "HS256"
TokenType = Literal["staff", "parent", "media"]
MediaPrincipalType = Literal["staff", "parent"]

# Unambiguous alphabet: no 0/O, 1/I/L to make codes dictation-friendly over the phone.
_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 8

# Magic-link row ``purpose`` that marks a staff revocation of the parent's portal access.
REVOCATION_PURPOSE = "revoked_at"

DEFAULT_MEDIA_TOKEN_TTL_MINUTES = 10


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired or of the wrong type."""


def hash_password(raw: str) -> str:
    """Hash a password with bcrypt (cost 12). Returns the modular-crypt string."""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    """Constant-time check of ``raw`` against a bcrypt hash; False on malformed hashes."""
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _encode(payload: dict[str, Any], ttl: timedelta) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    # ``iat_ms``: issue time in milliseconds — ``iat`` has second precision, too coarse for revocation checks.
    claims = {
        **payload,
        "iat": int(now.timestamp()),
        "iat_ms": int(now.timestamp() * 1000),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=JWT_ALGORITHM)


def token_issued_at(claims: dict[str, Any]) -> datetime:
    """Issue time of a decoded token (millisecond claim when present, else the end of the ``iat`` second)."""
    issued_ms = claims.get("iat_ms")
    if not isinstance(issued_ms, int):
        issued_ms = int(claims.get("iat") or 0) * 1000 + 999
    return datetime.fromtimestamp(issued_ms / 1000, tz=UTC)


def create_staff_token(user: StaffUser) -> str:
    """Issue a staff JWT: ``{sub, center_id, role, typ: "staff"}``, TTL ``staff_token_ttl_days``."""
    settings = get_settings()
    return _encode(
        {"sub": str(user.id), "center_id": str(user.center_id), "role": user.role.value, "typ": "staff"},
        timedelta(days=settings.staff_token_ttl_days),
    )


def create_parent_token(parent: Parent) -> str:
    """Issue a parent JWT: ``{sub, center_id, role: "parent", typ: "parent"}``, TTL ``parent_token_ttl_days``."""
    settings = get_settings()
    return _encode(
        {"sub": str(parent.id), "center_id": str(parent.center_id), "role": "parent", "typ": "parent"},
        timedelta(days=settings.parent_token_ttl_days),
    )


def media_token_ttl() -> timedelta:
    """TTL of media tokens: ``MEDIA_TOKEN_TTL_MINUTES`` (default 10 minutes)."""
    try:
        minutes = int(os.environ.get("MEDIA_TOKEN_TTL_MINUTES", str(DEFAULT_MEDIA_TOKEN_TTL_MINUTES)))
    except ValueError:
        minutes = DEFAULT_MEDIA_TOKEN_TTL_MINUTES
    return timedelta(minutes=max(1, minutes))


def create_media_token(
    *, principal: MediaPrincipalType, principal_id: uuid.UUID, center_id: uuid.UUID, lesson_id: uuid.UUID
) -> tuple[str, datetime]:
    """Issue a short-lived media JWT scoped to ``lesson_id``; returns ``(token, expires_at)``.

    Claims: ``{sub: principal_id, ptyp: "staff"|"parent", center_id, lesson_id, typ: "media"}``. The media
    routes load the staff user / parent behind ``sub`` and still apply their own visibility checks.
    """
    ttl = media_token_ttl()
    token = _encode(
        {
            "sub": str(principal_id),
            "ptyp": principal,
            "center_id": str(center_id),
            "lesson_id": str(lesson_id),
            "typ": "media",
        },
        ttl,
    )
    return token, datetime.now(UTC) + ttl


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a JWT; raise ``TokenError`` unless it is valid and of ``expected_type``."""
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("typ") != expected_type:
        raise TokenError("wrong token type")
    try:
        uuid.UUID(str(claims.get("sub")))
    except (ValueError, TypeError) as exc:
        raise TokenError("invalid subject") from exc
    if expected_type == "media":
        if claims.get("ptyp") not in ("staff", "parent"):
            raise TokenError("invalid media principal")
        try:
            uuid.UUID(str(claims.get("lesson_id")))
        except (ValueError, TypeError) as exc:
            raise TokenError("invalid media scope") from exc
    return claims


def _hash_token(token: str) -> str:
    # utf-8 (not ascii): a non-ASCII token from a client must hash to a miss, never raise a 500.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_magic_link(session: AsyncSession, parent: Parent, *, purpose: str = "portal") -> str:
    """Create a one-time-issued magic link for a parent.

    Stores only the sha256 of a 32-byte urlsafe token in ``magic_links`` and returns the full
    URL ``PUBLIC_BASE_URL/p/<token>``. The row is flushed, not committed.
    """
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    link = MagicLink(
        parent_id=parent.id,
        token_hash=_hash_token(token),
        purpose=purpose,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.magic_link_ttl_hours),
    )
    session.add(link)
    await session.flush()
    return f"{settings.public_base_url.rstrip('/')}/p/{token}"


async def exchange_magic_link(session: AsyncSession, token: str) -> Parent | None:
    """Resolve a magic-link token to its parent.

    The link stays valid until ``expires_at`` even after first use so the parent can reopen it from
    Telegram; ``used_at`` is set on first use. Returns ``None`` for unknown/expired tokens.
    """
    if not token or len(token) > 128:
        return None
    now = datetime.now(UTC)
    result = await session.execute(
        select(MagicLink).where(MagicLink.token_hash == _hash_token(token), MagicLink.purpose != REVOCATION_PURPOSE)
    )
    link = result.scalar_one_or_none()
    if link is None or link.expires_at <= now:
        return None
    parent = await session.get(Parent, link.parent_id)
    if parent is None:
        return None
    if link.used_at is None:
        link.used_at = now
        await session.flush()
    return parent


async def parent_tokens_revoked_after(session: AsyncSession, parent_id: uuid.UUID) -> datetime | None:
    """Timestamp of the latest staff revocation for the parent (``None`` when access was never revoked).

    Parent JWTs issued (``iat``) before this moment must be rejected. The marker is a ``magic_links`` row with
    ``purpose = "revoked_at"`` (indexed by ``parent_id``), so no schema change is needed.
    """
    result = await session.execute(
        select(func.max(MagicLink.created_at)).where(
            MagicLink.parent_id == parent_id, MagicLink.purpose == REVOCATION_PURPOSE
        )
    )
    return result.scalar_one_or_none()


async def revoke_parent_access(session: AsyncSession, parent: Parent) -> None:
    """Invalidate the parent's portal access: expire magic links and reject JWTs issued so far.

    Also unbinds Telegram and rotates the invite code, so a forwarded deep link or a lost phone can no longer
    receive reports; staff then re-invite the parent. Flushed, not committed.
    """
    now = datetime.now(UTC)
    await session.execute(
        update(MagicLink)
        .where(MagicLink.parent_id == parent.id, MagicLink.purpose != REVOCATION_PURPOSE, MagicLink.expires_at > now)
        .values(expires_at=now)
    )
    session.add(
        MagicLink(
            parent_id=parent.id,
            token_hash=_hash_token(secrets.token_urlsafe(32)),
            purpose=REVOCATION_PURPOSE,
            expires_at=now,
            used_at=now,
            created_at=now,
        )
    )
    parent.telegram_chat_id = None
    parent.invite_code = await issue_unique_invite_code(session)
    await session.flush()


def generate_invite_code() -> str:
    """Return an 8-character invite code from an unambiguous uppercase alphabet."""
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


async def issue_unique_invite_code(session: AsyncSession, *, attempts: int = 10) -> str:
    """Generate an invite code that no parent currently holds (``RuntimeError`` after ``attempts`` collisions)."""
    for _ in range(attempts):
        code = generate_invite_code()
        taken = (await session.execute(select(Parent.id).where(Parent.invite_code == code))).first()
        if taken is None:
            return code
    raise RuntimeError("could not generate a unique invite code")
