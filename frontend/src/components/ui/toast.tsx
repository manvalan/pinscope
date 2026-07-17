"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleCheck } from "lucide-react";
import { cn } from "@/lib/utils";

interface ToastMessage {
  id: number;
  message: string;
}

export function useToast() {
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const show = useCallback((message: string) => {
    setToast({ id: Date.now() + Math.random(), message });
  }, []);
  return { toast, show };
}

export function Toast({ toast }: { toast: ToastMessage | null }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!toast) return;
    setVisible(true);
    const hideTimer = setTimeout(() => setVisible(false), 1800);
    return () => clearTimeout(hideTimer);
  }, [toast]);

  return (
    <div
      aria-live="polite"
      className={cn(
        "fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-card px-4 py-2.5 text-sm shadow-lg transition-all duration-200",
        visible
          ? "opacity-100 translate-y-0"
          : "opacity-0 translate-y-2 pointer-events-none"
      )}
      role="status"
    >
      <CircleCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
      <span>{toast?.message}</span>
    </div>
  );
}
