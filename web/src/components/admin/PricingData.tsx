// Admin · Usage · Pricing data — per-account monthly totals + what each client
// costs us under an editable cost basis. Record only; nothing is billed.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Typography, Button, Table, TableBody, TableCell, TableHead, TableRow, TextField,
} from '@mui/material';
import HelpTip from '../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface Basis { eur_per_server_month: number; cores_per_server: number; eur_per_gb_month: number; eur_per_cpu_hour: number }
type Acc = Record<string, unknown> & {
  account: string; plan: string; domain: string; jobs: number; failed: number; stopped: number;
  cpu_h: number; wall_h: number; active_days: number; logins: number; report_pdf: number;
  datasheet_export: number; mcp_calls: number; mcp_429: number; agent_runs: number;
  storage_gb_avg: number; peak_concurrent: number; cost_eur: number;
};
interface Report { month: string; basis: Basis; accounts: Acc[]; totals: { cpu_h: number; cost_eur: number; jobs: number } }

const COLS: [keyof Acc, string][] = [
  ['account', 'account'], ['plan', 'plan'], ['domain', 'domain'], ['jobs', 'jobs'],
  ['failed', 'failed'], ['stopped', 'stopped'], ['cpu_h', 'CPU-h'], ['wall_h', 'wall-h'],
  ['peak_concurrent', 'peak ∥'], ['active_days', 'active d'], ['logins', 'logins'],
  ['report_pdf', 'reports'], ['datasheet_export', 'datasheets'], ['mcp_calls', 'MCP'],
  ['mcp_429', '429'], ['agent_runs', 'agent runs'], ['storage_gb_avg', 'GB'], ['cost_eur', '€ cost'],
];

const thisMonth = () => new Date().toISOString().slice(0, 7);

const PricingData: React.FC = () => {
  const [month, setMonth] = useState(thisMonth());
  const [rep, setRep] = useState<Report | null>(null);
  const [basis, setBasis] = useState<Basis | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/admin/usage/monthly?month=${month}`);
      if (!r.ok) throw new Error(`monthly HTTP ${r.status}`);
      const d: Report = await r.json(); setRep(d); setBasis(d.basis); setErr(null);
    } catch (e) { setErr(String(e)); }
  }, [month]);
  useEffect(() => { void load(); }, [load]);

  const saveBasis = async () => {
    if (!basis) return;
    const r = await fetch(`${API}/api/admin/usage/cost_basis`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ eur_per_server_month: basis.eur_per_server_month,
        cores_per_server: basis.cores_per_server, eur_per_gb_month: basis.eur_per_gb_month }),
    });
    if (!r.ok) { setErr(`cost basis HTTP ${r.status}`); return; }
    void load();
  };

  const download = async (fmt: 'csv' | 'json') => {
    const r = await fetch(`${API}/api/admin/usage/monthly?month=${month}&format=${fmt}`);
    if (!r.ok) { setErr(`export HTTP ${r.status}`); return; }
    const blob = fmt === 'json' ? new Blob([JSON.stringify(await r.json(), null, 1)], { type: 'application/json' }) : await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `usage_${month}.${fmt}`; a.click();
    URL.revokeObjectURL(url);
  };

  const num = (k: keyof Basis, label: string, tip: string) => (
    <TextField size="small" type="number" label={label} sx={{ width: 150 }}
      value={basis ? basis[k] : ''}
      onChange={(e) => basis && setBasis({ ...basis, [k]: Number(e.target.value) })}
      InputProps={{ endAdornment: <HelpTip title={tip} /> }} />
  );

  return (
    <Box sx={{ mt: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1 }}>
        <Typography sx={{ fontWeight: 600, fontSize: 13 }}>Pricing data</Typography>
        <HelpTip title="What each account cost us this month. Record only — nothing is billed." />
        <TextField size="small" type="month" value={month} onChange={(e) => setMonth(e.target.value)} sx={{ width: 160 }} />
        <Box sx={{ flex: 1 }} />
        <Button size="small" onClick={() => void download('csv')}>CSV</Button>
        <Button size="small" onClick={() => void download('json')}>JSON</Button>
      </Box>
      {basis && (
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
          {num('eur_per_server_month', '€ / server-month', 'eu1 = Hetzner AX42-1: €100/month (confirm from the invoice).')}
          {num('cores_per_server', 'threads / server', 'AX42-1: 8 cores / 16 threads. CPU-hours are counted per thread.')}
          {num('eur_per_gb_month', '€ / GB-month', 'Storage price; 0 until set.')}
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
            = €{basis.eur_per_cpu_hour.toFixed(4)} / CPU-h
          </Typography>
          <HelpTip title="€ per CPU-hour = €/month ÷ (threads × 730 h)." />
          <Button size="small" variant="outlined" onClick={() => void saveBasis()}>Save</Button>
        </Box>
      )}
      {err && <Typography sx={{ color: '#f87171', fontSize: 12 }}>{err}</Typography>}
      {rep && (
        <>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 0.5 }}>
            total {rep.totals.jobs} jobs · {rep.totals.cpu_h.toFixed(2)} CPU-h · €{rep.totals.cost_eur.toFixed(2)}
          </Typography>
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead><TableRow>{COLS.map(([, h]) => <TableCell key={h}>{h}</TableCell>)}</TableRow></TableHead>
              <TableBody>
                {rep.accounts.length === 0 && <TableRow><TableCell colSpan={COLS.length} sx={{ color: 'var(--text-4)' }}>no usage recorded this month</TableCell></TableRow>}
                {rep.accounts.map((a) => (
                  <TableRow key={a.account}>
                    {COLS.map(([k]) => {
                      const v = a[k];
                      return <TableCell key={String(k)}>{typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(k === 'cost_eur' ? 2 : 3)) : String(v ?? '—')}</TableCell>;
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        </>
      )}
    </Box>
  );
};

export default PricingData;
