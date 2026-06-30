"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Bot, User, GitPullRequest, AlertCircle } from "lucide-react";
import { api, type Ticket, type User as UserType, type Repo } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const COLUMNS = [
  {
    key: "open",
    label: "Open",
    dot: "#71717a",
    headerBg: "bg-zinc-900/60",
    headerText: "text-zinc-300",
    countBg: "bg-zinc-800 text-zinc-400",
  },
  {
    key: "in_progress",
    label: "In Progress",
    dot: "#3b82f6",
    headerBg: "bg-blue-950/60",
    headerText: "text-blue-300",
    countBg: "bg-blue-900/50 text-blue-400",
  },
  {
    key: "in_review",
    label: "In Review",
    dot: "#f59e0b",
    headerBg: "bg-amber-950/60",
    headerText: "text-amber-300",
    countBg: "bg-amber-900/50 text-amber-400",
  },
  {
    key: "done",
    label: "Done",
    dot: "#22c55e",
    headerBg: "bg-green-950/60",
    headerText: "text-green-300",
    countBg: "bg-green-900/50 text-green-400",
  },
] as const;

const SOURCE_ICON = {
  manual: <User className="w-3 h-3 text-[#555]" />,
  sentry: <AlertCircle className="w-3 h-3 text-red-400" />,
  github_ci: <GitPullRequest className="w-3 h-3 text-orange-400" />,
};

