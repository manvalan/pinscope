/** Migrate localStorage keys from pre-rebrand `pinscopex:` prefix. */

export function migrateLocalKey(newKey: string, legacyKey: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    const current = localStorage.getItem(newKey);
    if (current != null) return current;
    const legacy = localStorage.getItem(legacyKey);
    if (legacy != null) {
      localStorage.setItem(newKey, legacy);
      localStorage.removeItem(legacyKey);
      return legacy;
    }
  } catch {
    /* ignore */
  }
  return null;
}

export function reviewedFindingsKey(projectId: string) {
  return `periscopex:reviewed-findings:${projectId}`;
}

export function legacyReviewedFindingsKey(projectId: string) {
  return `pinscopex:reviewed-findings:${projectId}`;
}

export function deratingSettingsKey(projectId: string) {
  return `periscopex:derating-settings:${projectId}`;
}

export function legacyDeratingSettingsKey(projectId: string) {
  return `pinscopex:derating-settings:${projectId}`;
}

export function deratingOverridesKey(projectId: string) {
  return `periscopex:derating-overrides:${projectId}`;
}

export function legacyDeratingOverridesKey(projectId: string) {
  return `pinscopex:derating-overrides:${projectId}`;
}
