// ─────────────────────────────────────────────────────────────────────────────
// DOE screening — Latin-Hypercube sample the whole design box at a FIXED current,
// FEM-evaluate each point, and rank which variables drive ripple / torque /
// efficiency (unbiased global importance via RandomForest).  Wraps the backend
// /api/optimization/doe job (was CLI-only).  Separate dataset — does NOT touch
// the optimizer's surrogate data.
// ─────────────────────────────────────────────────────────────────────────────
import React, { useEffect, useRef, useState } from 'react';
import { Box, Typography, TextField, Button, LinearProgress, Divider } from '@mui/material';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

function useLS<T>(key: string, def: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const r = localStorage.getItem(`doe.${key}`); return r == null ? def : (JSON.parse(r) as T); }
    catch { return def; }
  });
  return [v, (nv: T) => { setV(nv); try { localStorage.setItem(`doe.${key}`, JSON.stringify(nv)); } catch { /* ignore */ } }];
}
const readBool = (key: string, def: boolean): boolean => {
  try { return JSON.parse(localStorage.getItem(key) ?? String(def)) === true; } catch { return def; }
};
const readNum = (key: string, def: number): number => {
  try { const r = localStorage.getItem(key); const v = r == null ? def : Number(JSON.parse(r)); return Number.isFinite(v) ? v : def; }
  catch { return def; }
};

const TCOL: Record<string, string> = { ripple: '#f59e0b', torque: '#3b82f6', eff: '#a855f7', td: '#22c55e' };

const NumField: React.FC<{ label: string; value: number; onChange: (v: number) => void; disabled?: boolean }> =
  ({ label, value, onChange, disabled }) => {
    // String-backed so a leading "-" (or "0." mid-typing) isn't clobbered to NaN.
    const [s, setS] = useState(String(value));
    return (
      <TextField label={label} size="small" type="text" value={s} disabled={disabled}
        onChange={e => { const t = e.target.value; setS(t); const num = parseFloat(t); if (Number.isFinite(num)) onChange(num); }}
        inputProps={{ inputMode: 'decimal', style: { fontSize: 12, padding: '5px 8px' } }} sx={{ flex: 1 }} />
    );
  };

