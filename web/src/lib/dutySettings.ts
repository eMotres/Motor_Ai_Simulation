// Per-DUTY operating-point AND MATERIALS memory.
//
// One level below lib/dieSettings.ts (die → configuration → duty).  The die
// memory fixed the MESH keys travelling between machines; this fixes the
// OPERATING POINT travelling between the duties of ONE machine.
//
// The panel's operating point lives in one global sim.* block, so it used to be
// shared by every duty of a configuration: define "S2 peak" at 200 °C, click
// back to "S1 cont" — and S1 was still at 200 °C, because nothing ever put S1's
// own temperature back (user 2026-09-01: "почему эти режимы перезаписываются —
// токи и температуры должны быть разные для каждого duty").
//
// Two layers answer "what is this duty's point?", in priority order:
//   1. the LOCAL OVERLAY kept here — the user's un-saved edits for THAT duty,
//      keyed die+config+duty, surviving reloads like every other panel memory;
//   2. the duty's own SNAPSHOT (the sim.* block recorded into the yaml by an
//      explicit Save to duty) — applied by FamilyCatalog before the overlay.
//
// The overlay NEVER touches the stored duty: a saved duty changes only on an
// explicit duty-save, and that save CLEARS the overlay (the snapshot is then
// the truth).  See the no-silent-state rule.
//
// The same two layers carry the duty's MATERIALS (`mat.assign`, bottom of this
// file): a duty of this project is a whole thermal scenario ("peak 200C wire
// 120C NdFeB"), so what it is made of belongs to it, not to the machine.  The
// stored duty's own `materials:` dict is the snapshot layer there.
//
// What is NOT here, on purpose:
//   • mesh.* fidelity — belongs to the MACHINE (lib/dieSettings.ts);
//   • the battery — belongs to the MACHINE (lib/machineBattery.ts);
//   • sim.connection / sim.daxisDeg / sim.endWinding — properties of the
//     CONFIGURATION and its geometry, identical across its duties;
//   • sim.stepsPP and the other fidelity switches — how hard the point is
//     solved, not which point it is; the duty snapshot already restores them.

const KEY = 'family.dutyOp';
const CTX = 'family.opContext';
const MAX_DUTIES = 48;
const SEP = '\u001f';        // unit separator — cannot occur in a catalog name

/** The operating point, exactly the fields the panel's point is made of:
 *  the excitation (I, rpm, γ, temperature, motor/generator), the target the
 *  duty may be stated as, and the drive-side numbers that go with them. */
export const DUTY_OP_KEYS: readonly string[] = [
  'sim.current', 'sim.rpm', 'sim.frequency', 'sim.gamma',
  'sim.coilTemp', 'sim.opMode',
  'sim.targetKind', 'sim.targetValue',
  'sim.drive', 'sim.vPeak', 'sim.vDelta',
  'sim.vBus', 'sim.fSwitch', 'sim.fSwGroup', 'sim.fSwCustom',
  'sim.iBlock',
  // WHICH QUESTION THE COUPLED LOOP IS ASKED for THIS duty (owner 2026-09-18):
  // the steady state, or the first limit and the time to it.  It belongs here
  // and not to the machine because it is a property of the DUTY — a continuous
  // duty is a steady state by definition, a peak is a pull with a length — and
  // switching between them must bring each one's own question back.  The switch
  // itself (`sim.coupled`, on/off) stays global: that is "am I coupling at
  // all", which is a way of working rather than a property of a duty.
  'sim.coupledSolveTo',
  // The duty's MATERIALS — a PARTIAL ASSIGNMENT `{part: material}` (user
  // 2026-09-01: "смена магнитов не сохраняется в duty — нужно запоминать какие
  // магниты, и не только магниты: все материалы, для каждого duty").  A duty of
  // this project is a full thermal scenario ("peak 200C wire 120C NdFeB"), so
  // every material it is characterised with belongs to it, not to the machine.
  // It rides the per-request ?mat= payload only — the machine's own materials
  // ASSIGNMENT is never rewritten by a duty.  Not a `sim.` key on purpose: it
  // is a material, and the panel's usePersisted must not adopt it.
  'mat.assign',
  // LEGACY (earlier on 2026-09-01): the magnet temperature record alone, before
  // the same memory was generalized to every part.  Kept in this list so duties
  // saved that morning still restore — it is read UNDER `mat.assign` and any
  // write through the new API retires it (see activeDutyMaterials below).
  'mat.magnet',
];
const OP = new Set(DUTY_OP_KEYS);

