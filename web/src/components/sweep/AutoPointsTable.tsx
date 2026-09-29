import React, { useState } from 'react';
import { Box, Chip, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import { useMotorStore } from '../../stores/motorStore';

const fmt = (v: unknown, d = 2) =>
  (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—');

/**
 * Shaft torque of a cloud point, DERIVED when it was not stored — points
 * published before the backend's `_pt` carried T_em_Nm hold only torque density
 * and mass, and torque = td · mass exactly.
 */
const ptTorque = (p: any): number => {
  const t = Number(p?.torque);
  if (Number.isFinite(t)) return t;
  const td = Number(p?.td); const m = Number(p?.mass);
  return (Number.isFinite(td) && Number.isFinite(m)) ? td * m : NaN;
};

/**
 * EVERY EVALUATED DESIGN, AS A TABLE — the Sweep tab's table, on this run's own
 * cloud.  The charts answer "is it converging"; they cannot answer "WHAT is it
 * changing", which is the question asked while a 9-hour run is in flight.  Same
 * rules as Sweep's: the varied geometry values lead in blue, the FEM outputs
 * follow, any column sorts, and nothing is filtered — a design over the ripple
 * gate is shown with its ripple, not hidden.
 *
 * Columns appear only where the data exists: runs whose points predate a metric
 * simply have one column fewer, instead of a table full of dashes.
 */
const AutoPointsTable: React.FC<{ points: any[]; gate: number; rpm?: number }> =
  ({ points, gate, rpm }) => {
  const omega = 2 * Math.PI * (Number(rpm) || 0) / 60;
  const rows = React.useMemo(() => (points || [])
    .map((p: any, i: number) => {
      const T = ptTorque(p);
      const mass = Number(p.mass);
      return {
        i: i + 1, kind: String(p.kind || ''), ov: p.overrides || {},
        T, mass, td: Number(p.td),
        eff: Number(p.eff) * 100,
        ripple: Number(p.ripple),
        thd: Number(p.thd),
        F: Number(p.F),
        I: Number(p.current_a), g: Number(p.gamma_deg),
        P: omega > 0 && Number.isFinite(T) ? T * omega / 1000 : NaN,
        ploss: Number(p.p_loss_w), vpk: Number(p.v_peak), j: Number(p.j_coil),
        dominates: !!p.dominates,
      };
    })
    .filter((r: any) => Number.isFinite(r.T)), [points, omega]);

  const cols = React.useMemo(() => {
    const num = (v: any) => (typeof v === 'number' && Number.isFinite(v));
    const has = (get: (r: any) => number) => rows.some((r: any) => num(get(r)));
    const varied = (get: (r: any) => number) =>
      new Set(rows.map((r: any) => Math.round(Number(get(r)) * 1e4) / 1e4)).size > 1;
    const c: { id: string; label: string; get: (r: any) => number;
               fmt: (v: number) => string; vcol?: boolean }[] = [];
    c.push({ id: 'i', label: '#', get: r => r.i, fmt: v => String(v) });
    // OUTPUTS FIRST, variables after — the one place this departs from the Sweep
    // table, and for a measured reason: a sweep varies two or three parameters,
    // an optimization varies all eighteen, and with the variables leading, every
    // number a design is JUDGED by (torque, efficiency, ripple) sat off the
    // right edge behind a horizontal scrollbar.  Scrolling right now explores
    // the geometry; the verdict is always on screen.
    c.push({ id: 'T', label: 'T (N·m)', get: r => r.T, fmt: v => v.toFixed(2) });
    if (has(r => r.P)) c.push({ id: 'P', label: 'P (kW)', get: r => r.P, fmt: v => v.toFixed(1) });
    c.push({ id: 'eff', label: 'η %', get: r => r.eff, fmt: v => v.toFixed(2) });
    c.push({ id: 'ripple', label: 'ripple %', get: r => r.ripple, fmt: v => v.toFixed(2) });
    c.push({ id: 'td', label: 'N·m/kg', get: r => r.td, fmt: v => v.toFixed(2) });
    if (has(r => r.mass)) c.push({ id: 'mass', label: 'mass (kg)', get: r => r.mass, fmt: v => v.toFixed(3) });
    if (has(r => r.ploss)) c.push({ id: 'ploss', label: 'P_loss (W)', get: r => r.ploss, fmt: v => v.toFixed(0) });
    if (has(r => r.vpk)) c.push({ id: 'vpk', label: 'V_peak (V)', get: r => r.vpk, fmt: v => v.toFixed(0) });
    if (has(r => r.j)) c.push({ id: 'j', label: 'J (A/mm²)', get: r => r.j, fmt: v => v.toFixed(1) });
    if (has(r => r.thd)) c.push({ id: 'thd', label: 'THD_LL %', get: r => r.thd, fmt: v => v.toFixed(2) });
    // F is the run's OWN verdict on the row — the signed perpendicular distance
    // above the current-only baseline line.  Sorting by it answers "which of my
    // 400 designs did the objective actually like", which no other column can.
    if (has(r => r.F)) c.push({ id: 'F', label: 'F', get: r => r.F, fmt: v => v.toFixed(4) });
    // …then the design itself: the operating point, then every geometry value
    // that actually moved (a variable the search never changed is a column of
    // one repeated number, so it is not shown).
    if (has(r => r.I) && varied(r => r.I))
      c.push({ id: 'I', label: 'I (A)', get: r => r.I, fmt: v => v.toFixed(1), vcol: true });
    if (has(r => r.g) && varied(r => r.g))
      c.push({ id: 'g', label: 'γ (°)', get: r => r.g, fmt: v => v.toFixed(1), vcol: true });
    const keys = new Set<string>();
    rows.forEach((r: any) => Object.keys(r.ov).forEach(k => keys.add(k)));
    [...keys].sort().forEach(k => {
      const get = (r: any) => Number(r.ov[k]);
      if (has(get) && varied(get))
        c.push({ id: 'ov:' + k, label: k, get, fmt: v => String(Math.round(v * 1000) / 1000), vcol: true });
    });
    return c;
  }, [rows]);

  const [sortId, setSortId] = useState('F');
  const [dir, setDir] = useState<1 | -1>(-1);
  const [all, setAll] = useState(false);
  const sorted = React.useMemo(() => {
    const col = cols.find(c => c.id === sortId) || cols[0];
    const s = [...rows].sort((a: any, b: any) => {
      const x = col.get(a), y = col.get(b);
      if (!Number.isFinite(x)) return 1;
      if (!Number.isFinite(y)) return -1;
      return (x - y) * dir;
    });
    return all ? s : s.slice(0, 60);
  }, [rows, cols, sortId, dir, all]);
  const onSort = (id: string) => {
    if (sortId === id) setDir(d => (d === 1 ? -1 : 1));
    else { setSortId(id); setDir(id === 'i' ? 1 : -1); }
  };

  if (!rows.length) return null;
  const th: React.CSSProperties = { position: 'sticky', top: 0, cursor: 'pointer',
    padding: '4px 7px', textAlign: 'right', whiteSpace: 'nowrap',
    borderBottom: '1px solid var(--line)', userSelect: 'none' };
  return (
    <Box sx={{ mt: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
          All evaluated designs ({rows.length}) — <span style={{ color: '#93c5fd' }}>variables</span> + outputs · click a column to sort
        </Typography>
        <HelpTip title={'Every eval that produced metrics, including the ones over the '
          + `${fmt(gate, 1)}% ripple gate — nothing is filtered here. Rows over the gate are `
          + 'amber. F is the objective: the signed perpendicular distance above the '
          + 'current-only baseline line, so F > 0 means the design beats simply raising '
          + 'the current. Sort by it to see what the search actually likes.'} />
        {rows.length > 60 && (
          <Chip size="small" label={all ? 'show top 60' : `show all ${rows.length}`}
            onClick={() => setAll(v => !v)} sx={{ fontSize: 10, height: 20 }} />
        )}
      </Box>
      <Box sx={{ overflowX: 'auto', maxHeight: 420, overflowY: 'auto',
        border: '1px solid var(--line-soft)', borderRadius: 1 }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 10.5, width: '100%',
          fontFamily: 'var(--font-mono, monospace)' }}>
          <thead>
            <tr>{cols.map(c => (
              <th key={c.id} onClick={() => onSort(c.id)}
                style={{ ...th, background: c.vcol ? 'var(--line-accent)' : 'var(--panel-2)',
                  color: c.vcol ? 'var(--brand)' : 'var(--text-1)',
                  fontWeight: sortId === c.id ? 700 : 400 }}>
                {c.label}{sortId === c.id ? (dir === 1 ? ' ▲' : ' ▼') : ''}
              </th>))}</tr>
          </thead>
          <tbody>
            {sorted.map((r: any, i: number) => (
              <tr key={r.i} style={{ background: i % 2 ? 'var(--panel-2)' : 'var(--line-soft)' }}>
                {cols.map(c => (
                  <td key={c.id} style={{ padding: '3px 7px', textAlign: 'right',
                    whiteSpace: 'nowrap', borderBottom: '1px solid var(--line-soft)',
                    // The gate is a CONSTRAINT, so a row that breaks it must be
                    // readable as broken at a glance — it is still shown.
                    color: (Number.isFinite(r.ripple) && r.ripple > gate) ? '#f59e0b'
                           : r.dominates ? '#e879f9' : 'var(--text-0)' }}>
                    {c.fmt(c.get(r))}
                  </td>))}
              </tr>))}
          </tbody>
        </table>
      </Box>
    </Box>
  );
};

/**
 * The table as the Optimize tab mounts it: reads the SAME run state the cards
 * and charts read, so it needs no props and can sit wherever it reads best —
 * which the user placed at the very bottom, under every chart.
 */
const AutoPointsTableCard: React.FC = () => {
  const { descentState } = useMotorStore();
  const st: any = descentState || {};
  const gate = Number(st.auto?.max_ripple_pct);
  const rpm = Number(st.auto?.operating_point?.rpm);
  return (
    <AutoPointsTable points={(st.points || []) as any[]}
      gate={Number.isFinite(gate) ? gate : 100}
      rpm={Number.isFinite(rpm) ? rpm : undefined} />
  );
};

export default AutoPointsTableCard;
