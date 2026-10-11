/** Staff authentication context: token + user, login/logout, 401 handling, role helpers. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { onUnauthorized } from "@/api/client";
import { authApi } from "@/api/staff";
import type { StaffUser, TokenResponse } from "@/types/api";
import {
  clearStaffAuth,
  getCachedStaffUser,
  getStaffToken,
  isTokenExpired,
  setCachedStaffUser,
  setStaffToken,
} from "./storage";

interface StaffAuthValue {
  user: StaffUser | null;
  token: string | null;
  /** True while the cached session is being validated on first load. */
  loading: boolean;
  isTeacher: boolean;
  canManagePeople: boolean;
  canSeeManagerReport: boolean;
  applyToken: (resp: TokenResponse) => void;
  logout: () => void;
}

const StaffAuthContext = createContext<StaffAuthValue | null>(null);

export function StaffAuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(() => {
    const t = getStaffToken();
    if (t && isTokenExpired(t)) {
      clearStaffAuth();
      return null;
    }
    return t;
  });
  const [user, setUser] = useState<StaffUser | null>(() => (token ? getCachedStaffUser() : null));
  const [loading, setLoading] = useState<boolean>(Boolean(token));

  const logout = useCallback(() => {
    clearStaffAuth();
    setToken(null);
    setUser(null);
    navigate("/login", { replace: true });
  }, [navigate]);

  const applyToken = useCallback((resp: TokenResponse) => {
    setStaffToken(resp.access_token);
    setCachedStaffUser(resp.user);
    setToken(resp.access_token);
    setUser(resp.user);
  }, []);

  // Validate the stored token against /api/auth/me once on mount.
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    authApi
      .me()
      .then((me) => {
        if (cancelled) return;
        setUser(me);
        setCachedStaffUser(me);
      })
      .catch(() => {
        // 401 is handled by the global listener; other errors keep the cached user.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(
    () =>
      onUnauthorized((realm) => {
        if (realm !== "staff") return;
        setToken(null);
        setUser(null);
        navigate("/login", { replace: true, state: { expired: true } });
      }),
    [navigate],
  );

  const value = useMemo<StaffAuthValue>(() => {
    const role = user?.role;
    return {
      user,
      token,
      loading,
      isTeacher: role === "teacher",
      canManagePeople: role === "owner" || role === "methodist",
      canSeeManagerReport: role !== "teacher",
      applyToken,
      logout,
    };
  }, [user, token, loading, applyToken, logout]);

  return <StaffAuthContext.Provider value={value}>{children}</StaffAuthContext.Provider>;
}

export function useStaffAuth(): StaffAuthValue {
  const ctx = useContext(StaffAuthContext);
  if (!ctx) throw new Error("useStaffAuth must be used inside StaffAuthProvider");
  return ctx;
}