/** Is this localStorage key part of a duty's operating point? */
export const isDutyOpKey = (k: string): boolean => OP.has(k);

export interface DutyRef { die: string; config: string; duty: string; }

/** die + config + duty — the identity an operating point is filed under. */
export function dutyKey(die?: string | null, config?: string | null,
                        duty?: string | null): string | null {
  if (!die || !config || !duty) return null;
  return `${die}${SEP}${config}${SEP}${duty}`;
}

type Block = Record<string, unknown>;
type Store = Record<string, { at: number; op: Block }>;

function readStore(): Store {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') as Store; }
  catch { return {}; }
}

function writeStore(s: Store): void {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* quota */ }
}

// ── which duty the panel is currently editing ────────────────────────────────
// A local record, not a fetch: the write-through below runs inside a render
// effect on every keystroke and cannot wait for /api/family/context.  It is
// written by the ONE place that selects a duty (FamilyCatalog.applyDuty) and
// therefore agrees with the server context by construction.

/** The duty the panel is editing right now — null when none is selected, and
 *  then everything behaves exactly as it did before per-duty memory. */
export function activeDuty(): DutyRef | null {
  try {
    const r = JSON.parse(localStorage.getItem(CTX) || 'null');
    if (r?.die && r?.config && r?.duty) {
      return { die: String(r.die), config: String(r.config), duty: String(r.duty) };
    }
  } catch { /* unreadable */ }
  return null;
}

export function setActiveDuty(die: string, config: string, duty: string): void {
  try {
    localStorage.setItem(CTX, JSON.stringify({ die, config, duty, at: Date.now() }));
  } catch { /* quota */ }
}

/** No duty is being edited any more (a my-motors copy, a bare configuration):
 *  panel edits stop being filed under whatever duty was last selected. */
export function clearActiveDuty(): void {
  try { localStorage.removeItem(CTX); } catch { /* nothing to clear */ }
}

// ── the overlay ──────────────────────────────────────────────────────────────

/** The operating point as it stands in the panel right now. */
export function snapshotOp(): Block {
  const out: Block = {};
  for (const k of DUTY_OP_KEYS) {
    const raw = localStorage.getItem(k);
    if (raw == null) continue;
    try { out[k] = JSON.parse(raw); } catch { out[k] = raw; }
  }
  return out;
}

function prune(s: Store): void {
  const names = Object.keys(s);
  if (names.length <= MAX_DUTIES) return;
  names.sort((a, b) => (s[a].at || 0) - (s[b].at || 0));
  for (const n of names.slice(0, names.length - MAX_DUTIES)) delete s[n];
}

/** File the WHOLE current point as this duty's local point.  Used when leaving
 *  a duty, to catch values written by something other than the panel's own
 *  fields (a Compare apply, a descent restore). */
export function rememberDutyOp(key: string | null): void {
  if (!key) return;
  const s = readStore();
  s[key] = { at: Date.now(), op: { ...(s[key]?.op ?? {}), ...snapshotOp() } };
  prune(s);
  writeStore(s);
}

/** One field changed in the panel — record it against the active duty only.
 *  This is the ONLY write path a keystroke takes: the stored duty is never
 *  touched outside an explicit Save to duty. */
export function noteDutyOpEdit(key: string | null, lsKey: string, value: unknown): void {
  if (!key || !OP.has(lsKey)) return;
  const s = readStore();
  const prev = s[key]?.op ?? {};
  // Same value already on record → no write (this runs on every mount of every
  // persisted field; a localStorage write per field per render is pure churn).
  if (key in s && lsKey in prev
      && JSON.stringify(prev[lsKey]) === JSON.stringify(value)) return;
  s[key] = { at: Date.now(), op: { ...prev, [lsKey]: value } };
  prune(s);
  writeStore(s);
}

/** Write this duty's remembered point back into the panel's keys.
 *  Returns how many fields the overlay actually had to say. */
export function restoreDutyOp(key: string | null): number {
  if (!key) return 0;
  const e = readStore()[key];
  if (!e) return 0;
  let n = 0;
  for (const [k, v] of Object.entries(e.op)) {
    if (!OP.has(k)) continue;
    try { localStorage.setItem(k, JSON.stringify(v)); n++; } catch { /* quota */ }
  }
  return n;
}

