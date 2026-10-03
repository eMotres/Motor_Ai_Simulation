/**
 * Admin · Motors access — per-die visibility: private (default), public
 * (every signed-in account, including MCP agents of any user), or selected
 * clients (named accounts, read-only). One compact table; click a row for
 * the three-way choice + client picker in a drawer.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Paper, Chip, Button, CircularProgress, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Drawer, RadioGroup, FormControlLabel,
  Radio, Checkbox, Divider, Autocomplete, Dialog, DialogTitle, DialogContent,
  DialogActions,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import HelpTip from '../../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

type Visibility = 'private' | 'public' | 'selected';
const VIS_LABEL: Record<Visibility, string> = { private: 'Private', public: 'Public', selected: 'Selected clients' };
const VIS_COLOR: Record<Visibility, string> = { private: 'var(--text-3)', public: '#4ade80', selected: '#60a5fa' };

type Source = 'open' | 'private';
const SOURCE_HELP = 'Open = public GitHub repository (Apache-2.0): publishing pushes there at once and is public immediately. Private = our private repository. Only MOTRES reference data; customer dies never go to git.';

type MoveStatus = 'prepared' | 'running' | 'incomplete' | 'pending' | 'done' | 'rolled_back';
interface PendingMove {
  id: string; status: MoveStatus; from: Source; to: Source; branch: string; at: string;
  prs: { repo: string; url: string; opened?: boolean }[]; failed_step?: string | null; error?: string | null;
}
interface DieRow {
  name: string; stator_diameter: number | null; configs: number; duties: number;
  visibility: Visibility; clients: string[]; used_by: number;
  source?: Source; source_movable?: boolean; source_clash?: boolean; source_error?: string | null;
  source_pending?: PendingMove | null;
}
interface Blocker { kind: string; name: string; reason: string }
interface MovePreview {
  die: string; from: Source; to: Source; warning: string; notice: string; public_push: boolean;
  snapshot: string; blockers: Blocker[];
  manifest: {
    files: { path: string; bytes: number; sha256: string }[]; total_bytes: number;
    geometry: Record<string, unknown>; configs: { name: string; role: string | null; duties: string[] }[];
    materials: string[]; devices: string[]; results: string[];
  };
}

const sourceLabel = (d: DieRow) => {
  const m = d.source_pending;
  if (m) return m.status === 'pending' ? `Pending ${m.to === 'open' ? 'publish' : 'withdraw'}` : 'Move incomplete';
  return d.source === 'open' ? 'Open' : 'Private';
};
const sourceColor = (d: DieRow) => (d.source_pending ? (d.source_pending.status === 'pending' ? '#fbbf24' : '#f87171') : d.source === 'open' ? '#4ade80' : 'var(--text-3)');
const detailMsg = (j: { detail?: unknown }, status: number) => {
  const d = j.detail as { message?: string } | string | undefined;
  return typeof d === 'string' ? d : d?.message ?? `HTTP ${status}`;
};
interface RegistryUser { email: string }

const statusLabel = (d: DieRow) =>
  d.visibility === 'public' ? 'Public' : d.visibility === 'selected' ? `${d.clients.length} client${d.clients.length === 1 ? '' : 's'}` : 'Private';

const MotorsAccessSection: React.FC = () => {
  const [dies, setDies] = useState<DieRow[]>([]);
  const [users, setUsers] = useState<RegistryUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [selChecked, setSelChecked] = useState<Set<string>>(new Set());
  const [drawerFor, setDrawerFor] = useState<DieRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [d, u] = await Promise.all([
        fetch(`${API}/api/admin/dies`).then((r) => { if (!r.ok) throw new Error(`dies HTTP ${r.status}`); return r.json(); }),
        fetch(`${API}/api/auth/users`).then((r) => (r.ok ? r.json() : { users: [] })).catch(() => ({ users: [] })),
      ]);
      setDies(d.dies ?? []); setUsers(u.users ?? []);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return dies.filter((d) => !q || d.name.toLowerCase().includes(q));
  }, [dies, search]);

  const setAccess = async (die: string, visibility: Visibility, clients: string[]) => {
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/admin/dies/${encodeURIComponent(die)}/access`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visibility, clients }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setNotice(`✗ ${j.detail ?? `HTTP ${r.status}`}`); return; }
      setDies((ds) => ds.map((d) => (d.name === die ? { ...d, visibility, clients } : d)));
      setNotice(`✓ ${die}: ${VIS_LABEL[visibility]}`);
    } catch (e) { setNotice(`✗ ${e instanceof Error ? e.message : String(e)}`); } finally { setBusy(false); }
  };

  const bulkSet = async (visibility: 'public' | 'private') => {
    if (!selChecked.size) return;
    setBusy(true);
    try {
      await Promise.all([...selChecked].map((die) =>
        fetch(`${API}/api/admin/dies/${encodeURIComponent(die)}/access`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ visibility, clients: [] }),
        })));
      setDies((ds) => ds.map((d) => (selChecked.has(d.name) ? { ...d, visibility, clients: [] } : d)));
      setNotice(`✓ ${selChecked.size} die(s) set to ${VIS_LABEL[visibility]}`);
      setSelChecked(new Set());
    } finally { setBusy(false); }
  };

  const reconcile = async () => {
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/admin/dies/source/reconcile`, { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setNotice(`✗ ${detailMsg(j, r.status)}`); return; }
      setNotice(`✓ ${(j.completed ?? []).length} merged move(s) completed`);
      await load();
    } finally { setBusy(false); }
  };

  const toggleChecked = (name: string) => setSelChecked((s) => {
    const n = new Set(s); if (n.has(name)) n.delete(name); else n.add(name); return n;
  });

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>Motors access</Typography>
        <HelpTip title="Who may see each die beyond its owner: private (default), public (every signed-in account, including MCP agents — list_machines/check_fit respect this too), or a named list of clients. Read-only for recipients." />
        {notice && <Typography sx={{ fontSize: 11, color: notice.startsWith('✓') ? '#34d399' : '#f87171' }}>{notice}</Typography>}
        <Box sx={{ flex: 1 }} />
        <TextField size="small" placeholder="Search dies" value={search} onChange={(e) => setSearch(e.target.value)}
          sx={{ minWidth: 180, '& .MuiInputBase-input': { fontSize: 12.5, py: 0.6 } }} />
        {selChecked.size > 0 && (
          <>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{selChecked.size} selected</Typography>
            <Button size="small" disabled={busy} onClick={() => void bulkSet('public')} sx={{ textTransform: 'none', fontSize: 11, color: '#4ade80' }}>Make public</Button>
            <Button size="small" disabled={busy} onClick={() => void bulkSet('private')} sx={{ textTransform: 'none', fontSize: 11, color: 'var(--text-3)' }}>Make private</Button>
          </>
        )}
        <Button size="small" disabled={busy} onClick={() => void reconcile()} sx={{ textTransform: 'none', fontSize: 11, color: 'var(--text-2)' }}>Check merged PRs</Button>
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} onClick={() => void load()} disabled={loading}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>Refresh</Button>
      </Box>

      {loading && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, color: 'var(--text-3)', py: 4 }}>
          <CircularProgress size={18} /> <Typography sx={{ fontSize: 13 }}>Loading dies…</Typography>
        </Box>
      )}
      {err && !loading && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 1 }}>{err}</Typography>}

      {!loading && !err && (
        <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
          <Table size="small" sx={{
            '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
            '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
          }}>
            <TableHead>
              <TableRow>
                <TableCell padding="checkbox" />
                <TableCell>Die</TableCell>
                <TableCell align="right">Ø mm</TableCell>
                <TableCell align="center">Status</TableCell>
                <TableCell align="center">
                  <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>Source <HelpTip title={SOURCE_HELP} /></Box>
                </TableCell>
                <TableCell align="right">Used by</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((d) => (
                <TableRow key={d.name} hover sx={{ cursor: 'pointer' }}>
                  <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                    <Checkbox size="small" checked={selChecked.has(d.name)} onChange={() => toggleChecked(d.name)} />
                  </TableCell>
                  <TableCell onClick={() => setDrawerFor(d)}>
                    <Typography sx={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 600 }}>{d.name}</Typography>
                  </TableCell>
                  <TableCell align="right" onClick={() => setDrawerFor(d)} sx={{ color: 'var(--text-2)' }}>{d.stator_diameter ?? '—'}</TableCell>
                  <TableCell align="center" onClick={() => setDrawerFor(d)}>
                    <Chip size="small" label={statusLabel(d)}
                      sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: VIS_COLOR[d.visibility] }} />
                  </TableCell>
                  <TableCell align="center" onClick={() => setDrawerFor(d)}>
                    <Chip size="small" label={sourceLabel(d) + (d.source_clash ? ' ⚠' : '')}
                      sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: sourceColor(d) }} />
                  </TableCell>
                  <TableCell align="right" onClick={() => setDrawerFor(d)} sx={{ color: 'var(--text-3)' }}>{d.used_by}</TableCell>
                </TableRow>
              ))}
              {filtered.length === 0 && (
                <TableRow><TableCell colSpan={6} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>No dies match.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <DieAccessDrawer die={drawerFor} users={users} busy={busy}
        onClose={() => setDrawerFor(null)}
        onSave={(vis, clients) => { if (drawerFor) void setAccess(drawerFor.name, vis, clients); }}
        onMoved={(msg) => { setNotice(msg); setDrawerFor(null); void load(); }} />
    </Box>
  );
};

const DieAccessDrawer: React.FC<{
  die: DieRow | null; users: RegistryUser[]; busy: boolean;
  onClose: () => void; onSave: (vis: Visibility, clients: string[]) => void;
  onMoved: (msg: string) => void;
}> = ({ die, users, busy, onClose, onSave, onMoved }) => {
  const [vis, setVis] = useState<Visibility>('private');
  const [clients, setClients] = useState<string[]>([]);
  useEffect(() => { if (die) { setVis(die.visibility); setClients(die.clients); } }, [die]);

  return (
    <Drawer anchor="right" open={!!die} onClose={onClose} PaperProps={{ sx: { width: 360, bgcolor: 'var(--panel)', p: 2.5 } }}>
      {die && (
        <>
          <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>{die.name}</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 2 }}>
            {die.configs} config{die.configs === 1 ? '' : 's'} · used directly by {die.used_by} account{die.used_by === 1 ? '' : 's'}
          </Typography>

          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 0.5 }}>Visibility</Typography>
          <RadioGroup value={vis} onChange={(e) => setVis(e.target.value as Visibility)}>
            <FormControlLabel value="private" control={<Radio size="small" />}
              label={<Typography sx={{ fontSize: 13 }}>Private <span style={{ color: 'var(--text-4)' }}>— only owner/admins</span></Typography>} />
            <FormControlLabel value="public" control={<Radio size="small" />}
              label={<Typography sx={{ fontSize: 13 }}>Public <span style={{ color: 'var(--text-4)' }}>— every signed-in user, read-only, incl. MCP agents</span></Typography>} />
            <FormControlLabel value="selected" control={<Radio size="small" />}
              label={<Typography sx={{ fontSize: 13 }}>Selected clients <span style={{ color: 'var(--text-4)' }}>— read-only, named accounts</span></Typography>} />
          </RadioGroup>

          {vis === 'selected' && (
            <Autocomplete
              multiple size="small" options={users.map((u) => u.email)} value={clients}
              onChange={(_, v) => setClients(v)}
              renderInput={(params) => <TextField {...params} label="Clients (e-mail)" size="small" sx={{ mt: 1 }} />}
              sx={{ mt: 1 }}
            />
          )}

          <Divider sx={{ my: 2, borderColor: 'var(--line-soft)' }} />
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" onClick={onClose} sx={{ textTransform: 'none' }}>Cancel</Button>
            <Button size="small" variant="contained" disabled={busy} onClick={() => { onSave(vis, vis === 'selected' ? clients : []); onClose(); }}
              sx={{ textTransform: 'none' }}>Save</Button>
          </Box>

          <Divider sx={{ my: 2, borderColor: 'var(--line-soft)' }} />
          <SourceBlock die={die} onMoved={onMoved} />
        </>
      )}
    </Drawer>
  );
};

/** Source: Open / Private — separate from visibility; a move opens PRs. */
const SourceBlock: React.FC<{ die: DieRow; onMoved: (msg: string) => void }> = ({ die, onMoved }) => {
  const [preview, setPreview] = useState<MovePreview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [ack, setAck] = useState(false);
  const current: Source = die.source ?? 'private';
  const target: Source = current === 'open' ? 'private' : 'open';
  const pend = die.source_pending;

  const openPreview = async () => {
    setErr(null); setAck(false); setWorking(true);
    try {
      const r = await fetch(`${API}/api/admin/dies/${encodeURIComponent(die.name)}/source/preview?target=${target}`);
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(typeof j.detail === 'string' ? j.detail : `HTTP ${r.status}`); return; }
      setPreview(j as MovePreview);
    } finally { setWorking(false); }
  };

  const confirmMove = async () => {
    if (!preview) return;
    setWorking(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/admin/dies/${encodeURIComponent(die.name)}/source`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, confirm: true, snapshot: preview.snapshot }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setErr(detailMsg(j, r.status));
        if (j.detail?.move) { setPreview(null); onMoved(`✗ ${die.name}: move incomplete — ${detailMsg(j, r.status)}`); }
        return;
      }
      setPreview(null);
      onMoved(target === 'open'
        ? `✓ ${die.name}: published (branch pushed to the public repository); PRs opened for merge into main`
        : `✓ ${die.name}: withdraw PRs opened — pending until merged`);
    } finally { setWorking(false); }
  };

  const moveAction = async (action: 'resume' | 'rollback') => {
    if (!pend) return;
    if (action === 'rollback' && !window.confirm(
      `Roll back move ${pend.id}? The pushed branches are deleted and the PRs closed.` +
      (pend.to === 'open' ? ' Anything already pushed to the public repository may have been cloned — rollback cannot unpublish it.' : ''))) return;
    setWorking(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/admin/dies/source/moves/${encodeURIComponent(pend.id)}/${action}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(action === 'rollback' ? { confirm: true } : {}),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(detailMsg(j, r.status)); return; }
      onMoved(`✓ ${die.name}: move ${action === 'resume' ? `resumed — ${j.status}` : 'rolled back'}`);
    } finally { setWorking(false); }
  };

  const m = preview?.manifest;
  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' }}>Source</Typography>
        <HelpTip title={SOURCE_HELP} />
      </Box>
      <Typography sx={{ fontSize: 13, color: sourceColor(die), fontWeight: 700 }}>{sourceLabel(die)}</Typography>
      {die.source_clash && (
        <Typography sx={{ fontSize: 11, color: '#f87171' }} title={die.source_error ?? ''}>
          In more than one source — not used until one copy is removed or an override record picks one.
        </Typography>
      )}
      {pend && pend.status !== 'pending' && (
        <Box sx={{ mt: 0.5 }}>
          <Typography sx={{ fontSize: 11, color: '#f87171' }} title={pend.error ?? ''}>
            Stopped at {pend.failed_step ?? 'start'}{pend.error ? `: ${pend.error.slice(0, 140)}` : ''}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
            <Button size="small" disabled={working} onClick={() => void moveAction('resume')} sx={{ textTransform: 'none', fontSize: 12 }}>Resume</Button>
            <Button size="small" disabled={working} onClick={() => void moveAction('rollback')} sx={{ textTransform: 'none', fontSize: 12, color: '#f87171' }}>Rollback</Button>
          </Box>
        </Box>
      )}
      {pend && (
        <Box sx={{ mt: 0.5 }}>
          {(pend.prs ?? []).map((p) => (
            <Typography key={p.url} sx={{ fontSize: 11 }}>
              <a href={p.url} target="_blank" rel="noreferrer" style={{ color: '#60a5fa' }}>{p.repo}</a>
              {p.opened === false ? ' — open the PR by hand' : ''}
            </Typography>
          ))}
        </Box>
      )}
      {!pend && die.source_movable === false && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: 0.5 }}>Not in a data repository — private, cannot be moved.</Typography>
      )}
      {!pend && die.source_movable !== false && (
        <Button size="small" disabled={working} onClick={() => void openPreview()}
          sx={{ mt: 1, textTransform: 'none', fontSize: 12, color: target === 'open' ? '#4ade80' : 'var(--text-2)' }}>
          {target === 'open' ? 'Publish (make open)…' : 'Make private…'}
        </Button>
      )}
      {err && !preview && <Typography sx={{ fontSize: 11, color: '#f87171', mt: 0.5 }}>{err}</Typography>}

      <Dialog open={!!preview} onClose={() => setPreview(null)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ fontSize: 15, fontWeight: 800 }}>
          {target === 'open' ? `Publish “${die.name}” as open data?` : `Move “${die.name}” to private?`}
        </DialogTitle>
        <DialogContent dividers>
          {preview && m && (
            <>
              {preview.public_push && (
                <Typography sx={{ fontSize: 13, color: '#f87171', fontWeight: 800, mb: 1 }}>
                  Confirming publishes immediately — the branch is pushed to the public repository and is public at once. The PR is only for review and merge into main.
                </Typography>
              )}
              <Typography sx={{ fontSize: 12.5, color: '#fbbf24', fontWeight: 700, mb: 1.5 }}>{preview.warning}</Typography>
              {preview.blockers.length > 0 && (
                <Box sx={{ mb: 1.5 }}>
                  <Typography sx={{ fontSize: 12, color: '#f87171', fontWeight: 700 }}>Blocked — fix these first:</Typography>
                  {preview.blockers.map((b) => (
                    <Typography key={`${b.kind}:${b.name}`} sx={{ fontSize: 12 }}>• {b.kind} <b>{b.name}</b> — {b.reason}</Typography>
                  ))}
                </Box>
              )}
              <Typography sx={{ fontSize: 12, fontWeight: 700 }}>
                {target === 'open' ? 'Will be published' : 'Will be moved'}: {m.files.length} file(s), {(m.total_bytes / 1024).toFixed(1)} KiB
                <span style={{ color: 'var(--text-4)', fontWeight: 400 }}> · snapshot {preview.snapshot.slice(0, 12)}</span>
              </Typography>
              <Box component="ul" sx={{ fontSize: 11.5, m: 0, pl: 2.5, maxHeight: 140, overflow: 'auto' }}>
                {m.files.map((f) => <li key={f.path}>{f.path}</li>)}
              </Box>
              <Typography sx={{ fontSize: 12, fontWeight: 700, mt: 1 }}>Geometry</Typography>
              <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>
                {Object.keys(m.geometry).length} parameters{m.geometry.stator_diameter != null ? ` · Ø ${String(m.geometry.stator_diameter)} mm` : ''}
                {m.geometry.num_slots != null ? ` · ${String(m.geometry.num_slots)}s-${String(m.geometry.num_poles)}p` : ''}
              </Typography>
              <Typography sx={{ fontSize: 12, fontWeight: 700, mt: 1 }}>Configurations</Typography>
              <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>
                {m.configs.map((c) => `${c.name} (${c.duties.join(', ') || 'no duties'})`).join(' · ') || '—'}
              </Typography>
              <Typography sx={{ fontSize: 12, fontWeight: 700, mt: 1 }}>Materials</Typography>
              <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>{m.materials.join(', ') || '—'}</Typography>
              <Typography sx={{ fontSize: 12, fontWeight: 700, mt: 1 }}>Devices</Typography>
              <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>{m.devices.join(', ') || '—'}</Typography>
              <Typography sx={{ fontSize: 12, fontWeight: 700, mt: 1 }}>Results</Typography>
              <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>{m.results.length ? `${m.results.length} item(s)` : 'none'}</Typography>
              {preview.blockers.length === 0 && (
                <FormControlLabel sx={{ mt: 1.5 }} control={<Checkbox size="small" checked={ack} onChange={(e) => setAck(e.target.checked)} />}
                  label={<Typography sx={{ fontSize: 12 }}>I understand — {target === 'open' ? 'this publishes immediately and cannot be undone' : 'the public history keeps what was published'}.</Typography>} />
              )}
              {err && <Typography sx={{ fontSize: 11.5, color: '#f87171', mt: 1 }}>{err}</Typography>}
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => setPreview(null)} sx={{ textTransform: 'none' }}>Cancel</Button>
          <Button size="small" variant="contained" color={target === 'open' ? 'warning' : 'primary'}
            disabled={working || !ack || (preview?.blockers.length ?? 0) > 0} onClick={() => void confirmMove()}
            sx={{ textTransform: 'none' }}>
            {target === 'open' ? 'Publish now' : 'Move to private'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default MotorsAccessSection;
