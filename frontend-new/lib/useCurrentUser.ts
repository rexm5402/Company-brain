"use client";
// Lightweight "who am I" hook — stores the current username in localStorage.
// Shows an in-page modal instead of prompt() (which is blocked in sandboxed iframes).
import { useEffect, useState } from "react";

const KEY = "brain_os_user";

export function useCurrentUser(): string | null {
  const [user, setUser] = useState<string | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem(KEY);
    setUser(stored || null);
  }, []);

  return user;
}

export function setCurrentUser(username: string) {
  if (typeof window === "undefined") return;
  localStorage.setItem(KEY, username);
}

export function getCurrentUser(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(KEY) || "";
}