/** The duty was SAVED: its snapshot now states the point, so the local
 *  overlay has nothing left to add and must go — otherwise yesterday's edit
 *  would keep winning over the value the user just saved. */
export function clearDutyOp(key: string | null): void {
  if (!key) return;
  const s = readStore();
  if (!(key in s)) return;
  delete s[key];
  writeStore(s);
}

export function hasDutyOp(key: string | null): boolean {
  return !!key && !!readStore()[key];
}

// ── the duty's MATERIALS ─────────────────────────────────────────────────────
// Same two layers as the operating point, same keys: the duty SNAPSHOT writes
// ASSIGN_KEY back on select (it rides the duty's own `materials:` dict in the
// yaml), the local overlay then wins.  What is different is the DEFAULT: an
// unset key means "the machine's own materials", so selecting a duty must CLEAR
// the key before the two layers speak — otherwise the previous duty's 120 °C
// magnet (or its 20SW1200 steel) would still be standing when a duty that never
// picked one is loaded, and the solve would silently use materials nobody chose
// for it.
//
// The value is a PARTIAL assignment: only the parts this duty has an opinion
// about.  A part's value may be `null` — that is the duty SAYING "the machine's
// own", a statement that has to outrank the legacy magnet key and survive a
// switch away and back, which an absent entry could not do.
//
// Nothing here validates a name.  Resolution against the library (does the
// record still exist? is it the same category / the same magnet grade?) is
// lib/dutyMaterials.ts, so the ?mat= payload and the badge cannot disagree.

export const ASSIGN_KEY = 'mat.assign';
/** LEGACY key: the magnet-only ancestor of ASSIGN_KEY (2026-09-01, morning). */
export const MAGNET_KEY = 'mat.magnet';

/** This duty's partial assignment as it stands right now — `{}` when the duty
 *  has no opinion, and ALWAYS `{}` when no duty is selected: materials memory is
 *  a property of a duty, and with none active the machine's assignment is the
 *  whole truth (pre-feature behaviour, byte for byte).
 *
 *  A `null` value means "the machine's own" and is returned as such, so callers
 *  can tell "no opinion" (absent) from "explicitly the machine's" (null). */
export function activeDutyMaterials(): Record<string, string | null> {
  if (!activeDuty()) return {};
  const out: Record<string, string | null> = {};
  // MIGRATION, read-side: a duty saved before the generalization carries only
  // the magnet, under its own key.  It is read FIRST so the map below (newer by
  // construction — every write goes through the map) always outranks it.
  try {
    const raw = localStorage.getItem(MAGNET_KEY);
    if (raw != null) {
      const v = JSON.parse(raw);
      if (typeof v === 'string' && v) out.magnet = v;
    }
  } catch { /* unreadable legacy key — the machine's magnet it is */ }
  try {
    const raw = localStorage.getItem(ASSIGN_KEY);
    if (raw != null) {
      const m = JSON.parse(raw);
      if (m && typeof m === 'object' && !Array.isArray(m)) {
        for (const [part, v] of Object.entries(m as Record<string, unknown>)) {
          if (typeof v === 'string' && v) out[part] = v;
          else out[part] = null;      // this duty asked for the machine's own
        }
      }
    }
  } catch { /* unreadable map — fall back to the machine's materials */ }
  return out;
}

/** The duty-scoped MAGNET record, or null for "the machine's".  One entry of
 *  the map above, kept as its own accessor because the Simulation badge speaks
 *  about exactly this one part. */
export function activeDutyMagnet(): string | null {
  return activeDutyMaterials().magnet ?? null;
}

/** The user assigned `material` to `part` while a duty is active (null = back
 *  to the machine's own).  Writes the live key + files it under that duty,
 *  exactly the path a panel keystroke takes.  No duty → no-op: there is nothing
 *  to file it under, and with no duty the Materials tab's change is simply the
 *  machine's, as it always was. */
