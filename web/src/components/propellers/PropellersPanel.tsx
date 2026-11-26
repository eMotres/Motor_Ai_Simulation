/** Propellers page (Simulations > Propellers, owner 2026-11-15): the one place for all propeller work.
 *
 *  First stage: the existing catalogue, read-only, from `GET /api/propellers`.  The blade-element evaluation, the own-propeller
 *  designer (10 / 11 / 12 / 13 in), the two-objective optimisation view and later the CFD runs are added here, stage by stage;
 *  Configure keeps only its own propeller picker for the cooling air.
 */
import { useEffect, useMemo, useState } from 'react';
import { Alert, Box, CircularProgress, Paper, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

import SubTabs from '../common/SubTabs';
import { oneOf } from '../../lib/viewMemory';
import { useRememberedView } from '../../lib/useRememberedView';
import { catalogRows } from '../../lib/propellersCatalog';
import type { Quality } from '../../lib/propellersCatalog';
import DesignerPage from './DesignerPage';
import { useHelpContext } from '../support/useHelpContext';
import { propellersHelp } from '../../lib/helpContexts';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const QUALITY_COLOR: Record<Quality, string> = {
  measured: 'var(--ok, #2e7d32)', powerEstimated: 'var(--warn, #ed6c02)', geometryOnly: 'var(--text-3)',
};

export default function PropellersPanel() {
  const { t } = useTranslation('simulation');
  const [view, setView] = useRememberedView<'catalog' | 'designer'>('propellers.view', 'catalog', oneOf(['catalog', 'designer'] as const));
  const [resp, setResp] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    fetch(`${API.replace(/\/$/, '')}/api/propellers`, { cache: 'no-store', signal: ctl.signal })
      .then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j) => { setResp(j); setError(null); })
      .catch((e) => { if (e?.name !== 'AbortError') setError(String(e?.message ?? e)); });
    return () => ctl.abort();
  }, []);

  const rows = useMemo(() => catalogRows(resp), [resp]);
  useHelpContext(propellersHelp({ view, rows: rows.length, error }));
  const head = (label: string, tip: string) => (
    <TableCell sx={{ fontSize: 12, whiteSpace: 'nowrap' }}><Tooltip title={tip}><span>{label}</span></Tooltip></TableCell>
  );

  return (
    <Box sx={{ height: '100%', overflow: 'auto' }} data-testid="propellers-panel">
      <Box sx={{ px: 2, pt: 1 }}>
        <SubTabs value={view} onChange={setView} ariaLabel={t('propTabCatalog')} testId="propellers-tabs"
          items={[{ id: 'catalog', label: t('propTabCatalog') }, { id: 'designer', label: t('propTabDesigner') }]} />
      </Box>
      {view === 'designer' ? <DesignerPage /> : (
      <Box sx={{ p: 2 }}>
      <Paper variant="outlined" sx={{ p: 1.5, borderColor: 'var(--line)' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>{t('propellersCatalog', { count: rows.length })}</Typography>
        {error && <Alert severity="error" sx={{ mb: 1 }}>{t('propellersLoadFailed', { error })}</Alert>}
        {!resp && !error && <CircularProgress size={20} />}
        {resp !== null && rows.length === 0 && !error && <Typography sx={{ fontSize: 13 }}>{t('propellersEmpty')}</Typography>}
        {rows.length > 0 && (
          <Table size="small" data-testid="propellers-table">
            <TableHead>
              <TableRow>
                {head(t('propellersColName'), t('propellersColNameTip'))}
                {head(t('propellersColDiameter'), t('propellersColDiameterTip'))}
                {head(t('propellersColPitch'), t('propellersColPitchTip'))}
                {head(t('propellersColBlades'), t('propellersColBladesTip'))}
                {head(t('propellersColData'), t('propellersColDataTip'))}
                {head(t('propellersColRpm'), t('propellersColRpmTip'))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id} hover>
                  <TableCell sx={{ fontSize: 13 }}>{r.name}</TableCell>
                  <TableCell sx={{ fontSize: 13 }}>{r.diameterIn ?? '—'}</TableCell>
                  <TableCell sx={{ fontSize: 13 }}>{r.pitchIn ?? '—'}</TableCell>
                  <TableCell sx={{ fontSize: 13 }}>{r.blades ?? '—'}</TableCell>
                  <TableCell sx={{ fontSize: 13, color: QUALITY_COLOR[r.quality] }}>
                    <Tooltip title={t(`propellersQuality_${r.quality}Tip`)}><span>{t(`propellersQuality_${r.quality}`)}</span></Tooltip>
                  </TableCell>
                  <TableCell sx={{ fontSize: 13 }}>{r.maxRpmTested ?? '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Paper>
      </Box>
      )}
    </Box>
  );
}
