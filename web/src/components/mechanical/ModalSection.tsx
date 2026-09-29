/** Modal section of the Mechanical tab — ring modes, and the shaft's criticals.
 *
 * Added 2026-09-05 for the user's request: "нам нужно сделать ещё модальный
 * анализ, чтобы понять все частоты — это очень важно для 20000 rpm".
 *
 * Two models side by side because they answer two different questions, and
 * only one of them decides whether the machine may be run at 20 000 rpm:
 *
 *   • MODES  — 2-D in-plane eigenmodes of the iron, per unit length. These are
 *     the ring / ovalisation frequencies that whine when an excitation order
 *     lands on them. They say nothing about bending along the shaft.
 *   • CRITICAL SPEEDS — a 1-D beam of the shaft line with the stack as mass and
 *     inertia, gyroscopics included. This is the one that gates the speed.
 *
 * Nothing solves on mount, and nothing re-solves when a field changes: both are
 * a deliberate press, like the stress solve above.
 *
 * 2026-09-06: both results and every input here moved into
 * `stores/mechanicalStore` with the rest of the tab — user, on leaving and
 * re-entering Mechanical: "графики пропадают".  A modal solve is as expensive as
 * a stress solve and was being thrown away by the same click.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, MenuItem, Paper, Select, TextField,
  Tooltip, Typography,
} from '@mui/material';

import { isStale, useMechanicalStore } from '../../stores/mechanicalStore';
import { useMotorStore } from '../../stores/motorStore';
import FieldViewer from '../common/FieldViewer';
import { SolveTimer, solvedIn } from './SolveTimer';
import { modeOutputs } from './fieldAdapters';
import { fmt } from './api';
import type {
  BeamInputs, CriticalSpeed, Excitation, ModalBody, ModalResult, ModalSupport,
} from './api';

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const mono = { fontFamily: 'monospace', fontSize: 12 } as const;

/** Width the canvases size themselves to. */
function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(420);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setW(el.clientWidth || 420));
    ro.observe(el);
    setW(el.clientWidth || 420);
    return () => ro.disconnect();
  }, []);
  return [ref as React.RefObject<HTMLDivElement>, w];
}

/* ── small input, remembered ─────────────────────────────────────────────── */

const Num: React.FC<{
  label: string; tip: string; value: string; width?: number;
  onChange: (v: string) => void;
}> = ({ label, tip, value, width = 96, onChange }) => (
  <Tooltip title={tip} placement="top">
    <TextField label={label} size="small" value={value}
      onChange={(e) => onChange(e.target.value)} sx={{ width }}
      inputProps={{ style: { fontSize: 11 } }}
      InputLabelProps={{ style: { fontSize: 11 } }} />
  </Tooltip>
);

/* ── the mode shape ──────────────────────────────────────────────────────── */

/** One mode shape, on the SHARED field viewer.
 *
 * It used to be a fourth hand-written canvas (a sibling of StressMap, with no
 * legend and no zoom).  User 2026-09-06: "интерфейс должен быть единым для всех
 * графиков", so a mode shape is now just another `FieldOutput` — same camera,
 * same banded ramp, same colour bar as every electromagnetic and mechanical
 * map.  What stays special is what a mode shape IS: no units (the solver
 * normalises it so the peak component is 1), so the exaggeration is a fraction
 * of the model's own size and the bar reads "of peak", not microns.
 */
const ModeView: React.FC<{
  modal: ModalResult; sel: number; onSel: (i: number) => void;
  exagg: string; onExagg: (v: string) => void; height?: number;
}> = ({ modal, sel, onSel, exagg, onExagg, height = 320 }) => {
  const row = modal.modes[sel];
  const out = useMemo(() => {
    const base = modeOutputs(modal.field, sel, Number(exagg) || 8,
      `Mode ${sel + 1} — ${fmt(row?.f_hz, row && row.f_hz >= 1000 ? 0 : 1)} Hz`);
    return {
      ...base,
      statText: row?.order === null || row?.order === undefined
        ? undefined : `circumferential order n = ${row.order}`,
    };
  }, [modal.field, sel, exagg, row]);

  const controls = (
    <>
      <Tooltip title="Which mode to draw. The table on the left selects the same thing — clicking a row and picking here are one state.">
        <Select size="small" value={sel} onChange={(e) => onSel(Number(e.target.value))}
          sx={{ fontSize: 12, height: 30, minWidth: 120 }}>
          {modal.modes.map((m, i) => (
            <MenuItem key={m.index} value={i} sx={{ fontSize: 12 }}>
              {i + 1} · {m.f_hz >= 1000 ? m.f_hz.toFixed(0) : m.f_hz.toFixed(1)} Hz
            </MenuItem>
          ))}
        </Select>
      </Tooltip>
      <Num label="deform %" width={84} value={exagg}
        tip="A mode shape has no amplitude — only a shape — so it is drawn exaggerated. This is the peak displacement as a percentage of the model's own radius. The dashed outline is the undeformed metal."
        onChange={onExagg} />
    </>
  );

  return (
    <FieldViewer
      outputs={[out]} selected={out.id} onSelect={() => { /* single output */ }}
      height={height} controls={controls}
      contextLabel={`${modal.body} · ${modal.support === 'free' ? 'free-free' : 'OD pinned'}`
        + ` · ${modal.mesh.n_triangles.toLocaleString()} tri · P${modal.mesh.element_order}`}
      contextTip={modal.assumptions}
      placeholder={
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          no mode shapes in this result — press Solve modes
        </Typography>} />
  );
};

