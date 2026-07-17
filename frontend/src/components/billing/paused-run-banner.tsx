"use client";

import type { PauseCheckpoint } from "@/lib/types";

// Open-core seam: the cloud/gateway build replaces this file with the
// paused-on-credits banner. Open-source pipelines never pause on credits.

interface Props {
  projectId: string;
  checkpoint: PauseCheckpoint | null;
  resuming: boolean;
  onResume: () => void;
}

export function PausedRunBanner(_props: Props) {
  return null;
}
