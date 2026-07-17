"use client";

import Link from "next/link";
import { Cpu, ArrowRight } from "lucide-react";
import { useOptionalAuth } from "@/hooks/use-optional-auth";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme/theme-toggle";

export function Nav() {
  const { isSignedIn } = useOptionalAuth();

  return (
    <header className="sticky top-0 z-50 border-b border-border/50 bg-background/80 backdrop-blur-lg">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-blue-500" />
          <span className="text-sm font-semibold tracking-tight">Pinscope</span>
        </Link>
        <nav className="hidden sm:flex items-center gap-6 text-sm text-muted-foreground">
          <Link href="/#features" className="hover:text-foreground transition-colors">
            Features
          </Link>
          <Link href="/#security" className="hover:text-foreground transition-colors">
            Security
          </Link>
          <Link href="/#pricing" className="hover:text-foreground transition-colors">
            Pricing
          </Link>
          <Link href="/contact" className="text-foreground">
            Contact
          </Link>
        </nav>
        <div className="flex items-center gap-3">
          <ThemeToggle />
          {isSignedIn ? (
            <Link href="/dashboard">
              <Button
                size="sm"
                className="bg-blue-600 hover:bg-blue-500 text-white border-0"
              >
                Dashboard
                <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            </Link>
          ) : (
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
          )}
        </div>
      </div>
    </header>
  );
}
