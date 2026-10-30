/** "Save to <duty>" and the build: the refusal and the move, read for the strip.
 *
 *  POST /api/family/duty refuses (409) a live machine whose build differs from
 *  the configuration.  Owner 2026-10-30, after three refused "Save to peak" on
 *  'CIANO56 400_80 vadim 5' (wire 7 -> 7.5 made the slot 7.74 -> 8.24): the
 *  refusal stays on the Save button, and the offered way out must be one that
 *  works - a DIE-level difference (another lamination) cannot be saved as a new
 *  configuration of the same die, only on a new die.  When the backend could
 *  resolve it itself it moved the configuration to the die the machine IS and
 *  answers `transitioned`.  Pure. */

export interface BuildClashInfo {
  /** 'die': a die-level difference - only a new die carries it; 'config': a new configuration of this die does. */
  kind: 'config' | 'die';
  /** One line for the button area. */
  short: string;
  /** The server's whole refusal, for the tooltip. */
  full: string;
}
export type BuildClash = BuildClashInfo | null;

/** The differences the refusal names: `configuration 'X' (a; b; c) — ...`. */
export function clashDiffs(message: string): string[] {
  const m = /configuration '.*?' \((.*?)\) — /s.exec(String(message || ''));
  return m ? m[1].split('; ').map((x) => x.trim()).filter(Boolean) : [];
}

export function buildClashOf(message: string): BuildClash {
  const full = String(message || '');
  if (!(full.includes('cannot carry its own geometry') || full.includes('build differs from configuration'))) {
    return null;
  }
  const diffs = clashDiffs(full);
  const kind = diffs.some((d) => d.startsWith('die ')) ? 'die' : 'config';
  const named = diffs.map((d) => d.replace(/^die /, '').replace(/_/g, ' '));
  const shown = named.slice(0, 2).join('; ') + (named.length > 2 ? ` (+${named.length - 2})` : '');
  return { kind, full, short: `✗ not saved — build differs${shown ? `: ${shown}` : ''}` };
}

export interface MovedSave {
  die: string;
  config: string;
  fromDie: string;
  created: boolean;
  diffs: { key: string; label?: string; die: unknown; live: unknown }[];
}

/** The duty save's `transitioned` answer, or null when the save stayed on its die. */
export function movedSave(rj: any): MovedSave | null {
  const t = rj?.transitioned;
  if (!t?.die) return null;
  return { die: String(t.die), config: String(rj?.config || t.config || ''),
           fromDie: String(t.from_die || ''), created: t.created === true,
           diffs: Array.isArray(t.diffs) ? t.diffs : [] };
}

const num = (v: unknown) => (typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : String(v));

/** "moved to new die 'X' (slot width 7.74 → 8.24); 'Y' is untouched". */
export function movedLine(m: MovedSave): string {
  const why = m.diffs.map((d) => `${d.label || d.key} ${num(d.die)} → ${num(d.live)}`).join(', ');
  return `another lamination${why ? ` (${why})` : ''}: moved to ${m.created ? 'new' : 'existing'} die '${m.die}'`
    + (m.fromDie ? `; '${m.fromDie}' is untouched` : '');
}
