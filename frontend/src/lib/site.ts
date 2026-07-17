export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL?.trim().replace(/\/$/, "") ||
  "https://pinscope.ai"
);

export const SITE_NAME = "Pinscope";

export const SITE_DESCRIPTION =
  "Pinscope reviews your schematic against every datasheet and catches the errors that would otherwise surface at bring-up. Works with KiCad, Altium, OrCAD, Cadence, Siemens, EasyEDA, and EAGLE.";

export const SITE_TAGLINE = "Agentic schematic validation";

export const TWITTER_HANDLE = "@getFaradWorks";

import type { Metadata } from "next";

/**
 * Build per-page metadata that preserves root openGraph/twitter fields.
 * Next.js shallow-overwrites the openGraph and twitter keys, so any page
 * that sets its own values must re-declare siteName/card/images/etc.
 */
export function pageMetadata({
  title,
  description,
  path,
}: {
  title: string;
  description: string;
  path: string;
}): Metadata {
  const url = path.startsWith("/") ? path : `/${path}`;
  const fullTitle = `${title} · ${SITE_NAME}`;
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      locale: "en_US",
      url,
      title: fullTitle,
      description,
      images: [
        {
          url: "/opengraph-image",
          width: 1200,
          height: 630,
          alt: `${SITE_NAME} — ${SITE_TAGLINE}`,
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      site: TWITTER_HANDLE,
      creator: TWITTER_HANDLE,
      title: fullTitle,
      description,
      images: ["/twitter-image"],
    },
  };
}

