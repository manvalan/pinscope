import { NextResponse } from "next/server";
import { PROJECTS } from "@/lib/mock-data";

export async function GET() {
  return NextResponse.json(PROJECTS);
}
