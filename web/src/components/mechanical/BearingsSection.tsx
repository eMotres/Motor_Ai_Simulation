/** "Shaft & bearings" — the bearings the MACHINE is built with.
 *
 * Added 2026-09-08.  The block above it (Shaft line) is honest about being
 * assumptions: span, overhangs and bearing stiffness are typed by hand because
 * nothing in the motor config carried them.  `bearing_k_n_per_m` was the worst
 * of them — "the single biggest lever on the answer", ASSUMED at 2e8 across
 * every machine from an 8 mm miniature to a 55 mm spindle bearing, which span
 * three decades of real stiffness.
 *
 * Naming the actual part fixes two things at once:
 *   • the critical-speed solve gets a stiffness with a part number behind it;
 *   • the loss picture gets its mechanical half.  On the measured 150 mm free
 *     run the bearings were 43-170 W out of 83-319 W — the largest single term
 *     below 3000 rpm — and until now the datasheet said they were "not
 *     included" (docs/measurements/2026-08-04_noload_decomposition_150mm.md).
 *
 * The pickers are the PANEL's working copy (persisted like every other field on
 * this tab, server-side, so they survive a reload and follow the user between
 * browsers).  "Save to machine" is what makes them the machine's, through the
 * same PATCH the battery uses.  Until it is pressed, the numbers on screen are
 * a PREVIEW and say so.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, MenuItem, Select, TextField, Tooltip,
  Typography,
} from '@mui/material';

import { useMechanicalStore } from '../../stores/mechanicalStore';
import {
  bearingsChipLabel, fetchBearingLibrary, fetchBearingLosses,
  loadMachineBearings, saveMachineBearings,
} from '../../lib/machineBearings';
import type {
  BearingLibrary, BearingLosses, BearingsBlock, Lubrication,
} from '../../lib/machineBearings';
import { fmt } from './api';

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const mono = { fontFamily: 'monospace', fontSize: 12 } as const;

/** "1.40e6" — the speed parameter every bearing catalogue is compared on. */
const sci = (v: number | null | undefined): string =>
  (v == null || !Number.isFinite(v)) ? '—' : v.toExponential(2).replace('e+', 'e');

/** The stiffness the shaft-line field may be overwritten with.
 *
 *  Same bargain as the battery's V_bus prefill (`busIsPrefill`): a value WE put
 *  there is ours to replace when the card changes; a number the user typed is
 *  theirs and survives.  `seed` is the stiffness this section last wrote.
 */
// NOT exported: a component module that exports a plain function loses Vite's
// Fast Refresh ("kIsPrefill export is incompatible") and every edit to this
// file then reloads the user's whole page mid-solve (2026-09-08).
function kIsPrefill(current: string | undefined, seed: string | null,
                           factoryDefault: number): boolean {
  const c = String(current ?? '').trim();
  if (!c) return true;
  const n = Number(c);
  if (!Number.isFinite(n)) return true;
  if (seed != null && Number.isFinite(Number(seed))) {
    return Math.abs(n - Number(seed)) <= 1e-6 * Math.max(1, Math.abs(n));
  }
  return Math.abs(n - factoryDefault) <= 1e-6 * factoryDefault;
}

const FACTORY_K = 2e8;   // DEFAULT_BEAM.bearing_k_n_per_m

