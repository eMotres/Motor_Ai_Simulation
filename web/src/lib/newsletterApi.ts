// Newsletter consent + in-app notices (backend: routes/newsletter.py).
// The Authorization header is added by the global fetch wrapper (apiAuth.ts).
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

/** Keep in step with newsletter.CONSENT_TEXT on the server (it stores its version). */
export const CONSENT_LINE = 'Send me product news and updates';
export const CONSENT_HELP = 'Product news and updates, at most 2 per month; unsubscribe in one click. We mail a confirmation link first.';

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { detail?: string }).detail || `HTTP ${r.status}`);
  return j as T;
}

export interface NewsletterStatus {
  status: 'none' | 'pending' | 'confirmed' | 'unsubscribed';
  subscribed: boolean; confirmed_at: number | null; outcome?: string;
}
export const getMyNewsletter = () => call<NewsletterStatus>('/api/newsletter/me');
export const setMyNewsletter = (subscribe: boolean) =>
  call<NewsletterStatus>('/api/newsletter/me', { method: 'POST', body: JSON.stringify({ subscribe }) });
export const confirmNewsletter = (token: string) =>
  call<{ ok: boolean }>('/api/newsletter/confirm', { method: 'POST', body: JSON.stringify({ token }) });
export const unsubscribeNewsletter = (token: string) =>
  call<{ ok: boolean }>('/api/newsletter/unsubscribe', { method: 'POST', body: JSON.stringify({ token }) });

export interface Notice {
  id: string; title: string; body: string; level: 'info' | 'warning' | 'important';
  created: number; read: boolean;
}
export const getNotices = () => call<{ notices: Notice[] }>('/api/notices');
export const markNoticeRead = (id: string) =>
  call<{ ok: boolean }>(`/api/notices/${encodeURIComponent(id)}/read`, { method: 'POST' });

/** Mailed newsletter links: `?nl_confirm=…` / `?unsubscribe=…`. */
export function pendingNewsletterLink(): { kind: 'confirm' | 'unsubscribe'; token: string } | null {
  try {
    const q = new URLSearchParams(window.location.search);
    const c = q.get('nl_confirm');
    if (c) return { kind: 'confirm', token: c };
    const u = q.get('unsubscribe');
    if (u) return { kind: 'unsubscribe', token: u };
  } catch { /* no window */ }
  return null;
}

export function clearNewsletterLink(): void {
  try {
    const u = new URL(window.location.href);
    u.searchParams.delete('nl_confirm');
    u.searchParams.delete('unsubscribe');
    window.history.replaceState(null, '', u.pathname + u.search + u.hash);
  } catch { /* ignore */ }
}

// ── admin ───────────────────────────────────────────────────────────────────
export interface CampaignStats {
  pending: number; sending: number; sent: number; failed: number; bounced: number;
  skipped: number; total: number; unsubscribes: number;
}
export interface Campaign {
  id: string; subject: string; body_md: string; roles: string[]; author: string;
  created: number; status: 'draft' | 'scheduled' | 'sending' | 'done' | 'cancelled';
  scheduled_at: number | null; started_at: number | null; finished_at: number | null;
  stats: CampaignStats;
}
export interface Subscriber {
  email: string; status: string; role: string; source: string; text_version: string;
  consent_at: number | null; confirmed_at: number | null; unsubscribed_at: number | null;
}
export interface AdminStatus {
  smtp: boolean; rate_per_min: number; daily_cap: number; sent_24h: number;
  confirmed: number; pending: number; unsubscribed: number;
}
export interface AdminNotice {
  id: string; title: string; body: string; level: string; emails: string[]; roles: string[];
  created: number; expires_at: number | null; active: boolean; read_count: number;
}
export interface Draft { subject: string; body_md: string; roles: string[] }

export const nlAdmin = {
  status: () => call<AdminStatus>('/api/newsletter/admin/status'),
  subscribers: () => call<{ subscribers: Subscriber[] }>('/api/newsletter/admin/subscribers'),
  csvUrl: `${API}/api/newsletter/admin/subscribers?format=csv`,
  campaigns: () => call<{ campaigns: Campaign[] }>('/api/newsletter/admin/campaigns'),
  create: (d: Draft) => call<Campaign>('/api/newsletter/admin/campaigns', { method: 'POST', body: JSON.stringify(d) }),
  update: (id: string, d: Partial<Draft>) =>
    call<Campaign>(`/api/newsletter/admin/campaigns/${id}`, { method: 'PUT', body: JSON.stringify(d) }),
  preview: (d: Draft) =>
    call<{ html: string; text: string; audience: number }>('/api/newsletter/admin/preview', { method: 'POST', body: JSON.stringify(d) }),
  test: (d: Draft) => call<{ ok: boolean; to: string }>('/api/newsletter/admin/test', { method: 'POST', body: JSON.stringify(d) }),
  send: (id: string, at: number | null) =>
    call<Campaign>(`/api/newsletter/admin/campaigns/${id}/send`, { method: 'POST', body: JSON.stringify({ at }) }),
  cancel: (id: string) => call<Campaign>(`/api/newsletter/admin/campaigns/${id}/cancel`, { method: 'POST' }),
  notices: () => call<{ notices: AdminNotice[] }>('/api/notices/admin'),
  postNotice: (n: { title: string; body: string; level: string; emails: string[]; roles: string[] }) =>
    call<AdminNotice>('/api/notices/admin', { method: 'POST', body: JSON.stringify(n) }),
  withdrawNotice: (id: string) => call<{ ok: boolean }>(`/api/notices/admin/${id}/withdraw`, { method: 'POST' }),
};

/** CSV export needs the auth header, so fetch it and save a blob. */
export async function downloadSubscribersCsv(): Promise<void> {
  const r = await fetch(nlAdmin.csvUrl);
  if (!r.ok) throw new Error(`CSV HTTP ${r.status}`);
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'subscribers.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
