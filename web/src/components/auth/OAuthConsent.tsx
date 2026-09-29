// OAuth consent page for MCP connectors (Stage 2, docs/MCP_2026-09-28.md).
// /oauth/authorize (API) redirects here with ?request=<id>; the signed-in owner
// sees the client name + scopes and approves or denies.  The API answers with
// the client's redirect URL (code or access_denied) and the browser goes there.
import React, { useEffect, useState } from 'react';
import { Box, Button, Paper, Typography, Chip, Alert, CircularProgress, Checkbox } from '@mui/material';
import { useAuth } from '../../contexts/AuthContext';
import { getStoredToken } from '../../lib/localAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

const SCOPE_TEXT: Record<string, string> = {
  'catalog:read': 'Read the materials and dies catalog',
  'machines:read': 'Read your machines and their saved results',
  'designs:write': 'Create draft machines in your workspace (never your saved motors or open machine)',
  simulate: 'Queue simulations of those drafts in your job queue (daily fair-use limit)',
};
const WRITE_SCOPES = ['designs:write', 'simulate'];

interface Req {
  client_name: string; client_uri?: string | null; redirect_host: string; scopes: string[]; account: string;
  scope_descriptions?: Record<string, string>;
  /** Resumed from a sign-up confirmation link (bound to this account). */
  started_by_sign_up?: boolean;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken();
  const r = await fetch(`${API}/api/oauth/requests/${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = ((await r.json()) as { detail?: string }).detail ?? msg; } catch { /* keep */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const OAuthConsent: React.FC = () => {
  const { user, resolved, signIn } = useAuth();
  const rid = new URLSearchParams(window.location.search).get('request') ?? '';
  const [req, setReq] = useState<Req | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Stage 3: the owner may untick scopes — the grant then carries only these.
  const [picked, setPicked] = useState<string[]>([]);

  useEffect(() => {
    if (!user || !rid) return;
    call<Req>(encodeURIComponent(rid))
      .then((r) => { setReq(r); setPicked(r.scopes); })
      .catch((e) => setErr(String(e.message ?? e)));
  }, [user, rid]);

  const decide = async (approve: boolean) => {
    setBusy(true); setErr(null);
    try {
      const j = await call<{ redirect: string }>(encodeURIComponent(rid), {
        method: 'POST', body: JSON.stringify(approve ? { approve, scopes: picked } : { approve }),
      });
      window.location.assign(j.redirect);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };

  let body: React.ReactNode;
  if (!rid) body = <Alert severity="error">Missing authorization request.</Alert>;
  else if (!resolved && !user) body = <CircularProgress size={20} />;
  else if (!user) {
    // Sign-up happens HERE, in the browser window the AI app opened — never
    // in the chat.  The consent path rides through the sign-up and its
    // confirmation mail, so the user lands back on this page and continues.
    const returnTo = `/agent-consent?request=${encodeURIComponent(rid)}`;
    body = (
      <>
        <Typography sx={{ mb: 2, fontSize: 14 }}>
          Sign in to connect an AI app to your account — or create an account first.
        </Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button variant="contained" onClick={() => { void signIn({ returnTo }); }}>Sign in</Button>
          <Button variant="outlined" onClick={() => { void signIn({ mode: 'register', returnTo }); }}>
            Create account
          </Button>
        </Box>
        <Typography sx={{ fontSize: 11, color: 'var(--text-2)', mt: 2 }}>
          A new e-mail account works after you open the confirmation link we send; it brings you back here.
        </Typography>
      </>
    );
  } else if (err) body = <Alert severity="error">{err}</Alert>;
  else if (!req) body = <CircularProgress size={20} />;
  else {
    body = (
      <>
        <Typography sx={{ fontSize: 15, mb: 1 }}>
          <b>{req.client_name}</b> wants{req.scopes.some((s) => WRITE_SCOPES.includes(s)) ? ' ' : ' read-only '}
          access to your eMotres account <b>{req.account}</b>.
        </Typography>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)', mb: 1.5 }}>
          It will return to <b>{req.redirect_host}</b>. Untick what you do not want to allow.
        </Typography>
        {req.started_by_sign_up && (
          <Alert severity="warning" sx={{ mb: 1.5, fontSize: 12 }}>
            You reached this page from the confirmation e-mail of your new account. Allow only if you
            started connecting <b>{req.client_name}</b> ({req.redirect_host}) yourself just now.
          </Alert>
        )}
        <Box sx={{ mb: 2 }}>
          {req.scopes.map((s) => (
            <Box key={s} sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 0.5 }}>
              <Checkbox size="small" sx={{ p: 0.25 }} checked={picked.includes(s)}
                onChange={() => setPicked((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]))} />
              <Chip size="small" label={s} sx={{ fontSize: 11 }} />
              <Typography sx={{ fontSize: 13 }}>{req.scope_descriptions?.[s] ?? SCOPE_TEXT[s] ?? s}</Typography>
            </Box>
          ))}
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button variant="contained" disabled={busy || !picked.length} onClick={() => { void decide(true); }}>Allow</Button>
          <Button variant="outlined" disabled={busy} onClick={() => { void decide(false); }}>Deny</Button>
        </Box>
        <Typography sx={{ fontSize: 11, color: 'var(--text-2)', mt: 2 }}>
          Disconnect any time under Access for agents.
        </Typography>
      </>
    );
  }

  return (
    <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      bgcolor: 'var(--bg-0, #111)', p: 2 }}>
      <Paper sx={{ p: 3, maxWidth: 440, width: '100%' }}>
        <Typography variant="h6" sx={{ fontSize: 17, mb: 2 }}>Connect an AI app</Typography>
        {body}
      </Paper>
    </Box>
  );
};

export default OAuthConsent;
