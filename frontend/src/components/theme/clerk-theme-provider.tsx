"use client";

// Open-core seam: the cloud/gateway build replaces this file with a
// theme-aware ClerkProvider. The open-source build has no auth provider.
export function ClerkThemeProvider({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
