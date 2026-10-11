/**
 * Mount smoke test: the app renders its public routes in jsdom without runtime errors
 * and shows Russian copy. No network: fetch is stubbed to reject.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { ToastProvider } from "./components/Toast";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

async function mount(path: string): Promise<HTMLDivElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(container);
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
  return container;
}

beforeAll(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
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

describe("App routes", () => {
  it("renders the staff login page", async () => {
    const el = await mount("/login");
    expect(el.textContent).toContain("Вход для сотрудников центра");
    expect(el.textContent).toContain("Зарегистрировать центр");
  });

  it("redirects unauthenticated staff to /login", async () => {
    const el = await mount("/lessons");
    expect(el.textContent).toContain("Вход для сотрудников центра");
  });

  it("renders the register page", async () => {
    const el = await mount("/register");
    expect(el.textContent).toContain("Регистрация центра");
  });

  it("shows the friendly expired page for parents without a session", async () => {
    const el = await mount("/parent");
    expect(el.textContent).toContain("Ссылка устарела — запросите новую у центра");
  });

  it("shows an error on a failed magic-link exchange", async () => {
    const el = await mount("/p/some-token");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(el.textContent).toContain("Ссылка устарела");
  });

  it("renders the not-found page", async () => {
    const el = await mount("/nope/nothing");
    expect(el.textContent).toContain("Страница не найдена");
  });
});
