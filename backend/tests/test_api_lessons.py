"""Lesson upload validation, listing, reprocess, media access, dashboard."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis, Frame, FrameReason, Lesson, LessonStatus, MediaKind, Report, ReportKind
from tests.conftest import CenterCtx, CenterFactory


@pytest.fixture
async def people(center: CenterCtx, make_student, make_teacher) -> tuple[dict, dict]:
    return await make_student(center.headers), await make_teacher(center.headers)


def _form(student: dict, teacher: dict, **overrides: str) -> dict[str, str]:
    data = {
        "student_id": student["id"],
        "teacher_id": teacher["id"],
        "scheduled_at": datetime.now(UTC).isoformat(),
        "subject": "russian",
        "consent_confirmed": "true",
        "language_hints": "ru,kk",
    }
    data.update(overrides)
    return data


async def test_upload_rejects_without_consent(client: AsyncClient, center: CenterCtx, people, tiny_wav: Path) -> None:
    student, teacher = people
    with tiny_wav.open("rb") as fh:
        resp = await client.post(
            "/api/lessons",
            data=_form(student, teacher, consent_confirmed="false"),
            files={"file": ("lesson.wav", fh, "audio/wav")},
            headers=center.headers,
        )
    assert resp.status_code == 422
    assert "согласие" in resp.json()["detail"]


async def test_upload_rejects_bad_extension(client: AsyncClient, center: CenterCtx, people) -> None:
    student, teacher = people
    resp = await client.post(
        "/api/lessons",
        data=_form(student, teacher),
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        headers=center.headers,
    )
    assert resp.status_code == 422
    assert "формат" in resp.json()["detail"].lower()


async def test_upload_rejects_cross_tenant_student(
    client: AsyncClient, center_factory: CenterFactory, make_student, make_teacher, tiny_wav: Path, no_pipeline
) -> None:
    a = await center_factory(name="Центр А")
    b = await center_factory(name="Центр Б")
    student_a = await make_student(a.headers)
    teacher_b = await make_teacher(b.headers)
    with tiny_wav.open("rb") as fh:
        resp = await client.post(
            "/api/lessons",
            data=_form(student_a, teacher_b),
            files={"file": ("lesson.wav", fh, "audio/wav")},
            headers=b.headers,
        )
    assert resp.status_code == 404


async def test_upload_accepted_and_lifecycle(
    client: AsyncClient, center: CenterCtx, people, tiny_wav: Path, no_pipeline, media_root: Path
) -> None:
    student, teacher = people
    with tiny_wav.open("rb") as fh:
        resp = await client.post(
            "/api/lessons",
            data=_form(student, teacher),
            files={"file": ("Урок 1.WAV", fh, "audio/wav")},
            headers=center.headers,
        )
    assert resp.status_code == 201, resp.text
    lesson = resp.json()
    assert lesson["status"] == "uploaded"
    assert lesson["stage_index"] == 0 and lesson["stage_total"] == 8
    assert lesson["media_kind"] == "audio"
    assert lesson["student_name"] == student["full_name"]
    assert lesson["teacher_name"] == teacher["full_name"]
    assert lesson["has_transcript"] is False and lesson["has_analysis"] is False
    assert lesson["report_kinds"] == []
    assert lesson["language_hints"] == ["ru", "kk"]
    assert lesson["consent_confirmed"] is True

    stored = media_root / str(center.center_id) / lesson["id"] / "source.wav"
    assert stored.is_file() and stored.stat().st_size == tiny_wav.stat().st_size

    got = await client.get(f"/api/lessons/{lesson['id']}", headers=center.headers)
    assert got.status_code == 200 and got.json()["id"] == lesson["id"]

    listing = await client.get("/api/lessons", params={"student_id": student["id"]}, headers=center.headers)
    assert listing.json()["total"] == 1
    filtered = await client.get("/api/lessons", params={"status": "done"}, headers=center.headers)
    assert filtered.json()["total"] == 0

    assert (await client.get(f"/api/lessons/{lesson['id']}/transcript", headers=center.headers)).status_code == 404
    assert (await client.get(f"/api/lessons/{lesson['id']}/analysis", headers=center.headers)).status_code == 404
    assert (await client.get(f"/api/lessons/{lesson['id']}/frames", headers=center.headers)).json() == []
    assert (await client.get(f"/api/lessons/{lesson['id']}/reports", headers=center.headers)).json() == []
    send = await client.post(f"/api/lessons/{lesson['id']}/reports/parent/send", headers=center.headers)
    assert send.status_code == 409

    reprocess = await client.post(f"/api/lessons/{lesson['id']}/reprocess", headers=center.headers)
    assert reprocess.status_code == 200
    assert reprocess.json()["status"] == "uploaded" and reprocess.json()["error"] is None

    source = await client.get(f"/api/media/lessons/{lesson['id']}/source", headers=center.headers)
    assert source.status_code == 200
    assert source.headers["content-type"].startswith("audio/wav")
    assert len(source.content) == tiny_wav.stat().st_size


async def test_upload_rejects_too_large(
    client: AsyncClient, center: CenterCtx, people, no_pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    student, teacher = people
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    payload = io.BytesIO(b"\0" * (1024 * 1024 + 10))
    resp = await client.post(
        "/api/lessons",
        data=_form(student, teacher),
        files={"file": ("big.mp3", payload, "audio/mpeg")},
        headers=center.headers,
    )
    assert resp.status_code == 413
    assert (await client.get("/api/lessons", headers=center.headers)).json()["total"] == 0


async def test_pipeline_missing_yields_503(
    client: AsyncClient, center: CenterCtx, people, tiny_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if name == "app.pipeline.runner":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    student, teacher = people
    with tiny_wav.open("rb") as fh:
        resp = await client.post(
            "/api/lessons",
            data=_form(student, teacher),
            files={"file": ("lesson.wav", fh, "audio/wav")},
            headers=center.headers,
        )
    assert resp.status_code == 503
    assert "detail" in resp.json()


async def test_media_frame_access_header_and_media_token(
    client: AsyncClient,
    center_factory: CenterFactory,
    db_session: AsyncSession,
    make_student,
    make_teacher,
    make_parent,
    media_root: Path,
) -> None:
    """Media routes: session JWT in the header, or a lesson-scoped media token in ``?access_token=`` — never a
    session token in the URL (it would leak into proxy logs / browser history)."""
    from app.services.storage import frames_dir

    a = await center_factory(name="Центр А")
    b = await center_factory(name="Центр Б")
    student = await make_student(a.headers)
    other_student = await make_student(a.headers, "Другой Ребёнок")
    teacher = await make_teacher(a.headers)
    parent = await make_parent(a.headers, [student["id"]])
    stranger = await make_parent(a.headers, [other_student["id"]], full_name="Чужой Родитель")

    def _lesson() -> Lesson:
        return Lesson(
            center_id=a.center_id,
            teacher_id=uuid.UUID(teacher["id"]),
            student_id=uuid.UUID(student["id"]),
            scheduled_at=datetime.now(UTC),
            media_kind=MediaKind.video,
            status=LessonStatus.done,
            consent_confirmed=True,
        )

    lesson, other_lesson = _lesson(), _lesson()
    db_session.add_all([lesson, other_lesson])
    await db_session.flush()
    frame_path = frames_dir(a.center_id, lesson.id) / "0000.jpg"
    Image.new("RGB", (32, 24), color=(200, 30, 30)).save(frame_path, "JPEG")
    frame = Frame(
        lesson_id=lesson.id,
        index=0,
        timestamp_sec=1.5,
        path=str(frame_path),
        width=32,
        height=24,
        reason=FrameReason.scene,
    )
    db_session.add(frame)
    # a parent report + an analysis that points at frame 0, so the parent lesson page lists it
    db_session.add(
        Report(
            lesson_id=lesson.id,
            kind=ReportKind.parent,
            data={"topic_plain": "Тема", "what_to_repeat": [], "encouragement": "Молодец!"},
            markdown="# Отчёт",
            version=1,
        )
    )
    db_session.add(
        Analysis(
            lesson_id=lesson.id,
            provider="mock",
            data={
                "errors": [{"frame_ids": [0], "description": "Ошибка в окончании"}],
                "visual_observations": [{"frame_id": 0, "relevance": "high", "issue": "«в школе» вместо «в школу»"}],
            },
        )
    )
    await db_session.commit()

    frames = await client.get(f"/api/lessons/{lesson.id}/frames", headers=a.headers)
    assert frames.status_code == 200
    assert frames.json()[0]["url"] == f"/api/media/frames/{frame.id}"
    assert frames.json()[0]["reason"] == "scene"

    url = f"/api/media/frames/{frame.id}"
    assert (await client.get(url)).status_code == 401
    ok = await client.get(url, headers=a.headers)
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
    # a staff session JWT in the query string is NOT accepted
    assert (await client.get(url, params={"access_token": a.token})).status_code == 401
    assert (await client.get(url, headers=b.headers)).status_code == 404

    token_resp = await client.get(f"/api/lessons/{lesson.id}/media-token", headers=a.headers)
    assert token_resp.status_code == 200, token_resp.text
    media_token = token_resp.json()["access_token"]
    assert token_resp.json()["lesson_id"] == str(lesson.id)
    via_query = await client.get(url, params={"access_token": media_token})
    assert via_query.status_code == 200 and via_query.content == ok.content
    source = await client.get(f"/api/media/lessons/{lesson.id}/source", params={"access_token": media_token})
    assert source.status_code == 404  # scoped correctly; the lesson simply has no source file
    # the token is scoped to its lesson: another lesson's media is not visible with it
    other_token = (await client.get(f"/api/lessons/{other_lesson.id}/media-token", headers=a.headers)).json()
    assert (await client.get(url, params={"access_token": other_token["access_token"]})).status_code == 404
    assert (await client.get(f"/api/lessons/{lesson.id}/media-token", headers=b.headers)).status_code == 404

    async def parent_token(p: dict) -> str:
        link = await client.post(f"/api/parents/{p['id']}/magic-link", headers=a.headers)
        token = link.json()["url"].rsplit("/", 1)[1]
        return (await client.post("/api/parent/auth/exchange", json={"token": token})).json()["access_token"]

    linked = await parent_token(parent)
    headers = {"Authorization": f"Bearer {linked}"}
    assert (await client.get(url, headers=headers)).status_code == 200
    assert (await client.get(url, params={"access_token": linked})).status_code == 401  # parent JWT not in URLs
    report = await client.get(f"/api/parent/students/{student['id']}/lessons/{lesson.id}", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert [f["index"] for f in body["frames"]] == [0]
    assert body["frames"][0]["caption"] == "«в школе» вместо «в школу»"
    parent_media = body["media_token"]
    assert parent_media
    assert (await client.get(url, params={"access_token": parent_media})).status_code == 200
    assert (
        await client.get(f"/api/media/lessons/{lesson.id}/source", params={"access_token": parent_media})
    ).status_code == 403
    unlinked = await parent_token(stranger)
    stranger_headers = {"Authorization": f"Bearer {unlinked}"}
    assert (await client.get(url, headers=stranger_headers)).status_code == 404
    report_url = f"/api/parent/students/{student['id']}/lessons/{lesson.id}"
    assert (await client.get(report_url, headers=stranger_headers)).status_code == 404


async def test_teacher_does_not_get_manager_indicators(
    client: AsyncClient, center: CenterCtx, db_session: AsyncSession, make_student, make_teacher, make_teacher_staff
) -> None:
    """CONTRACT §6: teacher = «без manager-отчёта» — also through /analysis and /dashboard."""
    student = await make_student(center.headers)
    teacher = await make_teacher(center.headers)
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
        Analysis(
            lesson_id=lesson.id,
            provider="mock",
            data={"topic": "Тест", "manager_indicators": {"objectives_checked": True, "needs_methodist_review": True}},
        )
    )
    await db_session.commit()
    _, token = await make_teacher_staff(center.center_id, uuid.UUID(teacher["id"]))
    headers = {"Authorization": f"Bearer {token}"}

    owner_view = await client.get(f"/api/lessons/{lesson.id}/analysis", headers=center.headers)
    assert owner_view.json()["data"]["manager_indicators"]["needs_methodist_review"] is True
    teacher_view = await client.get(f"/api/lessons/{lesson.id}/analysis", headers=headers)
    assert teacher_view.status_code == 200
    assert teacher_view.json()["data"]["manager_indicators"] is None
    assert teacher_view.json()["data"]["topic"] == "Тест"

    owner_dash = (await client.get("/api/dashboard", headers=center.headers)).json()
    assert owner_dash["needs_review_count"] == 1 and len(owner_dash["needs_review_lessons"]) == 1
    teacher_dash = (await client.get("/api/dashboard", headers=headers)).json()
    assert teacher_dash["needs_review_count"] is None and teacher_dash["needs_review_lessons"] == []
    assert teacher_dash["lessons_total"] == 1


async def test_reprocess_gate_rejects_active_but_allows_stale(
    client: AsyncClient,
    center: CenterCtx,
    db_session: AsyncSession,
    make_student,
    make_teacher,
    tiny_wav: Path,
    no_pipeline,
) -> None:
    from sqlalchemy import update

    from app.services.storage import source_path

    student = await make_student(center.headers)
    teacher = await make_teacher(center.headers)
    lesson = Lesson(
        center_id=center.center_id,
        teacher_id=uuid.UUID(teacher["id"]),
        student_id=uuid.UUID(student["id"]),
        scheduled_at=datetime.now(UTC),
        media_kind=MediaKind.audio,
        status=LessonStatus.analyzing,
        consent_confirmed=True,
    )
    db_session.add(lesson)
    await db_session.flush()
    src = source_path(center.center_id, lesson.id, "lesson.wav")
    src.write_bytes(tiny_wav.read_bytes())
    lesson.source_path = str(src)
    await db_session.commit()

    busy = await client.post(f"/api/lessons/{lesson.id}/reprocess", headers=center.headers)
    assert busy.status_code == 409  # a fresh intermediate status: another run owns the lesson

    await db_session.execute(
        update(Lesson).where(Lesson.id == lesson.id).values(updated_at=datetime.now(UTC) - timedelta(hours=2))
    )
    await db_session.commit()
    stale = await client.post(f"/api/lessons/{lesson.id}/reprocess", headers=center.headers)
    assert stale.status_code == 200, stale.text  # abandoned run (updated_at is old): may be restarted
    assert stale.json()["status"] == "uploaded"


async def test_dashboard_metrics(
    client: AsyncClient, center: CenterCtx, db_session: AsyncSession, make_student, make_teacher
) -> None:
    student = await make_student(center.headers)
    teacher = await make_teacher(center.headers)

    def _lesson(status: LessonStatus) -> Lesson:
        return Lesson(
            center_id=center.center_id,
            teacher_id=uuid.UUID(teacher["id"]),
            student_id=uuid.UUID(student["id"]),
            scheduled_at=datetime.now(UTC),
            media_kind=MediaKind.video,
            status=status,
            consent_confirmed=True,
        )

    done_checked = _lesson(LessonStatus.done)
    done_unchecked = _lesson(LessonStatus.done)
    processing = _lesson(LessonStatus.transcribing)
    failed = _lesson(LessonStatus.failed)
    old_recording = _lesson(LessonStatus.done)
    old_recording.scheduled_at = datetime.now(UTC) - timedelta(days=30)  # uploaded today, taught a month ago
    db_session.add_all([done_checked, done_unchecked, processing, failed, old_recording])
    await db_session.flush()

    def _analysis(lesson: Lesson, checked: bool, review: bool) -> Analysis:
        return Analysis(
            lesson_id=lesson.id,
            provider="mock",
            data={"manager_indicators": {"objectives_checked": checked, "needs_methodist_review": review}},
        )

    db_session.add_all([_analysis(done_checked, True, False), _analysis(done_unchecked, False, True)])
    await db_session.commit()

    resp = await client.get("/api/dashboard", headers=center.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lessons_total"] == 5
    assert body["lessons_last_7_days"] == 4  # «За последние 7 дней» = by lesson date, not by upload time
    assert body["lessons_processing"] == 1  # only "transcribing"; failed is terminal
    assert body["lessons_failed"] == 1
    assert body["share_objectives_checked"] == 0.5
    assert body["needs_review_count"] == 1
    assert len(body["recent_lessons"]) == 5
    assert [item["id"] for item in body["needs_review_lessons"]] == [str(done_unchecked.id)]
    assert body["recent_lessons"][0]["has_analysis"] in (True, False)
