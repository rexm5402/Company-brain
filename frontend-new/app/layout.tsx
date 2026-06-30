import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { UserSetupModal } from "@/components/UserSetupModal";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Company Brain OS",
  description: "AI-powered engineering platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} dark`}>
      <body className="min-h-screen bg-background text-foreground antialiased flex">
        <UserSetupModal />
        <Sidebar />
        <main
          className="flex-1 overflow-y-auto"
          style={{ marginLeft: 220, minHeight: "100vh" }}
        >
          <div className="px-6 py-6 max-w-[1400px]">{children}</div>
        </main>
      </body>
    </html>
  );
}
