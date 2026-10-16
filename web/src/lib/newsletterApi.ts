// In-app product notices and the links of mails sent by the RETIRED newsletter (backend: routes/newsletter.py).
// The newsletter opt-in itself is gone: the only place to choose e-mail is the Communication preferences page (lib/commPrefsApi.ts).
// The Authorization header is added by the global fetch wrapper (apiAuth.ts).
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { detail?: string }).detail || `HTTP ${r.status}`);
  return j as T;
}

/** The one-click unsubscribe of an OLD newsletter mail; the server also switches every new topic off. */
export const unsubscribeNewsletter = (token: string) =>
  call<{ ok: boolean }>('/api/newsletter/unsubscribe', { method: 'POST', body: JSON.stringify({ token }) });

export interface Notice {
  id: string; title: string; body: string; level: 'info' | 'warning' | 'important';
  created: number; read: boolean;
}
export const getNotices = () => call<{ notices: Notice[] }>('/api/notices');
export const markNoticeRead = (id: string) =>
  call<{ ok: boolean }>(`/api/notices/${encodeURIComponent(id)}/read`, { method: 'POST' });

/** Links of old newsletter mails: `?unsubscribe=…` (works) and `?nl_confirm=…` (the opt-in is retired). */
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

// ── admin (read-only history of the old newsletter + in-app notices) ─────────────
export interface CampaignStats {
  pending: number; sending: number; sent: number; failed: number; bounced: number;
  skipped: number; total: number; unsubscribes: number;
}
export interface Campaign {
  id: string; subject: string; roles: string[]; author: string;
  created: number; status: string;
  scheduled_at: number | null; started_at: number | null; finished_at: number | null;
  stats: CampaignStats;
}
export interface LegacyStatus { confirmed: number; pending: number; unsubscribed: number }
export interface AdminNotice {
  id: string; title: string; body: string; level: string; emails: string[]; roles: string[];
  created: number; expires_at: number | null; active: boolean; read_count: number;
}

export const nlAdmin = {
  status: () => call<LegacyStatus>('/api/newsletter/admin/status'),
  csvUrl: `${API}/api/newsletter/admin/subscribers?format=csv`,
  campaigns: () => call<{ campaigns: Campaign[] }>('/api/newsletter/admin/campaigns'),
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
  a.download = 'old-newsletter-subscribers.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
