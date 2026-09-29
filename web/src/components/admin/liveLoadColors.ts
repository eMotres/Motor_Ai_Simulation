// Colour system for the Admin · Overview live-load charts — no React, no
// fetch, so it can be unit-tested with plain node:test. Replaces the earlier
// all-grey palette (owner feedback: "too grey/dull").
//
// Built from the validated categorical eight (dataviz skill, references/
// palette.md), checked with scripts/validate_palette.js against THIS app's
// real chart surfaces (light `--panel-2` #ffffff, dark `--panel-2` #0b1220,
// from index.css) rather than the skill's generic ones:
//   node scripts/validate_palette.js "<8 hexes>" --mode light --surface "#ffffff"
//   node scripts/validate_palette.js "<8 hexes>" --mode dark  --surface "#0b1220"
// -> ALL CHECKS PASS both modes (worst adjacent CVD ΔE 9.1 light / 8.4 dark,
// >= 8 target; worst normal-vision ΔE 19.6 light / 19.3 dark, >= 15 floor).
// Three light-mode slots (aqua/yellow/magenta) sit below 3:1 contrast on
// white by design (documented in palette.md) — the relief channel is the
// always-on legend + tooltip text, never colour alone.
//
// This module SPLITS that eight two ways so a user's colour can never be
// mistaken for the outside-app family's, by construction (not by luck of the
// hash draw):
//   - USER_PALETTE: 6 of the 8 slots (blue, yellow, magenta, green, violet,
//     red) — hash-assigned per user. Re-validated as its own 6-chain (still
//     ALL CHECKS PASS both modes) since removing two slots creates one new
//     adjacency (blue next to yellow) the original 8-chain never tested.
//   - The other two slots are reserved: orange for the container family,
//     aqua for "host" — see below.
export type Mode = 'light' | 'dark';

const USER_PALETTE: Record<Mode, string[]> = {
  light: ['#2a78d6', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
  dark: ['#3987e5', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
};

// The full eight, for the per-server CPU/RAM charts (chart 1 & 2) — those
// never share a legend with the user/outside-app chart, so no reservation
// is needed there; all eight validated slots are free to use.
const SERVER_PALETTE: Record<Mode, string[]> = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
};

// Reserved slot 2 (orange) — every outside-app container, one shared warm
// hue varied by opacity (an ordinal composite: hue = "outside app", shade =
// individual container) rather than more distinct hues, which is what
// `validate_palette.js` says every design hits past ~3 simultaneous series
// (see palette.md's own "fold to Other" note) — the family IS the signal,
// the legend + tooltip carry individual identity.
const CONTAINER_HUE: Record<Mode, string> = { light: '#eb6834', dark: '#d95926' };
const CONTAINER_OPACITY_STEPS = [0.85, 0.68, 0.53, 0.4];

// Reserved slot 3 (aqua) — "host" (processes outside any container): the
// cool half of the "warm/cool family" the container hue is the warm half of.
const HOST_HUE: Record<Mode, string> = { light: '#1baf7a', dark: '#199e70' };

// A genuinely new pair, not one of the eight (so it can't collide with a
// user's hash-assigned colour): "app (idle/overhead)" — muted so it recedes
// behind the real signal, but warm-toned rather than flat grey. Validated
// pairwise against all eight master hues in both modes (worst normal-vision
// ΔE 17.3 light, comfortably >= 15) so it's safe next to any user colour too.
const OVERHEAD_HUE: Record<Mode, string> = { light: '#92400e', dark: '#c2831f' };

/** djb2 string hash, avalanched through a Thomas-Wang integer finalizer, ->
 *  non-negative int. Deterministic and pure: same id, same output, forever —
 *  no Math.random, no Date, no external state. This is what makes every
 *  colour below "hash-stable": a user or container keeps its colour across
 *  refreshes and page reloads regardless of what else is in the currently-
 *  visible top-N (rank changes don't repaint survivors).
 *
 *  The finalizer matters: djb2's low bits alone are weak against a SMALL
 *  modulo (`% 6`) -- ['alice','bob','carol','dave','eve','frank'] collapsed
 *  onto just 2 of the 6 slots without it (verified against this exact user
 *  set while building this module). Avalanching first (xor-shift + two
 *  Math.imul rounds, the same shape as Thomas Wang's 32-bit integer hash)
 *  spreads the bits so `% slots` sees uniform input instead. */
export function hashSlot(id: string, slots: number): number {
  let h = 5381;
  for (let i = 0; i < id.length; i++) {
    h = ((h << 5) + h + id.charCodeAt(i)) >>> 0; // djb2: h*33 + c, unsigned 32-bit
  }
  h ^= h >>> 16; h = Math.imul(h, 0x45d9f3b) >>> 0;
  h ^= h >>> 16; h = Math.imul(h, 0x45d9f3b) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  return h % Math.max(1, slots);
}

/** A server's shared colour for both its CPU (solid) and RAM (dashed) area. */
export function serverColor(id: string, mode: Mode): string {
  const p = SERVER_PALETTE[mode];
  return p[hashSlot(id, p.length)];
}

/** A user's bright, hash-stable colour (6-slot reserved subset — never
 *  orange or aqua, so it can never be confused with the outside-app family
 *  drawn from those two reserved slots). */
export function userColor(id: string, mode: Mode): string {
  const p = USER_PALETTE[mode];
  return p[hashSlot(id, p.length)];
}

/** An outside-app container's colour: the shared warm hue at a hash-stable
 *  opacity step (so two different containers read as two different shades
 *  of the same "outside app" family, not two arbitrary hues). */
export function containerColor(id: string, mode: Mode): { stroke: string; fillOpacity: number } {
  return { stroke: CONTAINER_HUE[mode], fillOpacity: CONTAINER_OPACITY_STEPS[hashSlot(id, CONTAINER_OPACITY_STEPS.length)] };
}

export function hostColor(mode: Mode): string {
  return HOST_HUE[mode];
}

export function overheadColor(mode: Mode): string {
  return OVERHEAD_HUE[mode];
}