const DOEPanel: React.FC = () => {
  const [n, setN] = useLS('n', 60);
  const current = readNum('sim.current', 100);   // single source: Simulation tab's current
  const [band, setBand] = useLS('band', 0.3);
  const [steps, setSteps] = useLS('steps', 18);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; n_ok: number } | null>(null);
  const [imp, setImp] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const stopRef = useRef(false);

  // Persist the importance result so it survives reload / tab-switch (like the
  // sweep result): saved on completion, restored on mount + re-adopted from backend.
  const saveImp = (i: any) => { setImp(i); try { localStorage.setItem('doe.lastImportance', JSON.stringify(i)); } catch { /* quota */ } };

  // Shared poll loop — used by run() and the resume-on-mount effect.
  const poll = async () => {
    while (!stopRef.current) {
      await new Promise(r => setTimeout(r, 2000));
      let st: any;
      try { st = await (await fetch(`${API}/api/optimization/doe/progress`)).json(); } catch { continue; }
      setProgress({ done: st.done ?? 0, total: st.total ?? n, n_ok: st.n_ok ?? 0 });
      if (!st.running) { if (st.importance) saveImp(st.importance); if (st.error) setErr(st.error); break; }
    }
  };

  const run = async () => {
    setErr(null); setImp(null); try { localStorage.removeItem('doe.lastImportance'); } catch { /* ignore */ }
    setRunning(true); setProgress(null); stopRef.current = false;
    try {
      const res = await fetch(`${API}/api/optimization/doe/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ n, current_a: current, band, steps_per_period: steps,
          n_sectors: Math.max(1, Math.round(readNum('mesh.nSectors', 4))),   // single source: Mesh tab (same as Simulation)
          pole_copy: readBool('mesh.poleCopy', false), torque_filter: readBool('sim.torqueFilter', true) }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
      await poll();
    } catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setRunning(false); }
  };

  // On mount (reload / tab-switch): restore the last importance from localStorage
  // instantly, then reconcile with the backend — resume a running DOE, or adopt
  // its completed result.
  useEffect(() => {
    try { const c = localStorage.getItem('doe.lastImportance'); if (c) setImp(JSON.parse(c)); } catch { /* ignore */ }
    (async () => {
      try {
        const st = await (await fetch(`${API}/api/optimization/doe/progress`)).json();
        if (st.running) {
          stopRef.current = false; setRunning(true);
          setProgress({ done: st.done ?? 0, total: st.total ?? n, n_ok: st.n_ok ?? 0 });
          await poll(); setRunning(false);
        } else if (st.importance) {
          saveImp(st.importance);
        }
      } catch { /* ignore */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const targets: [string, any][] = imp?.ok ? Object.entries(imp.targets || {}) : [];

  return (
    <Box>
      <Typography sx={{ fontSize: 11, color: '#64748b', mb: 1.5 }}>
        Latin-Hypercube screening at a <strong>fixed current</strong> — samples the whole design box
        (±band per variable), FEM-evaluates each, and ranks which variables drive
        ripple / torque / efficiency (unbiased global importance, RandomForest). Fixed current →
        torque varies → modelable. Separate dataset; doesn't touch the optimizer's surrogate.
      </Typography>
      <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
        <NumField label="samples" value={n} onChange={setN} disabled={running} />
        <TextField label="current (A) · Sim" size="small" value={current} disabled
          inputProps={{ style: { fontSize: 12, padding: '5px 8px' } }} sx={{ flex: 1 }} />
        <NumField label="band ±frac" value={band} onChange={setBand} disabled={running} />
        <NumField label="steps" value={steps} onChange={setSteps} disabled={running} />
      </Box>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1 }}>
        {!running ? (
          <Button size="small" variant="contained" onClick={run} disabled={n < 10}
            sx={{ textTransform: 'none', fontWeight: 700 }}>Run DOE ({n})</Button>
        ) : (
          <Button size="small" variant="outlined" disabled sx={{ textTransform: 'none' }}>Running…</Button>
        )}
        {progress && running && (
          <Typography sx={{ fontSize: 11, color: '#64748b' }}>
            {progress.done}/{progress.total} · ok {progress.n_ok}
          </Typography>
        )}
        {err && <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>✗ {err}</Typography>}
      </Box>
      {running && <LinearProgress variant={progress ? 'determinate' : 'indeterminate'}
        value={progress ? (100 * progress.done) / Math.max(1, progress.total) : 0}
        sx={{ mb: 1.5, height: 4, borderRadius: 2 }} />}

      {imp && !imp.ok && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>
          Not enough successful points to fit the model ({imp.n}/{imp.need}). Increase samples.
        </Typography>
      )}
      {targets.length > 0 && (
        <>
          <Divider sx={{ borderColor: '#1e293b', my: 1 }} />
          <Typography sx={{ fontSize: 11, color: '#64748b', mb: 1 }}>
            Variable importance (n={imp.n} LHS samples) — higher = stronger driver
          </Typography>
          {targets.map(([key, info]) => (
            <Box key={key} sx={{ mb: 1.5 }}>
              <Typography sx={{ fontSize: 11, fontWeight: 700, color: TCOL[key] || '#cbd5e1', mb: 0.5 }}>
                {info.label} <span style={{ color: '#475569', fontWeight: 400 }}>(R²={info.r2?.toFixed?.(2)})</span>
              </Typography>
              {(info.ranking || []).slice(0, 6).map((row: any) => (
                <Box key={row.var} sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: '2px' }}>
                  <Typography sx={{ fontSize: 10, color: '#94a3b8', width: 130, textAlign: 'right', flexShrink: 0 }}>{row.var}</Typography>
                  <Box sx={{ flex: 1, height: 10, bgcolor: '#0a1628', borderRadius: 0.5, overflow: 'hidden' }}>
                    <Box sx={{ width: `${Math.round((row.importance || 0) * 100)}%`, height: '100%', bgcolor: TCOL[key] || '#64748b' }} />
                  </Box>
                  <Typography sx={{ fontSize: 10, color: '#64748b', width: 38, textAlign: 'right', flexShrink: 0 }}>
                    {((row.importance || 0) * 100).toFixed(0)}%
                  </Typography>
                </Box>
              ))}
            </Box>
          ))}
        </>
      )}
    </Box>
  );
};

export default DOEPanel;
