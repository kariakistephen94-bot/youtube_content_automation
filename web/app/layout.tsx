import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "tutorial-ai",
  description: "Turn a repository into a programming tutorial video",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-zinc-950 font-sans">{children}</body>
    </html>
  );
}
