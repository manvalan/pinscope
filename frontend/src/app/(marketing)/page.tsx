import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { Cpu, Shield, Lock, ServerCog, Users, GitBranch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { APP_VERSION_DATE } from "@/lib/version";
import { SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "@/lib/site";
import {
  PRICING_JSON_LD_OFFERS,
  PRICING_NAV_LINK,
  PricingSection,
} from "@/components/marketing/pricing-section";
import { NavAuthCluster, PrimaryCta } from "./landing-cta";

// Landing page uses the root layout's metadata as-is so the file-convention
// OG/Twitter images (which Next merges only when no openGraph override exists)
// remain in the head. Canonical is already "/" from the root.
export const metadata: Metadata = {};

const LATEST_CHANGE_LABEL = APP_VERSION_DATE
  ? new Date(`${APP_VERSION_DATE}T00:00:00Z`).toLocaleDateString("en-US", {
      month: "long",
      day: "numeric",
      year: "numeric",
      timeZone: "UTC",
    })
  : null;

const STEPS = [
  { label: "Upload", detail: "Your design files" },
  { label: "Read", detail: "Every datasheet" },
  { label: "Verify", detail: "Pin by pin" },
  { label: "Report", detail: "Findings and fixes" },
];

const FEATURES = [
  {
    title: "Every finding, traceable",
    description:
      "Each recommendation links straight to the datasheet page and figure that backs it up. No black box — verify the reasoning, not just the verdict.",
    placeholder: "Datasheet grounded recommendations",
    image: "/datasheet.gif",
  },
  {
    title: "Auto-documentation",
    description:
      "For mission-critical projects. The artifacts your reviewers expect — generated on every run, ready to share.",
    placeholder: "Derating documentation",
    image: "/derating.png",
  },
];

const EDA_TOOLS = [
  { name: "KiCad", logo: "/eda-logos/kicad.svg" },
  { name: "Altium Designer", logo: "/eda-logos/altium.svg" },
  { name: "OrCAD", logo: "/eda-logos/orcad.svg" },
  { name: "Cadence Allegro", logo: "/eda-logos/cadence.svg" },
  { name: "Siemens Xpedition", logo: "/eda-logos/siemens.svg" },
  { name: "EasyEDA", logo: "/eda-logos/easyeda.svg" },
  { name: "Autodesk EAGLE", logo: "/eda-logos/autodesk.svg" },
];

const jsonLd = [
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: SITE_NAME,
    applicationCategory: "DeveloperApplication",
    operatingSystem: "Web",
    description: SITE_DESCRIPTION,
    url: SITE_URL,
    image: `${SITE_URL}/opengraph-image`,
    screenshot: `${SITE_URL}/report.png`,
    ...(PRICING_JSON_LD_OFFERS ? { offers: PRICING_JSON_LD_OFFERS } : {}),
    publisher: {
      "@type": "Organization",
      name: "Faradworks",
      url: "https://faradworks.com",
    },
  },
  {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: "Faradworks",
    url: "https://faradworks.com",
    logo: `${SITE_URL}/faradworks-logo-white.png`,
    sameAs: [
      "https://www.linkedin.com/company/faradworks",
      "https://x.com/getFaradWorks",
    ],
    address: {
      "@type": "PostalAddress",
      streetAddress: "33 W 17th St",
      addressLocality: "New York",
      addressRegion: "NY",
      addressCountry: "US",
    },
    contactPoint: {
      "@type": "ContactPoint",
      contactType: "customer support",
      email: "dev@faradworks.com",
    },
  },
];

export default function LandingPage() {
  return (
    <div className="flex flex-col min-h-full">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* ── Nav ── */}
      <header className="sticky top-0 z-50 border-b border-border/50 bg-background/80 backdrop-blur-lg">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2">
            <Cpu className="h-5 w-5 text-blue-500" />
            <span className="text-sm font-semibold tracking-tight">
              Pinscope
            </span>
          </Link>
          <nav className="hidden sm:flex items-center gap-6 text-sm text-muted-foreground">
            <a
              href="#features"
              className="hover:text-foreground transition-colors"
            >
              Features
            </a>
            <a
              href="#security"
              className="hover:text-foreground transition-colors"
            >
              Security
            </a>
            {PRICING_NAV_LINK && (
              <a
                href={PRICING_NAV_LINK.href}
                className="hover:text-foreground transition-colors"
              >
                {PRICING_NAV_LINK.label}
              </a>
            )}
            <Link
              href="/file-guide"
              className="hover:text-foreground transition-colors"
            >
              Docs
            </Link>
            <Link
              href="/contact"
              className="hover:text-foreground transition-colors"
            >
              Contact
            </Link>
          </nav>
          <div className="flex items-center gap-3">
            <ThemeToggle />
            <NavAuthCluster />
          </div>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="mx-auto max-w-5xl px-6 pt-24 sm:pt-36 pb-12">
        <h1 className="font-headline text-[2.75rem] leading-[1.08] sm:text-6xl lg:text-7xl tracking-tight animate-fade-up">
          Ship hardware that
          <br className="hidden lg:block" /> works the
          <br className="hidden lg:block" /> first time
        </h1>
        <p className="mt-6 text-lg sm:text-xl text-muted-foreground max-w-xl leading-relaxed animate-fade-up [animation-delay:100ms]">
          Pinscope reviews your schematic against every datasheet and
          catches the errors that would otherwise surface at bring-up.
        </p>
        <div className="mt-8 flex flex-col items-start gap-4 animate-fade-up [animation-delay:200ms]">
          <PrimaryCta />
          {LATEST_CHANGE_LABEL && (
            <Link
              href="/changelog"
              className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <GitBranch className="h-4 w-4" />
              Read Latest Changes: {LATEST_CHANGE_LABEL}
            </Link>
          )}
        </div>
      </section>

      {/* ── Hero product screenshot ── */}
      <section className="mx-auto max-w-5xl px-6 pb-24 animate-fade-up [animation-delay:350ms]">
        <div className="rounded-xl border border-border overflow-hidden bg-card/40">
          <Image
            src="/report.png"
            alt="Pinscope validation report"
            width={2400}
            height={1500}
            className="w-full h-auto"
            priority
            unoptimized
          />
        </div>
      </section>

      {/* ── Supported EDA tools ── */}
      <section className="border-t border-border/50">
        <div className="mx-auto max-w-6xl px-6 py-14">
          <p className="text-center text-xs font-mono uppercase tracking-[0.18em] text-muted-foreground">
            Works with your EDA tool
          </p>
          <div className="mt-8 grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-x-8 gap-y-10 items-center">
            {EDA_TOOLS.map((tool) => (
              <div
                key={tool.name}
                className="flex flex-col items-center justify-center gap-2"
                title={tool.name}
              >
                <Image
                  src={tool.logo}
                  alt={`${tool.name} logo`}
                  width={140}
                  height={40}
                  className="h-7 sm:h-8 w-auto object-contain [filter:brightness(0)] dark:[filter:brightness(0)_invert(1)] opacity-50 hover:opacity-90 transition-opacity"
                  unoptimized
                />
                <span className="text-[11px] text-muted-foreground/80">
                  {tool.name}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-10 text-center text-xs text-muted-foreground">
            <Link href="/file-guide" className="hover:text-foreground transition-colors">
              See export instructions for each tool →
            </Link>
          </p>
        </div>
      </section>

      {/* ── Pipeline ── */}
      <section className="border-y border-border/50">
        <div className="mx-auto max-w-3xl px-6 py-16">
          {/* Desktop: horizontal connected steps */}
          <div className="hidden sm:flex items-start justify-between relative">
            <div className="absolute top-3 left-[12.5%] right-[12.5%] h-px bg-border" />
            {STEPS.map((s, i) => (
              <div
                key={s.label}
                className="relative flex flex-col items-center text-center flex-1"
              >
                <div className="h-6 w-6 rounded-full bg-background border border-border flex items-center justify-center text-[11px] font-mono text-muted-foreground z-10">
                  {i + 1}
                </div>
                <span className="mt-3 text-sm font-medium">{s.label}</span>
                <span className="mt-1 text-xs text-muted-foreground">
                  {s.detail}
                </span>
              </div>
            ))}
          </div>

          {/* Mobile: vertical */}
          <div className="sm:hidden flex flex-col gap-6 relative pl-8">
            <div className="absolute left-[11px] top-3 bottom-3 w-px bg-border" />
            {STEPS.map((s, i) => (
              <div key={s.label} className="relative flex items-start gap-4">
                <div className="absolute -left-8 h-6 w-6 rounded-full bg-background border border-border flex items-center justify-center text-[11px] font-mono text-muted-foreground z-10">
                  {i + 1}
                </div>
                <div>
                  <span className="text-sm font-medium">{s.label}</span>
                  <span className="block text-xs text-muted-foreground mt-0.5">
                    {s.detail}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Features — alternating text + image ── */}
      <section id="features" className="scroll-mt-16">
        {FEATURES.map((f, i) => (
          <div
            key={f.title}
            className={
              i < FEATURES.length - 1 ? "border-b border-border/30" : ""
            }
          >
            <div className="mx-auto max-w-6xl px-6 py-20 sm:py-24 grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-16 items-center">
              <div className={i % 2 === 1 ? "lg:order-2" : ""}>
                <h2 className="font-headline text-3xl sm:text-4xl tracking-tight leading-tight">
                  {f.title}
                </h2>
                <p className="mt-4 text-muted-foreground leading-relaxed">
                  {f.description}
                </p>
              </div>
              <div
                className={`rounded-xl border border-border bg-card/40 aspect-[4/3] flex items-center justify-center overflow-hidden ${
                  i % 2 === 1 ? "lg:order-1" : ""
                }`}
              >
                {f.image ? (
                  <Image
                    src={f.image}
                    alt={f.placeholder}
                    width={800}
                    height={600}
                    className="w-full h-full object-cover"
                    unoptimized
                  />
                ) : (
                  <p className="text-sm text-muted-foreground/60">
                    {f.placeholder}
                  </p>
                )}
              </div>
            </div>
          </div>
        ))}
      </section>

      {/* ── Security ── */}
      <section id="security" className="border-y border-border/50 scroll-mt-16">
        <div className="mx-auto max-w-5xl px-6 py-20 sm:py-24">
          <h2 className="font-headline text-3xl sm:text-4xl tracking-tight text-center">
            Your designs stay yours
          </h2>
          <p className="mt-3 text-muted-foreground text-center max-w-lg mx-auto">
            Hardware IP is sensitive. Pinscope is built to keep it that way.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mt-12">
            {[
              {
                icon: Lock,
                title: "Encrypted end-to-end",
                detail: "AES-256 at rest, TLS 1.3 in transit.",
              },
              {
                icon: Shield,
                title: "Never used for training",
                detail: "Your files never train anyone's model. Zero retention from our AI providers.",
              },
              {
                icon: ServerCog,
                title: "SOC 2 infrastructure",
                detail: "Audit logging, continuous monitoring, and incident response.",
              },
              {
                icon: Users,
                title: "Role-based access",
                detail: "Owner and collaborator roles. You decide who sees what.",
              },
            ].map((item) => (
              <div
                key={item.title}
                className="rounded-xl border border-border bg-card/40 p-5 flex flex-col gap-3"
              >
                <item.icon className="h-5 w-5 text-emerald-500" />
                <h3 className="text-sm font-semibold">{item.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {item.detail}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <PricingSection />

      {/* ── Final CTA ── */}
      <section className="border-t border-border/50">
        <div className="mx-auto max-w-3xl px-6 py-20 sm:py-24 text-center">
          <h2 className="font-headline text-3xl sm:text-4xl tracking-tight">
            Stop reviewing schematics by hand
          </h2>
          <p className="mt-4 text-muted-foreground max-w-md mx-auto">
            Upload your first design. Get a full review in minutes.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
            <PrimaryCta />
            <Link href="/contact">
              <Button size="lg" variant="outline" className="px-8 text-base h-12">
                Contact us
              </Button>
            </Link>
          </div>
          <p className="mt-4 text-xs text-muted-foreground">
            Questions? Reach us at{" "}
            <a
              href="mailto:dev@faradworks.com"
              className="text-foreground hover:underline"
            >
              dev@faradworks.com
            </a>
          </p>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-border/50 mt-auto">
        <div className="mx-auto max-w-6xl px-6 py-10">
          <div className="flex flex-col gap-8 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex flex-col gap-4">
              <Image
                src="/faradworks-logo-white.png"
                alt="Faradworks"
                width={569}
                height={230}
                className="h-8 w-auto self-start opacity-90 invert dark:invert-0"
              />
              <address className="text-xs not-italic leading-relaxed text-muted-foreground">
                33 W 17th St
                <br />
                New York, NY
              </address>
            </div>
            <div className="flex flex-col items-start gap-4 sm:items-end">
              <a
                href="https://www.linkedin.com/company/faradworks"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Faradworks on LinkedIn"
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  className="h-4 w-4"
                  aria-hidden="true"
                >
                  <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.95v5.66H9.34V9h3.42v1.56h.05a3.75 3.75 0 0 1 3.37-1.85c3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.07 2.07 0 1 1 0-4.13 2.07 2.07 0 0 1 0 4.13zM7.12 20.45H3.56V9h3.56v11.45zM22.23 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 1.77 24h20.46c.98 0 1.77-.77 1.77-1.72V1.72C24 .77 23.21 0 22.23 0z" />
                </svg>
              </a>
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <Link href="/contact" className="hover:text-foreground transition-colors">Contact</Link>
                <Link href="/changelog" className="hover:text-foreground transition-colors">Changelog</Link>
                <Link href="/privacy" className="hover:text-foreground transition-colors">Privacy</Link>
                <Link href="/terms" className="hover:text-foreground transition-colors">Terms</Link>
              </div>
              <span className="text-xs text-muted-foreground">&copy; {new Date().getFullYear()} Faradworks</span>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
