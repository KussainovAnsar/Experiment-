"""Security hardening: input robustness, rate limits, SECRET_KEY check, parent access revocation,
teacher-visible notification payloads."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Lesson, LessonStatus, MediaKind, Report, ReportKind
from app.services import ratelimit
from tests.conftest import CenterCtx


async def test_exchange_rejects_non_ascii_token_with_422(client: AsyncClient) -> None:
    resp = await client.post("/api/parent/auth/exchange", json={"token": "ééééééééééé"})
    assert resp.status_code == 422
    assert "detail" in resp.json()


async def test_webhook_non_ascii_secret_is_403(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret-path")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAE-test-token-not-real-0123456789abcd")
    get_settings.cache_clear()
    try:
        assert (await client.post("/api/telegram/webhook/s%C3%A9cret", json={})).status_code == 403
        # raw non-ASCII header bytes (what a hostile client would send); Starlette decodes them as latin-1
        bad_header = await client.post(
            "/api/telegram/webhook/s3cret-path",
            json={},
            headers={"X-Telegram-Bot-Api-Secret-Token": "сéкрет".encode()},  # type: ignore[dict-item]
        )
        assert bad_header.status_code == 403
    finally:
        get_settings.cache_clear()


async def test_login_rate_limited_per_email(
    client: AsyncClient, center: CenterCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    ratelimit.reset_all()
    try:
        body = {"email": center.email, "password": "wrong-password"}
        for _ in range(5):
            assert (await client.post("/api/auth/login", json=body)).status_code == 401
        limited = await client.post("/api/auth/login", json=body)
        assert limited.status_code == 429
        assert limited.headers.get("retry-after")
        assert "Слишком много" in limited.json()["detail"]
        # the per-IP budget (10/min) is shared: a different e-mail from the same client still gets through
        other = await client.post("/api/auth/login", json={"email": "someone@example.com", "password": "x"})
        assert other.status_code == 401
    finally:
        ratelimit.reset_all()


async def test_register_center_rate_limited_per_ip(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    ratelimit.reset_all()
    try:
        statuses = []
        for i in range(6):
            resp = await client.post(
                "/api/auth/register-center",
                json={
                    "center_name": f"Центр {i}",
                    "owner_name": "Владелец",
                    "email": f"spam-{uuid.uuid4().hex[:6]}@example.com",
                    "password": "password123",
                },
            )
            statuses.append(resp.status_code)
        assert statuses[:5] == [201] * 5 and statuses[5] == 429
    finally:
        ratelimit.reset_all()


def test_sliding_window_limiter() -> None:
    limiter = ratelimit.SlidingWindowLimiter(2, 60.0)
    assert limiter.check("k", now=0.0) is None
    assert limiter.check("k", now=1.0) is None
    retry = limiter.check("k", now=2.0)
    assert retry is not None and 57.0 < retry <= 58.0
    assert limiter.check("k", now=61.0) is None  # the first hit left the window
    assert limiter.check("other", now=2.0) is None


def test_secret_key_check_fails_fast_in_prod() -> None:
    from app.main import check_secret_key  # re-exported from app.startup_checks (also used by app.worker)

    prod_default = get_settings().model_copy(update={"app_env": "prod"})
    prod_default.secret_key = "dev-secret-key-change-me-please-32-bytes"
    with pytest.raises(RuntimeError):
        check_secret_key(prod_default)
    prod_short = get_settings().model_copy(update={"app_env": "prod", "secret_key": "short"})
    with pytest.raises(RuntimeError):
        check_secret_key(prod_short)
    prod_ok = get_settings().model_copy(update={"app_env": "prod", "secret_key": "x" * 48})
    check_secret_key(prod_ok)
    placeholder = "change-me-please-32-bytes-minimum"
    dev_default = get_settings().model_copy(update={"app_env": "dev", "secret_key": placeholder})
    check_secret_key(dev_default)  # only a warning outside prod


async def test_revoke_access_kills_links_tokens_and_telegram(
    client: AsyncClient, center: CenterCtx, db_session: AsyncSession, make_student, make_parent
) -> None:
    from app.db.models import Parent

    student = await make_student(center.headers)
    parent = await make_parent(center.headers, [student["id"]])
    row = await db_session.get(Parent, uuid.UUID(parent["id"]))
    assert row is not None
    row.telegram_chat_id = 424242
    await db_session.commit()

    link = (await client.post(f"/api/parents/{parent['id']}/magic-link", headers=center.headers)).json()["url"]
    token = link.rsplit("/", 1)[1]
    exchanged = await client.post("/api/parent/auth/exchange", json={"token": token})
    assert exchanged.status_code == 200
    headers = {"Authorization": f"Bearer {exchanged.json()['access_token']}"}
    assert (await client.get("/api/parent/me", headers=headers)).status_code == 200

    revoked = await client.post(f"/api/parents/{parent['id']}/revoke-access", headers=center.headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["telegram_linked"] is False
    assert revoked.json()["invite_code"] != parent["invite_code"]
    # issued parent JWT and the outstanding magic link are dead
    assert (await client.get("/api/parent/me", headers=headers)).status_code == 401
    assert (await client.post("/api/parent/auth/exchange", json={"token": token})).status_code == 401
    # a fresh invitation works again
    link2 = (await client.post(f"/api/parents/{parent['id']}/magic-link", headers=center.headers)).json()["url"]
    again = await client.post("/api/parent/auth/exchange", json={"token": link2.rsplit("/", 1)[1]})
    assert again.status_code == 200
    assert (
        await client.get("/api/parent/me", headers={"Authorization": f"Bearer {again.json()['access_token']}"})
    ).status_code == 200


async def test_teacher_send_parent_report_hides_invite_secrets(
    client: AsyncClient,
    center: CenterCtx,
    db_session: AsyncSession,
    make_student,
    make_teacher,
    make_parent,
    make_teacher_staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bot import telegram as bot_module

    monkeypatch.setenv("MESSAGING_PROVIDER", "mock")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAE-test-token-not-real-0123456789abcd")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "lessonlens_test_bot")
    get_settings.cache_clear()
    bot_module.set_bot_username(None)
    try:
        student = await make_student(center.headers)
        teacher = await make_teacher(center.headers)
        parent = await make_parent(center.headers, [student["id"]])
        lesson = Lesson(
            center_id=center.center_id,
            teacher_id=uuid.UUID(teacher["id"]),
            student_id=uuid.UUID(student["id"]),
            scheduled_at=datetime.now(UTC),
            media_kind=MediaKind.video,
            status=LessonStatus.done,
            consent_confirmed=True,
        )
        db_session.add(lesson)
        await db_session.flush()
        db_session.add(
            Report(
                lesson_id=lesson.id,
                kind=ReportKind.parent,
                data={"topic_plain": "Тема", "encouragement": "Молодец!"},
                markdown="# Отчёт",
                version=1,
            )
        )
        await db_session.commit()
        _, token = await make_teacher_staff(center.center_id, uuid.UUID(teacher["id"]))
        headers = {"Authorization": f"Bearer {token}"}

        assert (await client.get(f"/api/parents/{parent['id']}", headers=headers)).status_code == 403
        sent = await client.post(f"/api/lessons/{lesson.id}/reports/parent/send", headers=headers)
        assert sent.status_code == 200, sent.text
        payloads = [n["payload"] for n in sent.json()]
        assert payloads and all("deep_link" not in p and "magic_url" not in p for p in payloads)
        assert payloads[0]["kind"] == "lesson_report"

        owner_sent = await client.post(f"/api/lessons/{lesson.id}/reports/parent/send", headers=center.headers)
        assert owner_sent.status_code == 200
        assert owner_sent.json()[0]["payload"]["deep_link"].endswith(f"?start={parent['invite_code']}")
    finally:
        get_settings.cache_clear()
        bot_module.set_bot_username(None)
