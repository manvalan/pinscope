import { NextResponse } from "next/server";

// Open-core seam: the cloud/gateway build replaces this file with a Clerk
// middleware that protects non-public routes. The open-source build has no
// auth — every route is public.
export function proxy() {
  return NextResponse.next();
}

export const config = {
  matcher: [
    // Skip Next.js internals and static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
