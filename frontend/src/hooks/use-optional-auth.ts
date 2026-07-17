"use client";

/**
 * Open-core seam: the cloud/gateway build replaces this file with wrappers
 * around Clerk's hooks. The open-source build always runs as the local
 * user, mirroring the backend: user_id "local", admin access granted
 * (backend is_admin() returns True when auth is disabled).
 */

export interface AppUser {
  id: string;
  name: string | null;
  email: string | null;
  isAdmin: boolean;
}

export interface OptionalAuth {
  isSignedIn: boolean;
  getToken: () => Promise<string | null>;
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
const LOCAL_AUTH: OptionalAuth = { isSignedIn: true, getToken: async () => null };
const LOCAL_USER_RESULT: OptionalUser = { user: LOCAL_USER, isLoaded: true };

export function useOptionalAuth(): OptionalAuth {
  return LOCAL_AUTH;
}

export function useOptionalUser(): OptionalUser {
  return LOCAL_USER_RESULT;
}
