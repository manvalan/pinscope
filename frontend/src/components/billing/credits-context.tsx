"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { CreditSnapshot } from "@/lib/types";

// Open-core seam: the cloud/gateway build replaces this file with a provider
// that polls the credits API. The open-source build has no billing — the
// balance stays null and consumers hide their credit UI.

interface CreditsContextValue {
  credits: CreditSnapshot | null;
  refresh: () => Promise<CreditSnapshot | null>;
  setCredits: (s: CreditSnapshot) => void;
}

const STUB: CreditsContextValue = {
  credits: null,
  refresh: async () => null,
  setCredits: () => {},
};

const CreditsContext = createContext<CreditsContextValue>(STUB);

export function CreditsProvider({ children }: { children: ReactNode }) {
  return (
    <CreditsContext.Provider value={STUB}>{children}</CreditsContext.Provider>
  );
}

export function useCredits(): CreditsContextValue {
  return useContext(CreditsContext);
}
