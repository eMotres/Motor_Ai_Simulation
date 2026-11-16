// Saved optimisation runs of the machine open in this workspace (headless runs imported on the motor, and runs filed
// from the tab): /api/optimization/runs.  Opening one puts its state into the same chart a run made here fills; the
// machine fingerprint inside it is checked by the existing re-check / Apply against the machine live at that moment.
import React, { useEffect, useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export interface SavedRunRow {
  run_id: string;
  kind?: string;
  saved_at?: string;
  n_evals?: number | null;
  best_cost?: number | null;
  gap_layers?: number | null;
  source?: string | null;
  variables?: string[];
}

/** "auto · 120 evals · gap 1" - what the row says about the run. */
export const runLabel = (r: SavedRunRow): string =>
  [r.kind ?? 'run', r.n_evals != null ? `${r.n_evals} evals` : null,
   r.gap_layers != null ? `gap ${r.gap_layers}` : null].filter(Boolean).join(' · ');

const SavedRuns: React.FC<{ running: boolean }> = ({ running }) => {
  const { connectedToApi } = useMotorStore();
  const [rows, setRows] = useState<SavedRunRow[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetch(`${API}/api/optimization/runs`);
      if (!r.ok) { setRows([]); return; }
      setRows((await r.json()).runs ?? []);
    } catch { setRows([]); }
  };

  useEffect(() => {
    if (connectedToApi && !running) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedToApi, running]);

  const openRun = async (id: string) => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/optimization/runs/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const rec = await r.json();
      useMotorStore.setState({ descentState: rec.state, descentRunning: false });
    } catch (e: any) { setErr(`could not open the run: ${String(e?.message ?? e)}`); }
    finally { setBusy(false); }
  };

  if (!rows || rows.length === 0) return null;
  return (
    <Box sx={{ mb: 1 }}>
      <Button size="small" variant="text" disabled={running}
        onClick={() => setOpen(o => !o)}
        sx={{ textTransform: 'none', fontSize: 11, minWidth: 0, color: 'var(--text-2)' }}
        title="Optimisation runs saved on this motor, including runs made without the app. Open one to see its charts; applying a point is checked against the machine loaded now.">
        Saved runs ({rows.length}){open ? ' ▴' : ' ▾'}
      </Button>
      {err && <Typography component="span" sx={{ fontSize: 11, color: '#f87171', ml: 1 }}>{err}</Typography>}
      {open && (
        <Box sx={{ mt: 0.5, border: '1px solid var(--line-soft)', borderRadius: 1, p: 0.75 }}>
          {rows.map(row => (
            <Box key={row.run_id} sx={{ display: 'flex', gap: 1, alignItems: 'center', py: 0.25, fontSize: 11 }}>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                title={`${row.run_id}${row.saved_at ? ' — ' + row.saved_at : ''}${row.source ? ' — ' + row.source : ''}${row.variables?.length ? ' — ' + row.variables.join(', ') : ''}`}>
                {row.run_id}
              </span>
              <span style={{ color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{runLabel(row)}</span>
              <Button size="small" disabled={busy || running} onClick={() => openRun(row.run_id)}
                sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>Open</Button>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

export default SavedRuns;
