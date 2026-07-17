import fs from "fs";
import path from "path";
import { ChangelogTimeline } from "@/components/legal/changelog-timeline";
import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Changelog",
  description:
    "Recent updates and improvements to Pinscope — new EDA tool support, review accuracy improvements, and platform changes.",
  path: "/changelog",
});

export default function ChangelogPage() {
  const content = fs.readFileSync(
    path.join(process.cwd(), "content", "changelog.md"),
    "utf-8",
  );
  return <ChangelogTimeline content={content} />;
}
