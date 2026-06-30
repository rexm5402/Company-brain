"use client";

import { useEffect, useState } from "react";
import { GitBranch, Plus, RefreshCw, CheckCircle2, Eye, EyeOff } from "lucide-react";
import { api, type Repo } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function ReposPage() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    name: "",
    ownerSlug: "", // combined "owner/repo" input
    github_token_override: "",
  });
  const [showToken, setShowToken] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState("");
  const [indexingId, setIndexingId] = useState<string | null>(null);
  const [indexedId, setIndexedId] = useState<string | null>(null);

  const load = () =>
    api.repos
      .list()
      .then(setRepos)
      .catch(() => {})
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  const handleCreate = async () => {
    setFormError("");
    const [owner, ...rest] = form.ownerSlug.trim().split("/");
    const slug = rest.join("/");
    if (!form.name.trim() || !owner || !slug) {
      setFormError("Name and Owner/Repo are required (format: owner/repo).");
      return;
    }
    setCreating(true);
    try {
      await api.repos.create({
        name: form.name.trim(),
        owner: owner.trim(),
        slug: slug.trim(),
        ...(form.github_token_override ? { github_token_override: form.github_token_override } : {}),
      });
      setForm({ name: "", ownerSlug: "", github_token_override: "" });
      load();
    } catch (e: unknown) {
      setFormError(e instanceof Error ? e.message : "Failed to connect repository.");
    } finally {
      setCreating(false);
    }
  };

  const handleIndex = async (id: string) => {
    setIndexingId(id);
    try {
      await api.repos.index(id);
      setIndexedId(id);
      setTimeout(() => setIndexedId(null), 3000);
    } catch {
      // silently ignore — could show toast in future
    } finally {
      setIndexingId(null);
    }
  };

  return (
    <div className="space-y-8">
      {/* Page header */}
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-7 h-7 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
            <GitBranch className="w-4 h-4 text-indigo-400" />
          </div>
          <h1 className="text-xl font-semibold text-white tracking-tight">Repositories</h1>
        </div>
        <p className="text-[#888] text-sm mt-1">
          Connect GitHub repos for the agent to work across.
        </p>
      </div>

      {/* Connected repos list */}
      <div>
        <h2 className="text-[10px] font-semibold text-[#555] mb-3 uppercase tracking-widest">
          Connected Repos
        </h2>
        {loading ? (
          <div className="flex items-center gap-2 text-[#555] text-sm py-6">
            <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
          </div>
        ) : repos.length === 0 ? (
          <div className="border border-dashed border-[#1f1f1f] rounded-xl py-14 text-center">
            <GitBranch className="w-8 h-8 text-[#333] mx-auto mb-3" />
            <p className="text-sm text-[#555]">No repos connected yet.</p>
            <p className="text-xs text-[#444] mt-1">Add one below to get started.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {repos.map((repo, idx) => (
              <div
                key={repo.id}
                className="bg-[#111] border border-[#1f1f1f] rounded-xl px-5 py-4 flex items-center justify-between hover:border-indigo-500/30 transition-colors"
              >
                {/* Left: icon + name + slug */}
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-8 h-8 rounded-lg bg-[#1a1a1a] border border-[#2a2a2a] flex items-center justify-center shrink-0">
                    <GitBranch className="w-4 h-4 text-indigo-400" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-white leading-tight">{repo.name}</p>
                    <p className="text-xs font-mono text-[#888] mt-0.5">{repo.owner}/{repo.slug}</p>
                  </div>
                </div>

                {/* Right: badges + button */}
                <div className="flex items-center gap-2 shrink-0 ml-4">
                  {idx === 0 && (
                    <Badge
                      variant="outline"
                      className="text-[10px] text-green-400 border-green-800/60 bg-green-950/30 px-2 py-0"
                    >
                      Primary
                    </Badge>
                  )}
                  {repo.has_token_override && (
                    <Badge
                      variant="outline"
                      className="text-[10px] text-indigo-400 border-indigo-800/60 bg-indigo-950/30 px-2 py-0"
                    >
                      Custom Token
                    </Badge>
                  )}
                  <span className="text-[10px] text-[#555]">
                    {new Date(repo.created_at).toLocaleDateString()}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleIndex(repo.id)}
                    disabled={indexingId === repo.id}
                    className="h-7 text-xs px-2.5 bg-transparent border-[#2a2a2a] text-[#888] hover:text-white hover:border-[#444] gap-1.5"
                  >
                    {indexedId === repo.id ? (
                      <>
                        <CheckCircle2 className="w-3 h-3 text-green-400" />
                        <span className="text-green-400">Indexed</span>
                      </>
                    ) : indexingId === repo.id ? (
                      <>
                        <RefreshCw className="w-3 h-3 animate-spin" /> Indexing…
                      </>
                    ) : (
                      "Index Docs"
                    )}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add repo form */}
      <div className="bg-[#111] border border-[#1f1f1f] rounded-xl p-5">
        <div className="flex items-center gap-2 mb-5">
          <Plus className="w-4 h-4 text-indigo-400 shrink-0" />
          <span className="text-sm font-semibold text-white">Add Repository</span>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="repo-name" className="text-[10px] text-[#888] uppercase tracking-wider">
                Name
              </Label>
              <Input
                id="repo-name"
                placeholder="e.g. My Backend"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                className="mt-1.5 bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555]"
              />
            </div>
            <div>
              <Label htmlFor="repo-slug" className="text-[10px] text-[#888] uppercase tracking-wider">
                Owner / Repo
              </Label>
              <Input
                id="repo-slug"
                placeholder="owner/repo-name"
                value={form.ownerSlug}
                onChange={(e) => setForm((f) => ({ ...f, ownerSlug: e.target.value }))}
                className="mt-1.5 bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555] font-mono"
              />
            </div>
          </div>

          <div>
            <Label htmlFor="repo-token" className="text-[10px] text-[#888] uppercase tracking-wider">
              GitHub Token Override{" "}
              <span className="normal-case text-[#555]">(optional)</span>
            </Label>
            <div className="relative mt-1.5">
              <Input
                id="repo-token"
                type={showToken ? "text" : "password"}
                placeholder="ghp_…"
                value={form.github_token_override}
                onChange={(e) => setForm((f) => ({ ...f, github_token_override: e.target.value }))}
                className="bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555] pr-10 font-mono"
              />
              <button
                type="button"
                onClick={() => setShowToken((v) => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#555] hover:text-[#888] transition-colors"
              >
                {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {formError && <p className="text-red-400 text-sm">{formError}</p>}

          <Button
            onClick={handleCreate}
            disabled={creating}
            className="bg-indigo-600 hover:bg-indigo-500 text-white border-0 gap-1.5"
          >
            <GitBranch className="w-4 h-4" />
            {creating ? "Connecting…" : "Connect Repository"}
          </Button>
        </div>
      </div>
    </div>
  );
}