/* ── the Campbell canvas ─────────────────────────────────────────────────── */

interface CampbellProps {
  rpmMax: number;
  rated: number;
  overspeed?: number;
  yMax: number;
  excitations: Excitation[];
  /** horizontal lines: FE mode frequencies that do not move with speed */
  modeLines?: number[];
  branches?: { rpm: number[]; backward: number[][]; forward: number[][] };
  criticals?: CriticalSpeed[];
  height?: number;
}

/** Frequency against speed: excitation rays, mode lines, whirl branches.
 *
 * An excitation whose `order` is null (the inverter carrier) is drawn
 * HORIZONTAL — it switches at its own rate whatever the shaft does — and every
 * other one as the ray f = order × rpm/60. The order comes from the backend
 * with the frequency; reading it off the row's name would be a guess.
 */
const CampbellChart: React.FC<CampbellProps> = ({
  rpmMax, rated, overspeed, yMax, excitations, modeLines, branches, criticals,
  height = 300,
}) => {
  const cv = useRef<HTMLCanvasElement | null>(null);
  const [box, w] = useWidth();

  useEffect(() => {
    const el = cv.current;
    if (!el) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.max(240, w), H = height;
    el.width = W * dpr; el.height = H * dpr;
    el.style.width = `${W}px`; el.style.height = `${H}px`;
    const g = el.getContext('2d');
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    const L = 46, R = 96, T = 10, B = 26;
    const px = (r: number) => L + (r / (rpmMax || 1)) * (W - L - R);
    const py = (f: number) => H - B - (f / (yMax || 1)) * (H - T - B);
    const clipY = (f: number) => Math.max(T, Math.min(H - B, py(f)));

    // frame + grid
    g.strokeStyle = 'rgba(140,140,150,0.35)';
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(L, T); g.lineTo(L, H - B); g.lineTo(W - R, H - B);
    g.stroke();
    g.font = '9px monospace';
    g.fillStyle = 'var(--text-4)';
    g.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
      const f = (yMax * i) / 4;
      g.fillText(f >= 1000 ? `${(f / 1000).toFixed(1)}k` : f.toFixed(0), L - 4, py(f) + 3);
      g.strokeStyle = 'rgba(140,140,150,0.14)';
      g.beginPath(); g.moveTo(L, py(f)); g.lineTo(W - R, py(f)); g.stroke();
    }
    g.textAlign = 'center';
    for (let i = 0; i <= 4; i++) {
      const r = (rpmMax * i) / 4;
      g.fillText(r >= 1000 ? `${Math.round(r / 1000)}k` : r.toFixed(0), px(r), H - B + 12);
    }
    g.fillText('rpm', (L + W - R) / 2, H - 3);

    // rated and overspeed
    const vline = (r: number, colour: string, text: string) => {
      if (!(r > 0) || r > rpmMax) return;
      g.save();
      g.strokeStyle = colour; g.lineWidth = 1.2; g.setLineDash([4, 3]);
      g.beginPath(); g.moveTo(px(r), T); g.lineTo(px(r), H - B); g.stroke();
      g.restore();
      g.fillStyle = colour; g.textAlign = 'center'; g.font = '9px monospace';
      g.fillText(text, px(r), T + 8);
    };
    vline(rated, 'rgba(96,165,250,0.9)', 'rated');
    if (overspeed) vline(overspeed, 'rgba(251,191,36,0.9)', '×1.2');

    // excitation lines
    g.font = '9px monospace';
    g.textAlign = 'left';
    for (const e of excitations) {
      const yEnd = e.order === null ? e.hz : (e.order * rpmMax) / 60;
      if (yEnd <= 0) continue;
      g.strokeStyle = 'rgba(226,120,90,0.75)';
      g.lineWidth = 1;
      g.beginPath();
      if (e.order === null) {
        if (e.hz > yMax) continue;
        g.moveTo(L, py(e.hz)); g.lineTo(W - R, py(e.hz));
      } else {
        // the ray leaves the top of the box on a fast order; stop it there
        const rEnd = Math.min(rpmMax, (yMax * 60) / e.order);
        g.moveTo(px(0), py(0)); g.lineTo(px(rEnd), clipY((e.order * rEnd) / 60));
      }
      g.stroke();
      const lastR = e.order === null ? rpmMax : Math.min(rpmMax, (yMax * 60) / e.order);
      const lastF = e.order === null ? e.hz : (e.order * lastR) / 60;
      g.fillStyle = 'rgba(226,120,90,0.95)';
      g.fillText(e.name, Math.min(px(lastR) + 3, W - R + 3), clipY(lastF) + 3);
    }

    // horizontal FE mode lines
    for (const f of modeLines ?? []) {
      if (f > yMax) continue;
      g.strokeStyle = 'rgba(120,200,255,0.85)';
      g.lineWidth = 1.2;
      g.beginPath(); g.moveTo(L, py(f)); g.lineTo(W - R, py(f)); g.stroke();
    }

    // whirl branches
    if (branches) {
      const draw = (curves: number[][], colour: string, dash: number[]) => {
        const nb = curves[0]?.length ?? 0;
        for (let k = 0; k < nb; k++) {
          g.save();
          g.strokeStyle = colour; g.lineWidth = 1.4; g.setLineDash(dash);
          g.beginPath();
          curves.forEach((row, i) => {
            const x = px(branches.rpm[i]), y = clipY(row[k]);
            if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
          });
          g.stroke();
          g.restore();
        }
      };
      draw(branches.backward, 'rgba(160,160,180,0.9)', [4, 3]);
      draw(branches.forward, 'rgba(74,222,128,0.95)', []);
    }

    // critical speeds
    for (const c of criticals ?? []) {
      if (c.rpm > rpmMax || c.hz > yMax) continue;
      g.fillStyle = c.excited_by_unbalance ? '#f87171' : 'rgba(160,160,180,0.9)';
      g.beginPath();
      g.arc(px(c.rpm), py(c.hz), c.excited_by_unbalance ? 4 : 3, 0, 2 * Math.PI);
      g.fill();
    }
  }, [rpmMax, rated, overspeed, yMax, excitations, modeLines, branches, criticals, w, height]);

  return (
    <Box ref={box} sx={{ width: '100%', minWidth: 0 }}>
      <canvas ref={cv} style={{ display: 'block', borderRadius: 4, background: 'var(--panel-2)' }} />
    </Box>
  );
};

