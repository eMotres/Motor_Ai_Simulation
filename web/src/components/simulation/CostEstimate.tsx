/**
 * CostEstimate — material cost of the CURRENT motor, computed THROUGH the modular
 * kernel pipeline (geometry.2d -> cost). The first user-facing feature that runs
 * end-to-end on the module system. Fully additive — independent of the FEM flow,
 * so it cannot affect the existing Simulation behaviour.
 */
import React, { useEffect, useState } from 'react';
import { Box, Paper, Typography, Button, CircularProgress } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface CostLine { item: string; mass_kg?: number | null; unit_price_per_kg?: number | null; cost: number; }
interface CostIR { currency: string; total: number; lines: CostLine[]; }

const CARD = { bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1.5, p: 2 } as const;

const CostEstimate: React.FC = () => {
  const [cost, setCost] = useState<CostIR | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // The motor currently being configured (same geometry the FEM tab uses), so the
  // cost reflects THIS motor — passed to geometry.2d as a param override.
  const geometry = useMotorStore((s: any) => s.geometry);

  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/kernel/study`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capabilities: ['geometry.2d', 'cost'],
          payload: { params: geometry || {} },
        }),
      });
      const j = await r.json();
      const c = j?.steps?.cost?.result;
      if (!j.ok || !c) throw new Error(j?.steps?.cost?.error || 'pipeline failed');
      setCost(c as CostIR);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  useEffect(() => { void run(); }, [geometry]);   // re-estimate when the motor changes (light: no FEM)

  return (
    <Paper sx={CARD}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>Material cost</Typography>
        <Typography sx={{ fontSize: 10.5, color: '#64748b', fontFamily: 'monospace' }}>
          via modules: geometry.2d → cost
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Button size="small" variant="outlined" onClick={run} disabled={busy}
          sx={{ textTransform: 'none', fontSize: 11 }}>
          {busy ? 'Computing…' : 'Re-estimate'}
        </Button>
      </Box>

      {busy && !cost && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: '#64748b', py: 1 }}>
          <CircularProgress size={14} /> Running pipeline through the kernel…
        </Box>
      )}
      {err && <Typography sx={{ fontSize: 11.5, color: '#fca5a5' }}>Cost pipeline failed: {err}</Typography>}

      {cost && (
        <Box>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.4 }}>
            {cost.lines.map((ln) => (
              <Box key={ln.item} sx={{ display: 'flex', justifyContent: 'space-between',
                fontSize: 11.5, color: '#cbd5e1', fontFamily: 'monospace' }}>
                <span style={{ textTransform: 'capitalize' }}>
                  {ln.item}{ln.mass_kg != null ? `  (${ln.mass_kg} kg)` : ''}
                </span>
                <span>${ln.cost.toFixed(2)}</span>
              </Box>
            ))}
          </Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 1, pt: 1,
            borderTop: '1px solid #1e293b', fontSize: 13, fontWeight: 700, color: '#34d399' }}>
            <span>Total</span><span>${cost.total.toFixed(2)} {cost.currency}</span>
          </Box>
        </Box>
      )}
    </Paper>
  );
};

export default CostEstimate;
