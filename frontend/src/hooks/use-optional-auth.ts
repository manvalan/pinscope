"use client";

/**
 * Open-core seam: cloud/gateway replaces this with Clerk hooks.
 * Self-host local mode uses Pinscope JWT in localStorage.
 */

import { useCallback, useEffect, useState } from "react";
import { authEnabled, localAuthEnabled, TOKEN_STORAGE_KEY } from "@/lib/auth";

export interface AppUser {
  id: string;
  name: string | null;
  email: string | null;
  isAdmin: boolean;
}

export interface OptionalAuth {
  isSignedIn: boolean;
  getToken: () => Promise<string | null>;
  signOut?: () => void;
}

export interface OptionalUser {
  user: AppUser | null;
  isLoaded: boolean;
}

const LOCAL_USER: AppUser = {
  id: "local",
  name: "Local User",
  email: null,
  isAdmin: true,
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

function readStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeAuthToken(token: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event("pinscope-auth-changed"));
}

export function useOptionalAuth(): OptionalAuth {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(!localAuthEnabled);

  useEffect(() => {
    if (!localAuthEnabled) {
      setReady(true);
      return;
    }
    const sync = () => setToken(readStoredToken());
    sync();
    setReady(true);
    window.addEventListener("pinscope-auth-changed", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("pinscope-auth-changed", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const getToken = useCallback(async () => {
    if (localAuthEnabled) return readStoredToken();
    return null;
  }, []);

  const signOut = useCallback(() => {
    storeAuthToken(null);
    setToken(null);
  }, []);

  if (!authEnabled) {
    return { isSignedIn: true, getToken: async () => null };
  }

  if (localAuthEnabled) {
    return {
      isSignedIn: ready && Boolean(token),
      getToken,
      signOut,
    };
  }

  // Clerk build replaces this file; open-core without local mode stays open.
  return { isSignedIn: true, getToken: async () => null };
}

export function useOptionalUser(): OptionalUser {
  const { isSignedIn, getToken } = useOptionalAuth();
  const [user, setUser] = useState<AppUser | null>(authEnabled ? null : LOCAL_USER);
  const [isLoaded, setIsLoaded] = useState(!localAuthEnabled);

  useEffect(() => {
    if (!authEnabled) {
      setUser(LOCAL_USER);
      setIsLoaded(true);
      return;
    }
    if (!localAuthEnabled) {
      setUser(LOCAL_USER);
      setIsLoaded(true);
      return;
    }
    let cancelled = false;
    (async () => {
      setIsLoaded(false);
      const token = await getToken();
      if (!token) {
        if (!cancelled) {
          setUser(null);
          setIsLoaded(true);
        }
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/api/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) {
          storeAuthToken(null);
          if (!cancelled) setUser(null);
          return;
        }
        const data = await res.json();
        if (!cancelled) {
          setUser({
            id: data.user_id,
            name: data.name ?? null,
            email: data.email ?? null,
            isAdmin: Boolean(data.is_admin),
          });
        }
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setIsLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isSignedIn, getToken]);

  return { user, isLoaded };
}
