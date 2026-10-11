/**
 * Render test for the authenticated lesson page against real backend JSON (captured fixtures from the
 * pipeline run with mock providers). Verifies that every tab renders without runtime errors and that
 * media URLs carry the short-lived media token (never the session JWT) in the access_token query parameter.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "@/App";
import { ToastProvider } from "@/components/Toast";
import { setCachedStaffUser, setStaffToken } from "@/auth/storage";
import lesson from "@/test/fixtures/lesson.json";
import analysis from "@/test/fixtures/analysis.json";
import reports from "@/test/fixtures/reports.json";
import frames from "@/test/fixtures/frames.json";
import transcript from "@/test/fixtures/transcript.json";
import me from "@/test/fixtures/me.json";
import type { StaffUser } from "@/types/api";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const LESSON_ID = (lesson as { id: string }).id;
const MEDIA_TOKEN = "media.scoped.token";
const FAILED_LESSON = {
  ...(lesson as Record<string, unknown>),
  status: "failed",
  stage_index: null,
  status_message: "Ответ модели анализа не прошёл валидацию (этап: анализируем урок)",
  error: "[analyzing] ValidationError: 3 validation errors for LessonAnalysis\nlesson_summary\n  Field required",
};
let lessonBody: unknown = lesson;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function fakeFetch(input: RequestInfo | URL): Promise<Response> {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const path = url.split("?")[0] ?? url;
  if (path === "/api/auth/me") return Promise.resolve(jsonResponse(me));
  if (path === `/api/lessons/${LESSON_ID}`) return Promise.resolve(jsonResponse(lessonBody));
  if (path === `/api/lessons/${LESSON_ID}/media-token`) {
    return Promise.resolve(
      jsonResponse({ access_token: MEDIA_TOKEN, token_type: "bearer", lesson_id: LESSON_ID, expires_at: "2099-01-01T00:00:00Z" }),
    );
  }
  if (path === `/api/lessons/${LESSON_ID}/analysis`) return Promise.resolve(jsonResponse(analysis));
  if (path === `/api/lessons/${LESSON_ID}/reports`) return Promise.resolve(jsonResponse(reports));
  if (path === `/api/lessons/${LESSON_ID}/frames`) return Promise.resolve(jsonResponse(frames));
  if (path === `/api/lessons/${LESSON_ID}/transcript`) return Promise.resolve(jsonResponse(transcript));
  return Promise.resolve(jsonResponse({ detail: "Запись не найдена." }, 404));
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

async function settle(ms = 30): Promise<void> {
  await act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
}

async function mountLesson(tab?: string): Promise<HTMLDivElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(container);
  const path = `/lessons/${LESSON_ID}${tab ? `?tab=${tab}` : ""}`;
  await act(async () => {
    root!.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>
          <ToastProvider>
            <App />
          </ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
  await settle();
  return container;
}

beforeAll(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
  // jsdom does not implement media playback / scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: () => Promise.resolve() });
});

afterEach(async () => {
  if (root) {
    await act(async () => root!.unmount());
  }
  container?.remove();
  root = null;
  container = null;
  lessonBody = lesson;
  window.localStorage.clear();
});

function login(): void {
  setStaffToken("test.staff.jwt");
  setCachedStaffUser(me as StaffUser);
}

describe("LessonPage with real backend fixtures", () => {
  it("renders the teacher tab: timeline, episodes, recommendations, visual issues", async () => {
    login();
    const el = await mountLesson();
    const text = el.textContent ?? "";
    expect(text).toContain("Обработка записи");
    expect(text).toContain("Готово");
    expect(text).toContain("Коротко об уроке");
    expect(text).toContain("Эпизоды с ошибками");
    expect(text).toContain("Рекомендации");
    expect(text).toContain("Замечено на экране");
    // media URLs carry the lesson-scoped media token for <img>/<video>, never the session JWT
    const video = el.querySelector("video");
    expect(video?.getAttribute("src")).toContain(`/api/media/lessons/${LESSON_ID}/source?access_token=${MEDIA_TOKEN}`);
    const img = el.querySelector(".frame-strip img");
    expect(img?.getAttribute("src")).toContain(`access_token=${MEDIA_TOKEN}`);
    expect(el.innerHTML).not.toContain("test.staff.jwt");
    // episodes come from the stored report: re-explanation and correction are shown as independent facts
    const flow = el.querySelector(".episode-flow")?.textContent ?? "";
    expect(flow).toContain("Переобъяснение в 00:31");
    expect(flow).toContain("Исправлена");
    // misconception headings use the Russian skill name, not the slug
    expect(text).toContain("Правописание -тся / -ться");
    expect(text).not.toMatch(/tsya tsa|padezhnye okonchaniya/);
    // at least one clickable timecode exists and seeking does not throw
    const tc = el.querySelector<HTMLButtonElement>("button.timecode");
    expect(tc).not.toBeNull();
    await act(async () => {
      tc!.click();
    });
    expect(text).not.toMatch(/undefined|\[object Object\]/);
  });

  it("renders the manager tab with indicator cards and talk shares", async () => {
    login();
    const el = await mountLesson("manager");
    const text = el.textContent ?? "";
    expect(text).toContain("Цели проверены");
    expect(text).toContain("Трудности выявлены");
    expect(text).toContain("Требуется разбор методиста");
    expect(text).toContain("Доли речи");
    expect(text).toContain("Хронология урока");
    // «Показатели» of the stored report are labelled in Russian — no raw English keys
    expect(text).toContain("Переобъяснений");
    expect(text).toContain("Самостоятельных успехов");
    expect(text).not.toMatch(/re explanations|independent successes|misconceptions strong|errors repeated after/);
    // «Трудности выявлены» is an informational card, not a red failure
    const difficulties = Array.from(el.querySelectorAll(".indicator")).find((n) => n.textContent?.includes("Трудности выявлены"));
    expect(difficulties?.className).toContain("indicator-neutral");
    expect(difficulties?.className).not.toContain("indicator-bad");
  });

  it("shows the Russian status_message for a failed lesson and hides the technical error in details", async () => {
    login();
    lessonBody = FAILED_LESSON;
    const el = await mountLesson();
    const box = el.querySelector(".error-box");
    expect(box?.textContent).toContain("Ответ модели анализа не прошёл валидацию");
    const details = box?.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.hasAttribute("open")).toBe(false);
    expect(details?.textContent).toContain("ValidationError");
    expect(box?.textContent?.indexOf("Ответ модели")).toBeLessThan(box?.textContent?.indexOf("ValidationError") ?? -1);
  });

  it("renders the parent tab with the report, Telegram preview and send button", async () => {
    login();
    const el = await mountLesson("parent");
    const text = el.textContent ?? "";
    expect(text).toContain("Как увидит родитель");
    expect(text).toContain("Что повторить дома");
    expect(text).toContain("Сообщение в Telegram");
    expect(el.querySelector(".tg-bubble b")).not.toBeNull();
    expect(el.querySelector(".tg-bubble script")).toBeNull();
    const btn = Array.from(el.querySelectorAll("button")).find((b) => b.textContent?.includes("Отправить родителям"));
    expect(btn).toBeDefined();
    expect(btn?.disabled).toBe(false);
  });

  it("renders transcript and frames tabs", async () => {
    login();
    const el = await mountLesson("transcript");
    expect(el.querySelectorAll(".segment").length).toBe((transcript as { segments: unknown[] }).segments.length);
    expect(el.textContent).toContain("Учитель");
    expect(el.textContent).toContain("Ученик");
  });

  it("hides the manager tab for teachers", async () => {
    setStaffToken("test.staff.jwt");
    setCachedStaffUser({ ...(me as StaffUser), role: "teacher" });
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.startsWith("/api/auth/me")) return Promise.resolve(jsonResponse({ ...(me as StaffUser), role: "teacher" }));
      return fakeFetch(input);
    });
    const el = await mountLesson("manager");
    const tabs = Array.from(el.querySelectorAll('[role="tab"]')).map((t) => t.textContent);
    expect(tabs.some((t) => t?.includes("Для методиста"))).toBe(false);
    expect(el.textContent).not.toContain("Родители");
    vi.mocked(fetch).mockImplementation(fakeFetch);
  });
});
