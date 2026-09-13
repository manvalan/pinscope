/**
 * Open-core auth switch.
 *
 * - Clerk: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY set (cloud build).
 * - Local: NEXT_PUBLIC_AUTH_MODE=local (self-host Periscope accounts).
 * - Off: neither — signed-in stub user "local", matching backend.
 *
 * NEXT_PUBLIC_* vars are inlined at build time.
 */
export const authEnabled = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ||
    process.env.NEXT_PUBLIC_AUTH_MODE === "local",
);

export const localAuthEnabled =
  process.env.NEXT_PUBLIC_AUTH_MODE === "local" &&
  !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export const TOKEN_STORAGE_KEY = "periscope_token";
/** Pre-rebrand localStorage key — read once and migrate. */
export const LEGACY_TOKEN_STORAGE_KEY = "pinscope_token";

export function readAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const current = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (current) return current;
    const legacy = window.localStorage.getItem(LEGACY_TOKEN_STORAGE_KEY);
    if (legacy) {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, legacy);
      window.localStorage.removeItem(LEGACY_TOKEN_STORAGE_KEY);
      return legacy;
    }
  } catch {
    /* ignore */
  }
  return null;
}