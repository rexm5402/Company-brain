"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Sidebar } from "@/components/Sidebar";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const isLoginPage = pathname === "/login";

  useEffect(() => {
    if (!loading && !user && !isLoginPage) {
      router.replace("/login");
    }
  }, [user, loading, router, isLoginPage]);

  // On the login page, render children directly (no sidebar, no guard)
  if (isLoginPage) return <>{children}</>;

  if (loading)
    return (
      <div className="flex-1 flex items-center justify-center min-h-screen">
        <div className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );

  if (!user) return null;

  return (
    <>
      <Sidebar />
      {children}
    </>
  );
}
