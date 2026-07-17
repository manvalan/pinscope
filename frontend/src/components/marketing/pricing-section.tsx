// Open-core seam: the cloud/gateway build replaces this file with the
// hosted-service pricing section (tiers, JSON-LD offer, nav link). The
// open-source build has no pricing.

export const PRICING_NAV_LINK: { href: string; label: string } | null = null;

export const PRICING_JSON_LD_OFFERS: Record<string, unknown> | null = null;

export function PricingSection() {
  return null;
}
