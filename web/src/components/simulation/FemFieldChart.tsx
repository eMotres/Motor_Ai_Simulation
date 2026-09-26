/**
 * FemFieldChart — the Simulation tab's field inspector.
 *
 * DATA host, not a renderer, since 2026-09-06.  It still owns everything that
 * decides WHAT is on screen — the /fem_field2d fetches and their mesh settings,
 * the free snapshot probes (a field view never solves by itself), the run's own
 * demag map off the stored transient, the eddy / loss / thermal gates, the
 * material-override readiness gate, the provenance labels — and hands the
 * result to the SHARED `common/FieldViewer` as `FieldOutput`s.
 *
 * The picture, the camera (wheel-zoom at the cursor, drag-pan, Fit), the output
 * menu and the colour bar are that viewer's job now, and they are the same ones
 * the Mechanical and Modal tabs use.  User 2026-09-06: "интерфейс должен быть
 * единым для всех графиков — электромагнитных, механических и термо".
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Paper, Typography, Button, Tooltip } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import HelpTip from '../common/HelpTip';
import FieldViewer from '../common/FieldViewer';
import { outputStub } from '../common/fieldOutput';
import type { FieldOutput } from '../common/fieldOutput';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// ── types ─────────────────────────────────────────────────────────────────
import type { FemPayload } from './fem-types';
import { tileFullRing } from './fem-types';
import { useMotorStore } from '../../stores/motorStore';
import { geoSignature } from '../common/geoSig';
import { matOverrideReady } from '../../lib/apiAuth';
import { EDDY_DEFAULT_STEPS } from '../../lib/eddySteps';
export type { FemPayload } from './fem-types';

/** True once a physics request would carry the page's `mat=` override — the
 *  gate every free cache/snapshot probe below waits behind.  A probe fired
 *  earlier goes out without the override and lands on a different backend
 *  key than the solve it is looking for: after a reload the picture the page
 *  had solved minutes ago was reported "not solved" (2026-09-04). */
function useMatOverrideReady(): boolean {
  const [ready, setReady] = useState<boolean>(() => matOverrideReady());
  useEffect(() => {
    if (ready) return;
    const on = () => setReady(true);
    window.addEventListener('mat-override-ready', on);
    if (matOverrideReady()) setReady(true);   // flipped between render and subscribe
    return () => window.removeEventListener('mat-override-ready', on);
  }, [ready]);
  return ready;
}

// ── ONE renderer for every view ───────────────────────────────────────────
// Geometry, colour ramp, banding and the legend's range all come out of
// ./fieldView — see the header there for why they had to stop being seven
// hand-written copies.  Nothing in this file may build a field colour of its
// own: that is exactly how the views drifted apart.
import { EM_MENU, emOutputs, outlinesFromMesh } from './fieldView';