const BearingsSection: React.FC<{ rpm: number }> = ({ rpm }) => {
  const st = useMechanicalStore();
  const { brg, brgKSeed, beam } = st;
  const setField = st.set;

  const [lib, setLib] = useState<BearingLibrary | null>(null);
  const [ctx, setCtx] = useState<{ die?: string | null; config?: string | null;
                                   canWrite: boolean;
                                   saved: BearingsBlock | null }>(
    { canWrite: false, saved: null });
  const [res, setRes] = useState<BearingLosses | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const names = useMemo(
    () => Object.keys(lib?.bearings ?? {}).sort(), [lib]);

  // ── the library and the machine's own pair ───────────────────────────────
  useEffect(() => { void fetchBearingLibrary().then(setLib); }, []);

  // Read through a ref, never a dependency: making `loadMachine` depend on the
  // picker would re-fetch the family context on every keystroke, and a reload
  // landing mid-selection is exactly what the guard below exists to stop.
  const pickedA = useRef(brg.A);
  pickedA.current = brg.A;
  // Which MACHINE the pickers were last seeded for.  The draft in the pickers
  // is per browser; the machine is per server — so after a load of another
  // machine the section showed the previous one's 71910 pair on a machine that
  // had none, with "unsaved on the machine" as the only hint (2026-09-08,
  // G2-L40 after the Ø200).  A new machine re-seeds the pickers from ITS saved
  // pair — or clears them when it has none.
  const seededFor = useRef<string>('');
  const loadMachine = useCallback(async () => {
    const r = await loadMachineBearings();
    setCtx({ die: r.die, config: r.config, canWrite: r.canWrite,
             saved: r.bearings });
    const key = `${r.die ?? ''}/${r.config ?? ''}`;
    const fromSaved = r.bearings ? {
      A: r.bearings.A?.card ?? '', B: r.bearings.B?.card ?? '',
      lubrication: r.bearings.lubrication,
      preloadN: String(r.bearings.preload_n ?? 0),
      tempSource: r.bearings.temp_source,
      tempC: String(r.bearings.temp_c ?? 70),
    } : { A: '', B: '', lubrication: 'grease' as const, preloadN: '0',
          tempSource: 'thermal' as const, tempC: '70' };
    if (key !== seededFor.current) {
      seededFor.current = key;
      setField('brg', fromSaved);
      return;
    }
    // Same machine: its pair seeds the pickers only while the panel has none —
    // a selection the user is in the middle of making must not be replaced by
    // a background reload.
    if (r.bearings && !pickedA.current) setField('brg', fromSaved);
  }, [setField]);

  useEffect(() => {
    void loadMachine();
    const on = () => { void loadMachine(); };
    window.addEventListener('family-context-changed', on);
    window.addEventListener('family-changed', on);
    return () => {
      window.removeEventListener('family-context-changed', on);
      window.removeEventListener('family-changed', on);
    };
  }, [loadMachine]);

  // ── picking a card sets the shaft line's stiffness ───────────────────────
  useEffect(() => {
    const card = lib?.bearings?.[brg.A];
    const k = card?.stiffness_n_per_m;
    if (!k || !Number.isFinite(Number(k))) return;
    const next = String(k);
    if (String(beam.bearing_k_n_per_m ?? '') === next) return;
    if (!kIsPrefill(beam.bearing_k_n_per_m, brgKSeed, FACTORY_K)) return;
    st.setBeam('bearing_k_n_per_m', next);
    setField('brgKSeed', next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brg.A, lib]);

  const kOverridden = !kIsPrefill(beam.bearing_k_n_per_m, brgKSeed, FACTORY_K);

  // ── the temperature: this panel's field, or the Thermal tab's answer ─────
  // 2026-09-08 the store carries ONE temperature per solid instead of a rotor
  // and a sleeve maximum, so this reads the SHAFT — the part the bearing is
  // actually pressed onto — and only falls back to the rotor iron when the mesh
  // resolved no shaft.  The MAXIMUM, not the mean: unlike a thermal expansion
  // (which is a bulk strain and takes the part's average) a grease viscosity is
  // wanted at the hottest the ring can get, so this stays the upper bound the
  // tooltip has always described.
  const bearingTemp = useMemo(() => {
    const t = st.thermalTemps;
    if (!t || t.stale) return null;
    for (const p of [t.shaft, t.rotor]) {
      const v = p.max ?? p.avg;
      if (v != null && Number.isFinite(v)) return { v, part: p === t.shaft ? 'shaft' : 'rotor iron' };
    }
    return null;
  }, [st.thermalTemps]);

  const tempC = useMemo(() => {
    if (brg.tempSource === 'thermal' && bearingTemp) return bearingTemp.v;
    const v = Number(brg.tempC);
    return Number.isFinite(v) ? v : 70;
  }, [brg.tempSource, brg.tempC, bearingTemp]);

  const thermalUnavailable = brg.tempSource === 'thermal' && !bearingTemp;

  // ── the numbers, previewed at the duty speed ─────────────────────────────
  const abort = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!(rpm > 0) || !brg.A) { setRes(null); return; }
    abort.current?.abort();
    const ac = new AbortController();
    abort.current = ac;
    const t = window.setTimeout(() => {
      void fetchBearingLosses({
        rpm, tempC, preloadN: Number(brg.preloadN) || 0,
        lubrication: brg.lubrication as Lubrication,
        cardA: brg.A, cardB: brg.B || brg.A,
        die: ctx.die, cfg: ctx.config, signal: ac.signal,
      }).then((r) => { if (!ac.signal.aborted) setRes(r); });
    }, 250);   // one fetch per settled edit, not one per keystroke
    return () => { window.clearTimeout(t); ac.abort(); };
  }, [rpm, tempC, brg.A, brg.B, brg.preloadN, brg.lubrication,
      ctx.die, ctx.config]);

  const save = useCallback(async () => {
    setBusy(true); setErr(null);
    const block: BearingsBlock = {
      A: brg.A ? { card: brg.A } : null,
      B: (brg.B || brg.A) ? { card: brg.B || brg.A } : null,
      lubrication: brg.lubrication as Lubrication,
      preload_n: Number(brg.preloadN) || 0,
      temp_source: brg.tempSource,
      // The MANUAL field is what is stored even when the panel is reading the
      // Thermal tab: a temperature taken from a solve is a READING, not a
      // property of the machine, and freezing it into the yaml would make the
      // datasheet quote whatever the tab happened to be showing.
      temp_c: Number(brg.tempC) || null,
    };
    const r = await saveMachineBearings(ctx.die, ctx.config, ctx.canWrite, block);
    setBusy(false);
    setMsg(r.message);
    if (r.ok) { setCtx((c) => ({ ...c, saved: r.bearings })); }
    else setErr(r.message);
  }, [brg, ctx]);

  useEffect(() => {
    if (!msg) return;
    const t = window.setTimeout(() => setMsg(null), 6000);
    return () => window.clearTimeout(t);
  }, [msg]);

  const savedLabel = bearingsChipLabel(ctx.saved);
  const dirty = useMemo(() => {
    const s = ctx.saved;
    if (!s) return !!brg.A;
    return (s.A?.card ?? '') !== brg.A
      || (s.B?.card ?? '') !== (brg.B || brg.A)
      || s.lubrication !== brg.lubrication
      || Math.abs((s.preload_n ?? 0) - (Number(brg.preloadN) || 0)) > 1e-9;
  }, [ctx.saved, brg]);

  const cardTip = (name: string): string => {
    const c = lib?.bearings?.[name];
    if (!c) return 'pick a bearing from the library';
    return `${c.description || name}. ${c.d}×${c.D}×${c.B} mm, d_m ${c.d_m} mm`
      + (c.C_kn ? `, C ${c.C_kn} kN / C0 ${c.C0_kn} kN` : '')
      + (c.stiffness_n_per_m
         ? `, radial stiffness ~${sci(c.stiffness_n_per_m)} N/m (approximate)` : '')
      + (c.n_limit_grease_rpm
         ? `. Limit ${c.n_limit_grease_rpm.toLocaleString()} rpm on grease` : '')
      + (c.n_limit_oil_air_rpm
         ? `, ${c.n_limit_oil_air_rpm.toLocaleString()} rpm on oil-air` : '')
      + (c.note ? `. ${c.note}` : '');
  };

  return (
    <Box sx={{ borderTop: '1px solid var(--app-bg)', pt: 1, mt: 1 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap',
        mb: 0.75 }}>
        <Tooltip title="The bearings this MACHINE is built with — a real catalogue part, not a panel assumption. Picking one sets the shaft line's bearing stiffness above, and gives the loss picture its mechanical half: bearing friction (SKF frictional-moment model) and rotor windage, which on the measured 150 mm free run were the largest single loss below 3000 rpm. Analytic, not FEM.">
          <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help',
            borderBottom: '1px dotted var(--text-4)' }}>
            Shaft &amp; bearings
          </Typography>
        </Tooltip>

        <Tooltip title={cardTip(brg.A)}>
          <Select size="small" displayEmpty value={names.includes(brg.A) ? brg.A : ''}
            onChange={(e) => setField('brg', { ...brg, A: String(e.target.value) })}
            sx={{ fontSize: 11, height: 30, minWidth: 168 }}>
            <MenuItem value="" sx={{ fontSize: 11 }}>bearing A — none</MenuItem>
            {names.map((n) => (
              <MenuItem key={n} value={n} sx={{ fontSize: 11 }}>{n}</MenuItem>
            ))}
          </Select>
        </Tooltip>

        <Tooltip title={brg.B ? cardTip(brg.B)
          : 'Bearing B. Leave empty for a symmetric shaft line — the same card is used at both ends.'}>
          <Select size="small" displayEmpty value={names.includes(brg.B) ? brg.B : ''}
            onChange={(e) => setField('brg', { ...brg, B: String(e.target.value) })}
            sx={{ fontSize: 11, height: 30, minWidth: 168 }}>
            <MenuItem value="" sx={{ fontSize: 11 }}>bearing B — same as A</MenuItem>
            {names.map((n) => (
              <MenuItem key={n} value={n} sx={{ fontSize: 11 }}>{n}</MenuItem>
            ))}
          </Select>
        </Tooltip>

        <Tooltip title="Grease has no oil bath to churn, so M_drag = 0 — and its base oil is thick, which is what caps the speed. Oil-air is thinner and reaches a far higher n·d_m, at the price of a supply. The card's speed limit is checked against whichever you pick.">
          <Select size="small" value={brg.lubrication}
            onChange={(e) => setField('brg',
              { ...brg, lubrication: String(e.target.value) as Lubrication })}
            sx={{ fontSize: 11, height: 30, minWidth: 92 }}>
            <MenuItem value="grease" sx={{ fontSize: 11 }}>grease</MenuItem>
            <MenuItem value="oil_air" sx={{ fontSize: 11 }}>oil-air</MenuItem>
          </Select>
        </Tooltip>

        <Tooltip title="Axial preload per bearing [N] — what an angular-contact pair is set up with. It raises the sliding term and, at speed, adds to the ball load. 0 for a plain deep-groove pair.">
          <TextField label="preload N" size="small" value={brg.preloadN}
            onChange={(e) => setField('brg', { ...brg, preloadN: e.target.value })}
            sx={{ width: 92 }} inputProps={{ style: { fontSize: 11 } }}
            InputLabelProps={{ style: { fontSize: 11 } }} />
        </Tooltip>

        <Tooltip title="Where the bearing temperature comes from. It matters: the grease base oil thins with heat, and the rolling term goes as ν^0.6, so 40 °C and 90 °C are a factor of ~2 apart. 'from Thermal' takes the last Thermal-tab ROTOR maximum for this machine — an upper bound, since the outer ring runs cooler.">
          <Select size="small" value={brg.tempSource}
            onChange={(e) => setField('brg',
              { ...brg, tempSource: String(e.target.value) as 'manual' | 'thermal' })}
            sx={{ fontSize: 11, height: 30, minWidth: 116 }}>
            <MenuItem value="manual" sx={{ fontSize: 11 }}>temp: manual</MenuItem>
            <MenuItem value="thermal" sx={{ fontSize: 11 }}>from Thermal</MenuItem>
          </Select>
        </Tooltip>

        {brg.tempSource === 'manual' ? (
          <Tooltip title="Bearing temperature [°C] the grease viscosity is interpolated at (Walther / ASTM D341 between the card's 40 and 100 °C points).">
            <TextField label="temp °C" size="small" value={brg.tempC}
              onChange={(e) => setField('brg', { ...brg, tempC: e.target.value })}
              sx={{ width: 84 }} inputProps={{ style: { fontSize: 11 } }}
              InputLabelProps={{ style: { fontSize: 11 } }} />
          </Tooltip>
        ) : (
          <Tooltip title={thermalUnavailable
            ? `No fresh Thermal result for this machine — ${fmt(tempC, 0)} °C from the manual field is used instead. Solve the Thermal tab to use its shaft maximum.`
            : `${bearingTemp?.part === 'shaft' ? 'Shaft' : 'Rotor-iron'} maximum from the last Thermal solve: ${fmt(tempC, 0)} °C. The peak, not the mean — a grease viscosity is wanted at the hottest the ring can get.`}>
            <Typography sx={{ ...mono, color: thermalUnavailable ? '#fbbf24' : 'var(--text-2)',
              cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
              {fmt(tempC, 0)} °C{thermalUnavailable ? ' ⚠' : ''}
            </Typography>
          </Tooltip>
        )}

        <Button variant="contained" size="small" onClick={() => void save()}
          disabled={busy || !brg.A}
          startIcon={busy ? <CircularProgress size={13} color="inherit" /> : undefined}>
          {busy ? 'Saving' : 'Save to machine'}
        </Button>

        <Tooltip title={ctx.canWrite
          ? `Written into ${ctx.die ?? '?'}/${ctx.config ?? '?'}.yaml — the same file the battery lives in, so the datasheet and the Electromagnetic summary read one source.`
          : 'This machine is not yours to write, so the pair is remembered for it in THIS browser (keyed by the machine, never globally).'}>
          <Typography sx={{ ...lbl, cursor: 'help', ml: 'auto',
            color: dirty ? '#fbbf24' : 'var(--text-3)' }}>
            {dirty ? 'unsaved · ' : ''}on the machine: {savedLabel}
          </Typography>
        </Tooltip>
      </Box>

      {err && <Alert severity="error" sx={{ mb: 1, fontSize: 12 }}>{err}</Alert>}
      {msg && !err && (
        <Typography sx={{ ...lbl, color: '#4ade80', mb: 0.5 }}>{msg}</Typography>
      )}

      {!brg.A && (
        <Typography sx={{ ...lbl }}>
          Pick a bearing to give the shaft line a real stiffness and the losses their
          mechanical half.
        </Typography>
      )}

      {/* one line per bearing: the speed check, with M and P in the tooltip */}
      {brg.A && res?.has_bearings && (res.bearings ?? []).map((b) => {
        const s = b.speed;
        const colour = s.ok === false ? '#f87171'
          : s.ok === null ? 'var(--text-3)'
          : (s.ratio ?? 0) > 0.9 ? '#fbbf24' : '#4ade80';
        return (
          <Tooltip key={b.end} title={
            `${b.bearing} at ${Math.round(res.rpm).toLocaleString()} rpm: `
            + `M = ${b.M_total_Nm.toFixed(4)} N·m, P = ${fmt(b.P_W, 1)} W. `
            + `Rolling ${b.M_rr_Nm.toFixed(4)}, sliding ${b.M_sl_Nm.toExponential(1)}, `
            + `seal ${b.M_seal_Nm.toFixed(4)} N·m; grease ν = ${fmt(b.nu_mm2_s, 2)} mm²/s; `
            + `F_r = ${fmt(b.F_r_N, 1)} N, F_a = ${fmt(b.F_a_N, 0)} N`
            + (b.F_g_N > 0 ? `, ball centrifugal F_g = ${fmt(b.F_g_N, 0)} N` : '')
            + `. SKF frictional-moment model — analytic, not FEM.`
            + (b.seal_estimate ? ' Seal drag is an ESTIMATE for this seal type.' : '')
            + (res.preview ? ' PREVIEW — press Save to machine to make it this motor\'s.' : '')
          }>
            <Typography sx={{ ...mono, cursor: 'help', display: 'block' }}>
              <span style={{ color: 'var(--text-3)' }}>{b.end} {b.bearing}</span>
              {'  n·d_m '}{sci(s.n_dm)}
              <span style={{ color: 'var(--text-3)' }}>
                {s.n_dm_limit != null ? ` (limit ${sci(s.n_dm_limit)}, ` : ' ('}
                <span style={{ color: colour, fontWeight: 700 }}>{s.verdict}</span>)
              </span>
              {'  ·  '}{fmt(b.P_W, 1)} W
            </Typography>
          </Tooltip>
        );
      })}

      {brg.A && res?.has_bearings && (
        <Tooltip title={(res.notes ?? []).join('  ')}>
          <Typography sx={{ ...lbl, mt: 0.4, display: 'block', cursor: 'help',
            borderBottom: '1px dotted var(--text-4)', width: 'fit-content' }}>
            pair {fmt(res.P_bearings_W, 1)} W + windage {fmt(res.P_windage_W, 2)} W
            {' = '}{fmt(res.P_mech_extra_W, 1)} W mechanical
            {kOverridden ? ' · shaft-line k is your own, not the card\'s' : ''}
          </Typography>
        </Tooltip>
      )}
    </Box>
  );
};

export default BearingsSection;
