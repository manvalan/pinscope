import fs from "fs";
import path from "path";
import { LegalPage } from "@/components/legal/legal-page";
import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Privacy Policy",
  description:
    "How Pinscope collects, stores, and protects the schematics, datasheets, and BOMs you upload.",
  path: "/privacy",
});

export default function PrivacyPage() {
  const content = fs.readFileSync(
    path.join(process.cwd(), "content", "privacy.md"),
    "utf-8"
  );
  return <LegalPage content={content} />;
}
