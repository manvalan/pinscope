import fs from "fs";
import path from "path";
import { LegalPage } from "@/components/legal/legal-page";
import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Terms of Service",
  description:
    "Terms governing your use of Pinscope, including account, payment, and acceptable-use rules.",
  path: "/terms",
});

export default function TermsPage() {
  const content = fs.readFileSync(
    path.join(process.cwd(), "content", "terms.md"),
    "utf-8"
  );
  return <LegalPage content={content} />;
}
