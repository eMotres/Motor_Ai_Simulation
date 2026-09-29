/**
 * Max-element-size slider bounds for the Mesh panel (dependency-free, node-testable).
 *
 * The solver caps the iron element at the feature floor (2 elements across the
 * smallest tooth/slot, ÷4 hi-fi); above it every position meshes the same, so
 * the slider range is [floor/6 … floor].  On a tiny motor (Ø12 CIANO14: floor
 * 0.50 mm) the steps are 0.02 mm — the chip must print TWO decimals, or a move
 * 0.30 → 0.32 mm reads "0.3 mm" both times and the slider looks dead
 * (2026-09-28 «this is broken again»).
 */
export interface MeshSliderBounds {
  min: number;
  max: number;
  step: number;
  /** decimals the chip needs so that one step is always visible */
  decimals: number;
}

export function meshSliderBounds(floor: number | null | undefined): MeshSliderBounds {
  const f = (typeof floor === 'number' && isFinite(floor) && floor > 0) ? floor : null;
  if (!f) return { min: 1.5, max: 8, step: 0.5, decimals: 1 };
  const max = f;
  const min = Math.min(max, Math.max(0.05, +(f / 6).toFixed(2)));
  const step = Math.max(0.01, +((max - min) / 18).toFixed(2));
  const decimals = step < 0.1 ? 2 : 1;
  return { min, max, step, decimals };
}

/** The value the slider/chip show: the stored size clamped into the live range. */
export function meshSliderValue(sizeMm: number, b: MeshSliderBounds): number {
  return Math.min(Math.max(sizeMm, b.min), b.max);
}

/** Chip label — precision follows the step, so every move is visible. */
export function meshSizeLabel(sizeMm: number, b: MeshSliderBounds): string {
  return `${meshSliderValue(sizeMm, b).toFixed(b.decimals)} mm`;
}
