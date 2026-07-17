import fs from "fs";
import path from "path";
import { FileGuidePage } from "@/components/legal/file-guide-page";
import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "File Upload Guide",
  description:
    "Step-by-step export instructions for KiCad, Altium, OrCAD, Cadence Allegro, Siemens Xpedition, EasyEDA, and Autodesk EAGLE — netlists, BOMs, and datasheets ready for Pinscope.",
  path: "/file-guide",
});

export default function Page() {
  const content = fs.readFileSync(
    path.join(process.cwd(), "content", "file-guide.md"),
    "utf-8",
  );
  return <FileGuidePage content={content} />;
}
