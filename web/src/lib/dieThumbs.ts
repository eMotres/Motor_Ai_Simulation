/**
 * The cross-section drawings of the catalog dies (owner 2026-11-12).
 *
 * `GET /api/family/tree` no longer carries each die's `thumb_svg` (two thirds of its 3.4 MB, and the Motors tab waited for it
 * before drawing a single row).  `GET /api/family/thumbs` answers `{ thumbs: { <die>: <svg> } }` for the dies the tree lists
 * for the same caller; `lib/familyTree.fetchDieThumbs` fetches it and these pure functions read it.  A tree from an older
 * server still has `thumb_svg` on the die: that is used when the map has no entry.
 * Pure functions, no React, no window: `node --test` imports this file.
 */

export type DieThumbs = Record<string, string>;

/** The `thumbs` map of a `/api/family/thumbs` answer; anything that is not a name -> non-empty string map is left out. */
export function readThumbs(body: unknown): DieThumbs {
  const raw = (body as { thumbs?: unknown } | null)?.thumbs;
  const out: DieThumbs = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'string' && v) out[k] = v;
  }
  return out;
}

/** The drawing of a tree die: the thumbs map first, the die's own `thumb_svg` (older server) second, else undefined. */
export function thumbOf(die: { name?: unknown; thumb_svg?: unknown } | null | undefined, thumbs: DieThumbs | null | undefined): string | undefined {
  const name = typeof die?.name === 'string' ? die.name : '';
  const fromMap = name && thumbs ? thumbs[name] : undefined;
  if (fromMap) return fromMap;
  return typeof die?.thumb_svg === 'string' && die.thumb_svg ? die.thumb_svg : undefined;
}
