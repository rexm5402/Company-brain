"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth, signIn } from "@/lib/auth";
import { Brain, GitBranch } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function LoginPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user) router.replace("/tickets");
  }, [user, loading, router]);

  if (loading)
    return (
      <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
        <div className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );

  return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="w-full max-w-sm space-y-8 px-4 flex flex-col items-center">
        {/* Logo */}
        <div className="flex flex-col items-center gap-3">
          <div className="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center">
            <Brain className="w-6 h-6 text-white" />
          </div>
          <div className="text-center">
            <h1 className="text-xl font-semibold text-white">Brain OS</h1>
            <p className="text-sm text-[#888] mt-1">Your AI-powered engineering platform</p>
          </div>
        </div>

        {/* Sign in card */}
        <div className="bg-[#111] border border-[#1f1f1f] rounded-2xl p-6 space-y-4">
          <div>
            <h2 className="text-sm font-medium text-white">Sign in to continue</h2>
            <p className="text-xs text-[#555] mt-0.5">We use GitHub to verify your identity</p>
          </div>
          <Button
            onClick={signIn}
            className="w-full bg-white hover:bg-gray-100 text-black border-0 font-medium gap-2"
          >
            <GitBranch className="w-4 h-4" />
            Continue with GitHub
          </Button>
        </div>

        <p className="text-center text-xs text-[#555]">
          By signing in you agree to use this responsibly.
        </p>
      </div>
    </div>
  );
}
