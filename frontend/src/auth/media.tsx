/**
 * Media token context: the lesson page (staff) and the parent lesson page provide the short-lived media token
 * returned by the API; every `<img>` / `<video>` below builds its URL with it. Outside a provider media URLs
 * are left without a token (the request then fails with 401 instead of leaking a session JWT).
 */
import { createContext, useCallback, useContext } from "react";
import { withMediaToken } from "./storage";

export const MediaTokenContext = createContext<string | null>(null);

export function useMediaToken(): string | null {
  return useContext(MediaTokenContext);
}

/** `(url) => url?access_token=<media token>` bound to the current context. */
export function useMediaUrl(): (url: string) => string {
  const token = useMediaToken();
  return useCallback((url: string) => withMediaToken(url, token), [token]);
}
