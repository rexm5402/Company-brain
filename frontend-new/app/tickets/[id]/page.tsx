"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft, Bot, GitPullRequest, Send, CheckCircle2,
  Loader2, Zap, Shield, Database, Globe, XCircle
} from "lucide-react";
import { api, type Ticket, type Message, type AuditRow, type Deployment, type PRComment } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useCurrentUser } from "@/lib/useCurrentUser";

const STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  open: { bg: "bg-zinc-800", text: "text-zinc-300", label: "Open" },
  in_progress: { bg: "bg-blue-900/60", text: "text-blue-300", label: "In Progress" },
  in_review: { bg: "bg-amber-900/60", text: "text-amber-300", label: "In Review" },
  done: { bg: "bg-green-900/60", text: "text-green-300", label: "Done" },
};

const TOOL_ICON: Record<string, React.ReactNode> = {
  get_file_contents: <Database className="w-3 h-3 text-[#888]" />,
  list_repo_files: <Database className="w-3 h-3 text-[#888]" />,
  open_pull_request: <GitPullRequest className="w-3 h-3 text-green-400" />,
  self_review_code: <Shield className="w-3 h-3 text-blue-400" />,
  run_tests: <CheckCircle2 className="w-3 h-3 text-yellow-400" />,
  get_recent_errors: <Zap className="w-3 h-3 text-red-400" />,
  post_slack_message: <Globe className="w-3 h-3 text-indigo-400" />,
};

