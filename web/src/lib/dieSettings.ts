// Per-die panel-settings memory.
//
// The mesh/sim panel state lives in ONE global localStorage block, so it used
// to travel with the user across machines: work on the 40 mm (mesh 1.0, its
// max symmetry 1/2), load the 200 mm — and those values silently became the
// 200 mm's run settings, then got baked into its saved duty and restored
// forever after (user 2026-08-31: "всегда было 1/4 и max 2 mm — почему
// настройки не сохраняются?").
//
// This module remembers the panel state PER DIE:
//   • remember(die)  — snapshot the current mesh.*/sim.* block under the die
//     (called when the user leaves a die, and when they save a duty — both
//     moments where the current settings demonstrably belong to that die);
//   • restoreMesh(die) — write the die's remembered MESH settings back;
//   • restoreAll(die)  — full block, for duties that carry no snapshot.
//
// Priority on duty load: the duty's own snapshot describes how its recorded
// result was computed and is applied first; the die memory then overrides the
// MESH fidelity keys — the user's own mesh for this machine beats whatever
// the panel happened to hold when the duty was saved.  Operating-point keys
// (current, rpm, γ, mode) always come from the duty — they define the point.

const KEY = 'family.dieSettings';
const MAX_DIES = 24;

// Run triggers and result caches are NOT settings (writing runNonce back once
// auto-started a solve — 2026-08-25).
const SKIP = new Set(['sim.runNonce', 'sim.lastTransient', 'sim.lastSummary',
                      'sim.viewSummary']);

type Block = Record<string, unknown>;
type Store = Record<string, { at: number; settings: Block }>;

function readStore(): Store {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') as Store; }
  catch { return {}; }
}

function writeStore(s: Store): void {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* quota */ }
}

export function snapshotPanelSettings(): Block {
  const out: Block = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)!;
    if (!k.startsWith('mesh.') && !k.startsWith('sim.')) continue;
    if (SKIP.has(k)) continue;
    try { out[k] = JSON.parse(localStorage.getItem(k)!); }
    catch { out[k] = localStorage.getItem(k); }
  }
  return out;
}

/** Remember the CURRENT panel block as this die's settings. */
export function rememberDieSettings(die: string): void {
  if (!die) return;
  const s = readStore();
  s[die] = { at: Date.now(), settings: snapshotPanelSettings() };
  const names = Object.keys(s);
  if (names.length > MAX_DIES) {
    names.sort((a, b) => (s[a].at || 0) - (s[b].at || 0));
    for (const n of names.slice(0, names.length - MAX_DIES)) delete s[n];
  }
  writeStore(s);
}

function apply(block: Block, filter: (k: string) => boolean): number {
  let n = 0;
  for (const [k, v] of Object.entries(block)) {
    if (SKIP.has(k) || !filter(k)) continue;
    try { localStorage.setItem(k, JSON.stringify(v)); n++; } catch { /* quota */ }
  }
  return n;
}

/** The die's remembered MESH settings (fidelity: element sizes, symmetry,
 *  gap layers…) — the keys the user complained about losing. */
export function restoreDieMeshSettings(die: string): boolean {
  const e = readStore()[die];
  if (!e) return false;
  return apply(e.settings, (k) => k.startsWith('mesh.')) > 0;
}

/** The die's full remembered block — for loads where nothing else says what
 *  the panel should look like (a duty saved before settings were captured). */
export function restoreDieSettings(die: string): boolean {
  const e = readStore()[die];
  if (!e) return false;
  return apply(e.settings, () => true) > 0;
}

export function hasDieSettings(die: string): boolean {
  return !!readStore()[die];
}
