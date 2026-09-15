/**
 * "Add to Compare" — snapshot the CURRENT design (geometry + mesh + operating
 * point) together with the FEM summary on screen, and store it SERVER-side via
 * /api/sims/saved (config/saved_simulations.json).  Comparison points therefore
 * survive a reload, a different browser and a different machine — localStorage
 * would not.  ComparePanel renders these rows.
 *
 * Three physics file rows here, not one (user 2026-09-07: *"нужно везде сделать
 * такую же кнопку для сравнения всех величин в механических и температурных
 * моделированиях"*).  They share `buildParams()` — the machine is the machine —
 * and differ only in what they add: the Mechanical and Thermal tabs each write
 * their OWN inputs into `params` (prefixed `mech_` / `therm_`, so they can never
 * collide with a geometry key) and their own answer into `results.mechanical` /
 * `results.thermal`.  The EM summary keys stay FLAT, exactly where they have
 * always been, so every row already stored keeps rendering unchanged.
 */
import { readMeshSettings, readSimSettings } from '../common/motorSettings';
import { liveGeoSig } from '../common/geoSig';
import { isStale, useMechanicalStore } from '../../stores/mechanicalStore';
import { useThermalStore } from '../../stores/thermalStore';
import { CONTACT_PAIRS } from '../mechanical/api';
import { outerCooling } from '../thermal/types';
import { mechanicalRowFromResult, thermalRowFromResult } from './resultRows';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

/**
 * Flat params, keyed the way ComparePanel's PARAM_META / column-picker expects.
 * readSimSettings() speaks the PRESET vocabulary (max_current, phase_offset_deg),
 * so the operating point is re-keyed here; geometry comes straight from the config
 * so every dimension the user might compare on is captured, not a hand-picked few.
 *
 * `summary` is the EM run whose operating point this point is ABOUT.  It defaults
 * to the one on screen; the Mechanical and Thermal callers pass `{}` when that
 * run belongs to another machine, so the row falls back to the live settings
 * instead of quoting a different motor's current and angle.
 */
async function buildParams(summary?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const sim  = readSimSettings()  as Record<string, unknown>;
  const mesh = readMeshSettings() as Record<string, unknown>;
  const geometry: Record<string, number> = {};
  try {
    const cfg = await (await fetch(`${API}/api/config`)).json();
    for (const [k, v] of Object.entries(cfg?.geometry ?? {})) {
      if (typeof v === 'number') geometry[k] = v;
    }
  } catch { /* best-effort: the operating point alone is still comparable */ }
  // The operating point must be the one the RESULTS were solved at, not whatever
  // the panel is set to now — the two drift apart as soon as the user edits I or γ
  // without re-running (the summary card flags exactly that as "stale"), and a
  // comparison row pairing one design's inputs with another's outputs is a lie.
  const s = (summary ?? readShownSummary()) as Record<string, unknown>;
  const solved = (v: unknown, fallback: unknown) => (Number.isFinite(Number(v)) ? Number(v) : fallback);
  // MATERIALS are part of the design, not decoration: the same geometry with a
  // different magnet or steel is a different machine and a different row. They
  // were missing from the snapshot, so a stored point could be applied back as
  // geometry only and silently picked up whatever materials happened to be
  // assigned at the time — which is how a Fe16N2 result gets re-checked against
  // NdFeB without a word. Prefixed so they never collide with a geometry key.
  const materials: Record<string, string> = {};
  try {
    const cfg = await (await fetch(`${API}/api/config`)).json();
    for (const [part, name] of Object.entries(cfg?.materials ?? {})) {
      if (typeof name === 'string' && name) materials[`mat_${part}`] = name;
    }
  } catch { /* best-effort — a point without materials is still comparable */ }
  return {
    ...materials,
    // The machine stamp rides WITH the point, exactly as the geometry fields do
    // — one string that says "this row's numbers were solved on this motor", so
    // a stored point can be checked instead of assumed.  `geo_sig` is the
    // geometry captured above; `geo_sig_solved` is the stamp of the RUN whose
    // results this row carries.  They differ when the user edited the machine
    // and did not re-run, which is precisely the row that must not be believed.
    // Both read in the STORE's dialect (`liveGeoSig` / the run's own stamp), so
    // the two are comparable with `===`; the flat geometry fields below stay in
    // the config's dialect because they are display columns, not identity.
    geo_sig:        liveGeoSig(),
    geo_sig_solved: (s._geoSig as string) ?? '',
    // operating point
    I_phase_rms:        solved(s.I_phase_rms_A, sim.max_current),
    gamma_deg:          solved(s.gamma_deg,     sim.phase_offset_deg),
    rpm:                solved(s.rpm,           sim.rpm),
    frequency_hz:       sim.frequency,
    coil_temp_c:        solved(s.coil_temp_C,   sim.coil_temp_c),
    end_winding_factor: solved(s.end_winding_factor, sim.end_winding_factor),
    connection:         sim.connection,
    star_delta:         sim.star_delta ?? s.star_delta ?? 'star',
    steps_per_period:   sim.steps_per_period,
    // mesh
    n_sectors:    mesh.n_sectors,
    mesh_size_mm: mesh.mesh_size_mm,
    min_size_mm:  mesh.min_size_mm,
    // geometry
    ...geometry,
  };
}

