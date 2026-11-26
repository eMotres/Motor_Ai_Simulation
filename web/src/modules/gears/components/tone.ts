import type { Tone } from '../view';

export const TONE_COLOUR: Record<Tone, string> = {
  ok: 'var(--ok, #2e7d32)', estimated: 'var(--warn, #ed6c02)', unsupported: 'var(--text-3, #777)',
  failed: 'var(--err, #d32f2f)', unknown: 'var(--text-3, #777)',
};
