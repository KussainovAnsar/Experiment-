/**
 * Token storage: staff and parent JWTs live in localStorage under fixed keys.
 * All reads/writes are wrapped so a blocked storage (private mode) never crashes the UI.
 */
import type { ParentMe, StaffUser, StudentBrief } from "@/types/api";

export const STAFF_TOKEN_KEY = "ll_staff_token";
export const PARENT_TOKEN_KEY = "ll_parent_token";
const STAFF_USER_KEY = "ll_staff_user";
const PARENT_SESSION_KEY = "ll_parent_session";

export type Realm = "staff" | "parent";

export interface ParentSession {
  parent: ParentMe;
  students: StudentBrief[];
}

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable — ignore */
  }
}

function safeJson<T>(raw: string | null): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function getStaffToken(): string | null {
  return safeGet(STAFF_TOKEN_KEY);
}

export function setStaffToken(token: string | null): void {
  safeSet(STAFF_TOKEN_KEY, token);
}

export function getParentToken(): string | null {
  return safeGet(PARENT_TOKEN_KEY);
}

export function setParentToken(token: string | null): void {
  safeSet(PARENT_TOKEN_KEY, token);
}

export function getToken(realm: Realm): string | null {
  return realm === "staff" ? getStaffToken() : getParentToken();
}

export function getCachedStaffUser(): StaffUser | null {
  return safeJson<StaffUser>(safeGet(STAFF_USER_KEY));
}

export function setCachedStaffUser(user: StaffUser | null): void {
  safeSet(STAFF_USER_KEY, user ? JSON.stringify(user) : null);
}

export function getParentSession(): ParentSession | null {
  return safeJson<ParentSession>(safeGet(PARENT_SESSION_KEY));
}

export function setParentSession(session: ParentSession | null): void {
  safeSet(PARENT_SESSION_KEY, session ? JSON.stringify(session) : null);
}

export function clearStaffAuth(): void {
  setStaffToken(null);
  setCachedStaffUser(null);
}

export function clearParentAuth(): void {
  setParentToken(null);
  setParentSession(null);
}

/** Append ?access_token=<jwt> to a media URL (img/video tags cannot send headers). */
export function withAccessToken(url: string, realm: Realm): string {
  const token = getToken(realm);
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}access_token=${encodeURIComponent(token)}`;
}

/** Decode the JWT payload without verifying (for expiry checks only). */
export function jwtPayload(token: string | null): Record<string, unknown> | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length < 2 || !parts[1]) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const json = decodeURIComponent(
      Array.from(atob(padded))
        .map((c) => `%${c.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join(""),
    );
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** True when the token has an `exp` claim in the past. */
export function isTokenExpired(token: string | null): boolean {
  const payload = jwtPayload(token);
  const exp = payload?.exp;
  if (typeof exp !== "number") return false;
  return exp * 1000 < Date.now();
}
