import Link from "next/link";
import { Cpu, ArrowRight } from "lucide-react";
import { ContactForm } from "./contact-form";
import { Nav } from "./nav";
import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Contact",
  description:
    "Talk to the Pinscope team — questions, account help, or enterprise deployment.",
  path: "/contact",
});

export default function ContactPage() {
  return (
    <div className="flex flex-col min-h-full">
      <Nav />

      {/* ── Hero ── */}
      <section className="mx-auto max-w-3xl px-6 pt-24 sm:pt-36 pb-12">
        <h1 className="font-headline text-3xl sm:text-4xl tracking-tight animate-fade-up">
          Get in touch
        </h1>
        <p className="mt-4 text-muted-foreground max-w-lg leading-relaxed animate-fade-up [animation-delay:100ms]">
          Have a question about Pinscope, need help with your account, or want to
          discuss enterprise deployment? We&rsquo;d love to hear from you.
        </p>
      </section>

      {/* ── Form ── */}
      <section className="mx-auto max-w-3xl px-6 pb-24 w-full animate-fade-up [animation-delay:200ms]">
        <div className="rounded-xl border border-border bg-card/40 p-6 sm:p-8">
          <ContactForm />
        </div>
      </section>

      {/* ── Alternative ── */}
      <section className="border-t border-border/50 mt-auto">
        <div className="mx-auto max-w-3xl px-6 py-12 text-center">
          <p className="text-sm text-muted-foreground">
            Prefer email? Reach us directly at{" "}
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
      <footer className="border-t border-border/50">
        <div className="mx-auto max-w-6xl px-6 py-8 flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-blue-500" />
            <span>Pinscope</span>
          </div>
          <div className="flex items-center gap-4">
            <Link
              href="/privacy"
              className="hover:text-foreground transition-colors"
            >
              Privacy
            </Link>
            <Link
              href="/terms"
              className="hover:text-foreground transition-colors"
            >
              Terms
            </Link>
            <span>&copy; {new Date().getFullYear()} Faradworks</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
