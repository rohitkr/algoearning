import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import type { ReactNode } from "react";

import "./globals.css";
import { clerkAppearance } from "@/lib/clerk-appearance";

import { Providers } from "./providers";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: { default: "AlgoEarning: algo trading for Indian markets", template: "%s · AlgoEarning" },
  description: "Build, test and deploy options strategies on your own broker account.",
  metadataBase: new URL("https://algoearning.com"),
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7fb" },
    { media: "(prefers-color-scheme: dark)", color: "#0d0e12" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // suppressHydrationWarning: next-themes sets the theme class on <html> before React hydrates, and browser
    // extensions (e.g. ColorZilla's cz-shortcut-listen) add attributes to <body>. It only covers these two
    // elements' own attributes; mismatches inside the page are still reported.
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <body className="min-h-dvh" suppressHydrationWarning>
        <ClerkProvider appearance={clerkAppearance}>
          <Providers>{children}</Providers>
        </ClerkProvider>
      </body>
    </html>
  );
}
