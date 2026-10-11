"""Passwords (bcrypt), JWT (HS256) for staff and parents, and parent magic links."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import MagicLink, Parent, StaffUser

JWT_ALGORITHM = "HS256"
TokenType = Literal["staff", "parent"]

# Unambiguous alphabet: no 0/O, 1/I/L to make codes dictation-friendly over the phone.
_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


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
    claims = {**payload, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
    return jwt.encode(claims, settings.secret_key, algorithm=JWT_ALGORITHM)


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
    return claims


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


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
    result = await session.execute(select(MagicLink).where(MagicLink.token_hash == _hash_token(token)))
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


def generate_invite_code() -> str:
    """Return an 8-character invite code from an unambiguous uppercase alphabet."""
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))
