"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot, Ticket, Zap } from "lucide-react";
import { NotificationBell } from "./NotificationBell";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/tickets", label: "Tickets", icon: Ticket },
  { href: "/watchdog", label: "Watchdog", icon: Zap },
];

export function NavBar() {
  const path = usePathname();
  return (
    <header className="border-b border-border bg-card/60 backdrop-blur sticky top-0 z-40">
      <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-6">
        <Link href="/tickets" className="flex items-center gap-2 font-semibold text-primary">
          <Bot className="w-5 h-5" />
          Brain OS
        </Link>
        <nav className="flex items-center gap-1 flex-1">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors",
                path.startsWith(href)
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <Icon className="w-4 h-4" />
              {label}
            </Link>
          ))}
        </nav>
        <NotificationBell />
      </div>
    </header>
  );
}
