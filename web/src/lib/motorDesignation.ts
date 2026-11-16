/** The standard motor designation (owner 2026-11-16): `<letters><poles> <OD>_<L> <turns><F|R><connection><Y|D> [Rev X]`.
 *  The server generates it from the parameters (`motor_ai_sim.motor_designation`); this file only READS one: the same grammar as
 *  `motor_designation.parse`, for the decoder popover and the public help page (which works without sign-in and calls no API).
 *  Pure, so it runs under `node --test`. */

export const UNKNOWN = '?';

const DESIGNATION = /^(?<w>[CD?])(?<r>[IO?])(?<c>[ALO?])(?<s>[GNC?])(?<d>D?)(?<o>[O?]?)(?<f>[F?]?)(?<a>A?)(?<poles>\d+) (?<od>\d+(?:\.\d+)?)_(?<length>\d+(?:\.\d+)?) (?<turns>\d+)(?<wire>[FR?])(?<conn>\d+[SP](?:\d+P)?)(?<sd>[YD?])(?: Rev (?<rev>[A-Z]+))?$/;

export const SEGMENTS = ['winding', 'rotor', 'cooling', 'steel', 'double', 'open', 'ferrite', 'aluminium', 'poles', 'od', 'length',
  'turns', 'wire', 'connection', 'star_delta', 'rev'] as const;
export type Segment = typeof SEGMENTS[number];
export type DesignationFields = Record<Segment, string>;

export function parseDesignation(text: string): DesignationFields | null {
  const m = DESIGNATION.exec(String(text ?? '').trim());
  if (!m?.groups) return null;
  const g = m.groups;
  return { winding: g.w, rotor: g.r, cooling: g.c, steel: g.s, double: g.d ?? '', open: g.o ?? '', ferrite: g.f ?? '',
    aluminium: g.a ?? '', poles: g.poles, od: g.od, length: g.length, turns: g.turns, wire: g.wire, connection: g.conn,
    star_delta: g.sd, rev: g.rev ?? '' };
}

/** The i18n key of one segment's meaning: `designation.meaning.<segment>.<value>` for a letter, `...<segment>.n` for a number,
 *  `...<segment>.none` for an omitted optional letter, `...unknown` for `?`. */
export function meaningKey(segment: Segment, value: string): string {
  if (value === UNKNOWN) return 'designation.meaning.unknown';
  if (['poles', 'od', 'length', 'turns', 'connection', 'rev'].includes(segment)) return `designation.meaning.${segment}.n`;
  return `designation.meaning.${segment}.${value || 'none'}`;
}

/** The connection code in words: `4S` four coils in series, `2S2P` two in series x two parallel paths, `4P` four in parallel. */
export function connectionParts(code: string): { series: number; parallel: number } | null {
  const m = /^(\d+)S(?:(\d+)P)?$|^(\d+)P$/.exec(code);
  if (!m) return null;
  if (m[3]) return { series: 1, parallel: Number(m[3]) };
  return { series: Number(m[1]), parallel: m[2] ? Number(m[2]) : 1 };
}

/** The letter table of the help page: every position with its possible values ('' = omitted). */
export const LETTER_TABLE: ReadonlyArray<{ segment: Segment; values: string[] }> = [
  { segment: 'winding', values: ['C', 'D'] },
  { segment: 'rotor', values: ['I', 'O'] },
  { segment: 'cooling', values: ['A', 'L', 'O'] },
  { segment: 'steel', values: ['G', 'N', 'C'] },
  { segment: 'double', values: ['D', ''] },
  { segment: 'open', values: ['O', ''] },
  { segment: 'ferrite', values: ['F', ''] },
  { segment: 'aluminium', values: ['A', ''] },
  { segment: 'wire', values: ['F', 'R'] },
  { segment: 'star_delta', values: ['Y', 'D'] },
];
