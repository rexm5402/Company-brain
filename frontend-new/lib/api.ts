// Typed API client — all calls go to the FastAPI backend.
// In dev: backend runs on localhost:8077. In prod: same origin (FastAPI serves the static build).

export const BASE =
  typeof window !== "undefined" && window.location.port !== "3000"
    ? ""                           // prod: same origin
    : "http://localhost:8077";     // dev: explicit backend URL

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts?.headers ?? {}) },
    ...opts,
  });
  if (res.status === 401) {
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ---------- Types ----------

export type TicketStatus = "open" | "in_progress" | "in_review" | "done";
export type TicketSource = "manual" | "sentry" | "github_ci";

export interface Ticket {
  id: string;
  key: string;
  title: string;
  description: string;
  status: TicketStatus;
  assignee: string;
  reporter: string;
  source: TicketSource;
  details: string | null;
  channel: string | null;
  pr_url: string | null;
  repo_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Repo {
  id: string;
  name: string;
  owner: string;
  slug: string;
  has_token_override: boolean;
  created_at: string;
}

export interface Deployment {
  id: string;
  ticket_id: string;
  pr_url: string | null;
  deploy_url: string | null;
  branch: string | null;
  repo: string | null;
  status: string;
  created_at: string;
}

export interface PRComment {
  id: number;
  user: string | null;
  body: string | null;
  created_at: string | null;
  type: "review_comment" | "issue_comment";
  url: string | null;
}

export interface Message {
  id: number;
  author: string;
  text: string;
  is_bot: boolean;
  pr_url: string | null;
  ts: number;
  created_at: string;
}

export interface Notification {
  id: string;
  recipient: string;
  type: string;
  title: string;
  body: string | null;
  ticket_id: string | null;
  ticket_key: string | null;
  pr_url: string | null;
  read: boolean;
  created_at: string;
}

export interface WebhookEvent {
  id: string;
  source: string;
  event_type: string;
  external_id: string | null;
  ticket_id: string | null;
  error: string | null;
  processed_at: string;
}

export interface AuditRow {
  step: number;
  tool: string;
  success: boolean;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface Run {
  id: string;
  task: string;
  status: string;
  pr_url: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  model: string;
  created_at: string;
}

export interface User {
  id: string;
  github_username: string;
  display_name: string;
}

// ---------- Tickets ----------

export const api = {
  tickets: {
    list: () => req<Ticket[]>("/tickets"),
    get: (id: string) => req<Ticket>(`/tickets/${id}`),
    create: (body: { title: string; description: string; assignee: string; reporter: string; repo_id?: string }) =>
      req<Ticket>("/tickets", { method: "POST", body: JSON.stringify(body) }),
    start: (id: string) => req<Ticket>(`/tickets/${id}/start`, { method: "POST" }),
    complete: (id: string) => req<unknown>(`/tickets/${id}/complete`, { method: "POST" }),
    deployments: (id: string) => req<Deployment[]>(`/tickets/${id}/deployments`),
    prComments: (id: string) => req<PRComment[]>(`/tickets/${id}/pr-comments`),
  },

  repos: {
    list: () => req<Repo[]>("/repos"),
    create: (body: { name: string; owner: string; slug: string; github_token_override?: string }) =>
      req<Repo>("/repos", { method: "POST", body: JSON.stringify(body) }),
    index: (id: string) => req<{ chunks: number; repo_id: string }>(`/repos/${id}/index`, { method: "POST" }),
  },

  messages: {
    list: (ticketId: string) =>
      req<{ messages: Message[] }>(`/tickets/${ticketId}/messages`),
    send: (ticketId: string, user: string, text: string) =>
      req<{ ok: boolean }>(`/tickets/${ticketId}/send`, {
        method: "POST",
        body: JSON.stringify({ user, text }),
      }),
  },

  notifications: {
    list: (user: string, unreadOnly = false) =>
      req<Notification[]>(`/notifications?user=${encodeURIComponent(user)}&unread_only=${unreadOnly}`),
    count: (user: string) =>
      req<{ unread: number }>(`/notifications/count?user=${encodeURIComponent(user)}`),
    markRead: (id: string) =>
      req<{ ok: boolean }>(`/notifications/${id}/read`, { method: "POST" }),
    markAllRead: (user: string) =>
      req<{ marked: number }>(`/notifications/read-all?user=${encodeURIComponent(user)}`, { method: "POST" }),
  },

  webhookEvents: {
    list: (limit = 50) => req<WebhookEvent[]>(`/webhook-events?limit=${limit}`),
  },

  users: {
    list: () => req<User[]>("/users"),
  },

  runs: {
    list: () => req<Run[]>("/runs"),
    get: (id: string) => req<Run>(`/runs/${id}`),
    audit: (id: string) => req<AuditRow[]>(`/audit/${id}`),
    streamUrl: (id: string) => `${BASE}/runs/${id}/stream`,
  },

  watchdog: {
    simulateSentry: (body: {
      title: string;
      culprit: string;
      level: string;
      issue_id: string;
    }) => req<{ status: string }>("/webhooks/simulate/sentry", { method: "POST", body: JSON.stringify(body) }),
    simulateCI: (body: {
      workflow_name: string;
      conclusion: string;
      commit_message: string;
    }) => req<{ status: string }>("/webhooks/simulate/github_ci", { method: "POST", body: JSON.stringify(body) }),
  },
};