export function setDutyMaterial(part: string, material: string | null): void {
  const a = activeDuty();
  const key = dutyKey(a?.die, a?.config, a?.duty);
  if (!key || !part) return;
  let map: Record<string, string | null> = {};
  try {
    const raw = localStorage.getItem(ASSIGN_KEY);
    const m = raw == null ? null : JSON.parse(raw);
    if (m && typeof m === 'object' && !Array.isArray(m)) map = m;
  } catch { /* start from an empty map */ }
  // MIGRATION, write-side: ADOPT a legacy magnet-only pick into the map before
  // retiring it — otherwise the first change to ANY other part would silently
  // drop the magnet temperature this duty was already carrying.
  try {
    const raw = localStorage.getItem(MAGNET_KEY);
    const v = raw == null ? null : JSON.parse(raw);
    if (!('magnet' in map) && typeof v === 'string' && v) map.magnet = v;
  } catch { /* no legacy pick to adopt */ }
  map[part] = material || null;
  // …and retire it on BOTH layers (live key and this duty's overlay entry).
  // Left standing it would resurface the morning's pick the first time a duty
  // asked for the machine's own magnet — the map's `null` would be outranked by
  // a key that no longer has any writer.
  try { localStorage.removeItem(MAGNET_KEY); } catch { /* nothing to clear */ }
  noteDutyOpEdit(key, MAGNET_KEY, null);
  try { localStorage.setItem(ASSIGN_KEY, JSON.stringify(map)); } catch { /* quota */ }
  noteDutyOpEdit(key, ASSIGN_KEY, map);
  // One event for "this duty's materials moved" — the badge, the summary card's
  // staleness check and anything else that shows what the next solve will use.
  try { window.dispatchEvent(new CustomEvent('duty-materials-changed')); }
  catch { /* SSR/no-window */ }
}

/** The magnet is one part like any other; kept as its own name because the
 *  Simulation panel's temperature picker reads better this way. */
export function setDutyMagnet(name: string | null): void {
  setDutyMaterial('magnet', name);
}

/** A duty is being selected: drop the outgoing duty's materials (both the map
 *  and the legacy magnet key) so the incoming one starts from "the machine's".
 *  Its snapshot and its overlay are applied right after and put back whatever
 *  IT chose. */
export function clearDutyMaterialsKeys(): void {
  try { localStorage.removeItem(ASSIGN_KEY); } catch { /* nothing to clear */ }
  try { localStorage.removeItem(MAGNET_KEY); } catch { /* nothing to clear */ }
}

// ── the duty's DUTY CYCLE ────────────────────────────────────────────────────
// (2026-09-14)  What the machine actually DOES with this point: S1 continuous,
// S2 one pull, S3 an ED % of a cycle, or an explicit segment list.  It lives in
// the yaml with the duty (`routes/family.DutySpec.duty_cycle`) because a robot
// joint's cycle is part of the duty's DEFINITION — the steady answer to a two
// second peak is a temperature the machine never reaches.
//
// Exactly the two layers the operating point has, and in the same order:
//   1. the SNAPSHOT — the block the stored duty carries, written by ▶
//      (`dutySnapshot.applyDutyCycleBlock`);
//   2. the OVERLAY — the user's un-saved edit in the cycle editor.
// The overlay outranks the snapshot; a duty save sends the overlay and then
// CLEARS it, because the snapshot then states the cycle.
//
// Both layers live in ONE entry per duty, so "which of the two is speaking" is
// answerable from the store rather than inferred: `{snap, block}`.

const DC_KEY = 'family.dutyCycle';

/** One duty-cycle block — the shape `routes/family.DutySpec.duty_cycle` takes.
 *  Deliberately loose: the CATALOG owns the schema and validates it by name on
 *  every save, and a second copy of those rules here would be a second place
 *  for them to drift. */
export type DutyCycleBlock = Record<string, unknown>;

interface DcEntry { at?: number; snap?: DutyCycleBlock | null;
                    block?: DutyCycleBlock | null }
type DcStore = Record<string, DcEntry>;

function readDcStore(): DcStore {
  try { return JSON.parse(localStorage.getItem(DC_KEY) || '{}') as DcStore; }
  catch { return {}; }
}

function writeDcStore(s: DcStore): void {
  const names = Object.keys(s);
  if (names.length > MAX_DUTIES) {
    names.sort((a, b) => (s[a].at || 0) - (s[b].at || 0));
    for (const n of names.slice(0, names.length - MAX_DUTIES)) delete s[n];
  }
  try { localStorage.setItem(DC_KEY, JSON.stringify(s)); } catch { /* quota */ }
}

/** PURE.  Which of a duty's two layers actually states its cycle.
 *
 *  The overlay wins when it has anything to say — including an EMPTY block,
 *  which is the user saying "this duty has no cycle any more" and is the only
 *  way to retire one (the catalog reads an empty block the same way).  `null`
 *  is "nothing said here", which falls through to the snapshot and then to no
 *  cycle at all, i.e. the continuous point every duty was before this existed.
 *
 *  Copied verbatim into `lib/__tests__/dutyCycleBlock.test.mjs`. */
