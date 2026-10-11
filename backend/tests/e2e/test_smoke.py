"""End-to-end smoke test of the LessonLens MVP in a real browser (Playwright, Chromium).

The test is **opt-in**: it is skipped unless ``E2E=1`` is set, because it needs the seeded demo database and two
running servers (API on :8000, Vite dev server on :5173). It drives the UI exactly like a centre employee and a
parent would:

1. staff login -> dashboard shows numbers;
2. lessons list shows at least two processed (``done``) lessons;
3. lesson page: teacher / manager / parent tabs render, keyframe thumbnails load (HTTP 200 from /api/media/frames);
4. student page shows skill progress;
5. parents page: create a magic link -> open it on a phone-sized viewport -> parent portal shows the child, the
   lesson list, a lesson report and the progress view.

Screenshots are written to ``docs/screenshots`` (override with ``E2E_SCREENSHOT_DIR``). Any HTTP 5xx answered by
the API during the run fails the test.

Run::

    cd backend
    .venv/bin/python -m app.seed_demo --reset            # once
    .venv/bin/uvicorn app.main:app --port 8000 &          # API
    (cd ../frontend && npm run dev -- --port 5173) &      # frontend
    E2E=1 PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers .venv/bin/python -m pytest tests/e2e -q

Environment knobs: ``E2E_BASE_URL`` (default http://127.0.0.1:5173), ``E2E_EMAIL`` / ``E2E_PASSWORD``
(default demo owner login), ``E2E_SCREENSHOT_DIR``, ``E2E_HEADED=1`` to watch the browser.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("E2E") != "1", reason="browser smoke test: set E2E=1 to run")

pw_sync = pytest.importorskip("playwright.sync_api", reason="pip install playwright")

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:5173").rstrip("/")
EMAIL = os.environ.get("E2E_EMAIL", "admin@demo.kz")
PASSWORD = os.environ.get("E2E_PASSWORD", "demo12345")
REPO_DIR = Path(__file__).resolve().parents[3]
SHOTS = Path(os.environ.get("E2E_SCREENSHOT_DIR", REPO_DIR / "docs" / "screenshots"))
TIMEOUT_MS = int(os.environ.get("E2E_TIMEOUT_MS", "20000"))
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
MOBILE_VIEWPORT = {"width": 390, "height": 844}
TIMEZONE = os.environ.get("E2E_TIMEZONE", "Asia/Almaty")  # the demo centre timezone, so times match the seed output


class ServerErrors:
    """Collects API responses with status >= 500 and browser console errors for a hard assertion at the end."""

    def __init__(self) -> None:
        self.responses: list[str] = []
        self.console: list[str] = []

    def attach(self, page: pw_sync.Page) -> None:
        page.on("response", lambda r: self.responses.append(f"{r.status} {r.url}") if r.status >= 500 else None)
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" else None)

    def assert_clean(self) -> None:
        assert not self.responses, f"API answered 5xx during the smoke run: {self.responses}"


def _shot(page: pw_sync.Page, name: str, *, full_page: bool = True) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full_page)


def _same_origin(url: str) -> str:
    """Rewrite a link produced by the backend (PUBLIC_BASE_URL) onto the frontend origin under test."""
    parts = urlsplit(url)
    return f"{BASE_URL}{parts.path}{'?' + parts.query if parts.query else ''}"


@pytest.fixture(scope="module")
def browser() -> Iterator[pw_sync.Browser]:
    headed = os.environ.get("E2E_HEADED") == "1"
    with pw_sync.sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def staff_page(browser: pw_sync.Browser) -> Iterator[tuple[pw_sync.Page, ServerErrors]]:
    context = browser.new_context(viewport={"width": 1366, "height": 900}, locale="ru-RU", timezone_id=TIMEZONE)
    context.set_default_timeout(TIMEOUT_MS)
    page = context.new_page()
    errors = ServerErrors()
    errors.attach(page)
    try:
        yield page, errors
    finally:
        context.close()


def _login(page: pw_sync.Page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.fill("#email", EMAIL)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    page.get_by_text("Уроков всего", exact=True).wait_for()


def _stat_value(page: pw_sync.Page, label: str) -> int:
    tile_text = page.get_by_text(label, exact=True).locator("xpath=..").inner_text()
    numbers = re.findall(r"\d+", tile_text.replace(label, ""))
    assert numbers, f"stat tile «{label}» shows no number: {tile_text!r}"
    return int(numbers[0])


def test_staff_and_parent_smoke(browser: pw_sync.Browser, staff_page: tuple[pw_sync.Page, ServerErrors]) -> None:
    page, errors = staff_page

    # 1. login -> dashboard with numbers
    _login(page)
    assert _stat_value(page, "Уроков всего") >= 2
    assert _stat_value(page, "Цели проверены") >= 0
    _shot(page, "dashboard")

    # 2. lessons list: at least two done lessons
    page.goto(f"{BASE_URL}/lessons")
    page.locator("table.table tbody tr").first.wait_for()
    rows = page.locator("table.table tbody tr")
    done_rows = rows.filter(has_text="Готово")
    assert done_rows.count() >= 2, f"expected >= 2 lessons in status «Готово», got {done_rows.count()}"
    lesson_links = page.locator("table.table a[href]").evaluate_all(
        "els => els.map(e => e.getAttribute('href')).filter(h => /^\\/lessons\\/[0-9a-f-]{36}$/.test(h))"
    )
    lesson_ids = sorted({href.rsplit("/", 1)[-1] for href in lesson_links})
    assert len(lesson_ids) >= 2, lesson_links

    # 3. lesson page: teacher tab + thumbnails
    page.goto(f"{BASE_URL}/lessons/{lesson_ids[-1]}")
    page.get_by_text("Коротко об уроке", exact=True).wait_for()
    thumb = page.locator("img[src*='/api/media/frames/']").first
    thumb.wait_for(state="attached")
    thumb.scroll_into_view_if_needed()
    page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=thumb.element_handle())
    frame_src = thumb.get_attribute("src")
    assert frame_src and re.search(UUID_RE, frame_src), frame_src
    resp = page.request.get(frame_src if frame_src.startswith("http") else f"{BASE_URL}{frame_src}")
    assert resp.status == 200, f"frame thumbnail {frame_src} -> {resp.status}"
    assert resp.headers.get("content-type", "").startswith("image/"), resp.headers
    assert page.locator("img[src*='/api/media/frames/']").count() >= 5
    _shot(page, "lesson-teacher-tab")

    # manager tab
    page.get_by_role("tab", name="Для методиста").click()
    page.get_by_text("Краткое резюме", exact=True).wait_for()
    assert page.get_by_text("Цели проверены", exact=True).count() >= 1

    # parent tab
    page.get_by_role("tab", name="Для родителя").click()
    page.get_by_text("Как увидит родитель", exact=True).wait_for()
    page.get_by_text("Что повторить дома", exact=True).first.wait_for()
    assert page.get_by_role("button", name="Отправить родителям").count() == 1
    _shot(page, "lesson-parent-tab")

    # transcript + frames tabs render
    page.get_by_role("tab", name=re.compile(r"^Транскрипт")).click()
    page.locator("text=/\\d{1,2}:\\d{2}/").first.wait_for()
    page.get_by_role("tab", name=re.compile(r"^Кадры")).click()
    page.locator("img[src*='/api/media/frames/']").first.wait_for(state="attached")

    # 4. students page -> progress with skills
    page.goto(f"{BASE_URL}/students")
    student_link = page.locator("a[href^='/students/']").filter(has_not_text="Ученики").first
    student_link.wait_for()
    page.goto(f"{BASE_URL}{student_link.get_attribute('href')}")
    page.locator(".count-pill").first.wait_for()
    assert page.locator("details.skill").count() >= 3, "student page shows no skills"
    _shot(page, "student-progress")

    # 5. parents page -> magic link
    page.goto(f"{BASE_URL}/parents")
    page.get_by_role("button", name="Создать magic-ссылку").first.click()
    link_input = page.locator(".copy-box input")
    link_input.wait_for()
    magic_url = link_input.input_value()
    assert "/p/" in magic_url, magic_url

    # 6. parent portal on a phone-sized viewport
    mobile = browser.new_context(
        viewport=MOBILE_VIEWPORT, device_scale_factor=2, is_mobile=True, locale="ru-RU", timezone_id=TIMEZONE
    )
    mobile.set_default_timeout(TIMEOUT_MS)
    ppage = mobile.new_page()
    perrors = ServerErrors()
    perrors.attach(ppage)
    try:
        ppage.goto(_same_origin(magic_url))
        ppage.wait_for_url(re.compile(r"/parent$"))
        child_card = ppage.locator("a.child-card").first
        child_card.wait_for()
        _shot(ppage, "parent-portal-mobile", full_page=False)

        child_href = child_card.get_attribute("href")
        assert child_href and re.search(UUID_RE, child_href), child_href
        child_card.click()
        lesson_card = ppage.locator("a.lesson-card").first
        lesson_card.wait_for()
        assert ppage.locator("a.lesson-card").count() >= 2

        lesson_card.click()
        ppage.get_by_text("Что повторить дома", exact=True).wait_for()
        ppage.get_by_text("Что получилось самостоятельно", exact=True).wait_for()
        _shot(ppage, "parent-lesson-report")

        ppage.goto(f"{BASE_URL}{child_href}?view=progress")
        ppage.locator(".count-pill").first.wait_for()
        assert ppage.locator("details.skill").count() >= 3
        _shot(ppage, "parent-progress")
    finally:
        mobile.close()

    errors.assert_clean()
    perrors.assert_clean()
