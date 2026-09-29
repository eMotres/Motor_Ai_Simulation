// The MACHINE's battery — identity, local memory, and the pack arithmetic.
//
// A battery belongs to a motor, not to a panel.  On the vendor's own machines
// it lives in the family configuration yaml (config/dies/<die>/<cfg>.yaml,
// written by PATCH /api/family/config/{die}/{cfg}/battery) and that file stays
// the single source of truth — the catalog, the Configure tuner, the datasheet
// export and the Simulation tab's V_bus prefill all read it from there.
//
// Machines OUTSIDE that catalog (a client's ▶-copy, a legacy preset) have no
// yaml to write, so their battery is remembered client-side — but keyed by the
// MACHINE, never in the global sim.* block.  That block travels with the panel:
// the mesh settings used to ride it from the 40 mm onto the 200 mm and became
// that machine's run settings (see lib/dieSettings.ts).  A battery doing the
// same would silently re-scale another motor's PWM ripple.

const KEY = 'battery.local';
const MAX_MACHINES = 24;

export interface BatteryPack {
  chemistry?: string | null;
  cells?: number | null;
  v_cell_min?: number;
  v_cell_nom?: number | null;
  v_cell_max?: number;
  v_min: number;
  v_nom?: number | null;
  v_max: number;
  // ── CHARGE SIDE ────────────────────────────────────────────────────────
  // Optional, and absent on every pack stored before this existed: the fields
  // that turn a voltage nameplate into a circuit, so a GENERATOR duty can say
  // how many amps reach the pack (V_bus = V_oc + I_charge·R_pack, R_pack =
  // NS·r_int/NP) and at what C-rate.  When they are missing the backend fills
  // chemistry placeholders and flags them as placeholders — it never invents
  // one silently.
  n_parallel?: number | null;
  r_int_mohm?: number | null;      // PER CELL
  capacity_ah?: number | null;     // per string
  i_charge_max_a?: number | null;  // pack limit
}

/** What BatteryDialog hands back: the per-CELL specification. */
export interface CellSpec {
  chemistry: string;
  cells: number;
  v_cell_min: number;
  v_cell_nom: number;
  v_cell_max: number;
  n_parallel?: number;
  r_int_mohm?: number;
  capacity_ah?: number;
  i_charge_max_a?: number;
}

const round = (v: number, d: number) => {
  const f = 10 ** d;
  return Math.round(v * f) / f;
};

/** Pack totals from the cell specification — the SAME arithmetic and rounding
 *  the backend applies in routes/family.py::set_battery, so a locally
 *  remembered battery and a yaml-stored one read identically on screen.
 *  (Used ONLY on the local path: a family save takes the pack the backend
 *  returns.  The one place the two could differ is an exact .5 tie at the last
 *  kept digit, where Python rounds to even and JS rounds up — 0.1 V on a pack
 *  total, and never on a number the yaml holds.) */
export function packFromCells(s: CellSpec): BatteryPack {
  const n = Math.round(s.cells);
  const nom = Number.isFinite(s.v_cell_nom) ? s.v_cell_nom : null;
  return {
    chemistry: (s.chemistry || '').trim() || null,
    cells: n,
    v_cell_min: round(s.v_cell_min, 3),
    v_cell_nom: nom == null ? null : round(nom, 3),
    v_cell_max: round(s.v_cell_max, 3),
    v_min: round(n * s.v_cell_min, 1),
    v_nom: nom == null ? null : round(n * nom, 1),
    v_max: round(n * s.v_cell_max, 1),
    // Charge side rides through unrounded-but-clamped, and only when the
    // dialog actually carried it: a pack whose spec says nothing about these
    // must stay a pack that says nothing about them, so the backend's
    // placeholder path (and its "placeholder" flag) still applies.
    ...(s.n_parallel == null ? {} : { n_parallel: Math.max(1, Math.round(s.n_parallel)) }),
    ...(s.r_int_mohm == null ? {} : { r_int_mohm: round(s.r_int_mohm, 4) }),
    ...(s.capacity_ah == null ? {} : { capacity_ah: round(s.capacity_ah, 4) }),
    ...(s.i_charge_max_a == null ? {} : { i_charge_max_a: round(s.i_charge_max_a, 4) }),
  };
}

