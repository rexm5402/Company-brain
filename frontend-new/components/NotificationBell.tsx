"use client";

import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { api, type Notification } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useAuth } from "@/lib/auth";
import Link from "next/link";

export function NotificationBell() {
  const { user: authUser } = useAuth();
  const user = authUser?.sub ?? null;
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  const [notifs, setNotifs] = useState<Notification[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!user) return;
    const poll = () =>
      api.notifications.count(user).then((r) => setCount(r.unread)).catch(() => {});
    poll();
    const t = setInterval(poll, 15_000);
    return () => clearInterval(t);
  }, [user]);

  useEffect(() => {
    if (!open || !user) return;
    api.notifications.list(user).then(setNotifs).catch(() => {});
  }, [open, user]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const markRead = async (id: string) => {
    await api.notifications.markRead(id).catch(() => {});
    setNotifs((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
    setCount((c) => Math.max(0, c - 1));
  };

  const markAll = async () => {
    if (!user) return;
    await api.notifications.markAllRead(user).catch(() => {});
    setNotifs((prev) => prev.map((n) => ({ ...n, read: true })));
    setCount(0);
  };

  if (!user) return null;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative p-1.5 rounded-md hover:bg-[#1a1a1a] transition-colors"
        aria-label="Notifications"
      >
        <Bell className="w-4 h-4 text-[#888]" />
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 bg-red-500 text-white text-[10px] font-bold rounded-full w-4 h-4 flex items-center justify-center">
            {count > 9 ? "9+" : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute left-10 bottom-0 w-72 bg-[#111] border border-[#1f1f1f] rounded-xl shadow-2xl z-50">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#1f1f1f]">
            <span className="font-semibold text-sm text-white">Notifications</span>
            {count > 0 && (
              <button onClick={markAll} className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors">
                Mark all read
              </button>
            )}
          </div>
          <ScrollArea className="h-72">
            {notifs.length === 0 ? (
              <p className="text-sm text-[#555] text-center py-8">No notifications</p>
            ) : (
              notifs.map((n) => (
                <div
                  key={n.id}
                  className={`px-4 py-3 border-b border-[#1f1f1f] last:border-0 cursor-pointer hover:bg-[#1a1a1a] transition-colors ${
                    !n.read ? "bg-indigo-950/20" : ""
                  }`}
                  onClick={() => markRead(n.id)}
                >
                  <div className="flex items-start gap-2">
                    {!n.read && (
                      <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 mt-2 shrink-0" />
                    )}
                    <div className={!n.read ? "" : "ml-3.5"}>
                      <p className="text-xs font-medium leading-snug text-white">{n.title}</p>
                      {n.body && (
                        <p className="text-[10px] text-[#888] mt-0.5 line-clamp-2">{n.body}</p>
                      )}
                      {n.ticket_key && (
                        <Link
                          href={`/tickets/${n.ticket_id}`}
                          className="text-[10px] text-indigo-400 hover:underline mt-1 block"
                          onClick={(e) => e.stopPropagation()}
                        >
                          &rarr; {n.ticket_key}
                        </Link>
                      )}
                      <p className="text-[10px] text-[#555] mt-1">
                        {new Date(n.created_at).toLocaleString()}
                      </p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </ScrollArea>
        </div>
      )}
    </div>
  );
}
