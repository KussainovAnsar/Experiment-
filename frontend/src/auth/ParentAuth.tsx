/** Parent portal authentication: magic-link exchange result stored locally; 401 → "link expired" page. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { onUnauthorized } from "@/api/client";
import type { ParentExchangeResponse, ParentMe, StudentBrief } from "@/types/api";
import {
  clearParentAuth,
  getParentSession,
  getParentToken,
  isTokenExpired,
  setParentSession,
  setParentToken,
} from "./storage";

interface ParentAuthValue {
  token: string | null;
  parent: ParentMe | null;
  students: StudentBrief[];
  applyExchange: (resp: ParentExchangeResponse) => void;
  updateParent: (parent: ParentMe) => void;
  logout: () => void;
}

const ParentAuthContext = createContext<ParentAuthValue | null>(null);

export function ParentAuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(() => {
    const t = getParentToken();
    if (t && isTokenExpired(t)) {
      clearParentAuth();
      return null;
    }
    return t;
  });
  const [session, setSession] = useState(() => (token ? getParentSession() : null));

  const applyExchange = useCallback((resp: ParentExchangeResponse) => {
    setParentToken(resp.access_token);
    const s = { parent: resp.parent, students: resp.students };
    setParentSession(s);
    setToken(resp.access_token);
    setSession(s);
  }, []);

  const updateParent = useCallback((parent: ParentMe) => {
    setSession((prev) => {
      const next = { parent, students: prev?.students ?? [] };
      setParentSession(next);
      return next;
    });
  }, []);

  const logout = useCallback(() => {
    clearParentAuth();
    setToken(null);
    setSession(null);
    navigate("/parent/expired", { replace: true });
  }, [navigate]);

  useEffect(
    () =>
      onUnauthorized((realm) => {
        if (realm !== "parent") return;
        setToken(null);
        setSession(null);
        navigate("/parent/expired", { replace: true });
      }),
    [navigate],
  );

  const value = useMemo<ParentAuthValue>(
    () => ({
      token,
      parent: session?.parent ?? null,
      students: session?.students ?? [],
      applyExchange,
      updateParent,
      logout,
    }),
    [token, session, applyExchange, updateParent, logout],
  );

  return <ParentAuthContext.Provider value={value}>{children}</ParentAuthContext.Provider>;
}

export function useParentAuth(): ParentAuthValue {
  const ctx = useContext(ParentAuthContext);
  if (!ctx) throw new Error("useParentAuth must be used inside ParentAuthProvider");
  return ctx;
}
