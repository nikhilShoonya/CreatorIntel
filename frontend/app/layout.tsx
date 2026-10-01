import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { MobileNav, Sidebar } from "@/components/layout/Sidebar";
import { TokenBanner } from "@/components/layout/TokenHealth";
import { Topbar } from "@/components/layout/Topbar";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "CreatorIntel - Channel Insights",
  description: "Upload a creator list and enrich it with YouTube and Instagram data.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} antialiased`}>
      <body className="min-h-screen">
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <Topbar />
            <MobileNav />
            <main className="flex-1 px-4 py-6 sm:px-6">
              <TokenBanner />
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
