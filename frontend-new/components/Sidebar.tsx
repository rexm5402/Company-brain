"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Ticket, Zap, Settings, Brain, GitBranch } from "lucide-react";
import { NotificationBell } from "./NotificationBell";
import { useAuth, signOut } from "@/lib/auth";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/tickets", label: "Overview", icon: LayoutDashboard },
  { href: "/tickets", label: "Work Hub", icon: Ticket, exact: false },
  { href: "/watchdog", label: "Watchdog", icon: Zap },
];

const NAV_ITEMS = [
  { href: "/tickets", label: "Overview", icon: LayoutDashboard },
  { href: "/watchdog", label: "Watchdog", icon: Zap },
];

// Sidebar nav items
const SIDEBAR_NAV = [
  { href: "/tickets", label: "Work Hub", icon: Ticket },
  { href: "/repos", label: "Repositories", icon: GitBranch },
  { href: "/watchdog", label: "Watchdog", icon: Zap },
];

export function Sidebar() {
  const path = usePathname();
  const { user } = useAuth();
  const displayName = user?.name || user?.sub || null;
  const initials = displayName ? displayName.slice(0, 2).toUpperCase() : "??";

  return (
    <aside
      className="fixed left-0 top-0 h-screen flex flex-col z-40"
      style={{
        width: 220,
        background: "#0d0d0d",
        borderRight: "1px solid #1f1f1f",
      }}
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-[#1f1f1f]">
        <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center shrink-0">
          <Brain className="w-4 h-4 text-white" />
        </div>
        <span className="font-semibold text-sm tracking-tight text-white">Brain OS</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {SIDEBAR_NAV.map(({ href, label, icon: Icon }) => {
          const active = path === href || path.startsWith(href + "/");
          return (
            <Link
              key={label}
              href={href}
              className={cn(
                "flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors group relative",
                active
                  ? "bg-indigo-500/10 text-indigo-400 font-medium"
                  : "text-[#888] hover:text-white hover:bg-[#1a1a1a]"
              )}
              style={
                active
                  ? { borderLeft: "2px solid #6366f1", paddingLeft: "calc(0.75rem - 2px)" }
                  : { borderLeft: "2px solid transparent", paddingLeft: "calc(0.75rem - 2px)" }
              }
            >
              <Icon className={cn("w-4 h-4 shrink-0", active ? "text-indigo-400" : "text-[#555] group-hover:text-white")} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Bottom user area */}
      <div className="px-3 py-4 border-t border-[#1f1f1f] space-y-2">
        <div className="flex items-center justify-between px-2">
          <NotificationBell />
          <button className="p-1.5 rounded-md text-[#555] hover:text-[#888] hover:bg-[#1a1a1a] transition-colors">
            <Settings className="w-4 h-4" />
          </button>
        </div>
        {user && (
          <button
            onClick={signOut}
            title="Sign out"
            className="flex items-center gap-2.5 px-2 py-1.5 w-full rounded-md hover:bg-[#1a1a1a] transition-colors text-left"
          >
            {user.avatar ? (
              <img
                src={user.avatar}
                alt={displayName || ""}
                className="w-7 h-7 rounded-full shrink-0"
              />
            ) : (
              <div className="w-7 h-7 rounded-full bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center shrink-0">
                <span className="text-[10px] font-bold text-indigo-400">{initials}</span>
              </div>
            )}
            <span className="text-xs text-[#888] truncate">{displayName}</span>
          </button>
        )}
      </div>
    </aside>
  );
}