/** The summary the user is actually LOOKING at — the VIEW copy the summary
 *  card publishes (pressed buttons applied + view_flags saying which; user
 *  2026-08-25: Compare must record what the screen shows).  Falls back to the
 *  raw persisted summary for a card that predates the view publisher. */
function readShownSummary(): Record<string, unknown> {
  try {
    const view = JSON.parse(localStorage.getItem('sim.viewSummary') || 'null');
    const raw = JSON.parse(localStorage.getItem('sim.lastSummary') || 'null');
    // The view must describe the SAME run as the raw summary (same torque
    // base modulo the 3D factor is hard to compare — use rpm+current+gamma).
    if (view && raw
        && view.rpm === raw.rpm
        && view.I_phase_rms_A === raw.I_phase_rms_A
        && view.gamma_deg === raw.gamma_deg) return view;
    return raw || view || {};
  } catch { return {}; }
}

/** True when there is a FEM result on screen worth snapshotting. */
export function hasSummaryToSave(): boolean {
  const s = readShownSummary();
  return Number.isFinite(Number((s as any).T_em_avg_Nm));
}

/** Default label: the MOTOR's name and when it was saved — nothing else (user
 *  request, 2026-08-04).  The current and gamma are columns of the row already,
 *  so repeating them in the name only crowded it.  Renameable in Compare. */