/* ── the section ─────────────────────────────────────────────────────────── */

const BEAM_FIELDS: { key: keyof BeamInputs; label: string; tip: string }[] = [
  { key: 'bearing_span_mm', label: 'span mm', tip: 'Distance between the two bearings. ASSUMED — nothing in the motor config carries it, and the first critical speed falls roughly as the square of it.' },
  { key: 'stack_offset_mm', label: 'stack offset', tip: 'Where the rotor stack sits along the span: 0 is centred, + moves it toward bearing B. An off-centre stack lowers the first critical.' },
  { key: 'overhang_a_mm', label: 'overhang A', tip: 'Shaft sticking out past bearing A. Overhung mass adds its own mode below the between-bearings ones.' },
  { key: 'overhang_b_mm', label: 'overhang B', tip: 'Shaft sticking out past bearing B (the drive end, usually).' },
  { key: 'shaft_od_mm', label: 'shaft OD', tip: 'Shaft outside diameter away from the stack. 0 takes it from the geometry: twice rotor_inner_radius.' },
  { key: 'shaft_id_mm', label: 'shaft ID', tip: 'Bore of a hollow shaft. -1 takes it from the geometry: twice shaft_inner_radius. A tube loses far more mass than stiffness, which raises the criticals.' },
  { key: 'bearing_k_n_per_m', label: 'bearing k N/m', tip: 'Radial stiffness of ONE bearing — the single biggest lever on the critical speed. Pick a bearing in "Shaft & bearings" below and this field takes that card\'s catalogue stiffness; type your own and it is kept (the line under the bearings says so). With no card it is ASSUMED at 2e8, a mid-size preloaded angular-contact pair, and a real bearing is 1e8…1e9 — so try both ends of that range.' },
  { key: 'stack_stiffness_fraction', label: 'stack EI ×', tip: 'How much of the lamination stack\'s own bending stiffness to count. 0 is the conservative default and the standard first pass: laminations are not bonded axially, so the stack adds mass and no stiffness. Raise it toward 1 to see how much the answer depends on that.' },
];

