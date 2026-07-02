"use client";
import { useEffect, useState } from "react";

const BASE =
  typeof window !== "undefined" && window.location.port !== "3000"
    ? ""
    : "http://localhost:8077";

export interface AuthUser {
  sub: string;    // github_username
  name: string;   // display_name
  avatar: string; // avatar_url
}

export function useAuth(): { user: AuthUser | null; loading: boolean } {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${BASE}/auth/me`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => setUser(data))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  return { user, loading };
}

export function signIn() {
  window.location.href = `${BASE}/auth/github`;
}

export function signOut() {
  fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "include" }).finally(
    () => (window.location.href = "/login")
  );
}
