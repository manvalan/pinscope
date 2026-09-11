"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useOptionalAuth, useOptionalUser } from "@/hooks/use-optional-auth";
import { localAuthEnabled } from "@/lib/auth";

/** When local auth is on, send unsigned users to /sign-in. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { isSignedIn } = useOptionalAuth();
  const { isLoaded } = useOptionalUser();

  useEffect(() => {
    if (!localAuthEnabled || !isLoaded) return;
    if (!isSignedIn) {
      const next = encodeURIComponent(pathname || "/");
      router.replace(`/sign-in?next=${next}`);
    }
  }, [isLoaded, isSignedIn, pathname, router]);

  if (!localAuthEnabled) return <>{children}</>;
  if (!isLoaded) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (!isSignedIn) return null;
  return <>{children}</>;
}
