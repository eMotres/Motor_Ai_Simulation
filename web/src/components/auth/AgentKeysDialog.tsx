// "Access for agents" — the account's own MCP keys (GET/POST/DELETE /api/agent_keys).
// A key is shown ONCE on creation; the server keeps only its hash.
// docs/MCP_2026-09-28.md
import React, { useCallback, useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, Table, TableBody,
  TableCell, TableHead, TableRow, Typography, TextField, Box, Chip, Checkbox,
  FormControlLabel, Alert, CircularProgress, Tooltip,
} from '@mui/material';
import { getStoredToken } from '../../lib/localAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

interface GrantRow {
  id: string; client_name: string; scopes: string[];
  created_at: number; last_used_at: number | null; revoked_at: number | null; active: boolean;
}

interface KeyRow {
  id: string; name: string; prefix: string; scopes: string[];
  created_at: number; last_used_at: number | null; revoked_at: number | null; active: boolean;
}

const when = (s?: number | null) =>
  (s ? new Date(s * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—');

async function call<T>(path: string, init?: RequestInit, base = '/api/agent_keys'): Promise<T> {
  const token = getStoredToken();
  const r = await fetch(`${API}${base}${path}`, {
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

const AgentKeysDialog: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const [rows, setRows] = useState<KeyRow[]>([]);
  const [grants, setGrants] = useState<GrantRow[]>([]);
  const [allScopes, setAllScopes] = useState<string[]>([]);
  const [scopeText, setScopeText] = useState<Record<string, string>>({});
  const [simPerDay, setSimPerDay] = useState<number | null | undefined>(undefined);
  const [scopes, setScopes] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [fresh, setFresh] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true); setErr(null);
    try {
      const j = await call<{ keys: KeyRow[]; scopes: string[]; default_scopes?: string[];
        scope_descriptions?: Record<string, string>;
        limits?: { simulations_per_day?: number | null } }>('');
      setRows(j.keys); setAllScopes(j.scopes);
      setScopeText(j.scope_descriptions ?? {});
      setSimPerDay(j.limits?.simulations_per_day);
      const g = await call<{ grants: GrantRow[] }>('', undefined, '/api/oauth/grants');
      setGrants(g.grants);
      // read-only by default: the owner ticks designs:write / simulate himself
      setScopes((s) => (s.length ? s : (j.default_scopes ?? j.scopes)));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }, []);
  useEffect(() => { if (open) { setFresh(null); void load(); } }, [open, load]);

  const create = async () => {
    setBusy(true); setErr(null);
    try {
      const j = await call<{ token: string }>('', { method: 'POST', body: JSON.stringify({ name, scopes }) });
      setFresh(j.token); setName('');
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const revoke = async (id: string) => {
    setBusy(true);
    try { await call(`/${id}`, { method: 'DELETE' }); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const disconnect = async (id: string) => {
    setBusy(true);
    try { await call(`/${id}`, { method: 'DELETE' }, '/api/oauth/grants'); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const toggle = (s: string) =>
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem' }}>
        Access for agents
        <Tooltip arrow title="Keys let an AI agent (Claude, ChatGPT) work in your account through https://aerostator.com/mcp: read the catalog and results; with designs:write / simulate it creates DRAFT machines (🤖 in Motors) and queues their runs — never your saved motors or open machine.">
          <Typography component="span" sx={{ ml: 1, fontSize: 12, color: 'var(--text-2)', cursor: 'help' }}>ⓘ</Typography>
        </Tooltip>
      </DialogTitle>
      <DialogContent sx={{ pt: '8px !important' }}>
        {err && <Alert severity="error" sx={{ mb: 1, fontSize: 12 }}>{err}</Alert>}
        {fresh && (
          <Alert severity="success" sx={{ mb: 1, fontSize: 12, wordBreak: 'break-all' }}
            action={<Button size="small" onClick={() => { void navigator.clipboard?.writeText(fresh); }}>Copy</Button>}>
            Copy now — shown once: <code>{fresh}</code>
          </Alert>
        )}
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1, flexWrap: 'wrap' }}>
          <TextField size="small" label="Key name" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Claude Desktop" sx={{ minWidth: 220 }} />
          {allScopes.map((s) => (
            <FormControlLabel key={s} sx={{ '& .MuiFormControlLabel-label': { fontSize: 12 } }}
              control={<Checkbox size="small" checked={scopes.includes(s)} onChange={() => toggle(s)} />}
              label={<Tooltip arrow title={(scopeText[s] ?? s) + (s === 'simulate' ? (simPerDay === null ? ' — unlimited for this account' : simPerDay !== undefined ? ` — ${simPerDay} per day` : '') : '')}><span>{s}</span></Tooltip>} />
          ))}
          <Button size="small" variant="contained" disabled={busy || !scopes.length} onClick={() => { void create(); }}>
            Create key
          </Button>
          {busy && <CircularProgress size={16} />}
        </Box>
        <Table size="small" sx={{ '& td, & th': { fontSize: 12 } }}>
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell><TableCell>Key</TableCell><TableCell>Scopes</TableCell>
              <TableCell>Created</TableCell><TableCell>Last used</TableCell><TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.id} sx={{ opacity: r.active ? 1 : 0.5 }}>
                <TableCell>{r.name}</TableCell>
                <TableCell><code>{r.prefix}…</code></TableCell>
                <TableCell>{r.scopes.map((s) => <Chip key={s} size="small" label={s} sx={{ mr: 0.5, fontSize: 10 }} />)}</TableCell>
                <TableCell>{when(r.created_at)}</TableCell>
                <TableCell>{when(r.last_used_at)}</TableCell>
                <TableCell align="right">
                  {r.active
                    ? <Button size="small" color="error" onClick={() => { void revoke(r.id); }}>Revoke</Button>
                    : <Typography variant="caption">revoked</Typography>}
                </TableCell>
              </TableRow>
            ))}
            {!rows.length && (
              <TableRow><TableCell colSpan={6}><Typography variant="caption">No keys yet.</Typography></TableCell></TableRow>
            )}
          </TableBody>
        </Table>
        <Typography sx={{ fontSize: 13, fontWeight: 600, mt: 2, mb: 0.5 }}>
          Connected apps
          <Tooltip arrow title="Apps you approved through Sign in (claude.ai, ChatGPT connectors). Disconnect revokes their tokens.">
            <Typography component="span" sx={{ ml: 1, fontSize: 12, color: 'var(--text-2)', cursor: 'help' }}>ⓘ</Typography>
          </Tooltip>
        </Typography>
        <Table size="small" sx={{ '& td, & th': { fontSize: 12 } }}>
          <TableHead>
            <TableRow>
              <TableCell>App</TableCell><TableCell>Scopes</TableCell>
              <TableCell>Connected</TableCell><TableCell>Last used</TableCell><TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {grants.map((g) => (
              <TableRow key={g.id} sx={{ opacity: g.active ? 1 : 0.5 }}>
                <TableCell>{g.client_name}</TableCell>
                <TableCell>{g.scopes.map((s) => <Chip key={s} size="small" label={s} sx={{ mr: 0.5, fontSize: 10 }} />)}</TableCell>
                <TableCell>{when(g.created_at)}</TableCell>
                <TableCell>{when(g.last_used_at)}</TableCell>
                <TableCell align="right">
                  {g.active
                    ? <Button size="small" color="error" onClick={() => { void disconnect(g.id); }}>Disconnect</Button>
                    : <Typography variant="caption">{g.revoked_at ? 'revoked' : 'expired'}</Typography>}
                </TableCell>
              </TableRow>
            ))}
            {!grants.length && (
              <TableRow><TableCell colSpan={5}><Typography variant="caption">No connected apps.</Typography></TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
};

export default AgentKeysDialog;
