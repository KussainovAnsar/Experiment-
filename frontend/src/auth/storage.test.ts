import { beforeEach, describe, expect, it } from "vitest";
import {
  clearParentAuth,
  clearStaffAuth,
  getParentToken,
  getStaffToken,
  isTokenExpired,
  PARENT_TOKEN_KEY,
  setParentToken,
  setStaffToken,
  STAFF_TOKEN_KEY,
  withMediaToken,
} from "./storage";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (s: string) => btoa(s).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64(JSON.stringify({ alg: "HS256" }))}.${b64(JSON.stringify(payload))}.sig`;
}

describe("auth storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("stores staff and parent tokens under the contract keys", () => {
    setStaffToken("staff.jwt");
    setParentToken("parent.jwt");
    expect(window.localStorage.getItem(STAFF_TOKEN_KEY)).toBe("staff.jwt");
    expect(window.localStorage.getItem(PARENT_TOKEN_KEY)).toBe("parent.jwt");
    expect(getStaffToken()).toBe("staff.jwt");
    expect(getParentToken()).toBe("parent.jwt");
  });

  it("clears realms independently", () => {
    setStaffToken("a");
    setParentToken("b");
    clearStaffAuth();
    expect(getStaffToken()).toBeNull();
    expect(getParentToken()).toBe("b");
    clearParentAuth();
    expect(getParentToken()).toBeNull();
  });

  it("appends the media token (never the session JWT) to media URLs for img/video tags", () => {
    setStaffToken("session.jwt");
    expect(withMediaToken("/api/media/frames/abc", "tok en")).toBe("/api/media/frames/abc?access_token=tok%20en");
    expect(withMediaToken("/api/x?y=1", "tok en")).toBe("/api/x?y=1&access_token=tok%20en");
    expect(withMediaToken("/api/media/frames/abc", null)).toBe("/api/media/frames/abc");
    expect(withMediaToken("/api/media/frames/abc", undefined)).not.toContain("session.jwt");
  });

  it("detects expired JWTs from the exp claim", () => {
    const past = Math.floor(Date.now() / 1000) - 60;
    const future = Math.floor(Date.now() / 1000) + 3600;
    expect(isTokenExpired(fakeJwt({ sub: "x", exp: past }))).toBe(true);
    expect(isTokenExpired(fakeJwt({ sub: "x", exp: future }))).toBe(false);
    expect(isTokenExpired("not-a-jwt")).toBe(false);
    expect(isTokenExpired(null)).toBe(false);
  });
});