export async function defaultPointName(): Promise<string> {
  const when = new Date().toLocaleString(undefined,
    { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  // The loaded die / configuration / duty IS the design's identity (user
  // rule): "CILN28 200 / M1-L200 / top-speed · 20.08 09:41".
  try {
    const c = await (await fetch(`${API}/api/family/context`)).json();
    if (c?.active && c.die && c.config) {
      const duty = c.duty ? ` / ${c.duty}` : '';
      return `${c.die} / ${c.config}${duty} · ${when}`;
    }
  } catch { /* no family context — fall back to the active motor's name */ }
  let motor = '';
  try {
    const raw = localStorage.getItem('motor.active');
    const m = raw ? JSON.parse(raw) as { name?: string } : null;
    motor = String(m?.name ?? '').trim();
  } catch { /* no active motor — the timestamp alone still separates points */ }
  return motor ? `${motor} · ${when}` : when;
}

export async function addCurrentPointToCompare(name: string): Promise<void> {
  const params  = await buildParams();
  const results = readShownSummary();
  // A comparison row pairing one machine's geometry with another machine's
  // results is not a data-quality nit — it is the whole point of the table
  // being wrong, permanently, in a store that outlives the session.  Refuse.
  const solved = String(params.geo_sig_solved || '');
  const live   = String(params.geo_sig || '');
  if (solved && live && solved !== live) {
    throw new Error(
      'These results were solved on a DIFFERENT machine than the one loaded — '
      + 'the point would pair this geometry with another motor\'s numbers. '
      + 'Run Simulation on the current geometry first, then add the point.');
  }
  await postPoint(name, params, results);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * The other two physics: Mechanical and Thermal
 *
 * Same library, same row, same guard.  A row may carry an EM summary AND a
 * temperature block at once — that is the point of one table: torque, efficiency
 * and the hot-spot of the same machine on one line.  What it may never carry is
 * two machines, so the EM half is included only when the run on screen was
 * solved on the geometry currently loaded (`emSummaryIfLive`), and the physics
 * half only when its own result is not stale.
 * ═══════════════════════════════════════════════════════════════════════════ */

/** A real number, or `undefined` — never `NaN`, never `Number('') === 0`. */
const n = (v: unknown): number | undefined => {
  if (v === null || v === undefined || v === '') return undefined;
  const x = Number(v);
  return Number.isFinite(x) ? x : undefined;
};
/** A non-empty string, or `undefined`. */
const t = (v: unknown): string | undefined =>
  (typeof v === 'string' && v.trim() !== '' ? v : undefined);

/** Drop the `undefined` entries, so an absent input is an absent COLUMN rather
 *  than a cell that reads as a zero the user never typed. */
function defined(o: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(o)) if (v !== undefined) out[k] = v;
  return out;
}

/**
 * The EM summary — but only when it describes the machine that is loaded.
 *
 * Exactly the guard `addCurrentPointToCompare` applies, reused rather than
 * re-implemented: a mechanical row that quietly carried the previous motor's
 * torque and efficiency would be the same lie, just harder to spot because the
 * user came for the stresses.
 */
function emSummaryIfLive(): Record<string, unknown> {
  const s = readShownSummary();
  if (!Number.isFinite(Number(s.T_em_avg_Nm))) return {};
  const solved = String(s._geoSig ?? '');
  const live = liveGeoSig();
  if (solved && live && solved !== live) return {};
  return s;
}

/** POST one row and let the Compare tab know, whatever physics built it — ONE
 *  place, so a point added from any tab lands the same way and fires the same
 *  event the Compare panel is listening for. */
async function postPoint(name: string, params: Record<string, unknown>,
                         results: Record<string, unknown>): Promise<void> {
  const r = await fetch(`${API}/api/sims/saved`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, params, results }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 160)}`);
  try { window.dispatchEvent(new CustomEvent('compare-points-changed')); } catch { /* SSR */ }
}

/* ── Mechanical ──────────────────────────────────────────────────────────── */

/** Why the Mechanical tab has nothing to add, as a sentence — or `null`.
 *  The button is disabled on this verdict and says it in its tooltip, rather
 *  than looking broken when pressed. */
export function mechanicalPointIssue(): string | null {
  const s = useMechanicalStore.getState();
  if (!s.stress.data) return 'nothing has been solved on the Mechanical tab yet';
  if (isStale(s.stress.geoSig, s.stress.backendStale)) {
    return 'the stress result is for a previous geometry — press Solve again first';
  }
  return null;
}

/**
 * The Mechanical tab's answer as a Compare row.
 *
 * The INPUTS are read off the RESULT wherever the result echoes them
 * (`res.rpm`, `res.interference_mm`, `res.thermal.rotor_temp_c`, the contacts,
 * the mesh size), not off the panel's fields.  The two drift apart the moment
 * the user edits a field without re-solving — and unlike a geometry edit, that
 * drift is invisible to the staleness check, so a row built from the fields
 * would pair one run's numbers with another run's boundary conditions.
 */
export async function addMechanicalPointToCompare(name: string): Promise<void> {
  const st = useMechanicalStore.getState();
  const res = st.stress.data;
  if (!res) {
    throw new Error('There is no mechanical result to add — press Solve on the '
      + 'Mechanical tab first.');
  }
  if (isStale(st.stress.geoSig, st.stress.backendStale)) {
    throw new Error(
      'This stress result was solved on a DIFFERENT machine than the one loaded — '
      + 'the point would pair this geometry with another motor\'s numbers. '
      + 'Press Solve on the Mechanical tab first, then add the point.');
  }
  // The modal and critical-speed answers are separate solves on the same tab:
  // they ride along only when they belong to THIS machine, and their absence is
  // an empty column, not a wrong one.
  const modal = st.modal.data && !isStale(st.modal.geoSig, st.modal.backendStale)
    ? st.modal.data : null;
  const rotordyn = st.rotordyn.data
    && !isStale(st.rotordyn.geoSig, st.rotordyn.backendStale)
    ? st.rotordyn.data : null;

  const em = emSummaryIfLive();
  const params = {
    ...(await buildParams(em)),
    ...defined({
      mech_rpm: n(res.rpm),
      mech_rpm_overspeed: n(res.overspeed_rpm),
      mech_loads: t(res.loads) ?? st.loads,
      mech_torque_nm: n(res.torque_nm) ?? n(st.torque),
      mech_interference_mm: n(res.interference_mm),
      mech_rotor_temp_c: n(res.thermal?.rotor_temp_c) ?? n(st.rotorTempC),
      mech_sleeve_temp_c: n(res.thermal?.sleeve_temp_c) ?? n(st.sleeveTempC),
      mech_mesh_mm: n(res.mesh?.mesh_size_mm) ?? n(st.meshMm),
      // ONE string, because the four joints are one model: two rows whose
      // contacts differ are not comparable, and the Comparison table highlights
      // a differing input in amber the moment they do.
      mech_contacts: CONTACT_PAIRS.map((p) => {
        const c = res.contacts?.[p] ?? st.contacts[p];
        if (!c) return `${p}:—`;
        return `${p}:${c.type}${c.type === 'separation' ? ` µ${c.mu}` : ''}`;
      }).join(' · '),
    }),
  };
  const results = {
    ...em,
    mechanical: mechanicalRowFromResult(res, modal, rotordyn, st.viewCase),
  };
  await postPoint(name, params, results);
}

/* ── Thermal ─────────────────────────────────────────────────────────────── */

/** Why the Thermal tab has nothing to add, as a sentence — or `null`. */
export function thermalPointIssue(): string | null {
  const s = useThermalStore.getState();
  if (!s.field.data) return 'nothing has been solved on the Thermal tab yet';
  if (isStale(s.field.geoSig, s.field.backendStale)) {
    return 'the temperature field is for a previous geometry — press Solve again first';
  }
  return null;
}

/**
 * The Thermal tab's answer as a Compare row — every part's MAXIMUM temperature
 * side by side with every other point's (user 2026-09-07: *"в температурном
 * нужно все максимальные температуры всех частей мотора сравнивать между
 * собой"*).
 *
 * As above, the cooling INPUTS come from the payload's own echoed boundary
 * block wherever it has them: switching the outer surface from air to a water
 * jacket without pressing Solve changes no geometry, so nothing would flag the
 * result as stale — but a row saying "liquid, 12 L/min" over temperatures
 * solved in still air is exactly the mismatch this module exists to prevent.
 */
export async function addThermalPointToCompare(name: string): Promise<void> {
  const st = useThermalStore.getState();
  const res = st.field.data;
  if (!res) {
    throw new Error('There is no thermal result to add — press Solve on the '
      + 'Thermal tab first.');
  }
  if (isStale(st.field.geoSig, st.field.backendStale)) {
    throw new Error(
      'This temperature field was solved on a DIFFERENT machine than the one '
      + 'loaded — the point would pair this geometry with another motor\'s '
      + 'temperatures. Press Solve on the Thermal tab first, then add the point.');
  }
  const coupled = st.coupled.data
    && !isStale(st.coupled.geoSig, st.coupled.backendStale)
    ? st.coupled.data : null;

  const outer = outerCooling(res.cooling);
  const inner = res.cooling?.inner;
  const outerMode = t(outer.mode) ?? st.coolMode;
  const boreMode = t(inner?.mode) ?? st.boreMode;

  const em = emSummaryIfLive();
  const params = {
    ...(await buildParams(em)),
    ...defined({
      therm_cooling_mode: outerMode,
      therm_ambient_c: n(res.ambient_temp) ?? n(st.ambientT),
      therm_air_speed_mps: outerMode === 'air'
        ? (n(outer.air_speed_mps) ?? n(st.airSpeed)) : undefined,
      therm_fluid: outerMode === 'liquid' ? (t(outer.fluid) ?? st.fluid) : undefined,
      therm_fluid_in_c: outerMode === 'liquid'
        ? (n(outer.t_in_c) ?? n(st.tIn)) : undefined,
      therm_flow_lpm: outerMode === 'liquid'
        ? (n(outer.flow_lpm) ?? n(st.flowLpm)) : undefined,
      // Only in manual mode: in every other mode the outer h is a RESULT (it is
      // in results.thermal.h_outer), and repeating it here as an input would
      // claim the user chose it.
      therm_h_conv: outerMode === 'manual'
        ? (n(outer.h_conv) ?? n(st.hConv)) : undefined,
      therm_bore_mode: boreMode,
      therm_bore_air_speed_mps: boreMode === 'air'
        ? (n(inner?.air_speed_mps) ?? n(st.boreAirSpeed)) : undefined,
      therm_bore_fluid: boreMode === 'liquid'
        ? (t(inner?.fluid) ?? st.boreFluid) : undefined,
      therm_bore_in_c: boreMode === 'liquid'
        ? (n(inner?.t_in_c) ?? n(st.boreTIn)) : undefined,
      therm_bore_flow_lpm: boreMode === 'liquid'
        ? (n(inner?.flow_lpm) ?? n(st.boreFlowLpm)) : undefined,
    }),
  };
  const results = { ...em, thermal: thermalRowFromResult(res, coupled) };
  await postPoint(name, params, results);
}