// ── helpers: read mesh params persisted by MeshPanel ──────────────────────
function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}
// Simulation-tab settings live under `sim.*` (single source the Simulation panel
// writes) — the multi-frame views ask for exactly the run's own settings so the
// backend can recognise the request as a run it already solved.
function readSimSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`sim.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

// ── R3F mesh component ────────────────────────────────────────────────────
// 'Temp' left this menu on 2026-09-07: a steady-state conduction solve has its
// own mesh, its own boundary conditions and its own minute of CPU, and an
// electromagnetic field view that can start one is a view that solves something
// nobody asked it for.  It is the Thermal tab now.
type FieldMode = 'Az' | 'Bmag' | 'J' | 'Jeddy' | 'Loss' | 'Demag';
// Only J⟳ (eddy-current crowding) needs the time-coupled σ(−∂A/∂t+U) solve.
//
// It does NOT necessarily need a NEW one: the Simulation run keeps its own last
// frame server-side (mesh + A + B + Jeddy + cycle-averaged loss density), so when
// the run solved this operating point with the coupled eddy on, J⟳ and Loss are
// replayed from it instantly.  Only a miss (different operating point / mesh /
// frame count, coupled eddy off in the run, or a back-end restart) runs a solve
// here — and the header then says so.  Loss otherwise falls back to the
// single-frame analytic density (fast, like |B|).
const EDDY_MODES = new Set<FieldMode>(['Jeddy']);

// The iso-line builder, the banded field mesh, the fit-on-payload camera and
// the colour bar all moved out on 2026-09-06: the first two into fieldView's
// emOutputs (they are field GEOMETRY, not host logic), the last two into the
// shared common/FieldViewer, which every tab now draws through.

// ── stats sidebar ─────────────────────────────────────────────────────────
const StatRow: React.FC<{ label: string; value: string; sub?: string }> = ({
  label, value, sub,
}) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between',
    py: 0.4, borderBottom: '1px solid var(--app-bg)' }}>
    <Box>
      <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{label}</Typography>
      {sub && <Typography sx={{ fontSize: 9, color: 'var(--line)' }}>{sub}</Typography>}
    </Box>
    <Typography sx={{ fontSize: 11, color: 'var(--text-1)', fontFamily: 'monospace' }}>
      {value}
    </Typography>
  </Box>
);

// ── main component ────────────────────────────────────────────────────────
interface Props {
  gamma_deg?: number;
  rotor_angle_deg?: number;
  I_phase_rms?: number;
  onPayload?: (p: FemPayload) => void;
  /**
   * If provided, the chart skips its own /fem_field2d fetch and renders the
   * supplied payload instead.  Used by the FemAnimationViewer to feed
   * per-step frames into the same rendering pipeline (mesh + iso lines +
   * |B| / Demag modes all work transparently with frame data).
   */
  payloadOverride?: FemPayload | null;
  /** Optional extra info line under the header (e.g. "Step 5 / 12  ·  rotor 6.4°"). */
  subHeader?: string;
  /** Hide the "Re-solve" button — useful when an external playback widget
   *  is in charge of (re)fetching frames. */
  hideRefresh?: boolean;
}

const FemFieldChart: React.FC<Props> = ({ gamma_deg = 0, rotor_angle_deg = 0,
                                          I_phase_rms, onPayload,
                                          payloadOverride, subHeader,
                                          hideRefresh }) => {
  const [fetchedPayload, setPayload] = useState<FemPayload | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error,   setError]   = useState<string | null>(null);
  // A free "does the server already hold this exact picture?" lookup is in
  // flight (cache probe, never a solve) — keeps the "not solved" line from
  // flashing before an answer that is usually instant.
  const [azProbing, setAzProbing] = useState<boolean>(false);
  const [mode,    setMode]    = useState<FieldMode>('Az');
  // Eddy-solve payload (J⟳ / Loss views) — separate from the fast magnetostatic
  // one, lazily fetched on first selection and re-fetched when γ / I change.
  const [eddyPayload, setEddyPayload] = useState<FemPayload | null>(null);
  const [eddyLoading, setEddyLoading] = useState<boolean>(false);
  // TRUE only once the snapshot probe has come back empty and this view is
  // actually running its own transient — so the spinner never claims to be
  // "fetching" while it is solving, or the other way round.
  const [eddySolving, setEddySolving] = useState<boolean>(false);
  const [eddyErr,     setEddyErr]     = useState<string | null>(null);
  // The free snapshot probe has already asked (and missed) for this operating
  // point — so the view stops asking and shows its placeholder instead of
  // re-probing on every render.
  const [eddyProbed,  setEddyProbed]  = useState<boolean>(false);
  // Loss view: the Simulation run's OWN cycle-averaged loss-density map, if that
  // run's snapshot matches this operating point.  Probed without solving
  // (snapshot_only); a miss leaves the single-frame analytic map in place.
  const [lossSnap,    setLossSnap]    = useState<FemPayload | null>(null);
  const [lossProbing, setLossProbing] = useState<boolean>(false);
  const [lossProbed,  setLossProbed]  = useState<boolean>(false);
  const [logLoss,     setLogLoss]     = useState<boolean>(true);   // log W/m³ map
  // Loss view: one shared W/m³ axis (default, comparable) vs each material
  // scaled to its own range (readable inside the weakest component, but a
  // colour no longer means one number).  Default OFF — the shared axis is the
  // number; this is a reading aid and the note under the view says so.
  const [perMat,      setPerMat]      = useState<boolean>(false);
  // Signature of the machine this view is drawing.  The fetch interceptor sends
  // exactly these numbers as `geo=` on every request, so when they change the
  // picture on screen is of a different motor and has to be re-fetched.  Same
  // construction TransientCharts uses to flag a stale run — one definition of
  // "the geometry changed" for both panels.
  const storeGeometry = useMotorStore(s => s.geometry);
  const geoSig = useMemo(
    () => geoSignature(storeGeometry as Record<string, unknown>), [storeGeometry]);
  const matReady = useMatOverrideReady();
  const isEddy = !payloadOverride && EDDY_MODES.has(mode);
  const isLoss = !payloadOverride && mode === 'Loss';
  // Demag rides the SAME run-snapshot probe as Loss: the honest map is the
  // transient's worst-over-period ratchet, not this view's single-frame
  // full-Br check.  A run that de-rated a magnet to Br 0.12 rendered here as
  // a uniform 99.9 % "nothing happened" because only the static payload was
  // ever consulted.
  const isDemagV = !payloadOverride && mode === 'Demag';

  // ── The RUN's OWN demag map, off the stored transient ─────────────────────
  // The Demag koef on the summary card is the area-weighted Br retention of
  // the transient's full-period ratchet (the worst field every element saw),
  // and that map travels WITH the run: `demag_field` in the transient
  // payload, persisted in localStorage and restored by the backend.  The
  // server-side snapshot the probe above looks for is only a cache of the
  // same thing, and it is emptied by a restart or a geometry save — after
  // which this view used to fall back to a single-angle full-Br check, a
  // MILDER picture that could not reproduce the card's number (user
  // 2026-09-04: "половина 100 %, половина 99 % — никак 94 % не получается";
  // the run's map showed 85–90 % over the magnet cores).  Read the run's map
  // directly instead: same machine (geometry stamp), tiled to the full ring.
  const numPoles = Number((storeGeometry as Record<string, unknown> | null)?.num_poles ?? 0);
  const [runMapNonce, setRunMapNonce] = useState(0);
  useEffect(() => {
    const bump = () => setRunMapNonce(n => n + 1);
    window.addEventListener('sim-transient-done', bump);
    window.addEventListener('sim-transient-restored', bump);
    return () => {
      window.removeEventListener('sim-transient-done', bump);
      window.removeEventListener('sim-transient-restored', bump);
    };
  }, []);
  const runDemag = useMemo<FemPayload | null>(() => {
    if (!isDemagV) return null;
    try {
      const s = localStorage.getItem('sim.lastTransient');
      if (!s) return null;
      const t = JSON.parse(s);
      const f = t?.demag_field;
      if (!f?.vertices?.length || !f?.triangles?.length || !f?.demag_coef_per_tri?.length) return null;
      // Another machine's run is never shown as this one's (the same rule the
      // summary card applies to its numbers).
      if (t._geoSig != null && t._geoSig !== geoSig) return null;
      // The run solves the machine's natural wedge; its magnet count against
      // the pole count says how many copies make the ring.
      const nMag = Number(f.mag_domains?.length ?? 0);
      const N = (numPoles > 0 && nMag > 0 && numPoles % nMag === 0) ? numPoles / nMag : 1;
      const I = t.I_phase_rms_solved_A ?? t.summary?.I_phase_rms_A;
      const g = t.gamma_effective_deg ?? t.summary?.gamma_deg;
      const when = t.computed_at ? new Date(t.computed_at).toLocaleTimeString() : '';
      const p = {
        n_vertices: f.vertices.length, n_triangles: f.triangles.length,
        vertices: f.vertices, triangles: f.triangles, domain_per_tri: f.domain_per_tri,
        demag_coef_per_tri: f.demag_coef_per_tri,
        A_z_per_node: [], Bmag_per_tri: [], extent: f.extent,
        // The motor around the magnets: class boundaries chained from the
        // run's own mesh (tileFullRing rotates them with the sector).
        outlines: outlinesFromMesh(f.vertices, f.triangles, f.domain_per_tri, N),
        n_sectors: N, symmetry_mult: N, anti_periodic: false,
        solve_time_s: 0, total_time_s: 0,
        // The sidebar formats these unconditionally (crashed the panel on
        // 2026-09-05 with "reading 'toFixed'" — they were simply absent).
        A_z_min: 0, A_z_max: 0, B_mag_max: 0,
        T_em_Nm: Number(t.summary?.T_em_avg_Nm ?? 0), P_cu_W: Number(t.summary?.P_stranded_W ?? 0),
        P_fe_W: Number(t.summary?.P_core_W ?? 0), P_mag_eddy_W: Number(t.summary?.P_solid_W ?? 0),
        P_loss_total_W: Number(t.summary?.P_loss_total_W ?? 0), P_mech_W: Number(t.summary?.P_mech_W ?? 0),
        efficiency: Number(t.summary?.efficiency ?? 0), freq_Hz: 0, rpm: Number(t.summary?.rpm ?? 0),
        source: 'transient-snapshot', from_transient: true,
        source_label: `simulation run${when ? ` ${when}` : ''}`
          + (I != null ? ` @ ${Number(I).toFixed(0)} A` : '')
          + (g != null ? `, γ ${Number(g).toFixed(1)}°` : '')
          + ' — worst field over the full electrical period (the Demag koef map)',
        transient_steps_per_period: t.summary?.n_steps_per_period ?? null,
        transient_computed_at: t.computed_at ?? null,
      } as unknown as FemPayload;
      return tileFullRing(p);
    } catch { return null; }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDemagV, geoSig, numPoles, runMapNonce]);

  const payload = payloadOverride
    ?? (isEddy ? eddyPayload
       : ((isLoss || isDemagV) && lossSnap) ? lossSnap
       : (isDemagV && runDemag) ? runDemag
       : fetchedPayload);
  const busy = isEddy ? eddyLoading
             : ((isLoss || isDemagV) && lossProbing) ? true
             : loading;
  const errMsg = isEddy ? eddyErr : error;
  // The static solver always computes a demag map (a check at full Br), but if
  // the user has demag modelling OFF the map (all 0 % at no-load) is just
  // confusing — only offer the Demag view when demag is actually enabled.
  const demagOn = (() => {
    try { return JSON.parse(localStorage.getItem('sim.demag') || 'false') === true; }
    catch { return false; }
  })();
  useEffect(() => { if (!demagOn && mode === 'Demag') setMode('Az'); }, [demagOn, mode]);

  // THE field view — geometry AND colour scale for whichever mode is showing,
  // built once and handed to the shared viewer, which bands the fill and labels
  // the colour bar off the SAME FieldScale.  (They used to derive their ranges
  // independently and could disagree about what a colour meant.)
  const emOut = useMemo(
    () => emOutputs(payload, mode, { logLoss, perMaterialLoss: perMat }),
    [payload, mode, logLoss, perMat]);

  /** Single-angle field (A_z / |B| / J / Demag static).  `probeOnly` asks the
   *  backend ONLY for a picture it already holds for this exact key (its
   *  field cache — `snapshot_only` returns before any solve) and shows the
   *  placeholder on a miss.  This is how the view comes back after a page
   *  reload without solving anything: the run's automatic solve is still in
   *  the server's cache, the page just lost its copy (user 2026-09-04: "и
   *  опять не считается автоматом" — it had; the Claude app restart reloaded
   *  the page and the picture with it). */
  const fetchFem = (probeOnly = false) => {
    if (payloadOverride) return;   // parent owns the data
    if (probeOnly) setAzProbing(true); else { setLoading(true); }
    setError(null);
    const comp = JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {}));
    // Transient-only policy: the separate "Eddy" static solve was retired —
    // the magnetostatic field view is just the per-frame field the transient
    // sweeps; losses/torque come from the sliding-band transient.
    const base = `${API}/api/simulation/physics/fem_field2d`;
    const params: Record<string, string> = {
      rotor_angle_deg:   String(rotor_angle_deg),
      gamma_deg:         String(gamma_deg),
      mesh_size_mm:      String(readMeshSetting('meshSize',    4.0)),
      min_size_mm:       String(readMeshSetting('minSize',     0.3)),
      outer_air_factor:  String(readMeshSetting('outerAir',    1.3)),
      motion_band:       String(readMeshSetting('motionBand',  true)),
      band_thickness_mm: String(readMeshSetting('bandThickness', 0.4)),
      n_sectors:         String(readMeshSetting('nSectors',    1)),
      stator_fillet_mm:  '0',   // native geometry — extra smoothing removed
      component_mesh:    comp,
      // The field view now runs the sliding-band solver for one frame; pass the
      // demag flag so it computes the irreversible-demag %-map when modelling is on.
      demag:             String(demagOn),
      // Bit-identical pole/slot mesh (Mesh-tab toggle) — keep the field view
      // consistent with the mesh/sim the user is verifying.
      pole_copy:         String(readMeshSetting('poleCopy', false)),
      // SAME mesh pipeline as the transient (Mesh-tab toggles) — the field
      // view must show the exact mesh the simulation solves on.
      iron_template:     String(readMeshSetting('ironTemplate', true)),
      geo_mesh:          String(readMeshSetting('geoMesh', true)),
      structured_gap:    String(readMeshSetting('structuredGap', false) || readMeshSetting('ironTemplate', true)),
      airgap_macro:      String(readMeshSetting('harmonicGap', false)),
      gap_layers:        String(readMeshSetting('gapLayers', 2)),
    };
    if (I_phase_rms !== undefined) {
      params.I_phase_rms = String(I_phase_rms);
    }
    // The cache key ignores this flag, so a hit is the very picture a full
    // fetch would return; a miss answers {no_snapshot} instead of solving.
    if (probeOnly) params.snapshot_only = 'true';
    const qs = new URLSearchParams(params).toString();
    fetch(`${base}?${qs}`, { cache: 'no-store' })
      .then(async r => {
        if (probeOnly && !r.ok) return { no_snapshot: true } as FemPayload;
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return r.json();
      })
      .then((d: FemPayload) => {
        if (probeOnly) setAzProbing(false);
        if (d && (d as any).no_snapshot) { setLoading(false); return; }   // probe miss → placeholder
        const full = tileFullRing(d);   // sector solve → full-ring display
        setPayload(full); setLoading(false);
        if (onPayload) onPayload(full);
      })
      .catch(e => {
        if (probeOnly) { setAzProbing(false); return; }   // a probe never reports
        setError(String(e)); setLoading(false);
      });
  };

  // ── A field view NEVER SOLVES BY ITSELF ───────────────────────────────────
  // This effect used to call fetchFem() — so opening the Simulation tab,
  // loading a machine or nudging γ / current started a full FEM solve per
  // view, with nothing on screen saying so (the transient strip only tracks
  // the main transient).  Measured 2026-09-03, 18:48-19:05: 14 automatic
  // /fem_field2d solves on one machine, 3 on a large one at 5-7 min each, two
  // of those fired twice within a second with an identical key — ~17 cores and
  // 345 threads busy while the panel showed nothing running.
  //
  // A changed machine / operating point still INVALIDATES the picture (showing
  // the old motor's field as the new one's is the stale-machine bug), it just
  // does not re-solve: the view goes back to its placeholder and waits for
  // Re-solve.  What the buttons compute is unchanged.
  useEffect(() => {
    if (payloadOverride) {
      // External owner — drop loading flag and forward upstream
      setLoading(false); setError(null);
      if (onPayload) onPayload(payloadOverride);
      return;
    }
    setPayload(null); setError(null); setLoading(false);
    // …then ask, for free, whether the server ALREADY holds this exact
    // picture (a page reload, or a point the user solved earlier and came
    // back to).  A hit paints it, a miss leaves the placeholder; no solve is
    // ever started here.  The multi-frame views do the same with their own
    // snapshot probes below.
    // …but only once the request would carry the page's material override —
    // before that the probe's key cannot match the solve's (see
    // useMatOverrideReady); the effect re-runs when `matReady` flips.
    if (matReady && !EDDY_MODES.has(mode) && mode !== 'Loss' && mode !== 'Demag') fetchFem(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gamma_deg, rotor_angle_deg, I_phase_rms, payloadOverride, geoSig, matReady]);

  // ── the picture depends on the GEOMETRY, so the geometry is a dependency ──
  // `geoSig` above is in that list, and this is the whole fix for "I edited the
  // geometry, saved it, re-ran, and every field view still shows the old
  // motor".  The back end was innocent: its keys already discriminate (a
  // config-side edit changes cfg_fingerprint, a per-request `geo=` is appended
  // to the field cache key).  What went wrong was up here — this view only ever
  // re-fetched on γ / rotor angle / current and on the `sim-design-applied`
  // event, and the Geometry tab's save path (motorStore.updateGeometryViaApi)
  // does not fire that event: only applying a design from Sweep / Compare does.
  // So the store's geometry became the new machine, every request would have
  // carried it — and no request was made.  The stale payload stayed on screen,
  // and Re-run Simulation did not help either: `sim-transient-done` only drops
  // the eddy/loss snapshots, so Loss then fell back to this same stale payload.
  //
  // Keying on the geometry itself rather than on an event covers EVERY way the
  // machine can change — Geometry tab, Sweep apply, Compare apply, a preset,
  // a catalog load — including the ones that do not exist yet.

  // Assigning a different magnet/steel changes the solve but NOT the URL, so this
  // view has no way to notice on its own — re-fetch on the same event a material
  // change fires.  Without it the map kept showing the previous material, and a
  // manual Re-solve only re-read the browser's cached response for that URL.
  // (…and it drops the picture rather than re-solving it, for the same reason
  // the effect above does: a solve is the user's decision, not the app's.)
  useEffect(() => {
    const onApplied = () => {
      if (!payloadOverride) { setPayload(null); setError(null); }
    };
    window.addEventListener('sim-design-applied', onApplied);
    return () => window.removeEventListener('sim-design-applied', onApplied);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payloadOverride, mode, gamma_deg, I_phase_rms]);

  // ── …except after RUN (user 2026-09-03: "когда я нажимаю Run, пусть
  //    считается автоматом — это логично").  A finished transient is the one
  //    moment the user has asked for a solve of THIS machine at THIS point, so
  //    the field picture follows it by itself: one fetch, after the run, never
  //    on tab opens or nudges.  `sim-transient-done` fires only for a REAL
  //    solve (TransientCharts skips it on restore), and the backend serves the
  //    run's own last-frame snapshot when the key matches — otherwise this is
  //    the single-angle A_z solve the Re-solve button would have made.
  useEffect(() => {
    const onDone = () => {
      if (payloadOverride) return;
      // Exactly what the Re-solve button does for the view that is open —
      // J⟳ / Loss included (user 2026-09-04: "field not solved" was still on
      // screen after Run because only the A_z view auto-fetched).
      if (isEddy) fetchEddy(true);
      else if (isLoss) { setLossSnap(null); setLossProbed(false); fetchFem(); }
      else fetchFem();
    };
    window.addEventListener('sim-transient-done', onDone);
    return () => window.removeEventListener('sim-transient-done', onDone);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payloadOverride, mode, gamma_deg, rotor_angle_deg, I_phase_rms, geoSig]);

  // Use the ACTUAL operating-point current — no silent substitution (a hidden
  // fallback to 120 A made the no-load Loss view show full I²R copper loss, which
  // read as "copper loss at I=0").  At I=0 the map now honestly shows the no-load
  // losses: iron + magnet eddy + the small copper eddy/proximity the spinning
  // magnets induce in the windings — there is NO I²R.
  const eddyCurrent = (I_phase_rms !== undefined && I_phase_rms > 0) ? I_phase_rms : 0;
  const eddyNoLoad = !(eddyCurrent > 0);

  // The Simulation tab's own run settings.  The multi-frame views are asked for
  // with EXACTLY these so the backend can recognise the request as the run it
  // already solved and hand back that run's field instead of solving again.
  // (Same localStorage keys the Simulation panel writes and TransientCharts
  // sends — one source, or the keys would never match.)
  const simSteps    = () => Number(readSimSetting('stepsPP', EDDY_DEFAULT_STEPS)) || EDDY_DEFAULT_STEPS;
  const simCoilTemp = () => Number(readSimSetting('coilTemp', 120.0));
  const simEddy     = () => readSimSetting<boolean>('eddyCoupled', true) !== false;

  /** Query shared by the J⟳ / Loss requests and the run itself.  `eddy` and the
   *  frame count differ per caller, everything else is the run's own settings. */
  const multiFrameParams = (eddy: boolean, steps: number): Record<string, string> => ({
    gamma_deg:        String(gamma_deg),
    I_phase_rms:      String(eddyCurrent),
    n_steps_per_period: String(steps),
    n_periods:          '1',
    eddy:             String(eddy),
    rotor_eddy:       'true',
    // Demag and coil temperature change the SOLVE, so they are part of what
    // makes a request "the same run" — omitting them meant the view asked for a
    // motor the simulation never solved and could never be served from it.
    demag:            String(demagOn),
    coil_temp_c:      String(simCoilTemp()),
    mesh_size_mm:     String(readMeshSetting('meshSize', 4.0)),
    min_size_mm:      String(readMeshSetting('minSize',  0.3)),
    outer_air_factor: String(readMeshSetting('outerAir', 1.3)),
    n_sectors:        String(readMeshSetting('nSectors', 1)),
    component_mesh:   JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {})),
    pole_copy:        String(readMeshSetting('poleCopy', false)),
    iron_template:    String(readMeshSetting('ironTemplate', true)),
    geo_mesh:         String(readMeshSetting('geoMesh', true)),
    structured_gap:   String(readMeshSetting('structuredGap', false) || readMeshSetting('ironTemplate', true)),
    airgap_macro:     String(readMeshSetting('harmonicGap', false)),
    gap_layers:       String(readMeshSetting('gapLayers', 2)),
  });

  /** J⟳ / eddy view.
   *
   *  `allowSolve` is the whole gate: the SNAPSHOT PROBE (step 1) is free — it
   *  asks the backend whether the last simulation run already solved this and
   *  never starts anything — so it may run on its own when the view opens.
   *  Step 2 is a real 10-frame transient and only ever runs from the Re-solve
   *  button.  Automatic step 2 is how this view quietly put 5-7 minute solves
   *  on the server (2026-09-03).
   */
  const fetchEddy = (allowSolve = true) => {
    if (payloadOverride) return;
    setEddyLoading(true); setEddyErr(null); setEddySolving(false);
    // SAME endpoint as the A_z / |B| / J views, with the SAME mesh-pipeline
    // toggles. It used to be a separate /fem_eddy_field2d whose defaults left
    // out template iron, the geo mesh and the structured belt, so the J⟳ view
    // solved a DIFFERENT mesh and drew a visibly different outline next to the
    // A_z view. Every mesh setting must stay in step with fetchFem — that
    // is the whole reason the two share one endpoint now.
    const base = `${API}/api/simulation/physics/fem_field2d`;
    const coupled = simEddy();
    // STEP 1 — ask whether the Simulation run already solved this exact thing
    // (snapshot_only: the backend answers from its store or says no; it never
    // starts a solve behind this request).  Two steps rather than one so the
    // spinner can tell the truth: "fetching" and "solving" are different waits,
    // and a single request that might do either has to guess which to claim.
    // With the coupled eddy off in the run there is nothing to find, so skip it.
    const askJ = (relaxed: boolean) => fetch(
      `${base}?${new URLSearchParams({
          ...multiFrameParams(true, simSteps()), snapshot_only: 'true',
          latest_run_field: relaxed ? 'true' : 'false',
        }).toString()}`, { cache: 'no-store' })
      .then(r => (r.ok ? r.json() : { no_snapshot: true }))
      .catch(() => ({ no_snapshot: true }));
    // Exact key, then the same-machine fallback (see the Loss probe below).
    const probe: Promise<FemPayload> = coupled
      ? askJ(false).then((d: any) => (d && d.no_snapshot) ? askJ(true) : d)
      : Promise.resolve({ no_snapshot: true } as FemPayload);
    probe.then((d: FemPayload) => {
      if (d && !d.no_snapshot && d.vertices) {         // the run's own field
        setEddyPayload(tileFullRing(d)); setEddyLoading(false);
        setEddyProbed(true);
        return;
      }
      setEddyProbed(true);
      if (!allowSolve) {
        // Nothing to replay and nobody asked for a solve → placeholder.
        setEddyLoading(false);
        return;
      }
      // STEP 2 — nothing to replay: solve it here, at the cheap 10 frames /
      // period this view always used (≥9 keeps the de-jitter savgol and still
      // resolves the 6f loss harmonic).  The payload labels itself as an
      // on-demand solve, and the spinner now says so too.
      setEddySolving(true);
      return fetch(`${base}?${new URLSearchParams(multiFrameParams(true, 10)).toString()}`,
                   { cache: 'no-store' })
        .then(async r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
          return r.json();
        })
        .then((d2: FemPayload) => {
          setEddyPayload(tileFullRing(d2));
          setEddyLoading(false); setEddySolving(false);
        });
    }).catch(e => { setEddyErr(String(e)); setEddyLoading(false); setEddySolving(false); });
  };

  /** Loss view: ask ONLY for the simulation run's own cycle-averaged map
   *  (snapshot_only → the backend never starts a solve for this).  A hit
   *  replaces the single-frame analytic estimate with the real transient map;
   *  a miss leaves the analytic one, which is what this view showed before. */
  const probeLossSnapshot = () => {
    if (payloadOverride) return;
    setLossProbing(true);
    const base = `${API}/api/simulation/physics/fem_field2d`;
    const P = multiFrameParams(simEddy(), simSteps());
    const ask = (relaxed: boolean) => fetch(
      `${base}?${new URLSearchParams({
        ...P, snapshot_only: 'true',
        latest_run_field: relaxed ? 'true' : 'false',
      }).toString()}`, { cache: 'no-store' })
      .then(async r => (r.ok ? r.json() : { no_snapshot: true }));
    // EXACT key first.  On a miss, ask for the last run of the SAME machine —
    // the backend crosses an operating-point / mesh difference but never a
    // geometry or material one, and it reports every difference in
    // `source_label`, which the header prints.  Three separate one-field
    // spelling mismatches have sent this view to its single-frame analytic
    // fallback (whose magnet term is ZERO), so a labelled real map beats an
    // unlabelled fake one.
    ask(false)
      .then((d: FemPayload) => (d && d.no_snapshot) ? ask(true) : d)
      .then((d: FemPayload) => {
        setLossSnap(d && d.no_snapshot ? null : tileFullRing(d));
        setLossProbing(false); setLossProbed(true);
      })
      .catch(() => { setLossSnap(null); setLossProbing(false); setLossProbed(true); });
  };

  // γ / I changed → the cached eddy solve is stale (rotor angle does NOT
  // matter — the eddy run sweeps a whole period — so we don't invalidate on it).
  useEffect(() => {
    setEddyPayload(null); setEddyErr(null); setEddyProbed(false);
    setLossSnap(null); setLossProbed(false);
    // geoSig: a different machine invalidates these just as surely as a
    // different operating point does — and the thermal solve is fed by them.
  }, [gamma_deg, I_phase_rms, mode, geoSig]);   // Loss (fast) vs J⟳ (coupled) need different solves

  // A finished simulation run has just produced a field snapshot → drop what
  // these views are holding so the next look comes from the RUN, not from a
  // solve of an older operating point.
  useEffect(() => {
    const onRun = () => {
      setEddyPayload(null); setEddyErr(null); setEddyProbed(false);
      setLossSnap(null); setLossProbed(false);
    };
    window.addEventListener('sim-transient-done', onRun);
    return () => window.removeEventListener('sim-transient-done', onRun);
  }, []);

  // First time a J⟳ view is shown: ask the run's snapshot (free, no solve).
  // A miss shows the placeholder — the 10-frame transient behind this view is
  // a Re-solve click, not a side effect of selecting the toggle.
  // (`matReady` gates both probes for the same reason as the single-frame one
  // above: a probe without the override keys a different machine and its miss
  // is then remembered as "the run has nothing" — wrongly.)
  useEffect(() => {
    if (matReady && isEddy && !eddyPayload && !eddyLoading && !eddyErr && !eddyProbed) {
      fetchEddy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEddy, eddyPayload, eddyLoading, eddyErr, eddyProbed, matReady]);

  // Loss AND Demag: probe the run's snapshot once per operating point (no
  // solve) — both maps only mean anything as the run's own cycle history.
  useEffect(() => {
    if (matReady && (isLoss || isDemagV) && !lossSnap && !lossProbed && !lossProbing) probeLossSnapshot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoss, isDemagV, lossSnap, lossProbed, lossProbing, matReady]);

  /* The thermal solve used to live here — its own fetch of
     /api/simulation/physics/thermal_field2d, with the cooling inputs in this
     viewer's toolbar.  It moved to the Thermal tab on 2026-09-07: it is a
     different physics on a different mesh, and it never belonged in an
     electromagnetic output menu. */

  // ── what the header says ─────────────────────────────────────────────────
  // Still built HERE, not in the viewer: only this host knows whether the Loss
  // map on screen is the run's own cycle-averaged one or the single-frame
  // analytic fallback, and saying which is not optional.
  const headerLabel = mode === 'Loss'
    ? (lossSnap ? 'Loss density (simulation run, cycle-averaged)'
                : 'Loss density (single frame, analytic)')
    : mode === 'Demag'
      ? ((lossSnap || runDemag)
           ? 'Demagnetisation, % of Br remaining (simulation run, worst over period)'
           : 'Demagnetisation, % of Br remaining (single frame, full-Br check)')
      : mode === 'Jeddy'
        ? 'Current density J⟳ — coupled eddy solve (proximity)'
        : mode === 'Az'
          ? 'Magnetic potential A_z — real scikit-fem solve'
          : (EM_MENU.find(m => m.id === mode)?.label ?? mode);

  const headerTip = (isEddy || isLoss)
    ? "Time-coupled eddy-current solve over a full electrical period. J⟳ shows the real current density σ(−∂A/∂t+U) crowding toward the slot mouth (proximity effect); Loss is the cycle-averaged dissipation density [W/m³] — iron Bertotti + copper DC+AC + magnet eddy, normalised so the map integrates to the reported component losses. When the last Simulation run solved this same operating point (with the coupled eddy solve on), these views replay THAT run's final frame — no second solve. Otherwise they compute here, and the line below says so."
    : "2-D magnetostatic field at the current rotor angle — the same per-frame field the sliding-band transient sweeps. Torque + losses are ×n_sectors for the full motor.";

  // The menu: only the outputs THIS host can have data for.  Demag exists only
  // with demag modelling on; the animation viewer feeds one frame at a time, and
  // J⟳ / Loss each need their own multi-frame solve.
  const outputs = useMemo<FieldOutput[]>(() => {
    const avail = EM_MENU.filter(m => {
      if (m.id === 'Demag') return demagOn;
      if (payloadOverride && (m.id === 'Jeddy' || m.id === 'Loss')) return false;
      return true;
    });
    return avail.map(m => (m.id === mode
      ? { ...emOut, label: headerLabel, tip: headerTip }
      : outputStub(m.id, m.menuLabel, m.label, m.group, { unit: m.unit, tip: m.tip })));
  }, [emOut, mode, demagOn, payloadOverride, headerLabel, headerTip]);

  // One SHORT visible line (user rule: no walls of text in the web — details
  // live in the tooltip).  Visible: where the picture came from + the operating
  // current.  Hover: mesh size, solve time, provenance, colour-scale semantics.
  const contextLabel = payload
    ? (subHeader
         ? subHeader
         : `${payload.from_transient
                ? ('from last simulation run'
                   // A relaxed match is the user's MOTOR but not the panel's
                   // numbers.  That has to be visible, not hover-only.
                   + ((payload as any).from_transient_relaxed ? ' (≠ panel)' : ''))
                : 'computed on demand'}`
           + `${(isEddy || isLoss || isDemagV) ? ` · @ ${eddyCurrent.toFixed(0)} A` : ''}`
           + ' · ⓘ')
    : busy
      ? (isEddy
           ? (eddySolving
                ? 'No matching simulation run — solving this view on demand…'
                : 'Looking for the last simulation run\'s field…')
           : isLoss ? 'Checking the last simulation run\'s loss map…'
           : 'Solving…')
      : 'field not solved for this machine — Re-solve';

  const contextTip = payload
    ? (`${payload.n_triangles.toLocaleString()} triangles · ×${payload.symmetry_mult} symmetry`
       + `${payload.from_transient ? '' : ` · solve ${payload.solve_time_s}s`}`
       + `${payload.source_label ? ` · ${payload.source_label}` : ''}`
       + `${emOut.scale ? ` — ${emOut.scale.note} · ${emOut.scale.bands} bands` : ''}`)
    : '';

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>

      <FieldViewer
        outputs={outputs}
        selected={mode}
        onSelect={(id) => setMode(id as FieldMode)}
        fitKey={geoSig}
        contextLabel={contextLabel}
        contextTip={contextTip}
        contextColor={payload?.from_transient ? '#38bdf8' : 'var(--text-4)'}
        busy={busy}
        error={errMsg}
        busyNote={(isEddy || isLoss) ? (
          <Typography sx={{ fontSize: 11, color: 'var(--text-2)', textAlign: 'center', maxWidth: 320 }}>
            {isLoss
                ? 'Looking for the last simulation run\'s loss map (no solve)…'
                : !eddySolving
                  // Still the snapshot probe — a lookup, not a solve.
                  ? 'Looking for the last simulation run\'s eddy field…'
                  : (simEddy()
                      ? 'The last simulation run does not cover this operating '
                        + 'point, so this view is solving its own 10-frame eddy '
                        + 'transient (~25 s).'
                      : 'Coupled eddy solve is OFF in the Simulation run, so this '
                        + 'view has to solve its own 10-frame eddy transient (~25 s). '
                        + 'Turn it on in the Simulation panel to get this instantly.')}
          </Typography>
        ) : undefined}
        /* Nothing solved, nothing running: say so in ONE line and point at the
           button.  This is what the view shows on tab open, on a machine load
           and after a settings change — the states that used to fire a solve
           nobody asked for. */
        placeholder={(!azProbing && !payloadOverride) ? (
          <Tooltip placement="top" title={
            'Field views solve only when you ask. Opening the tab or '
            + 'changing the machine/operating point clears the picture '
            + 'instead of starting a FEM solve behind your back — press '
            + 'Re-solve when you want this one computed. (The J⟳ / Loss / '
            + 'Demag views still look for the last simulation run\'s own '
            + 'field for free; this line means that lookup found nothing.)'}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)',
              cursor: 'help', textAlign: 'center' }}>
              field not solved for this machine — press <b>Re-solve</b> ⓘ
            </Typography>
          </Tooltip>
        ) : null}
        controls={
          <>
            {mode === 'Loss' && (
              <Button size="small" onClick={() => setLogLoss(v => !v)}
                title="Toggle log / linear colour scale"
                sx={{ color: '#93c5fd', fontSize: 10, textTransform: 'none',
                  minWidth: 0, px: 1, border: '1px solid var(--line-soft)' }}>
                {logLoss ? 'log' : 'lin'}
              </Button>
            )}
            {mode === 'Loss' && (
              <Button size="small" onClick={() => setPerMat(v => !v)}
                title={'Colour scale span. "shared" is the honest one: one W/m³ '
                  + 'axis for the whole cross-section, so copper (≈4e7 W/m³ here) '
                  + 'and the magnets (≈2e6) are directly comparable — and the '
                  + 'magnets sit low because they ARE low. "per-material" '
                  + 'rescales every material to its own range so the structure '
                  + 'inside the weakest one is readable; a colour then means a '
                  + 'different number in each material and levels cannot be '
                  + 'compared across them.'}
                sx={{ color: perMat ? '#fbbf24' : '#93c5fd', fontSize: 10,
                  textTransform: 'none', minWidth: 0, px: 1,
                  border: '1px solid var(--line-soft)' }}>
                {perMat ? 'per-material' : 'shared'}
              </Button>
            )}
          </>
        }
        actions={!hideRefresh ? (
          <Button size="small" startIcon={<RefreshIcon fontSize="small"/>}
            onClick={
                     // Explicitly (true): this is THE click that is allowed
                     // to run the 10-frame transient behind the J⟳ view.
                     isEddy ? (() => fetchEddy(true))
                     // Loss: re-check the run's snapshot AND refresh the
                     // single-frame map it falls back to.
                     : isLoss ? (() => { setLossSnap(null); setLossProbed(false); fetchFem(); })
                     // Wrapped: a bare `fetchFem` would receive the click
                     // event as `probeOnly` and never solve.
                     : (() => fetchFem())}
            disabled={busy}
            sx={{ color: '#93c5fd', fontSize: 11, textTransform: 'none' }}>
            Re-solve
          </Button>
        ) : undefined}
      />


      {/* Solver diagnostics strip — only the mesh/field numerics that are
          NOT already in the top summary table.  Sits BELOW the full-width
          field chart as a compact horizontal row. */}
      {payload && (
        <Box sx={{ display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 1, mt: 1 }}>
          {(isEddy
            ? [
                { label: 'Copper loss', value: `${(payload.P_cu_W ?? 0).toFixed(0)} W` },
                { label: 'Iron loss',   value: `${(payload.P_fe_W ?? 0).toFixed(0)} W` },
                { label: 'Magnet eddy', value: `${(payload.P_mag_eddy_W ?? 0).toFixed(1)} W` },
                { label: 'Efficiency',  value: `${((payload.efficiency ?? 0) * 100).toFixed(1)} %` },
              ]
            : [
                { label: 'Mesh vertices',  value: payload.n_vertices.toLocaleString() },
                { label: 'Mesh triangles', value: payload.n_triangles.toLocaleString() },
                { label: '|B|_max',        value: `${(payload.B_mag_max ?? 0).toFixed(2)} T` },
                { label: 'A_z range',      value: `[${((payload.A_z_min ?? 0)*1000).toFixed(2)}, ${((payload.A_z_max ?? 0)*1000).toFixed(2)}] mWb/m` },
              ]
          ).map(s => (
            <Box key={s.label} sx={{ p: 1, bgcolor: 'var(--panel-2)',
              border: '1px solid var(--app-bg)', borderRadius: 1 }}>
              <Typography sx={{ fontSize: 9, color: 'var(--text-4)',
                textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {s.label}
              </Typography>
              <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-1)' }}>
                {s.value}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      {/* Footer prose folded into a single ⓘ chip (UI rule: no always-visible
          explanation paragraphs — the line carries the label, the tooltip the
          reasoning). */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.5 }}>
        <Typography sx={{ fontSize: 9, color: isEddy ? 'var(--text-3)' : 'var(--line)' }}>
          {isEddy
            ? (eddyNoLoad ? 'σ(−∂A/∂t+U) · no winding current (I = 0)' : 'σ(−∂A/∂t+U) · proximity crowding')
            : 'Mesh & BC: same as the Mesh tab'}
        </Typography>
        <HelpTip title={isEddy
          ? ('Real current density σ(−∂A/∂t+U) from the eddy solve — current crowds toward the slot opening '
            + '(proximity). Compare with the uniform magnetostatic "J".'
            + (eddyNoLoad
              ? ' No winding current (I=0): the copper loss shown is ONLY eddy/proximity induced by the spinning'
                + ' magnets (concentrated near the slot opening) — there is no I²R. Set a load current to see I²R'
                + ' copper loss and current crowding.'
              : ''))
          : ('Same mesh + Solver-Domain settings as the Mesh tab (read from localStorage). Sector mode uses '
            + 'anti-periodic Dirichlet BC on the radial cuts so torque, |B| and flux linkages are physically '
            + 'correct and multiplied by n_sectors to represent the full motor.')} />
      </Box>
      {/* Demag warning banner removed — the per-magnet knee report over-flagged
          (the demag model over-derates sharp corners); the demag % map above is
          the honest per-element view. */}
    </Paper>
  );
};

export default FemFieldChart;