/** The pack as the SOLVER takes it — the `battery` request payload.
 *  Deliberately a flat payload of plain numbers, never a path into the family
 *  config: the solver must not know where a machine's battery is stored (the
 *  same rule v_bus follows).  Returns null when there is no pack, because
 *  "no battery" is a real answer and must not be faked as a 0 V one. */
export function batteryPayload(b: BatteryPack | null | undefined) {
  if (!b) return null;
  const v = (x: unknown) =>
    (x != null && Number.isFinite(Number(x))) ? Number(x) : null;
  const v_oc = v(b.v_nom) ?? ((v(b.v_min) != null && v(b.v_max) != null)
    ? (Number(b.v_min) + Number(b.v_max)) / 2 : null);
  if (v_oc == null) return null;
  return {
    v_oc,
    v_nom: v(b.v_nom), v_min: v(b.v_min), v_max: v(b.v_max),
    cells: v(b.cells) ?? 1,
    n_parallel: v(b.n_parallel),
    r_int_mohm: v(b.r_int_mohm),
    capacity_ah: v(b.capacity_ah),
    i_charge_max_a: v(b.i_charge_max_a),
    chemistry: b.chemistry ?? null,
  };
}

/** The client-side machine identity a battery is filed under.
 *  A family copy is die/config (what the user's strip already names); anything
 *  else is the active preset.  null = we cannot say WHICH machine this is, and
 *  a battery with no machine is exactly the thing that must not be stored. */
export function machineKey(
  loc: { die?: string | null; config?: string | null } | null | undefined,
  presetId?: string | null,
): string | null {
  if (loc?.die && loc?.config) return `fam:${loc.die}/${loc.config}`;
  if (presetId) return `preset:${presetId}`;
  return null;
}

type Store = Record<string, { at: number; battery: BatteryPack }>;

function readStore(): Store {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') as Store; }
  catch { return {}; }
}

export function readLocalBattery(key: string | null): BatteryPack | null {
  if (!key) return null;
  const e = readStore()[key];
  return e?.battery ?? null;
}

export function writeLocalBattery(key: string | null, battery: BatteryPack): void {
  if (!key) return;
  const s = readStore();
  s[key] = { at: Date.now(), battery };
  const names = Object.keys(s);
  if (names.length > MAX_MACHINES) {
    names.sort((a, b) => (s[a].at || 0) - (s[b].at || 0));
    for (const n of names.slice(0, names.length - MAX_MACHINES)) delete s[n];
  }
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* quota */ }
}

/** The die/config a non-writing client ▶-copied (written by FamilyCatalog). */
export function readLocalContext(): { die: string; config: string;
                                      duty?: string | null } | null {
  try {
    const loc = JSON.parse(localStorage.getItem('family.localContext') || 'null');
    if (loc?.die && loc?.config) return loc;
  } catch { /* unreadable */ }
  return null;
}

/** "NMC · 200S · 640/750/860 V" — chemistry, series count and the pack's
 *  min/nom/max voltages (user 2026-09-01: all three, not just the nominal —
 *  the min is what a duty is judged against, the max sets the insulation).
 *  Degrades to whatever subset the pack carries; "no battery" when none. */
export function batteryChipLabel(b: BatteryPack | null | undefined): string {
  if (!b) return 'no battery';
  const parts: string[] = [];
  // The chemistry alone — "NMC", not the supplier and part number the field
  // may carry ("NMC (GF Myriad semi-solid, FAP106136260)"); the full text is
  // the dialog's (user 2026-09-09: "просто NMC · 200S · 640/750/860 V").
  if (b.chemistry) parts.push(String(b.chemistry).split(/[\s(]/)[0] || String(b.chemistry));
  if (b.cells) parts.push(`${b.cells}S`);
  const f = (v: unknown) =>
    (v != null && Number.isFinite(Number(v))) ? String(Math.round(Number(v))) : null;
  const vs = [f(b.v_min), f(b.v_nom), f(b.v_max)].filter((x): x is string => x != null);
  if (vs.length === 3) parts.push(`${vs[0]}/${vs[1]}/${vs[2]} V`);
  else if (vs.length) parts.push(`${vs.join('–')} V`);
  return parts.length ? parts.join(' · ') : 'no battery';
}
