import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import path from "node:path";

const PROJECT_ROOT = path.resolve(process.cwd(), "..");

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const filePath = path.join(PROJECT_ROOT, id, "report.json");
  try {
    const data = await readFile(filePath, "utf-8");
    return NextResponse.json(JSON.parse(data));
  } catch {
    return NextResponse.json({ error: "Report not found" }, { status: 404 });
  }
}
