/**
 * Open-core auth switch.
 *
 * - Clerk: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY set (cloud build).
 * - Local: NEXT_PUBLIC_AUTH_MODE=local (self-host Pinscope accounts).
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

export const TOKEN_STORAGE_KEY = "pinscope_token";
