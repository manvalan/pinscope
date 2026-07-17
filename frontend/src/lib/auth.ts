/**
 * Open-core auth switch.
 *
 * When NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is unset the app runs in local/OSS
 * mode: no ClerkProvider, pass-through middleware, a stubbed signed-in
 * "local" user (matching the backend's LOCAL_DEV_USER), and all credits /
 * billing UI hidden. Pairs with BILLING_ENABLED=false on the backend —
 * mixed modes (key set but billing off, or the inverse) are unsupported.
 *
 * NEXT_PUBLIC_* vars are inlined at build time, so this is a build-time
 * constant — changing it requires a rebuild / dev-server restart.
 */
export const authEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
