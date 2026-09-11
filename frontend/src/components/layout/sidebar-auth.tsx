"use client";

// Open-core seam: cloud/gateway replaces with Clerk user button + credits.
// Local auth shows email and sign-out / sign-in links.

import Link from "next/link";
import { useOptionalAuth, useOptionalUser } from "@/hooks/use-optional-auth";
import { authEnabled, localAuthEnabled } from "@/lib/auth";
import { Button } from "@/components/ui/button";

export function SidebarCredits() {
  return null;
}

export function SidebarUserButton() {
  const { user, isLoaded } = useOptionalUser();
  const { signOut, isSignedIn } = useOptionalAuth();

  if (!authEnabled || !localAuthEnabled) {
    return null;
  }

  if (!isLoaded) {
    return <div className="px-2 text-xs text-muted-foreground">…</div>;
  }

  if (!isSignedIn || !user) {
    return (
      <div className="flex flex-col gap-1 px-1">
        <Button asChild size="sm" variant="outline" className="w-full justify-start">
          <Link href="/sign-in">Sign in</Link>
        </Button>
        <Button asChild size="sm" variant="ghost" className="w-full justify-start">
          <Link href="/sign-up">Create account</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 px-2 py-1">
      <div className="truncate text-xs font-medium">{user.name || user.email}</div>
      {user.email && user.name ? (
        <div className="truncate text-[11px] text-muted-foreground">{user.email}</div>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        className="h-7 justify-start px-0 text-xs text-muted-foreground"
        onClick={() => {
          signOut?.();
          window.location.href = "/sign-in";
        }}
      >
        Sign out
      </Button>
    </div>
  );
}