export function effectiveDutyCycle(entry: DcEntry | null | undefined):
    DutyCycleBlock | null {
  if (!entry) return null;
  if (entry.block !== undefined && entry.block !== null) {
    return Object.keys(entry.block).length ? entry.block : null;
  }
  if (entry.snap && Object.keys(entry.snap).length) return entry.snap;
  return null;
}

/** This duty's cycle as the editor must show it: the overlay, else the block ▶
 *  restored from the yaml, else null (no cycle — the continuous point). */
export function readDutyCycle(key: string | null): DutyCycleBlock | null {
  if (!key) return null;
  return effectiveDutyCycle(readDcStore()[key]);
}

/** Is the shown cycle an UN-SAVED edit?  The editor says so out loud — a block
 *  that lives only in this browser is not the one the report will read. */
export function dutyCycleEdited(key: string | null): boolean {
  if (!key) return false;
  const e = readDcStore()[key];
  return !!e && e.block !== undefined && e.block !== null;
}

/** The user changed the cycle in the editor.  Local only: the stored duty is
 *  never touched outside an explicit duty save, the same rule the operating
 *  point follows.  An EMPTY block is a real statement ("no cycle") and is kept
 *  as one; `null` retracts the overlay instead. */
export function noteDutyCycleEdit(key: string | null,
                                  block: DutyCycleBlock | null): void {
  if (!key) return;
  const s = readDcStore();
  const prev = s[key] ?? {};
  if (block === null) {
    if (prev.block === undefined) return;
    delete prev.block;
    s[key] = { ...prev, at: Date.now() };
  } else {
    if (JSON.stringify(prev.block) === JSON.stringify(block)) return;
    s[key] = { ...prev, at: Date.now(), block };
  }
  writeDcStore(s);
}

/** The duty was SAVED: the yaml now states the cycle, so the overlay has
 *  nothing left to add and must go — otherwise the pre-save edit would keep
 *  winning on every later selection of this duty.  The block that was just
 *  saved becomes the snapshot layer in the same breath. */
export function clearDutyCycle(key: string | null,
                               saved?: DutyCycleBlock | null): void {
  if (!key) return;
  const s = readDcStore();
  const prev = s[key];
  if (!prev && saved == null) return;
  const snap = saved === undefined ? prev?.snap ?? null : saved;
  if (!snap || !Object.keys(snap).length) delete s[key];
  else s[key] = { at: Date.now(), snap };
  writeDcStore(s);
}

/** ▶ on a duty: the block the yaml carries becomes this duty's SNAPSHOT layer.
 *  Called by `dutySnapshot.applyDutyCycleBlock`, before the overlay speaks. */
export function setDutyCycleSnapshot(key: string | null,
                                     block: DutyCycleBlock | null): void {
  if (!key) return;
  const s = readDcStore();
  const prev = s[key] ?? {};
  const snap = (block && Object.keys(block).length) ? block : null;
  if (!snap && prev.block === undefined) {
    if (!(key in s)) return;
    delete s[key];
  } else {
    s[key] = { ...prev, at: Date.now(), snap };
  }
  writeDcStore(s);
}

/** The cycle editor's own fields, as it holds them — text, because that is what
 *  the user is typing into, and a blank is "not stated" rather than zero. */
export interface DutyCycleForm {
  kind: string;
  /** which duty RUNS; '' = the calibration duty */
  duty?: string;
  tOn?: string;
  edPct?: string;
  cycleS?: string;
  /** '' = the machine stands UNPOWERED while resting (a real segment) */
  restDuty?: string;
  tStartC?: string;
  nCyclesMax?: string;
  calibrationDuty?: string;
  segments?: { duty: string; t_s: string }[];
}

/** PURE.  The editor's fields as the `duty_cycle:` block the catalog stores.
 *
 *  Same rule the cooling request follows: a field the chosen KIND does not use
 *  is not written.  An `ed_pct` sitting beside an S2 pull is a number the solver
 *  never reads and the reader of the yaml has to guess about, and a `rest_duty`
 *  left over from an S3 would quietly re-appear the next time the kind changed
 *  back.  A blank rest duty is NOT absent: it is the machine standing
 *  unpowered, which is a real segment of a real cycle, so it is written as an
 *  explicit `null`.
 *
 *  Copied verbatim into `lib/__tests__/dutyCycleBlock.test.mjs`. */
