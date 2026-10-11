"""Auth: centre registration, login, /me, health."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.conftest import CenterCtx, CenterFactory


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "X-Request-ID" in resp.headers


async def test_register_center_and_me(client: AsyncClient) -> None:
    email = f"owner-{uuid.uuid4().hex[:6]}@example.com"
    resp = await client.post(
        "/api/auth/register-center",
        json={
            "center_name": "Знание",
            "city": "Астана",
            "owner_name": "Асель",
            "email": email,
            "password": "secret123",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["role"] == "owner"
    assert body["user"]["center_name"] == "Знание"
    assert body["user"]["email"] == email

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]
    assert me.json()["teacher_id"] is None


async def test_register_center_rejects_short_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register-center",
        json={"center_name": "Знание", "owner_name": "Асель", "email": "a@example.com", "password": "short"},
    )
    assert resp.status_code == 422
    assert "detail" in resp.json()


async def test_register_center_duplicate_email(client: AsyncClient, center: CenterCtx) -> None:
    resp = await client.post(
        "/api/auth/register-center",
        json={
            "center_name": "Другой",
            "owner_name": "Кто-то",
            "email": center.email.upper(),
            "password": "password123",
        },
    )
    assert resp.status_code == 409


async def test_login_success_and_failure(client: AsyncClient, center: CenterCtx) -> None:
    ok = await client.post("/api/auth/login", json={"email": center.email, "password": center.password})
    assert ok.status_code == 200
    assert ok.json()["user"]["center_id"] == str(center.center_id)

    bad = await client.post("/api/auth/login", json={"email": center.email, "password": "wrong-password"})
    assert bad.status_code == 401
    assert bad.json()["detail"]


async def test_me_requires_token(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_parent_token_is_not_accepted_for_staff(client: AsyncClient, center_factory: CenterFactory) -> None:
    from app.db.models import Parent
    from app.services.auth import create_parent_token

    ctx = await center_factory()
    fake_parent = Parent(id=uuid.uuid4(), center_id=ctx.center_id, full_name="x", invite_code="ABCD2345")
    token = create_parent_token(fake_parent)
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
