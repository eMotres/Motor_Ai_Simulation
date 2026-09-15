/**
 * The Electromagnetic tab's client for the EM ↔ thermal ORCHESTRATOR.
 *
 * The toggle beside Run (`sim.coupled`) switches ONE thing: where the Run
 * request goes.  Off — every run this app has ever made — it goes to the kernel
 * as it always has, and the two temperatures on this tab are exactly what the
 * solve uses.  On, it goes to `POST /api/coupled/run` with the SAME payload, and
 * the backend iterates
 *
 *     EM run → thermal solve → winding / magnet averages → back into the EM run
 *
 * until both settle.  The answer it returns carries the last EM run's own
 * payload (`transient`), so the panel adopts it exactly as it adopts its own
 * Run — same charts, same summary, same localStorage copy.
 *
 * WHY THE COOLING RIDES THE REQUEST.  The backend can load the caller's
 * remembered Thermal-tab fields itself (routes/panel_settings), and it does when
 * this key is absent.  Sending the browser's own `therm.*` block is better when
 * there is one: it is what the Thermal tab is showing RIGHT NOW, including edits
 * the user has not solved with yet, and the promise in the toggle's tooltip is
 * "the Thermal tab's current settings" — not "whatever the server last stored".
 */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/coupled`;

/** One pass of the loop, as the backend records it. */
export interface CoupledIteration {
  iter: number;
  /** the temperatures THIS pass's electromagnetic run was solved at */
  T_coil_in: number;
  T_magnet_in: number | null;
  /** what the thermal solve came back with — winding / magnet BODY averages */
  T_coil_out: number | null;
  T_magnet_out: number | null;
  /** the hottest magnet element: reported for the demagnetisation check, and
   *  deliberately NOT fed back (a knee check is about the hottest element, a Br
   *  is about the body) */
  T_magnet_max: number | null;
  P_loss_W: number | null;
  T_em_Nm: number | null;
  /** THE MECHANICAL HALF of this pass (2026-09-08): the temperature the bearing
   *  friction was billed at, where that came from, and what it cost.  From pass
   *  2 the temperature is the SHAFT of the previous pass's map, so the friction
   *  in the loop is the friction of a bearing at the temperature the machine
   *  actually reaches.  `null` on a machine that names no bearings — which
   *  means UNKNOWN, never 0 W. */
  bearing_temp_c: number | null;
  bearing_temp_source: string | null;
  P_mech_extra_W: number | null;
}

/** The block the backend writes into the last run's SUMMARY, and returns here. */
export interface CouplingBlock {
  /** the pair the LAST electromagnetic run was solved at — i.e. the machine the
   *  cards on this tab describe */
  coil_temp_c: number;
  magnet_temp_c: number | null;
  magnet_temp_max_c: number | null;
  iterations: number;
  em_runs: number;
  converged: boolean;
  runaway: boolean;
  tol_K: number;
  damping: number;
  max_iter: number;
  /** how far the last thermal answer sat from the temperature it was solved at
   *  — inside `tol_K` when converged, and the honest uncertainty when not */
  residual_coil_K: number | null;
  residual_magnet_K: number | null;
  history: CoupledIteration[];
  /** The LAST run's mechanical numbers, present exactly when that run carried
   *  them.  ABSENT — not zero — on a machine with no bearings: a 0 W bearing
   *  loss on the Electromagnetic card is an efficiency nobody measured. */
  bearing_temp_c?: number;
  bearing_temp_source?: string;
  bearing_temp_note?: string;
  P_bearings_W?: number;
  P_windage_W?: number;
  P_mech_extra_W?: number;
  P_loss_total_incl_mech_W?: number;
  efficiency_shaft?: number;
  /** The mechanical step's verdict (phase 3), or its recorded refusal, or
   *  absent when the caller skipped it.  Loosely typed on purpose: the
   *  Mechanical tab's last result is the full answer, this is the line. */
  mechanical?: {
    ok?: boolean; error?: string; rpm?: number; torque_nm?: number;
    sf_min?: number | null; sf_min_p05?: number | null; note?: string;
    contact_fallback?: { pair?: string; from?: string; to?: string; reason?: string };
    /** The two temperature-free answers the same coupled run leaves
     *  (2026-09-13): the ring modes and the shaft's critical speeds, each
     *  one line's worth — the Mechanical tab holds the full results. */
    modes?: {
      ok?: boolean; error?: string; body?: string; n_modes?: number;
      f1_hz?: number | null; f1_order?: number | null; n_flagged?: number;
      tightest?: { f_hz?: number; order?: number | null; excitation?: string | null;
                   excitation_hz?: number | null; margin_pct?: number; flag?: boolean };
    } | null;
    critical_speeds?: {
      ok?: boolean; error?: string; rated_rpm?: number | null;
      first_forward_rpm?: number | null; first_forward_margin_pct?: number | null;
      n_forward_below_rated?: number; verdict?: string | null;
      bearing_span_mm?: number | null; bearing_k_n_per_m?: number | null;
    } | null;
  } | null;
  note: string | null;
  warning: string | null;
}

export interface CoupledRunResult {
  ok: boolean;
  coupling: CouplingBlock;
  /** The last electromagnetic run — the same payload a plain Run returns, and
   *  deliberately left loosely typed: `TransientCharts` owns the transient's
   *  shape (90-odd fields) and re-types it at the point of use, and a second
   *  copy of that interface here is a second copy that drifts. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  transient: Record<string, any>;
  /** the converged temperature map (already remembered as the Thermal tab's
   *  last result, so that tab needs no change to show it) */
  thermal: Record<string, unknown>;
  written_to_last_run: boolean;
  run_id: string;
  elapsed_s: number;
}

/** Is the toggle on?  Read straight from localStorage so the run builder does
 *  not need a prop threaded through three components — the same way it reads
 *  every other panel setting (`readSimSetting`). */
export function coupledEnabled(): boolean {
  try { return JSON.parse(localStorage.getItem('sim.coupled') || 'false') === true; }
  catch { return false; }
}

/** The Thermal tab's boundary conditions, as THAT TAB'S STORE holds them —
 *  registered by `stores/thermalStore` at load (it imports this module, so the
 *  dependency runs one way).  The store adopts the user's server-side settings
 *  when the tab first mounts, so its block is complete by construction.
 *
 *  It used to be read straight from the `therm.*` localStorage keys, one by one,
 *  skipping absent ones.  A browser in which the coolant flow had only ever been
 *  typed on ANOTHER browser holds `therm.coolMode = "liquid"` and no
 *  `therm.flowLpm` at all (the value came from the server into the store, never
 *  into localStorage), and the run then shipped a block with the mode and
 *  without the flow: the backend refused "coolant flow must be greater than
 *  0 L/min" while the Thermal tab itself showed 10 L/min (2026-09-09, user:
 *  "опять run не работает").  A partial block is worse than none — `null`
 *  makes the backend load the user's own stored settings, which are whole. */
let _thermalPanelBlock: (() => Record<string, string> | null) | null = null;

/** `stores/thermalStore` hands over the reader once; `null` from it means
 *  "not hydrated yet — let the backend use the stored settings". */
export function registerThermalPanelBlock(read: () => Record<string, string> | null): void {
  _thermalPanelBlock = read;
}

export function thermalSettings(): Record<string, string> | null {
  try { return _thermalPanelBlock ? _thermalPanelBlock() : null; }
  catch { return null; }
}

/** What the CALLER decides about the loop.  Defaulted to the Electromagnetic
 *  tab's toggle, which is the only caller that existed when this module was
 *  written; the Thermal tab's fallback (2026-09-08) asks for something
 *  deliberately smaller — see each field. */
export interface CoupledRunOptions {
  /** The Thermal panel's boundary conditions.  Omitted = read from this
   *  browser's `therm.*` keys; the Thermal tab passes what it is SHOWING,
   *  because it is the tab that asked for the run. */
  thermalSettings?: Record<string, string> | null;
  /** Solve the rotor stress at the converged temperatures too.  The toggle
   *  always asks for it; a Thermal-tab Solve does not — it asked for a
   *  temperature map, and a mechanical solve nobody pressed is minutes nobody
   *  budgeted. */
  mechanical?: boolean;
  /** Cap on the electromagnetic runs.  Omitted = the server reads the Thermal
   *  panel's own `maxIter`.  The Thermal tab's fallback passes 1: it is not
   *  iterating to a fixed point (that is this toggle's job), it is making the
   *  ONE run its map was missing. */
  maxIter?: number;
}

/**
 * Run the loop.  Blocks for its whole duration — minutes, and up to `max_iter`
 * full transients — exactly as the plain Run's fetch does; the strip above the
 * page polls `/api/coupled/progress` meanwhile, and Stop cancels by run-id.
 *
 * A refusal (422) comes back with a sentence an engineer can act on: the loop
 * checks everything that would make it impossible — a single frame, an imposed
 * voltage, the conducting solve switched off, a machine with nothing cooled —
 * BEFORE it spends the first transient on it.
 */
export async function runCoupled(
  payload: Record<string, unknown>,
  signal?: AbortSignal,
  opts: CoupledRunOptions = {},
): Promise<CoupledRunResult> {
  const settings = opts.thermalSettings === undefined
    ? thermalSettings() : opts.thermalSettings;
  const body: Record<string, unknown> = { ...payload };
  if (settings) body.thermal_settings = settings;
  // Phase 3: the loop ends with the rotor stress solved at the converged
  // per-part temperatures, so the Mechanical tab shows the same machine at the
  // same temperatures (user 2026-09-08: "чтобы температуры везде были
  // одинаковы").  Opt-in on the server; the toggle always asks for it.
  body.mechanical = opts.mechanical !== false;
  if (opts.maxIter !== undefined) body.max_iter = opts.maxIter;
  const r = await fetch(`${BASE}/run`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  });
  if (!r.ok) {
    let msg = await r.text();
    // The 422 carries {detail: {error, invalid_parameters}} — show the sentence,
    // not the JSON around it (the Thermal tab's fetch does the same).
    try {
      const d = JSON.parse(msg)?.detail;
      msg = typeof d === 'string' ? d : (d?.error ?? msg);
    } catch { /* not JSON — keep the raw text */ }
    throw new Error(msg.slice(0, 400));
  }
  return r.json() as Promise<CoupledRunResult>;
}

/** Stop the loop with this run-id.  Sets BOTH the loop's registry and the
 *  transient's, so a cancel that lands mid-transient is honoured by the frame
 *  march instead of waiting it out. */
export function cancelCoupled(runId: string | number): void {
  fetch(`${BASE}/cancel?run_id=${encodeURIComponent(String(runId))}`,
    { method: 'POST' }).catch(() => { /* the loop's own check still stops it */ });
}

/**
 * Put the converged pair into this tab's own two fields.
 *
 * The standing rule is that a temperature computed on one tab is never written
 * into another tab's inputs by itself (`thermal/api.writeSimCoilTemp` is behind
 * a button for exactly that reason).  This is not that case and the difference
 * is the whole toggle: the user asked THIS tab to solve for these two
 * temperatures, so leaving the fields showing the guess the loop started from
 * would be showing an input the run did not use.  Same key + event the panel's
 * own persisted fields use, so the fields update without a remount.
 */
/** The last coupled run's block, from the server's own memory (survives a
 *  reload and a backend restart) — `null` when the loop never ran or the
 *  lookup failed.  `stale` = it was solved for another geometry. */
export async function fetchCoupledLast(): Promise<{ coupling: CouplingBlock; stale: boolean; computedAt?: string } | null> {
  try {
    const r = await fetch(`${BASE}/last`);
    if (!r.ok) return null;
    const j = await r.json();
    const c = j?.result?.coupling;
    if (!j?.has_result || !c || typeof c.coil_temp_c !== 'number') return null;
    return { coupling: c as CouplingBlock, stale: j.stale_geometry === true,
             computedAt: j?.result?.computed_at };
  } catch { return null; }
}

export function adoptConvergedTemperatures(c: CouplingBlock, stamp?: string): void {
  try {
    localStorage.setItem('sim.coilTemp',
      JSON.stringify(Math.round(c.coil_temp_c * 10) / 10));
    // A string, because the field is "empty = the magnet card as quoted".
    localStorage.setItem('sim.magnetTempC', JSON.stringify(
      c.magnet_temp_c == null ? '' : String(Math.round(c.magnet_temp_c * 10) / 10)));
    // WHICH run these fields now describe (2026-09-09) — see `couplingStamp`.
    if (stamp) localStorage.setItem('sim.coupledAdopted', stamp);
    window.dispatchEvent(new Event('sim-settings-restored'));
  } catch { /* private mode — the answer is still on the card */ }
}

/** Identity of the coupled answer the temperature fields were taken from.
 *
 *  2026-09-09.  User: *"здесь одна температура, а здесь другая — как это
 *  понять?"* — the card showed a loop converged at 200 °C while the Coil
 *  temperature field still read 111, the value the run STARTED from, because
 *  the adopt above runs only on the live response: a page reload (or a coupled
 *  POST whose connection dropped after the server had finished, which happened
 *  the same evening) never reached it, and the next uncoupled Run would have
 *  solved the machine at a temperature nobody's result describes.
 *
 *  A RESTORED run therefore adopts too — but only once, keyed on this stamp, so
 *  a temperature typed by hand AFTER the run survives every later reload. */
export function couplingStamp(c: CouplingBlock, computedAt?: string): string {
  return [c.coil_temp_c, c.magnet_temp_c ?? '', c.iterations ?? '',
          computedAt ?? ''].join('|');
}

/** Has this coupled answer already been written into the panel's fields? */
export function couplingAdopted(stamp: string): boolean {
  try { return localStorage.getItem('sim.coupledAdopted') === stamp; }
  catch { return false; }
}

/** "winding 128 °C · magnets 163 °C · mechanical 63 W · 3 it." — the one line
 *  the summary shows.
 *
 *  The mechanical term joined it on 2026-09-08, when the loop started iterating
 *  the bearing temperature with the rest: a coupled run whose friction is not
 *  on the line is a loss the reader has to go looking for.  Omitted entirely on
 *  a machine that names no bearings, because "0 W" would be a claim. */
export function couplingLine(c: CouplingBlock): string {
  const m = c.magnet_temp_c == null ? null : `magnets ${c.magnet_temp_c.toFixed(0)} °C`;
  const w = c.P_mech_extra_W;
  const mech = w == null ? null : `mechanical ${w.toFixed(w < 10 ? 1 : 0)} W`;
  // The mechanical step's own footnotes (2026-09-09): a joint solved bonded
  // because separation left its part unretained, or a refusal.
  const mb = c.mechanical;
  const mechNote = !mb ? null
    : mb.ok === false ? 'mechanics ⚠'
    : mb.contact_fallback ? `${String(mb.contact_fallback.pair ?? 'joint').replace('_', '–')} solved ${mb.contact_fallback.to ?? 'bonded'}`
    : null;
  // The modes and the critical speeds the same run left (2026-09-13): the
  // first frequency and the first forward critical, each with its ⚠ when the
  // step refused or the number sits inside the separation rule.  Absent
  // entirely on a run that did not ask for mechanics — never "0 Hz".
  const mo = mb?.modes;
  const modesTerm = !mo ? null
    : mo.ok === false ? 'modes ⚠'
    : mo.f1_hz == null ? null
    : `f₁ ${fmtHz(mo.f1_hz)}${(mo.n_flagged ?? 0) > 0 ? ' ⚠' : ''}`;
  const cr = mb?.critical_speeds;
  const critTerm = !cr ? null
    : cr.ok === false ? 'criticals ⚠'
    : cr.first_forward_rpm == null ? null
    : `crit ${fmtRpm(cr.first_forward_rpm)}${(cr.n_forward_below_rated ?? 0) > 0
        || (cr.first_forward_margin_pct != null && cr.first_forward_margin_pct < 10) ? ' ⚠' : ''}`;
  return [`winding ${c.coil_temp_c.toFixed(0)} °C`, m, mech,
    `${c.iterations} it.${c.converged ? '' : ' ⚠'}`, mechNote, modesTerm, critTerm]
    .filter(Boolean).join(' · ');
}

/** 2104 → "2104 Hz", 12 430 → "12.4 kHz": the card line has no room for five digits. */
function fmtHz(f: number): string {
  return f >= 10000 ? `${(f / 1000).toFixed(1)} kHz` : `${Math.round(f)} Hz`;
}

/** 48 312 → "48 300 rpm" (three significant figures, thin-space grouped, as the report prints speeds). */
function fmtRpm(v: number): string {
  const r = v >= 10000 ? Math.round(v / 100) * 100 : Math.round(v / 10) * 10;
  return `${r.toLocaleString('en-US').replace(/,/g, ' ')} rpm`;
}

/** The tooltip: every iteration, in the order they ran. */
export function couplingTooltip(c: CouplingBlock): string {
  const rows = (c.history || []).map(h => {
    const inn = `${h.T_coil_in.toFixed(1)} °C`
      + (h.T_magnet_in == null ? '' : ` / ${h.T_magnet_in.toFixed(1)} °C`);
    const out = h.T_coil_out == null ? '—'
      : `${h.T_coil_out.toFixed(1)} °C`
        + (h.T_magnet_out == null ? '' : ` / ${h.T_magnet_out.toFixed(1)} °C`);
    const hot = h.T_magnet_max == null ? ''
      : `, hottest magnet ${h.T_magnet_max.toFixed(1)} °C`;
    const mech = h.P_mech_extra_W == null ? ''
      : `, bearings + windage ${h.P_mech_extra_W.toFixed(1)} W`
        + (h.bearing_temp_c == null ? ''
           : ` at ${h.bearing_temp_c.toFixed(0)} °C`);
    return `${h.iter}. solved at ${inn} → ${out}${hot}${mech}`;
  });
  return [
    'EM ↔ thermal loop: each pass solves the electromagnetic run at a winding '
    + 'and magnet temperature, then the thermal map from ITS loss field, and '
    + 'feeds the winding / magnet BODY AVERAGES back (the hottest magnet is the '
    + 'demagnetisation check, not the Br the solver uses).',
    `Boundary conditions: the Thermal tab's. Tolerance ${c.tol_K} K on both, `
    + `damping ${c.damping}, max ${c.max_iter} passes.`,
    c.P_mech_extra_W == null ? ''
      : 'The bearings and the rotor windage are ANALYTIC (SKF frictional moment '
        + '+ Couette/disc drag) and travel round the loop with the rest: each '
        + "pass takes the bearing temperature off the previous pass's map (the "
        + 'shaft at its exposed ends, else the shaft average), bills the '
        + 'friction at it, and puts that heat back into the shaft. It is NOT '
        + 'part of the convergence test — that stays on the winding and the '
        + 'magnet, the two temperatures that change the field.',
    ...rows,
    c.residual_coil_K == null ? ''
      : `Residual at the reported point: winding ${c.residual_coil_K} K`
        + (c.residual_magnet_K == null ? '' : `, magnets ${c.residual_magnet_K} K`),
    c.converged
      ? 'The cards above were computed at the temperatures on the left of the '
        + 'last line — the run stored for this machine IS that run.'
      : (c.warning || 'did not settle'),
    c.note || '',
    ...mechanicalRows(c),
  ].filter(Boolean).join('\n');
}

