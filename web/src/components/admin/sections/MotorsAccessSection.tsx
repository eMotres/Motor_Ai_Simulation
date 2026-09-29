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
  Radio, Checkbox, Divider, Autocomplete,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import HelpTip from '../../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

type Visibility = 'private' | 'public' | 'selected';
const VIS_LABEL: Record<Visibility, string> = { private: 'Private', public: 'Public', selected: 'Selected clients' };
const VIS_COLOR: Record<Visibility, string> = { private: 'var(--text-3)', public: '#4ade80', selected: '#60a5fa' };

interface DieRow {
  name: string; stator_diameter: number | null; configs: number; duties: number;
  visibility: Visibility; clients: string[]; used_by: number;
}
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
                  <TableCell align="right" onClick={() => setDrawerFor(d)} sx={{ color: 'var(--text-3)' }}>{d.used_by}</TableCell>
                </TableRow>
              ))}
              {filtered.length === 0 && (
                <TableRow><TableCell colSpan={5} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>No dies match.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <DieAccessDrawer die={drawerFor} users={users} busy={busy}
        onClose={() => setDrawerFor(null)}
        onSave={(vis, clients) => { if (drawerFor) void setAccess(drawerFor.name, vis, clients); }} />
    </Box>
  );
};

const DieAccessDrawer: React.FC<{
  die: DieRow | null; users: RegistryUser[]; busy: boolean;
  onClose: () => void; onSave: (vis: Visibility, clients: string[]) => void;
}> = ({ die, users, busy, onClose, onSave }) => {
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
        </>
      )}
    </Drawer>
  );
};

export default MotorsAccessSection;
