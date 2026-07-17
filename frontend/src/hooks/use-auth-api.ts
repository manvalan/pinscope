"use client";

import { useEffect } from "react";
import { setTokenGetter } from "@/lib/api";
import { useOptionalAuth } from "@/hooks/use-optional-auth";

/**
 * Initializes the API module with the auth token getter.
 * Must be rendered once near the root of the app (e.g., in the Sidebar or a provider).
 * In local/OSS mode the getter resolves null, so requests go out without an
 * Authorization header (the backend assigns user_id "local").
 */
export function useAuthApi() {
  const { getToken } = useOptionalAuth();

  useEffect(() => {
    setTokenGetter(getToken);
  }, [getToken]);
}