export function dutyCycleFromForm(f: DutyCycleForm): DutyCycleBlock {
  const num = (v: string | undefined): number | null => {
    const t = (v ?? '').trim();
    if (t === '') return null;
    const x = Number(t);
    return Number.isFinite(x) ? x : null;
  };
  const kind = ['S1', 'S2', 'S3', 'segments'].includes(f.kind) ? f.kind : 'S1';
  const out: DutyCycleBlock = { kind };
  if (kind !== 'segments' && (f.duty ?? '').trim()) out.duty = f.duty;
  if (kind === 'S2') out.t_on_s = num(f.tOn);
  if (kind === 'S3') {
    out.ed_pct = num(f.edPct);
    out.cycle_s = num(f.cycleS);
    // `null` = unpowered, and it must be WRITTEN: an absent key would read as
    // "the running duty", i.e. a machine that never rests.
    out.rest_duty = (f.restDuty ?? '').trim() ? f.restDuty : null;
  }
  if (kind === 'segments') {
    out.segments = (f.segments ?? [])
      .filter((s) => num(s.t_s) !== null)
      .map((s) => ({ duty: s.duty.trim() ? s.duty : null, t_s: num(s.t_s) }));
  }
  const t0 = num(f.tStartC);
  if (t0 !== null) out.t_start_c = t0;
  const nmax = num(f.nCyclesMax);
  if (nmax !== null) out.n_cycles_max = Math.max(1, Math.round(nmax));
  if ((f.calibrationDuty ?? '').trim()) out.calibration_duty = f.calibrationDuty;
  return out;
}

/** PURE.  One duty's cycle as a CHIP: "S2 25 s", "S3 ED 25 % · 60 s".
 *  `null` for a duty with no cycle — which reads as the continuous point every
 *  duty was assumed to be before 2026-09-14, and says nothing rather than
 *  claiming an S1 nobody wrote.
 *
 *  …and `null` for an S1 as well, since 2026-09-16.  A continuous duty IS the
 *  machine sitting at its point, which is what a duty with no block already
 *  means, and the two were chipped differently for no reason a reader could
 *  act on: one said "S1" and the other said nothing, about the same machine
 *  doing the same thing.  The rule the user asked for is the plain one — a
 *  cycle chip appears when there is a CYCLE (user 2026-09-16: S1 shows nothing
 *  cycle-related).  The block itself is untouched: `kind: S1` is still written,
 *  still stored, and still what the coupled loop reads.
 *
 *  Copied verbatim into `lib/__tests__/dutyCycleBlock.test.mjs`. */
export function dutyCycleChip(block: DutyCycleBlock | null | undefined):
    string | null {
  if (!block || !Object.keys(block).length) return null;
  const kind = String(block.kind ?? 'S1');
  // A blank is NOT a zero.  `Number(null)` is 0 and `Number('')` is 0, and an
  // S3 whose ED was left to the tool to find (2026-09-15) carries exactly those
  // — a chip reading "S3 ED 0 %" would state a duty ratio nobody wrote, for a
  // cycle that runs for no time at all.
  const n = (v: unknown): number | null => {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') {
      return null;
    }
    const x = Number(v);
    return Number.isFinite(x) ? x : null;
  };
  const g = (x: number) => String(Math.round(x * 100) / 100);
  if (kind === 'S2') {
    const t = n(block.t_on_s);
    return t != null ? `S2 ${g(t)} s` : 'S2';
  }
  if (kind === 'S3') {
    const ed = n(block.ed_pct), cyc = n(block.cycle_s);
    // no ED stated = the tool finds the allowable one, and the chip says so
    // rather than either inventing a ratio or hiding the cycle length
    if (ed == null) return cyc != null ? `S3 ${g(cyc)} s · ED found` : 'S3';
    const found = block.found === true ? ' found' : '';
    return cyc != null ? `S3 ED ${g(ed)} %${found} · ${g(cyc)} s`
                       : `S3 ED ${g(ed)} %${found}`;
  }
  if (kind === 'segments') {
    const segs = Array.isArray(block.segments) ? block.segments : [];
    const tot = segs.reduce((a: number, s) => a + (n((s as { t_s?: unknown })?.t_s) ?? 0), 0);
    return segs.length ? `${segs.length} segments · ${g(tot)} s` : 'segments';
  }
  return null;   // S1 — continuous, i.e. no cycle to chip
}
