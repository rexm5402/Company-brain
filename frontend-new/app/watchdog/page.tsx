"use client";

import { useEffect, useState } from "react";
import { AlertCircle, GitPullRequest, CheckCircle2, XCircle, Zap } from "lucide-react";
import { api, type WebhookEvent } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const SOURCE_ICON: Record<string, React.ReactNode> = {
  sentry: <AlertCircle className="w-4 h-4 text-red-400" />,
  github_ci: <GitPullRequest className="w-4 h-4 text-orange-400" />,
};

export default function WatchdogPage() {
  const [events, setEvents] = useState<WebhookEvent[]>([]);
  const [simSentry, setSimSentry] = useState({
    title: "TypeError: Cannot read property 'id' of null",
    culprit: "app/auth.py in get_user",
    level: "error",
    issue_id: `sim-${Date.now()}`,
  });
  const [simCI, setSimCI] = useState({
    workflow_name: "CI",
    conclusion: "failure",
    commit_message: "fix: update auth middleware",
  });
  const [firing, setFiring] = useState<string | null>(null);
  const [fired, setFired] = useState<string | null>(null);

  const load = () => api.webhookEvents.list(50).then(setEvents).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 5_000);
    return () => clearInterval(t);
  }, []);

  const fireSentry = async () => {
    setFiring("sentry");
    try {
      await api.watchdog.simulateSentry({ ...simSentry, issue_id: `sim-${Date.now()}` });
      setFired("sentry");
      setTimeout(() => setFired(null), 3000);
      setTimeout(load, 1500);
    } finally {
      setFiring(null);
    }
  };

  const fireCI = async () => {
    setFiring("ci");
    try {
      await api.watchdog.simulateCI(simCI);
      setFired("ci");
      setTimeout(() => setFired(null), 3000);
      setTimeout(load, 1500);
    } finally {
      setFiring(null);
    }
  };

  return (
    <div className="space-y-7">
      {/* Page header */}
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-7 h-7 rounded-lg bg-yellow-500/10 border border-yellow-500/20 flex items-center justify-center">
            <Zap className="w-4 h-4 text-yellow-400" />
          </div>
          <h1 className="text-xl font-semibold text-white tracking-tight">Watchdog</h1>
        </div>
        <p className="text-[#888] text-sm mt-1">
          Production signals &rarr; auto-tickets. Real-time feed of inbound Sentry and GitHub CI events.
        </p>
      </div>

      {/* Simulation panel */}
      <div className="grid grid-cols-2 gap-4">
        {/* Sentry simulation */}
        <div className="bg-[#111] border border-[#1f1f1f] rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span className="font-semibold text-sm text-white">Simulate Sentry Error</span>
          </div>
          <div className="space-y-3">
            <div>
              <Label className="text-[10px] text-[#888] uppercase tracking-wider">Error title</Label>
              <Input
                className="mt-1.5 text-xs bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555] h-8"
                value={simSentry.title}
                onChange={(e) => setSimSentry((s) => ({ ...s, title: e.target.value }))}
              />
            </div>
            <div>
              <Label className="text-[10px] text-[#888] uppercase tracking-wider">Culprit</Label>
              <Input
                className="mt-1.5 text-xs bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555] h-8"
                value={simSentry.culprit}
                onChange={(e) => setSimSentry((s) => ({ ...s, culprit: e.target.value }))}
              />
            </div>
          </div>
          <Button
            size="sm"
            onClick={fireSentry}
            disabled={firing === "sentry"}
            className="w-full bg-red-700 hover:bg-red-600 text-white border-0 text-xs h-8"
          >
            {firing === "sentry" ? "Firing…" : fired === "sentry" ? "✓ Fired!" : "Fire Sentry Alert"}
          </Button>
        </div>

        {/* CI simulation */}
        <div className="bg-[#111] border border-[#1f1f1f] rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-2">
            <GitPullRequest className="w-4 h-4 text-orange-400 shrink-0" />
            <span className="font-semibold text-sm text-white">Simulate CI Failure</span>
          </div>
          <div className="space-y-3">
            <div>
              <Label className="text-[10px] text-[#888] uppercase tracking-wider">Workflow name</Label>
              <Input
                className="mt-1.5 text-xs bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555] h-8"
                value={simCI.workflow_name}
                onChange={(e) => setSimCI((s) => ({ ...s, workflow_name: e.target.value }))}
              />
            </div>
            <div>
              <Label className="text-[10px] text-[#888] uppercase tracking-wider">Commit message</Label>
              <Input
                className="mt-1.5 text-xs bg-[#0a0a0a] border-[#1f1f1f] text-white placeholder:text-[#555] h-8"
                value={simCI.commit_message}
                onChange={(e) => setSimCI((s) => ({ ...s, commit_message: e.target.value }))}
              />
            </div>
          </div>
          <Button
            size="sm"
            onClick={fireCI}
            disabled={firing === "ci"}
            className="w-full bg-orange-700 hover:bg-orange-600 text-white border-0 text-xs h-8"
          >
            {firing === "ci" ? "Firing…" : fired === "ci" ? "✓ Fired!" : "Fire CI Failure"}
          </Button>
        </div>
      </div>

      {/* Event feed */}
      <div>
        <h2 className="text-[10px] font-semibold text-[#555] mb-3 uppercase tracking-widest">
          Recent Events
        </h2>
        {events.length === 0 ? (
          <div className="border border-dashed border-[#1f1f1f] rounded-xl py-14 text-center">
            <p className="text-sm text-[#555]">
              No webhook events yet. Fire a simulation above or configure real webhooks.
            </p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {events.map((ev) => {
              const borderColor = ev.source === "sentry" ? "#7f1d1d" : "#7c2d12";
              return (
                <div
                  key={ev.id}
                  className="flex items-start gap-3 bg-[#111] border border-[#1f1f1f] rounded-lg px-4 py-3"
                  style={{ borderLeft: `3px solid ${borderColor}` }}
                >
                  <span className="mt-0.5 shrink-0">
                    {SOURCE_ICON[ev.source] ?? <Zap className="w-4 h-4 text-[#888]" />}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge
                        variant="outline"
                        className={`text-[10px] border-[#2a2a2a] ${
                          ev.source === "sentry" ? "text-red-400" : "text-orange-400"
                        }`}
                      >
                        {ev.source}
                      </Badge>
                      <span className="text-xs text-[#888] font-mono">{ev.event_type}</span>
                      {ev.external_id && (
                        <span className="text-xs text-[#555] truncate max-w-[200px]">{ev.external_id}</span>
                      )}
                    </div>
                    <div className="mt-1.5 flex items-center gap-4 text-xs text-[#888]">
                      {ev.ticket_id ? (
                        <span className="flex items-center gap-1 text-green-400">
                          <CheckCircle2 className="w-3 h-3" /> Ticket created
                        </span>
                      ) : ev.error ? (
                        <span className="flex items-center gap-1 text-red-400">
                          <XCircle className="w-3 h-3" /> {ev.error}
                        </span>
                      ) : (
                        <span className="text-[#555]">Processed (no ticket)</span>
                      )}
                      <span className="text-[#555]">{new Date(ev.processed_at).toLocaleString()}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
