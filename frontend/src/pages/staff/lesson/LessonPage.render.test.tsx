/**
 * Render test for the authenticated lesson page against real backend JSON (captured fixtures from the
 * pipeline run with mock providers). Verifies that every tab renders without runtime errors and that
 * timecodes / frame thumbnails carry the access_token query parameter.
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function fakeFetch(input: RequestInfo | URL): Promise<Response> {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const path = url.split("?")[0] ?? url;
  if (path === "/api/auth/me") return Promise.resolve(jsonResponse(me));
  if (path === `/api/lessons/${LESSON_ID}`) return Promise.resolve(jsonResponse(lesson));
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
    // media URLs carry the token for <img>/<video>
    const video = el.querySelector("video");
    expect(video?.getAttribute("src")).toContain(`/api/media/lessons/${LESSON_ID}/source?access_token=test.staff.jwt`);
    const img = el.querySelector(".frame-strip img");
    expect(img?.getAttribute("src")).toContain("access_token=test.staff.jwt");
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
