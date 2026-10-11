/**
 * Fetch wrapper for the LessonLens API.
 * - Adds `Authorization: Bearer` from the realm's token.
 * - Normalises errors into ApiError with a Russian message.
 * - On 401 clears the realm's auth and notifies listeners (router redirects).
 */
import {
  clearParentAuth,
  clearStaffAuth,
  getToken,
  type Realm,
} from "@/auth/storage";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type UnauthorizedListener = (realm: Realm) => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Subscribe to 401 events (used by the auth providers to redirect). */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function emitUnauthorized(realm: Realm): void {
  if (realm === "staff") clearStaffAuth();
  else clearParentAuth();
  unauthorizedListeners.forEach((l) => l(realm));
}

const STATUS_MESSAGES: Record<number, string> = {
  400: "Некорректный запрос. Проверьте введённые данные.",
  401: "Требуется вход в систему.",
  403: "Недостаточно прав для этого действия.",
  404: "Запись не найдена.",
  409: "Действие сейчас невозможно: данные ещё не готовы.",
  413: "Файл слишком большой.",
  422: "Проверьте правильность заполнения полей.",
  429: "Слишком много запросов. Попробуйте чуть позже.",
  500: "Внутренняя ошибка сервера. Попробуйте ещё раз.",
  502: "Сервер временно недоступен.",
  503: "Сервис временно недоступен. Попробуйте позже.",
};

function messageFromDetail(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation error list
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    if (first?.msg) {
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1] ?? "") : "";
      return field ? `Поле «${field}»: ${first.msg}` : first.msg;
    }
  }
  return STATUS_MESSAGES[status] ?? `Ошибка запроса (код ${status}).`;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  realm?: Realm;
  /** JSON body (object) or raw BodyInit (FormData etc). */
  body?: unknown;
  /** Query params; null/undefined values are skipped. */
  params?: Record<string, string | number | boolean | null | undefined>;
  /** Do not trigger the global 401 handler. */
  silent401?: boolean;
}

/** Build a URL with query params. */
export function buildUrl(path: string, params?: RequestOptions["params"]): string {
  if (!params) return path;
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    search.set(k, String(v));
  }
  const qs = search.toString();
  return qs ? `${path}${path.includes("?") ? "&" : "?"}${qs}` : path;
}

/**
 * Perform an API request and parse JSON. Throws ApiError on non-2xx.
 * 204 responses resolve to `undefined`.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { realm = "staff", body, params, silent401, headers, ...rest } = options;
  const token = getToken(realm);
  const finalHeaders = new Headers(headers ?? {});
  finalHeaders.set("Accept", "application/json");
  if (token) finalHeaders.set("Authorization", `Bearer ${token}`);

  let payload: BodyInit | undefined;
  if (body instanceof FormData || body instanceof Blob || typeof body === "string") {
    payload = body as BodyInit;
  } else if (body !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, params), { ...rest, headers: finalHeaders, body: payload });
  } catch {
    throw new ApiError(0, "Нет соединения с сервером. Проверьте подключение к интернету.");
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!response.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail ?? data;
    if (response.status === 401 && !silent401) emitUnauthorized(realm);
    throw new ApiError(response.status, messageFromDetail(detail, response.status), detail);
  }
  return data as T;
}

export const http = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  delete: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: "DELETE" }),
};

/** Extract a user-facing message from any thrown value. */
export function errorMessage(err: unknown, fallback = "Что-то пошло не так. Попробуйте ещё раз."): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export interface UploadProgress {
  loaded: number;
  total: number;
  /** 0..1 */
  ratio: number;
}

/**
 * Upload multipart form data with XHR so we can report progress.
 * Resolves with the parsed JSON response; rejects with ApiError.
 */
export function uploadWithProgress<T>(
  path: string,
  form: FormData,
  onProgress: (p: UploadProgress) => void,
  options: { realm?: Realm; signal?: AbortSignal } = {},
): Promise<T> {
  const { realm = "staff", signal } = options;
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.responseType = "text";
    const token = getToken(realm);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Accept", "application/json");

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        onProgress({ loaded: ev.loaded, total: ev.total, ratio: ev.total ? ev.loaded / ev.total : 0 });
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Не удалось отправить файл. Проверьте соединение."));
    xhr.onabort = () => reject(new ApiError(0, "Загрузка отменена."));
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = xhr.responseText;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as T);
        return;
      }
      const detail = (data as { detail?: unknown } | null)?.detail ?? data;
      if (xhr.status === 401) emitUnauthorized(realm);
      reject(new ApiError(xhr.status, messageFromDetail(detail, xhr.status), detail));
    };
    if (signal) {
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.send(form);
  });
}
