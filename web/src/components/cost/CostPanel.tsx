/**
 * CostPanel — the COST module's dedicated interface (its own tab).
 *
 * Computes the configured motor's material cost THROUGH the modular kernel
 * (geometry.2d -> cost) with EDITABLE price assumptions, a breakdown table and a
 * cost-composition bar. Fully on the module system, no FEM — re-estimates live as
 * the geometry or the prices change. The cost module already accepts
 * payload.prices / payload.labor_usd, so this UI just drives those knobs.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Paper, Typography, Button, TextField, CircularProgress, Divider } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';
import SectionLabel from '../common/SectionLabel';
import HelpTip from '../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface CostLine { item: string; mass_kg?: number | null; unit_price_per_kg?: number | null; cost: number; }
interface CostIR { currency: string; total: number; lines: CostLine[]; }

const COLORS = { copper: '#d97706', magnet: '#dc2626', steel: 'var(--text-4)', shaft: '#0ea5e9', labor: '#4f46e5' };
const colorFor = (item: string): string => {
  const k = item.toLowerCase();
  if (k.includes('copper') || k.includes('wind') || k.includes('strand')) return COLORS.copper;
  if (k.includes('magnet')) return COLORS.magnet;
  if (k.includes('shaft')) return COLORS.shaft;
  if (k.includes('labor')) return COLORS.labor;
  return COLORS.steel;
};

const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 } as const;
const PRICE_FIELDS = [
  { key: 'copper', label: 'Copper' },
  { key: 'magnet', label: 'Magnet (NdFeB)' },
  { key: 'steel', label: 'Electrical steel' },
  { key: 'shaft', label: 'Shaft steel' },
];
const DEFAULT_PRICES: Record<string, number> = { copper: 9.0, magnet: 80.0, steel: 3.0, shaft: 2.0 };

const CostPanel: React.FC = () => {
  const geometry = useMotorStore((s: any) => s.geometry);
  const [cost, setCost] = useState<CostIR | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [prices, setPrices] = useState<Record<string, number>>(DEFAULT_PRICES);
  const [labor, setLabor] = useState<number>(25);

  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/kernel/study`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capabilities: ['geometry.2d', 'cost'],
          payload: { params: geometry || {}, prices, labor_usd: labor },
        }),
      });
      const j = await r.json();
      const c = j?.steps?.cost?.result;
      if (!j.ok || !c) throw new Error(j?.steps?.cost?.error || 'pipeline failed');
      setCost(c as CostIR);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  // Auto-estimate on mount + whenever geometry / prices / labor change (debounced).
  const depKey = useMemo(() => JSON.stringify([geometry, prices, labor]), [geometry, prices, labor]);
  useEffect(() => { const t = setTimeout(() => { void run(); }, 350); return () => clearTimeout(t); }, [depKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const lines = cost?.lines || [];
  const materialTotal = lines.filter(l => l.item.toLowerCase() !== 'labor').reduce((s, l) => s + l.cost, 0);
  const totalMass = lines.reduce((s, l) => s + (l.mass_kg || 0), 0);

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', p: 2.5, bgcolor: 'var(--panel-2)' }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1.5, mb: 0.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 20, fontWeight: 800, color: 'var(--text-0)' }}>Material Cost</Typography>
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)', fontFamily: 'monospace' }}>via modules: geometry.2d → cost</Typography>
        <HelpTip title="Active-material cost of the configured motor: region area × stack length × density × unit price, plus a flat labor line. Computed through the modular kernel (no FEM) — it re-estimates as the geometry or prices change." />
        <Box sx={{ flex: 1 }} />
        <Button size="small" variant="contained" onClick={run} disabled={busy} sx={{ textTransform: 'none' }}>
          {busy ? 'Computing…' : 'Re-estimate'}
        </Button>
      </Box>
      {err &&<Typography sx={{ fontSize: 12.5, color: '#fca5a5', mb: 1 }}>Cost pipeline failed: {err}</Typography>}

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* ── Price assumptions ── */}
        <Paper sx={{ ...CARD, width: 290, flexShrink: 0 }}>
          <SectionLabel sx={{ mb: 1.5 }}>Price assumptions</SectionLabel>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
            {PRICE_FIELDS.map(f => (
              <Box key={f.key} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: colorFor(f.key), flexShrink: 0 }} />
                <Typography sx={{ fontSize: 12, color: 'var(--text-1)', flex: 1 }}>{f.label}</Typography>
                <TextField type="number" size="small" value={prices[f.key]}
                  onChange={(e) => setPrices(p => ({ ...p, [f.key]: parseFloat(e.target.value) || 0 }))}
                  InputProps={{ endAdornment: <span style={{ fontSize: 10, color: 'var(--text-3)' }}>$/kg</span> }}
                  sx={{ width: 116, '& input': { fontSize: 12, py: 0.5 } }} />
              </Box>
            ))}
            <Divider sx={{ borderColor: 'var(--panel)', my: 0.5 }} />
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: COLORS.labor, flexShrink: 0 }} />
              <Typography sx={{ fontSize: 12, color: 'var(--text-1)', flex: 1 }}>Labor (flat)</Typography>
              <TextField type="number" size="small" value={labor}
                onChange={(e) => setLabor(parseFloat(e.target.value) || 0)}
                InputProps={{ endAdornment: <span style={{ fontSize: 10, color: 'var(--text-3)' }}>$</span> }}
                sx={{ width: 116, '& input': { fontSize: 12, py: 0.5 } }} />
            </Box>
          </Box>
          <Button size="small" onClick={() => { setPrices(DEFAULT_PRICES); setLabor(25); }}
            sx={{ textTransform: 'none', fontSize: 11, mt: 1.5 }}>Reset to defaults</Button>
        </Paper>

        {/* ── Breakdown + total ── */}
        <Paper sx={{ ...CARD, flex: 1, minWidth: 360 }}>
          {busy && !cost && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: 'var(--text-3)', py: 2 }}>
              <CircularProgress size={16} /> Running pipeline through the kernel…
            </Box>
          )}
          {cost && (
            <>
              {/* Composition bar */}
              <Box sx={{ display: 'flex', height: 16, borderRadius: 1, overflow: 'hidden', mb: 2, border: '1px solid var(--line-soft)' }}>
                {lines.filter(l => l.cost > 0).map(l => (
                  <Box key={l.item} title={`${l.item}: $${l.cost.toFixed(2)}`}
                    sx={{ width: `${(l.cost / Math.max(cost.total, 1e-6)) * 100}%`, bgcolor: colorFor(l.item) }} />
                ))}
              </Box>

              {/* Table */}
              <Box sx={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr', rowGap: 0.6, columnGap: 1, alignItems: 'center' }}>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Item</Typography>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'right' }}>Mass</Typography>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'right' }}>$/kg</Typography>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'right' }}>Cost</Typography>
                {lines.map(l => (
                  <React.Fragment key={l.item}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0 }}>
                      <Box sx={{ width: 8, height: 8, borderRadius: '2px', bgcolor: colorFor(l.item), flexShrink: 0 }} />
                      <Typography sx={{ fontSize: 12, color: 'var(--text-1)', textTransform: 'capitalize', fontFamily: 'monospace',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.item}</Typography>
                    </Box>
                    <Typography sx={{ fontSize: 12, color: 'var(--text-2)', textAlign: 'right', fontFamily: 'monospace' }}>
                      {l.mass_kg != null ? `${l.mass_kg.toFixed(4)} kg` : '—'}</Typography>
                    <Typography sx={{ fontSize: 12, color: 'var(--text-2)', textAlign: 'right', fontFamily: 'monospace' }}>
                      {l.unit_price_per_kg != null ? `$${l.unit_price_per_kg.toFixed(2)}` : '—'}</Typography>
                    <Typography sx={{ fontSize: 12, color: 'var(--text-0)', textAlign: 'right', fontFamily: 'monospace' }}>
                      ${l.cost.toFixed(2)}</Typography>
                  </React.Fragment>
                ))}
              </Box>

              <Divider sx={{ borderColor: 'var(--panel)', my: 1.5 }} />
              <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-2)', mb: 0.5 }}>
                <span>Active material ({totalMass.toFixed(3)} kg)</span><span>${materialTotal.toFixed(2)}</span>
              </Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>Total</Typography>
                <Typography sx={{ fontSize: 22, fontWeight: 800, color: '#34d399' }}>
                  ${cost.total.toFixed(2)} <span style={{ fontSize: 13, color: 'var(--text-3)' }}>{cost.currency}</span>
                </Typography>
              </Box>
            </>
          )}
        </Paper>
      </Box>
    </Box>
  );
};

export default CostPanel;
