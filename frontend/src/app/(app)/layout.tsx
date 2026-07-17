import { TooltipProvider } from "@/components/ui/tooltip";
import { Sidebar } from "@/components/layout/sidebar";
import { CreditsProvider } from "@/components/billing/credits-context";
import { RedditPixelMatchKeys } from "@/components/analytics/reddit-pixel-match-keys";

export default function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <TooltipProvider>
      <CreditsProvider>
        <div className="flex h-full">
          <Sidebar />
          <main className="flex-1 flex flex-col overflow-auto">
            {children}
          </main>
        </div>
        <RedditPixelMatchKeys />
      </CreditsProvider>
    </TooltipProvider>
  );
}