export default function TicketsPage() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [users, setUsers] = useState<UserType[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", assignee: "", reporter: "", repo_id: "" });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const load = () => api.tickets.list().then(setTickets).catch(() => {});
  useEffect(() => {
    load();
    api.users.list().then(setUsers).catch(() => {});
    api.repos.list().then(setRepos).catch(() => {});
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  const handleCreate = async () => {
    setError("");
    if (!form.title || !form.assignee || !form.reporter) {
      setError("Title, assignee, and reporter are required.");
      return;
    }
    setCreating(true);
    try {
      await api.tickets.create({ ...form, repo_id: form.repo_id || undefined });
      setOpen(false);
      setForm({ title: "", description: "", assignee: "", reporter: "", repo_id: "" });
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create ticket");
    } finally {
      setCreating(false);
    }
  };

  const byStatus = (s: string) => tickets.filter((t) => t.status === s);

  return (
    <div>
      {/* Page header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-semibold text-white tracking-tight">Work Hub</h1>
          <p className="text-[#888] text-sm mt-0.5">
            {tickets.length} ticket{tickets.length !== 1 ? "s" : ""} &middot;{" "}
            {byStatus("in_progress").length} in progress
          </p>
        </div>
        <Button
          onClick={() => setOpen(true)}
          className="gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white border-0 text-sm font-medium px-3.5 py-2 h-auto"
        >
          <Plus className="w-4 h-4" /> New Ticket
        </Button>
      </div>

      {/* Kanban columns */}
      <div className="grid grid-cols-4 gap-4">
        {COLUMNS.map((col) => {
          const colTickets = byStatus(col.key);
          return (
            <div key={col.key}>
              {/* Column header */}
              <div className="flex items-center gap-2 mb-3 px-1">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: col.dot }} />
                <span className={`text-xs font-semibold whitespace-nowrap ${col.headerText}`}>{col.label}</span>
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${col.countBg}`}>
                  {colTickets.length}
                </span>
              </div>

              {/* Cards */}
              <div className="space-y-2">
                {colTickets.map((ticket) => {
                  const assigneeInitials = ticket.assignee.slice(0, 2).toUpperCase();
                  return (
                    <Link key={ticket.id} href={`/tickets/${ticket.id}`} className="block">
                      <div
                        className="group bg-[#111] border border-[#1f1f1f] rounded-lg p-3 cursor-pointer transition-all hover:border-indigo-500/50"
                        style={{ boxShadow: "none" }}
                      >
                        {/* Key + source */}
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-[10px] text-[#555] font-mono tracking-wider">
                            {ticket.key}
                          </span>
                          <span title={ticket.source}>
                            {SOURCE_ICON[ticket.source as keyof typeof SOURCE_ICON] ?? SOURCE_ICON.manual}
                          </span>
                        </div>

                        {/* Title */}
                        <p className="text-sm font-medium text-white leading-snug line-clamp-3 group-hover:text-indigo-300 transition-colors">
                          {ticket.title}
                        </p>

                        {/* Footer */}
                        <div className="mt-3 flex items-center justify-between">
                          <div
                            className="w-6 h-6 rounded-full bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center"
                            title={ticket.assignee}
                          >
                            <span className="text-[9px] font-bold text-indigo-400">{assigneeInitials}</span>
                          </div>
                          <div className="flex items-center gap-1">
                            {ticket.repo_id && (() => {
                              const repo = repos.find((r) => r.id === ticket.repo_id);
                              return repo ? (
                                <Badge
                                  variant="outline"
                                  className="text-[10px] text-blue-400 border-blue-800/60 bg-blue-950/30 px-1.5 py-0"
                                >
                                  {repo.slug}
                                </Badge>
                              ) : null;
                            })()}
                            {ticket.pr_url && (
                              <Badge
                                variant="outline"
                                className="text-[10px] gap-1 text-green-400 border-green-800/60 bg-green-950/30 px-1.5 py-0"
                              >
                                <GitPullRequest className="w-2.5 h-2.5" /> PR
                              </Badge>
                            )}
                          </div>
                        </div>
                      </div>
                    </Link>
                  );
                })}

                {colTickets.length === 0 && (
                  <div className="border border-dashed border-[#1f1f1f] rounded-lg py-6 flex items-center justify-center">
                    <span className="text-[#555] text-xs">Empty</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Create ticket modal */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg bg-[#111] border-[#1f1f1f]">
          <DialogHeader>
            <DialogTitle className="text-white">New Ticket</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label htmlFor="title" className="text-[#888] text-xs">Title</Label>
              <Input
                id="title"
                placeholder="e.g. Fix login timeout bug"
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                className="mt-1.5 bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555]"
              />
            </div>
            <div>
              <Label htmlFor="desc" className="text-[#888] text-xs">Description</Label>
              <Textarea
                id="desc"
                placeholder="Context, acceptance criteria…"
                rows={3}
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                className="mt-1.5 bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555]"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-[#888] text-xs">Assignee</Label>
                <Select value={form.assignee} onValueChange={(v) => setForm((f) => ({ ...f, assignee: v ?? "" }))}>
                  <SelectTrigger className="mt-1.5 bg-[#0a0a0a] border-[#1f1f1f] text-white">
                    <SelectValue placeholder="Select…" />
                  </SelectTrigger>
                  <SelectContent className="bg-[#111] border-[#1f1f1f]">
                    {users.map((u) => (
                      <SelectItem key={u.github_username} value={u.github_username}>
                        {u.display_name}
                      </SelectItem>
                    ))}
                    {users.length === 0 && (
                      <>
                        <SelectItem value="alice">Alice</SelectItem>
                        <SelectItem value="bob">Bob</SelectItem>
                      </>
                    )}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-[#888] text-xs">Reporter</Label>
                <Select value={form.reporter} onValueChange={(v) => setForm((f) => ({ ...f, reporter: v ?? "" }))}>
                  <SelectTrigger className="mt-1.5 bg-[#0a0a0a] border-[#1f1f1f] text-white">
                    <SelectValue placeholder="Select…" />
                  </SelectTrigger>
                  <SelectContent className="bg-[#111] border-[#1f1f1f]">
                    {users.map((u) => (
                      <SelectItem key={u.github_username} value={u.github_username}>
                        {u.display_name}
                      </SelectItem>
                    ))}
                    {users.length === 0 && (
                      <>
                        <SelectItem value="alice">Alice</SelectItem>
                        <SelectItem value="bob">Bob</SelectItem>
                      </>
                    )}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {repos.length > 0 && (
              <div>
                <Label className="text-[#888] text-xs">Repository (optional)</Label>
                <Select value={form.repo_id} onValueChange={(v) => setForm((f) => ({ ...f, repo_id: v === "__none__" ? "" : (v ?? "") }))}>
                  <SelectTrigger className="mt-1.5 bg-[#0a0a0a] border-[#1f1f1f] text-white">
                    <SelectValue placeholder="No specific repo" />
                  </SelectTrigger>
                  <SelectContent className="bg-[#111] border-[#1f1f1f]">
                    <SelectItem value="__none__">No specific repo</SelectItem>
                    {repos.map((r) => (
                      <SelectItem key={r.id} value={r.id}>
                        {r.name} ({r.slug})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {error && <p className="text-red-400 text-sm">{error}</p>}
            <Button
              onClick={handleCreate}
              disabled={creating}
              className="w-full bg-indigo-600 hover:bg-indigo-500 text-white border-0"
            >
              {creating ? "Creating…" : "Create Ticket"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