const ModalSection: React.FC<{ rpm: number }> = ({ rpm }) => {
  const st = useMechanicalStore();
  const liveGeometry = useMotorStore((s) => s.geometry);
  // `meshMm` is the tab's ONE mesh size since 2026-09-06 ("сетка ... ей тоже
  // нужно как-то управлять"): this section used to carry a second "mesh mm" box
  // of its own, so the same rotor was meshed twice, at two different sizes, and
  // neither box said which one the picture belonged to.  The field lives in the
  // Mesh block above; here we only report what the answer was solved on.
  const {
    body, support, nModes, meshMm, modalExagg: exagg, modalSel: sel, beam,
  } = st;
  const setField = st.set;
  const setSel = useCallback((i: number) => setField('modalSel', i), [setField]);
  const modal = st.modal.data;
  const mBusy = st.modal.busy;
  const mErr = st.modal.err;
  const rotor = st.rotordyn.data;
  const rBusy = st.rotordyn.busy;
  const rErr = st.rotordyn.err;

  // Same two-witness staleness the stress result gets: a restored modal answer
  // of a machine that has since changed must SAY so rather than be quietly read
  // as this machine's frequencies (user 2026-09-06).
  const modalStale = useMemo(
    () => !!modal && isStale(st.modal.geoSig, st.modal.backendStale),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modal, st.modal.geoSig, st.modal.backendStale, liveGeometry]);
  const rotorStale = useMemo(
    () => !!rotor && isStale(st.rotordyn.geoSig, st.rotordyn.backendStale),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rotor, st.rotordyn.geoSig, st.rotordyn.backendStale, liveGeometry]);

  // Was this answer solved on the mesh size the tab is now set to?  Frequencies
  // converge from above as the mesh shrinks, so a modal result from another size
  // is a different answer and has to say so (2026-09-06, the one mesh block).
  const meshOff = useMemo(() => {
    const want = Number(meshMm);
    const had = modal?.mesh.mesh_size_mm;
    return !!modal && Number.isFinite(want) && had !== undefined
      && Math.abs(had - want) > 1e-6;
  }, [modal, meshMm]);

  // The rotor is solved free-free: nothing holds a rotor cross-section in its
  // own plane, and the backend refuses `pinned` on it.  Keep the control honest
  // rather than letting the user press Solve into a 422.
  useEffect(() => {
    if (body === 'rotor' && support !== 'free') setField('support', 'free');
  }, [body, support, setField]);

  const solveModesFn = st.solveModes;
  const solveCriticalsFn = st.solveCriticals;
  const solveModes = useCallback(() => { void solveModesFn(rpm); }, [solveModesFn, rpm]);
  const solveCriticals = useCallback(() => { void solveCriticalsFn(rpm); },
                                     [solveCriticalsFn, rpm]);

  const modeLines = useMemo(() => (modal?.modes ?? []).map((m) => m.f_hz), [modal]);
  const modalYMax = useMemo(() => {
    const top = modeLines.length ? Math.max(...modeLines) : 0;
    return Math.max(top * 1.15, 100);
  }, [modeLines]);
  const rdYMax = useMemo(() => {
    if (!rotor) return 100;
    const fw = rotor.campbell.forward;
    const top = fw.length ? Math.max(...fw.map((r) => Math.max(...r))) : 0;
    return Math.max(top * 1.1, rotor.rpm_plot_max / 60, 100);
  }, [rotor]);

  const flagged = (modal?.modes ?? []).filter((m) => m.nearest?.flag).length;

  return (
    <Paper sx={{ p: 1.25, mt: 1.5, bgcolor: 'var(--panel)' }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 0.75 }}>
        <Typography sx={{
          fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase',
          letterSpacing: '0.04em', fontWeight: 700,
        }}>
          Modal
        </Typography>
        <Tooltip title="Two different models. MODES are the 2-D in-plane eigenmodes of the iron cross-section, per unit length of stack — ring and ovalisation frequencies, no axial half-waves, every interface BONDED (a linear eigenproblem has no contact state) and no centrifugal prestress, so they are an upper bound. CRITICAL SPEEDS are a 1-D Timoshenko beam of the shaft line with the stack as mass and inertia and gyroscopics included — that is the model that decides whether the machine may be run at its rated speed.">
          <Typography sx={{ ...lbl, cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
            ring modes of the iron, and the shaft's critical speeds
          </Typography>
        </Tooltip>
      </Box>

      {/* ── in-plane modes ─────────────────────────────────────────────── */}
      <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
        <Tooltip title="Which body to solve. The rotor is the core + magnets + sleeve + shaft tube; the stator is the core with its teeth, with the winding copper added as non-structural mass on the slot walls.">
          <Select size="small" value={body} onChange={(e) => setField('body', e.target.value as ModalBody)}
            sx={{ fontSize: 12, height: 30, minWidth: 96 }}>
            <MenuItem value="rotor" sx={{ fontSize: 12 }}>rotor</MenuItem>
            <MenuItem value="stator" sx={{ fontSize: 12 }}>stator</MenuItem>
          </Select>
        </Tooltip>
        <Tooltip title={body === 'rotor'
          ? 'The rotor is solved free-free: nothing holds a rotor cross-section in its own plane, and the three rigid-body modes are found and dropped.'
          : 'Free: the bare core as it would ring on a bench. Pinned: the outer surface held RADIALLY by a housing, tangential motion left free — an exact constraint, not a penalty spring. A pinned ring can still spin rigidly inside its housing, so one zero mode is found and dropped.'}>
          <span>
            <Select size="small" value={support} disabled={body === 'rotor'}
              onChange={(e) => setField('support', e.target.value as ModalSupport)}
              sx={{ fontSize: 12, height: 30, minWidth: 96 }}>
              <MenuItem value="free" sx={{ fontSize: 12 }}>free-free</MenuItem>
              <MenuItem value="pinned" sx={{ fontSize: 12 }}>OD pinned</MenuItem>
            </Select>
          </span>
        </Tooltip>
        <Num label="modes" tip="How many ELASTIC modes to report. The rigid-body modes are found, checked and dropped before this count starts." value={nModes} width={80} onChange={(v) => setField('nModes', v)} />
        <Button variant="contained" size="small" onClick={solveModes} disabled={mBusy || !(rpm > 0)}
          startIcon={mBusy ? <CircularProgress size={13} color="inherit" /> : undefined}>
          {mBusy ? 'Solving' : 'Solve modes'}
        </Button>
        <SolveTimer busy={mBusy} startedAt={st.modal.startedAt} est={st.est.modal}
          what="modal solve" />
        {meshOff && (
          <Tooltip title={`These frequencies were solved on a ${fmt(modal?.mesh.mesh_size_mm, 2)} mm mesh; the Mesh block above now asks for ${meshMm} mm. Frequencies converge from above as the mesh shrinks, so the two are not the same answer — press Solve modes to recompute at this size.`}>
            <Typography sx={{ ...lbl, color: '#fbbf24', cursor: 'help',
              borderBottom: '1px dotted #fbbf24' }}>
              ⚠ modes are on a {fmt(modal?.mesh.mesh_size_mm, 2)} mm mesh
            </Typography>
          </Tooltip>
        )}
        {modalStale && (
          <Tooltip title="These frequencies were solved on a different cross-section than the one loaded now. A mode that was 10 % clear of an excitation on the previous geometry says nothing about this one — press Solve modes.">
            <Typography sx={{ ...lbl, color: '#fbbf24', fontWeight: 700,
              cursor: 'help', borderBottom: '1px dotted #fbbf24' }}>
              ⚠ modes are for a previous geometry
            </Typography>
          </Tooltip>
        )}
        {modal && (
          <Tooltip title={`${solvedIn(modal) || 'no timing in this result'}${modal.mesh.mesh_s ? `, of which ${fmt(modal.mesh.mesh_s, 1)} s was the mesh` : ''}. Measured on the backend, around the mesh, the assembly and the eigensolve. Mesh ${fmt(modal.mesh.mesh_size_mm, 2)} mm, ${modal.mesh.n_dof.toLocaleString()} DOF.`}>
            <Typography sx={{ ...lbl, ml: 'auto', cursor: 'help' }}>
              {modal.mesh.n_triangles.toLocaleString()} tri · P{modal.mesh.element_order} ·{' '}
              {modal.mesh.n_rigid_modes_dropped} rigid dropped
              {solvedIn(modal) && ` · ${solvedIn(modal)}`}
            </Typography>
          </Tooltip>
        )}
      </Box>

      {mErr && <Alert severity="error" sx={{ mb: 1, fontSize: 12 }}>{mErr}</Alert>}
      {!modal && !mErr && !mBusy && (
        <Typography sx={{ ...lbl, mb: 1 }}>
          Press Solve modes to find the ring frequencies of the iron and place them against this machine's excitation orders.
        </Typography>
      )}

      {modal && (
        <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', mb: 1.5 }}>
          {/* the table */}
          <Box sx={{ flex: '1 1 340px', minWidth: 300 }}>
            <Box sx={{
              display: 'grid',
              gridTemplateColumns: '28px minmax(72px,1fr) 40px minmax(96px,1.2fr) 68px',
              alignItems: 'center', rowGap: 0.1, columnGap: 0.5,
            }}>
              {['#', 'f  Hz', 'n', 'nearest', 'margin'].map((h, i) => (
                <Typography key={h} sx={{
                  fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
                  letterSpacing: '0.04em', fontWeight: 700,
                  textAlign: i >= 2 ? 'right' : 'left',
                }}>
                  {h}
                </Typography>
              ))}
              {modal.modes.map((m, i) => {
                const n = m.nearest;
                const on = i === sel;
                const cell = {
                  ...mono, cursor: 'pointer', py: 0.15,
                  bgcolor: on ? 'rgba(120,200,255,0.13)' : 'transparent',
                } as const;
                return (
                  <React.Fragment key={m.index}>
                    <Typography sx={{ ...cell, color: 'var(--text-4)', fontSize: 10 }} onClick={() => setSel(i)}>
                      {m.index}
                    </Typography>
                    <Typography sx={{ ...cell, fontWeight: 700 }} onClick={() => setSel(i)}>
                      {m.f_hz >= 1000 ? m.f_hz.toFixed(0) : m.f_hz.toFixed(1)}
                    </Typography>
                    <Tooltip placement="left" title={m.order === null
                      ? 'This mode has no radial component on the boundary the order is counted on — a pure in-plane shear of the ring. No order is invented for it.'
                      : `Circumferential order ${m.order}: ${m.order === 0 ? 'breathing (the whole ring grows and shrinks)' : m.order === 1 ? 'the ring translates as a body' : `${m.order} pairs of lobes round the circumference`}. Counted from the sign changes of the radial displacement around the ${modal.mesh.order_counted_on} boundary.`}>
                      <Typography sx={{ ...cell, textAlign: 'right' }} onClick={() => setSel(i)}>
                        {m.order ?? '—'}
                      </Typography>
                    </Tooltip>
                    <Tooltip placement="top" title={n?.name
                      ? `${n.name} at ${(n.hz ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} Hz — ${modal.excitations.find((e) => e.name === n.name)?.note ?? ''}`
                      : 'no excitation table (set a speed on the Electromagnetic tab)'}>
                      <Typography sx={{ ...cell, fontSize: 11, textAlign: 'right', color: 'var(--text-2)' }} onClick={() => setSel(i)}>
                        {n?.name ?? '—'}
                      </Typography>
                    </Tooltip>
                    <Tooltip placement="right" title="Separation from the nearest excitation, relative to the EXCITATION. Under 10 % either way is flagged: that is the usual first-pass separation margin — not a standard, the threshold at which an engineer stops and looks.">
                      <Typography sx={{
                        ...cell, textAlign: 'right', fontWeight: 700,
                        color: n?.flag ? '#f87171' : 'var(--text-2)',
                      }} onClick={() => setSel(i)}>
                        {n?.margin_pct === null || n?.margin_pct === undefined ? '—'
                          : `${n.margin_pct > 0 ? '+' : ''}${n.margin_pct.toFixed(1)} %`}
                      </Typography>
                    </Tooltip>
                  </React.Fragment>
                );
              })}
            </Box>
            <Typography sx={{ ...lbl, mt: 0.75, display: 'block' }}>
              {flagged > 0
                ? `${flagged} mode${flagged > 1 ? 's' : ''} within 10 % of an excitation`
                : 'every mode is more than 10 % clear of every excitation'}
              <Tooltip title={modal.assumptions}>
                <span style={{ borderBottom: '1px dotted var(--text-4)', cursor: 'help', marginLeft: 4 }}>
                  assumptions
                </span>
              </Tooltip>
              {modal.winding_mass_kg_per_m > 0 && (
                <Tooltip title="Winding copper, added as lumped non-structural mass on the slot walls: it adds mass and no stiffness, which is what a winding does. Smearing it into soft elements inside the slot would invent a stiffness and fill the first modes with slot artefacts.">
                  <span style={{ marginLeft: 8, cursor: 'help' }}>
                    · copper {fmt(modal.winding_mass_kg_per_m, 2)} kg/m
                  </span>
                </Tooltip>
              )}
            </Typography>
          </Box>

          {/* the mode shape — same viewer as every other field map, and the
              SAME SIZE as the stress map above it: its own full-width row at
              the StressMap height (user 2026-09-08: "сделай этот график точно
              такого же размера, как и предыдущий, во всю страницу"). */}
          {modal.field && (
            <Box sx={{ flex: '1 1 100%', minWidth: 0 }}>
              <ModeView modal={modal} sel={sel} onSel={setSel} exagg={exagg}
                onExagg={(v) => setField('modalExagg', v)} height={460} />
            </Box>
          )}

          {/* modes against the excitation orders — full width too, under the map */}
          <Box sx={{ flex: '1 1 100%', minWidth: 0 }}>
            <Tooltip title="THE STATOR RING. Every mode as a horizontal line — a 2-D in-plane frequency of the iron does not move with speed, because the stator does not turn — crossed by this machine's excitation orders. Where a blue line meets an orange one, that mode is excited at that speed. The vertical marks are rated and ×1.2 rated. This chart answers whether the stator will sing; the Campbell diagram below answers whether the ROTOR passes through a resonance on its way up.">
              <Typography sx={{ ...lbl, cursor: 'help', mb: 0.4, display: 'block' }}>
                STATOR ring modes vs excitation orders
              </Typography>
            </Tooltip>
            <CampbellChart
              rpmMax={rpm * 1.3} rated={rpm} overspeed={rpm * 1.2}
              yMax={modalYMax} excitations={modal.excitations}
              modeLines={modeLines} height={300} />
          </Box>
        </Box>
      )}

      {/* ── the shaft line ─────────────────────────────────────────────── */}
      <Box sx={{ borderTop: '1px solid var(--app-bg)', pt: 1 }}>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 0.75 }}>
          <Tooltip title="The shaft LINE — bearing span, overhangs, shaft diameters, bearing stiffness. The geometry of it does not come from the motor config: it is the drawing, not the electromagnetics, so those fields are assumptions with defaults. The STIFFNESS no longer has to be: name the bearing in 'Shaft & bearings' below and it comes from the catalogue card. Change a field and press Critical speeds.">
            <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
              Shaft line
            </Typography>
          </Tooltip>
          {BEAM_FIELDS.map((f) => (
            <Num key={f.key} label={f.label} tip={f.tip} value={beam[f.key] ?? ''}
              width={f.key === 'bearing_k_n_per_m' ? 108 : 92}
              onChange={(v) => st.setBeam(f.key, v)} />
          ))}
          <Button variant="contained" size="small" onClick={solveCriticals}
            disabled={rBusy || !(rpm > 0)}
            startIcon={rBusy ? <CircularProgress size={13} color="inherit" /> : undefined}>
            {rBusy ? 'Solving' : 'Critical speeds'}
          </Button>
          <SolveTimer busy={rBusy} startedAt={st.rotordyn.startedAt}
            est={st.est.rotordyn} what="critical-speed solve" />
          {rotorStale && (
            <Tooltip title="This Campbell diagram was computed on a different cross-section than the one loaded now — the stack mass and inertia it used are the previous machine's. Press Critical speeds to recompute it.">
              <Typography sx={{ ...lbl, color: '#fbbf24', fontWeight: 700,
                cursor: 'help', borderBottom: '1px dotted #fbbf24' }}>
                ⚠ criticals are for a previous geometry
              </Typography>
            </Tooltip>
          )}
          {rotor && (
            <Tooltip title={`${solvedIn(rotor) || 'no timing in this result'}. Measured on the backend, around the cross-section pass that gives the stack its mass and inertia and the speed sweep behind the Campbell diagram.`}>
              <Typography sx={{ ...lbl, ml: 'auto', cursor: 'help' }}>
                {rotor.layout.n_elements} beam elements
                {solvedIn(rotor) && ` · ${solvedIn(rotor)}`}
              </Typography>
            </Tooltip>
          )}
        </Box>

        {rErr && <Alert severity="error" sx={{ mb: 1, fontSize: 12 }}>{rErr}</Alert>}
        {!rotor && !rErr && !rBusy && (
          <Typography sx={{ ...lbl }}>
            Press Critical speeds for the forward and backward whirl of this shaft line against speed.
          </Typography>
        )}

        {rotor && (
          <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
            <Box sx={{ flex: '1 1 300px', minWidth: 280 }}>
              <Tooltip title="THE ROTOR on its bearings, not the stator ring above. Unbalance is a synchronous FORWARD force, so on isotropic bearings it excites the forward branches: a critical speed is where a forward curve crosses the 1× line. The backward crossings are listed too — an anisotropic mount or a bent shaft will find them — and marked as not excited by unbalance.">
                <Typography sx={{ ...lbl, cursor: 'help', display: 'block', mb: 0.4 }}>
                  ROTOR critical speeds vs rated {Math.round(rotor.rated_rpm).toLocaleString()} rpm
                </Typography>
              </Tooltip>
              <Box sx={{
                display: 'grid', gridTemplateColumns: '78px 1fr 78px',
                alignItems: 'center', rowGap: 0.1, columnGap: 0.5,
              }}>
                {rotor.critical_speeds.map((c, i) => (
                  <React.Fragment key={`${c.whirl}-${c.mode}-${i}`}>
                    <Typography sx={{
                      ...mono, fontWeight: 700,
                      color: c.excited_by_unbalance
                        ? (Math.abs(c.margin_vs_rated_pct ?? 999) < 20 ? '#f87171' : 'var(--text-0)')
                        : 'var(--text-3)',
                    }}>
                      {Math.round(c.rpm).toLocaleString()}
                    </Typography>
                    <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>
                      {c.whirl} · mode {c.mode} · {fmt(c.hz, 0)} Hz
                      {c.beyond_plot && ' · off-plot'}
                    </Typography>
                    <Typography sx={{ ...mono, fontSize: 11, textAlign: 'right', color: 'var(--text-2)' }}>
                      {c.margin_vs_rated_pct === null ? '—'
                        : `${c.margin_vs_rated_pct > 0 ? '+' : ''}${c.margin_vs_rated_pct.toFixed(0)} %`}
                    </Typography>
                  </React.Fragment>
                ))}
              </Box>
              <Typography sx={{
                fontSize: 11, mt: 0.75,
                color: rotor.verdict.startsWith('supercritical') ? '#fbbf24' : '#4ade80',
              }}>
                {rotor.verdict}
                <Tooltip title={rotor.assumptions}>
                  <span style={{ borderBottom: '1px dotted var(--text-4)', cursor: 'help', marginLeft: 4, color: 'var(--text-3)' }}>
                    assumptions
                  </span>
                </Tooltip>
              </Typography>
              <Tooltip title={`Shaft ${rotor.shaft.material}, OD ${fmt(rotor.shaft.od_mm, 1)} / ID ${fmt(rotor.shaft.id_mm, 1)} mm, EI ${fmt(rotor.shaft.EI_n_m2, 0)} N·m², ${fmt(rotor.shaft.mass_kg_per_m, 2)} kg/m. Stack ${fmt(rotor.stack.length_mm, 0)} mm adding ${fmt(rotor.stack.added_mass_kg, 2)} kg and ${fmt(rotor.stack.polar_inertia_kg_m2, 4)} kg·m² polar. Bearings at ${fmt(rotor.layout.bearing_a_mm, 0)} and ${fmt(rotor.layout.bearing_b_mm, 0)} mm on a ${fmt(rotor.layout.total_length_mm, 0)} mm shaft; the stack runs ${fmt(rotor.layout.stack_from_mm, 0)}…${fmt(rotor.layout.stack_to_mm, 0)} mm. Assumed inputs: ${rotor.inputs.assumed.join(', ')}.`}>
                <Typography sx={{ ...lbl, mt: 0.4, display: 'block', cursor: 'help', borderBottom: '1px dotted var(--text-4)', width: 'fit-content' }}>
                  shaft line as solved
                </Typography>
              </Tooltip>
            </Box>
            <Box sx={{ flex: '1 1 100%', minWidth: 0 }}>
              <Tooltip title="Campbell diagram. Green solid = forward whirl (rises with speed, and unbalance excites it), grey dashed = backward (falls). Orange rays are the excitation orders; the 1× ray is the one whose crossings are the critical speeds, marked red. The plot stops at 1.3 × rated — the sweep behind it runs to 3 × rated, so a critical above the band is still listed on the left.">
                <Typography sx={{ ...lbl, cursor: 'help', mb: 0.4, display: 'block' }}>
                  whirl vs speed
                </Typography>
              </Tooltip>
              <CampbellChart
                rpmMax={rotor.rpm_plot_max} rated={rotor.rated_rpm}
                overspeed={rotor.overspeed_rpm} yMax={rdYMax}
                excitations={rotor.excitations.filter((e) => e.order !== null && e.order <= 2)}
                branches={rotor.campbell} criticals={rotor.critical_speeds}
                height={320} />
            </Box>
          </Box>
        )}

        {/* The bearings section moved to the top of the tab, under the mesh /
            contacts block (2026-09-08) — the stiffness it seeds still lands in
            the shaft-line field above through the store. */}
      </Box>
    </Paper>
  );
};

export default ModalSection;