export default function TicketDetailPage() {
  const { id } = useParams<{ id: string }>();
  const user = useCurrentUser();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [auditRows, setAuditRows] = useState<AuditRow[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [prComments, setPrComments] = useState<PRComment[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  const loadTicket = () => api.tickets.get(id).then(setTicket).catch(() => {});
  const loadMessages = () =>
    api.messages.list(id).then((r) => setMessages(r.messages)).catch(() => {});

  useEffect(() => {
    loadTicket();
    loadMessages();
    api.tickets.deployments(id).then(setDeployments).catch(() => {});
    const t = setInterval(() => {
      loadTicket();
      loadMessages();
      api.tickets.deployments(id).then(setDeployments).catch(() => {});
    }, 3_000);
    return () => clearInterval(t);
  }, [id]);

  useEffect(() => {
    if (ticket?.status === "in_review" && ticket?.pr_url) {
      api.tickets.prComments(id).then(setPrComments).catch(() => {});
    }
  }, [ticket?.status, ticket?.pr_url, id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!activeRunId) return;
    const es = new EventSource(api.runs.streamUrl(activeRunId));
    sseRef.current = es;
    es.addEventListener("step", (e) => {
      const row = JSON.parse(e.data) as AuditRow;
      setAuditRows((prev) => {
        if (prev.find((r) => r.step === row.step)) return prev;
        return [...prev, row].sort((a, b) => a.step - b.step);
      });
    });
    es.addEventListener("done", () => { es.close(); sseRef.current = null; });
    es.addEventListener("error", () => { es.close(); sseRef.current = null; });
    return () => { es.close(); sseRef.current = null; };
  }, [activeRunId]);

  const handleStart = async () => {
    await api.tickets.start(id).catch(() => {});
    loadTicket();
  };

  const handleSend = async () => {
    if (!text.trim() || !user) return;
    setSending(true);
    try {
      await api.messages.send(id, user, text.trim());
      setText("");
      loadMessages();
    } finally {
      setSending(false);
    }
  };

  if (!ticket) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-[#555]" />
      </div>
    );
  }

  const statusStyle = STATUS_STYLE[ticket.status] ?? STATUS_STYLE.open;
  const isMember = user && (user === ticket.assignee || user === ticket.reporter);

  return (
    <div className="grid grid-cols-[1fr_300px] gap-5 h-[calc(100vh-3.5rem)]">
      {/* Left: ticket + chat */}
      <div className="flex flex-col min-h-0 overflow-hidden">
        <Link
          href="/tickets"
          className="flex items-center gap-1.5 text-xs text-[#888] hover:text-white mb-5 transition-colors w-fit"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Work Hub
        </Link>

        {/* Ticket header */}
        <div className="bg-[#111] border border-[#1f1f1f] rounded-xl p-5 mb-4 shrink-0">
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2 flex-wrap">
                <span className="text-[10px] font-mono text-[#555] tracking-widest">{ticket.key}</span>
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${statusStyle.bg} ${statusStyle.text}`}>
                  {statusStyle.label}
                </span>
                {ticket.source !== "manual" && (
                  <Badge variant="outline" className="text-[10px] border-[#1f1f1f] text-[#888]">
                    {ticket.source}
                  </Badge>
                )}
              </div>
              <h2 className="font-semibold text-base leading-snug text-white">{ticket.title}</h2>
              {ticket.description && (
                <p className="text-sm text-[#888] mt-1.5">{ticket.description}</p>
              )}
            </div>
            {ticket.pr_url && (
              <a href={ticket.pr_url} target="_blank" rel="noreferrer">
                <Badge className="gap-1 bg-green-950/60 text-green-400 border border-green-800/50 hover:bg-green-900/60 text-[10px]">
                  <GitPullRequest className="w-3 h-3" /> PR open
                </Badge>
              </a>
            )}
          </div>
          <Separator className="my-3 bg-[#1f1f1f]" />
          <div className="flex items-center gap-5 text-xs text-[#888]">
            <span>Assignee: <span className="text-white font-medium">{ticket.assignee}</span></span>
            <span>Reporter: <span className="text-white font-medium">{ticket.reporter}</span></span>
          </div>
          {ticket.status === "open" && (
            <Button
              size="sm"
              onClick={handleStart}
              className="mt-4 bg-indigo-600 hover:bg-indigo-500 text-white border-0 text-xs h-8"
            >
              Open Discussion Channel
            </Button>
          )}
        </div>

        {/* Staging deployments */}
        {deployments.length > 0 && (
          <div className="bg-[#111] border border-[#1f1f1f] rounded-xl p-4 mb-4 shrink-0">
            <p className="text-xs font-semibold text-[#888] mb-2">Staging Environments</p>
            {deployments.map((d) => (
              <div key={d.id} className="flex items-center justify-between text-xs">
                <span className="text-[#555]">{d.branch || "—"}</span>
                <span className={`px-2 py-0.5 rounded-full font-semibold ${d.status === "live" ? "bg-green-950/40 text-green-400" : "bg-zinc-800 text-zinc-400"}`}>{d.status}</span>
                {d.deploy_url && (
                  <a href={d.deploy_url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:underline">
                    View Staging →
                  </a>
                )}
              </div>
            ))}
          </div>
        )}

        {/* PR Comments (in_review) */}
        {ticket.status === "in_review" && ticket.pr_url && prComments.length > 0 && (
          <div className="bg-[#111] border border-[#1f1f1f] rounded-xl p-4 mb-4 shrink-0">
            <p className="text-xs font-semibold text-[#888] mb-2">PR Comments</p>
            <div className="space-y-2">
              {prComments.map((c) => (
                <div key={c.id} className="text-xs border-l-2 border-indigo-900/40 pl-3">
                  <p className="text-[#888] mb-0.5 font-semibold">{c.user || "unknown"}</p>
                  <p className="text-white whitespace-pre-wrap">{c.body}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* AI-enriched details */}
        {ticket.details && (
          <div className="bg-[#111]/50 border border-[#1f1f1f] rounded-xl p-4 mb-4 text-sm text-[#888] whitespace-pre-wrap shrink-0">
            {ticket.details}
          </div>
        )}

        {/* Chat */}
        {ticket.channel && (
          <>
            <ScrollArea className="flex-1 bg-[#111] border border-[#1f1f1f] rounded-xl p-4 min-h-0">
              <div className="space-y-4">
                {messages.map((m, i) => (
                  <div key={i} className={`flex gap-2.5 ${m.is_bot ? "" : "flex-row-reverse"}`}>
                    <div
                      className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 text-xs font-bold ${
                        m.is_bot
                          ? "bg-indigo-600/20 border border-indigo-500/30 text-indigo-400"
                          : "bg-[#1f1f1f] text-white"
                      }`}
                    >
                      {m.is_bot ? <Bot className="w-3.5 h-3.5" /> : m.author[0]?.toUpperCase()}
                    </div>
                    <div className={`max-w-[75%] ${m.is_bot ? "" : "items-end flex flex-col"}`}>
                      <p className="text-[10px] text-[#555] mb-1">
                        {m.author} · {new Date(m.created_at).toLocaleTimeString()}
                      </p>
                      <div
                        className={`text-sm rounded-xl px-3.5 py-2.5 whitespace-pre-wrap ${
                          m.is_bot
                            ? "bg-indigo-950/30 border border-indigo-900/40 text-white"
                            : "bg-[#1f1f1f] border border-[#2a2a2a] text-white"
                        }`}
                      >
                        {m.text}
                        {m.pr_url && (
                          <a
                            href={m.pr_url}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-1 mt-1.5 text-green-400 hover:underline text-xs"
                          >
                            <GitPullRequest className="w-3 h-3" /> View PR
                          </a>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            </ScrollArea>

            {isMember && ticket.status !== "done" && (
              <div className="flex gap-2 mt-3 shrink-0">
                <Input
                  placeholder={`Message as ${user}…`}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
                  className="flex-1 bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555]"
                />
                <Button
                  onClick={handleSend}
                  disabled={sending || !text.trim()}
                  size="icon"
                  className="bg-indigo-600 hover:bg-indigo-500 text-white border-0 shrink-0"
                >
                  {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                </Button>
              </div>
            )}
            {!isMember && ticket.status !== "done" && (
              <p className="text-xs text-[#555] mt-2 text-center shrink-0">
                Only {ticket.assignee} and {ticket.reporter} can post in this channel.
              </p>
            )}
          </>
        )}
      </div>

      {/* Right: agent activity panel */}
      <div className="flex flex-col min-h-0">
        <div className="bg-[#111] border border-[#1f1f1f] rounded-xl flex flex-col h-full">
          <div className="px-4 py-3 border-b border-[#1f1f1f] flex items-center gap-2 shrink-0">
            <div className="w-5 h-5 rounded bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center">
              <Bot className="w-3 h-3 text-indigo-400" />
            </div>
            <span className="font-semibold text-sm text-white">Agent Activity</span>
          </div>
          <ScrollArea className="flex-1 p-3">
            {auditRows.length === 0 ? (
              <p className="text-xs text-[#555] text-center mt-8 leading-relaxed">
                Live tool calls appear here when the agent runs.
              </p>
            ) : (
              <div className="space-y-1.5">
                {auditRows.map((r) => (
                  <div
                    key={r.step}
                    className={`flex items-start gap-2 text-xs rounded-lg p-2.5 border ${
                      r.success
                        ? "bg-green-950/20 border-green-900/30"
                        : "bg-red-950/20 border-red-900/30"
                    }`}
                  >
                    <span className="text-[#555] shrink-0 mt-0.5 tabular-nums">#{r.step}</span>
                    <span className="shrink-0 mt-0.5">
                      {TOOL_ICON[r.tool] ?? <Bot className="w-3 h-3 text-[#888]" />}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono font-medium text-white truncate">{r.tool}</span>
                        {r.success ? (
                          <CheckCircle2 className="w-3 h-3 text-green-400 shrink-0" />
                        ) : (
                          <XCircle className="w-3 h-3 text-red-400 shrink-0" />
                        )}
                      </div>
                      {r.error && <p className="text-red-400 mt-0.5 truncate text-[10px]">{r.error}</p>}
                      {r.latency_ms && (
                        <p className="text-[#555] text-[10px]">{r.latency_ms}ms</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}
