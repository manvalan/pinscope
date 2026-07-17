"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useOptionalAuth } from "@/hooks/use-optional-auth";
import { Button } from "@/components/ui/button";

export function NavAuthCluster() {
  const { isSignedIn } = useOptionalAuth();
  if (isSignedIn) {
    return (
      <Link href="/dashboard">
        <Button
          size="sm"
          className="bg-blue-600 hover:bg-blue-500 text-white border-0"
        >
          Dashboard
          <ArrowRight className="h-3.5 w-3.5" />
        </Button>
      </Link>
    );
  }
  return (
    <>
      <Link href="/login">
        <Button variant="ghost" size="sm">
          Sign in
        </Button>
      </Link>
      <Link href="/login">
        <Button
          size="sm"
          className="bg-blue-600 hover:bg-blue-500 text-white border-0"
        >
          Get started
        </Button>
      </Link>
    </>
  );
}

export function PrimaryCta() {
  const { isSignedIn } = useOptionalAuth();
  const href = isSignedIn ? "/dashboard" : "/login";
  const label = isSignedIn ? "Go to Dashboard" : "Start for free";
  return (
    <Link href={href}>
      <Button
        size="lg"
        className="bg-blue-600 hover:bg-blue-500 text-white border-0 px-8 text-base h-12"
      >
        {label}
        <ArrowRight className="h-4 w-4" />
      </Button>
    </Link>
  );
}

export function PricingCta() {
  const { isSignedIn } = useOptionalAuth();
  return (
    <Link href={isSignedIn ? "/billing" : "/login"}>
      <Button
        size="sm"
        className="bg-blue-600 hover:bg-blue-500 text-white border-0 gap-1.5"
      >
        {isSignedIn ? "Buy credits" : "Get started"}
        <ArrowRight className="h-3.5 w-3.5" />
      </Button>
    </Link>
  );
}
