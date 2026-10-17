/**
 * Admin · Production access — one row per die / configuration: whether it has a production, who the members are
 * (owner / engineer / OEM producer). Click a row for a drawer: create the production, add or remove members
 * (registered accounts, role per member). An OEM member without a signed NDA gets a warning chip.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Autocomplete, Box, Button, Chip, CircularProgress, Divider, Drawer, IconButton, MenuItem, Paper, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CloseIcon from '@mui/icons-material/Close';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../common/HelpTip';
import { productionApi } from '../../../modules/production/api';
import { makeBilingual, memberLabel, oemWithoutNda, productionKey } from '../../../modules/production/helpers';
import type { MemberRole, Production } from '../../../modules/production/types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const CHIP_SX = { height: 18, fontSize: 10.5, fontWeight: 700 } as const;
const ROLES: MemberRole[] = ['owner', 'engineer', 'oem'];
const ROLE_COLOR: Record<MemberRole, string> = { owner: '#2563eb', engineer: '#16a34a', oem: '#d97706' };
const WARN = '#dc2626';

interface DieLite { name: string; config_names?: string[] }
interface Row { die: string; config: string; production: Production | null }
interface RegistryUser { email: string }

const ProductionAccessSection: React.FC = () => {
  const { t } = useTranslation('admin');
  const [rows, setRows] = useState<Row[]>([]);
  const [users, setUsers] = useState<RegistryUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [sel, setSel] = useState<{ die: string; config: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [dies, prods, u] = await Promise.all([
        fetch(`${API}/api/admin/dies`).then((r) => { if (!r.ok) throw new Error(`dies HTTP ${r.status}`); return r.json(); }),
        productionApi.adminList(),
        fetch(`${API}/api/auth/users`).then((r) => (r.ok ? r.json() : { users: [] })).catch(() => ({ users: [] })),
      ]);
      const byKey = new Map(prods.map((p) => [productionKey(p), p]));
      const out: Row[] = [];
      for (const d of (dies.dies ?? []) as DieLite[]) {
        for (const c of d.config_names ?? []) {
          out.push({ die: d.name, config: c, production: byKey.get(productionKey({ die: d.name, config: c })) ?? null });
          byKey.delete(productionKey({ die: d.name, config: c }));
        }
      }
      // a production whose die/config is no longer in the list stays visible so it can be removed
      for (const p of byKey.values()) out.push({ die: p.die, config: p.config, production: p });
      setRows(out); setUsers(u.users ?? []);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((r) => !q || `${r.die} ${r.config}`.toLowerCase().includes(q));
  }, [rows, search]);
  const current = sel ? rows.find((r) => r.die === sel.die && r.config === sel.config) ?? null : null;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{t('production.title')}</Typography>
        <HelpTip title={t('production.tip')} />
        {notice && <Typography sx={{ fontSize: 11, color: notice.startsWith('✓') ? '#34d399' : '#f87171' }}>{notice}</Typography>}
        <Box sx={{ flex: 1 }} />
        <TextField size="small" placeholder={t('production.search')} value={search} onChange={(e) => setSearch(e.target.value)}
          sx={{ minWidth: 180, '& .MuiInputBase-input': { fontSize: 12.5, py: 0.6 } }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} onClick={() => void load()} disabled={loading}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>{t('production.refresh')}</Button>
      </Box>
      {loading && <Box sx={{ display: 'flex', gap: 1.5, color: 'var(--text-3)', py: 4 }}><CircularProgress size={18} /><Typography sx={{ fontSize: 13 }}>{t('production.loading')}</Typography></Box>}
      {err && !loading && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 1 }}>{err}</Typography>}
      {!loading && !err && (
        <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
          <Table size="small" sx={{
            '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
            '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
          }}>
            <TableHead>
              <TableRow>
                <TableCell>{t('production.colMotor')}</TableCell>
                <TableCell align="center">{t('production.colStatus')}</TableCell>
                <TableCell>{t('production.colMembers')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((r) => {
                const p = r.production;
                const noNda = p ? oemWithoutNda(p) : [];
                return (
                  <TableRow key={`${r.die}/${r.config}`} hover sx={{ cursor: 'pointer' }} onClick={() => setSel({ die: r.die, config: r.config })}>
                    <TableCell sx={{ color: 'var(--text-0)', fontWeight: 600 }}>{r.die} / {r.config}</TableCell>
                    <TableCell align="center">
                      <Chip size="small" label={p ? t(`production.status.${p.status}`) : t('production.none')}
                        sx={{ ...CHIP_SX, color: p ? '#16a34a' : 'var(--text-4)', bgcolor: p ? '#16a34a1f' : 'var(--panel)' }} />
                    </TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                        {(p?.members ?? []).map((m) => (
                          <Chip key={m.email} size="small" label={memberLabel(m)} sx={{ ...CHIP_SX, color: ROLE_COLOR[m.role], bgcolor: `${ROLE_COLOR[m.role]}1f` }} />
                        ))}
                        {noNda.length > 0 && (
                          <Tooltip title={t('production.noNdaTip', { list: noNda.join(', ') })}>
                            <Chip size="small" label={t('production.noNda', { count: noNda.length })} sx={{ ...CHIP_SX, color: WARN, bgcolor: `${WARN}1f` }} />
                          </Tooltip>
                        )}
                      </Box>
                    </TableCell>
                  </TableRow>
                );
              })}
              {filtered.length === 0 && (
                <TableRow><TableCell colSpan={3} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>{t('production.noRows')}</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}
      <ProductionDrawer row={current} users={users} onClose={() => setSel(null)}
        onChanged={(msg) => { setNotice(msg); void load(); }} />
    </Box>
  );
};

const ProductionDrawer: React.FC<{
  row: Row | null; users: RegistryUser[]; onClose: () => void; onChanged: (msg: string) => void;
}> = ({ row, users, onClose, onChanged }) => {
  const { t } = useTranslation('admin');
  const [title, setTitle] = useState('');
  const [qty, setQty] = useState('100');
  const [email, setEmail] = useState<string | null>(null);
  const [role, setRole] = useState<MemberRole>('oem');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setTitle(''); setQty('100'); setEmail(null); setRole('oem'); setErr(null); }, [row?.die, row?.config]);

  const act = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true); setErr(null);
    try { await fn(); onChanged(`✓ ${okMsg}`); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const p = row?.production ?? null;
  const taken = new Set((p?.members ?? []).map((m) => m.email.toLowerCase()));

  return (
    <Drawer anchor="right" open={!!row} onClose={onClose} PaperProps={{ sx: { width: 380, bgcolor: 'var(--panel)', p: 2.5 } }}>
      {row && (
        <>
          <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)', mb: 2 }}>{row.die} / {row.config}</Typography>
          {!p ? (
            <>
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 1 }}>{t('production.create')}</Typography>
              <TextField size="small" fullWidth label={t('production.titleField')} value={title} onChange={(e) => setTitle(e.target.value)} sx={{ mb: 1 }} />
              <TextField size="small" fullWidth label={t('production.qtyField')} value={qty} onChange={(e) => setQty(e.target.value)} sx={{ mb: 1.5 }} />
              <Button size="small" variant="contained" disabled={busy || !title.trim() || !(Number(qty) > 0)} sx={{ textTransform: 'none' }}
                onClick={() => void act(() => productionApi.adminCreate(row.die, row.config, makeBilingual(title, 'en'), Number(qty), []), t('production.created'))}>
                {t('production.create')}
              </Button>
            </>
          ) : (
            <>
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 0.5 }}>{t('production.colMembers')}</Typography>
              {p.members.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>{t('production.noMembers')}</Typography>}
              {p.members.map((m) => (
                <Box key={m.email} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.25 }}>
                  <Typography sx={{ fontSize: 12.5, flex: 1, color: 'var(--text-1)' }}>{m.email}</Typography>
                  <Chip size="small" label={t(`production.role.${m.role}`)} sx={{ ...CHIP_SX, color: ROLE_COLOR[m.role], bgcolor: `${ROLE_COLOR[m.role]}1f` }} />
                  {m.role === 'oem' && oemWithoutNda(p).some((e) => e.toLowerCase() === m.email.toLowerCase()) && (
                    <Tooltip title={t('production.noNdaOne')}><Chip size="small" label={t('production.noNdaShort')} sx={{ ...CHIP_SX, color: WARN, bgcolor: `${WARN}1f` }} /></Tooltip>
                  )}
                  <IconButton size="small" disabled={busy} aria-label={t('production.removeMember')}
                    onClick={() => void act(() => productionApi.adminRemoveMember(row.die, row.config, m.email), t('production.removed', { email: m.email }))}>
                    <CloseIcon sx={{ fontSize: 15 }} />
                  </IconButton>
                </Box>
              ))}
              <Divider sx={{ my: 2, borderColor: 'var(--line-soft)' }} />
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 1 }}>{t('production.addMember')}</Typography>
              <Autocomplete size="small" options={users.map((u) => u.email).filter((e) => !taken.has(e.toLowerCase()))} value={email}
                onChange={(_, v) => setEmail(v)} renderInput={(params) => <TextField {...params} label={t('production.account')} size="small" />} />
              <TextField select size="small" fullWidth label={t('production.roleField')} value={role} onChange={(e) => setRole(e.target.value as MemberRole)} sx={{ mt: 1, mb: 1.5 }}>
                {ROLES.map((r) => <MenuItem key={r} value={r}>{t(`production.role.${r}`)}</MenuItem>)}
              </TextField>
              <Button size="small" variant="contained" disabled={busy || !email} sx={{ textTransform: 'none' }}
                onClick={() => void act(async () => { await productionApi.adminAddMember(row.die, row.config, email!, role); setEmail(null); }, t('production.added'))}>
                {t('production.add')}
              </Button>
              <Divider sx={{ my: 2, borderColor: 'var(--line-soft)' }} />
              <Button size="small" disabled={busy} sx={{ textTransform: 'none', color: '#f87171' }}
                onClick={() => { if (window.confirm(t('production.deleteConfirm'))) void act(async () => { await productionApi.adminDelete(row.die, row.config); onClose(); }, t('production.deleted')); }}>
                {t('production.delete')}
              </Button>
            </>
          )}
          {err && <Typography sx={{ fontSize: 11, color: '#f87171', mt: 1 }}>{err}</Typography>}
        </>
      )}
    </Drawer>
  );
};

export default ProductionAccessSection;