/** The mechanical answers the run left, one line each: rotor stress, ring
 *  modes, critical speeds.  Nothing when the run did not ask for them. */
function mechanicalRows(c: CouplingBlock): string[] {
  const mb = c.mechanical;
  if (!mb) return [];
  const rows: string[] = [];
  if (mb.ok === false) rows.push(`Rotor stress: not solved — ${mb.error ?? 'refused'}.`);
  else if (mb.sf_min != null) {
    rows.push(`Rotor stress at ${mb.rpm != null ? fmtRpm(mb.rpm) : 'the run speed'}: `
      + `SF_min ${Number(mb.sf_min).toFixed(2)}${mb.note ? ` (${mb.note})` : ''}.`);
  }
  const mo = mb.modes;
  if (mo) {
    if (mo.ok === false) rows.push(`Ring modes: not solved — ${mo.error ?? 'refused'}.`);
    else {
      const t = mo.tightest;
      rows.push(`Ring modes (${mo.body ?? 'rotor'}, ${mo.n_modes ?? '?'} solved): `
        + `f₁ ${mo.f1_hz != null ? fmtHz(mo.f1_hz) : '—'}`
        + (mo.f1_order != null ? ` order ${mo.f1_order}` : '')
        + (t && t.margin_pct != null
            ? `; closest to an excitation: ${fmtHz(t.f_hz ?? 0)} vs ${t.excitation ?? '?'}`
              + ` (${t.margin_pct > 0 ? '+' : ''}${t.margin_pct.toFixed(1)} %${t.flag ? ' ⚠ inside 10 %' : ''})`
            : '')
        + '. Bonded, no prestress, no temperature — an upper bound.');
    }
  }
  const cr = mb.critical_speeds;
  if (cr) {
    if (cr.ok === false) rows.push(`Critical speeds: not solved — ${cr.error ?? 'refused'}.`);
    else {
      rows.push('Critical speeds: '
        + (cr.first_forward_rpm != null
            ? `first forward ${fmtRpm(cr.first_forward_rpm)}`
              + (cr.first_forward_margin_pct != null
                  ? `, ${cr.first_forward_margin_pct >= 0 ? '+' : ''}${cr.first_forward_margin_pct.toFixed(1)} % vs rated`
                    + (cr.rated_rpm != null ? ` ${fmtRpm(cr.rated_rpm)}` : '')
                  : '')
            : 'no forward critical in range')
        + (cr.verdict ? ` — ${cr.verdict}` : '')
        + (cr.bearing_span_mm != null
            ? `. Shaft line ASSUMED: span ${cr.bearing_span_mm} mm, bearing k `
              + `${cr.bearing_k_n_per_m != null ? Number(cr.bearing_k_n_per_m).toExponential(1) : '?'} N/m.`
            : '.'));
    }
  }
  return rows;
}
