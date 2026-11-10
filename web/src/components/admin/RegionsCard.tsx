// Admin · Servers · Regions (docs/regions/DESIGN.md): both regions' state — up, version, load, jobs, disk.  With no
// peer configured (today) it shows this server alone, marked "Single region".
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Chip, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { serverRows, type RegionsView } from './regions';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');
const POLL_MS = 30_000;

const RegionsCard: React.FC = () => {
  const { t } = useTranslation('admin');
  const [view, setView] = useState<RegionsView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // e-mails both servers host as their own account (a sign-up raced with the peer down); shown, never merged
  const [dups, setDups] = useState<string[]>([]);
  const load = useCallback(() => {
    fetch(`${API}/api/admin/regions`)
      .then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j: RegionsView) => { setView(j); setErr(null); })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
    fetch(`${API}/api/admin/regions/accounts`)
      .then((r) => (r.ok ? r.json() : { duplicates: [] }))
      .then((j: { duplicates?: unknown }) => setDups(Array.isArray(j.duplicates) ? j.duplicates.map(String) : []))
      .catch(() => setDups([]));
  }, []);
  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);
  const rows = serverRows(view);
  return (
    <Box sx={{ mb: 2.5 }} data-testid="regions-card">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
        <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{t('regions.title')}</Typography>
        <HelpTip title={t('regions.titleTip')} />
        {view?.mode === 'single' && (
          <Tooltip title={t('regions.singleTip')} arrow>
            <Chip size="small" label={t('regions.single')} sx={{ height: 18, fontSize: 10, bgcolor: 'var(--panel)', color: 'var(--text-3)' }} />
          </Tooltip>
        )}
        {dups.length > 0 && (
          <Tooltip title={`${t('regions.duplicatesTip')} ${dups.join(', ')}`} arrow>
            <Chip size="small" data-testid="regions-duplicates" label={t('regions.duplicates', { n: dups.length })}
              sx={{ height: 18, fontSize: 10, bgcolor: 'rgba(248,113,113,0.15)', color: '#f87171' }} />
          </Tooltip>
        )}
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      <Table size="small" sx={{ '& td, & th': { fontSize: 12 } }}>
        <TableHead><TableRow>
          <TableCell>{t('regions.region')}</TableCell>
          <TableCell>{t('regions.state')}</TableCell>
          <TableCell>{t('regions.version')}</TableCell>
          <TableCell><Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>{t('regions.load')}<HelpTip title={t('regions.loadTip')} /></Box></TableCell>
          <TableCell><Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>{t('regions.queue')}<HelpTip title={t('regions.queueTip')} /></Box></TableCell>
          <TableCell>{t('regions.disk')}</TableCell>
        </TableRow></TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={`${r.region}-${r.self ? 'self' : 'peer'}`}>
              <TableCell sx={{ fontWeight: 700 }}>
                {r.region}
                <Box component="span" sx={{ ml: 0.75, color: 'var(--text-4)', fontWeight: 400 }}>{r.self ? t('regions.thisServer') : t('regions.peer')}</Box>
              </TableCell>
              <TableCell sx={{ color: r.state === 'ok' ? '#4ade80' : '#f87171' }}>{r.state === 'ok' ? t('regions.up') : t('regions.down')}</TableCell>
              <TableCell sx={{ fontFamily: 'monospace' }}>{r.version}</TableCell>
              <TableCell>{r.load}</TableCell>
              <TableCell>{r.queue}</TableCell>
              <TableCell>{r.disk}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
};

export default RegionsCard;
