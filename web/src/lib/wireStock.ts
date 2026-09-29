/**
 * Enamelled flat copper wire physically in stock — pure helpers over the
 * `GET /api/wires/stock` payload (`motor_ai_sim.wire_stock` on the backend).
 *
 * WHY: the owner (2026-09-20) — a reference table of the strip actually on the
 * shelf, meant to become the source the winding editors restrict wire-size
 * choice to. This module owns no fetching (that lives in the React hook next
 * to it, `components/materials/useWireStock.ts`) so the row formatting and the
 * "nearest stocked size" search are plain functions a `node --test` run can
 * exercise without a DOM or a network mock.
 *
 * Nothing here RESTRICTS a wire selector — `nearestStockSizes` only hints.
 * The owner decides the hard restriction later.
 */

export interface WireStockRow {
  code: string;
  spec: string;
  thickness_mm: number;
  width_mm: number;
  insulation: string;
  self_bonding: boolean;
  thermal_class_c: number | null;
  stock_kg: number;
  warehouse: string;
  /** The warehouse label was ambiguous (typo, missing prefix) — unconfirmed. */
  check: boolean;
}

export interface WireStockSize {
  thickness_mm: number;
  width_mm: number;
  /** Summed stock_kg across every code stocked at this (thickness, width). */
  stock_kg: number;
  codes: string[];
}

export interface WireStockResponse {
  version?: number | string;
  updated?: string;
  unit?: string;
  wires: WireStockRow[];
  available_sizes: WireStockSize[];
}

// ─── Formatting (the row-formatting helper the node test covers) ───────────

/** "0.30" — one dimension, fixed to 2 decimals so a column does not jitter
 *  across rows of 0.2 / 0.30 / 1.44 mm. Thickness and width are their own
 *  sortable table columns (owner, 2026-09-20), so each is formatted alone. */
export function formatMm(v: number): string {
  return v.toFixed(2);
}

/** "0.30 × 3.50 mm" — combined, kept for callers that want one string (the
 *  nearest-size hint line uses its own trimmed form, see `formatNearestSizes`). */
export function formatSize(thickness_mm: number, width_mm: number): string {
  return `${formatMm(thickness_mm)} × ${formatMm(width_mm)} mm`;
}

/** The insulation column: "AIW", "AIW self-bonding", "QZYB-1-180", or "—" for
 *  a row whose label carried no recognisable insulation code (flag it via
 *  `check`, never invent one). `self_bonding` and `thermal_class_c` are read
 *  off their OWN fields, not parsed back out of `insulation`, so a card typed
 *  with the wrong prose still shows the right badge. */
export function formatInsulation(row: Pick<WireStockRow, 'insulation' | 'self_bonding' | 'thermal_class_c'>): string {
  const base = (row.insulation || '').trim();
  let label = base || '—';
  if (row.self_bonding && !/self.?bonding/i.test(label)) label += ' self-bonding';
  if (row.thermal_class_c && !new RegExp(String(row.thermal_class_c)).test(label)) {
    label += ` (${row.thermal_class_c} °C)`;
  }
  return label;
}

/** "6.0 kg" — one decimal, the table's unit rides alongside the header. */
export function formatStock(stock_kg: number): string {
  return `${stock_kg.toFixed(stock_kg < 10 ? 2 : 1)} kg`;
}

/** One row, pre-formatted for the table — what the component actually renders
 *  so the JSX stays free of number formatting. Thickness and width are two
 *  separate, independently sortable columns (owner, 2026-09-20). */
export interface FormattedWireRow {
  code: string;
  thicknessLabel: string;
  widthLabel: string;
  insulationLabel: string;
  stockLabel: string;
  warehouse: string;
  check: boolean;
}

export function formatWireRow(row: WireStockRow): FormattedWireRow {
  return {
    code: row.code,
    thicknessLabel: formatMm(row.thickness_mm),
    widthLabel: formatMm(row.width_mm),
    insulationLabel: formatInsulation(row),
    stockLabel: formatStock(row.stock_kg),
    warehouse: row.warehouse || '',
    check: !!row.check,
  };
}

// ─── Sorting / filtering ─────────────────────────────────────────────────────

/** Rows sorted by thickness then width then code — the table's default order
 *  (the backend already sorts `wires`, but a client-side filter can reorder a
 *  slice, so this is here for callers that build their own list). */
export function sortWires(rows: WireStockRow[]): WireStockRow[] {
  return [...rows].sort((a, b) =>
    a.thickness_mm - b.thickness_mm ||
    a.width_mm - b.width_mm ||
    a.code.localeCompare(b.code));
}

/** The table's three sortable columns and a click direction. */
export type WireSortKey = 'thickness_mm' | 'width_mm' | 'stock_kg';
export type SortDir = 'asc' | 'desc';

/**
 * Rows sorted by one clicked column, `dir` applied to THAT column only — ties
 * always break thickness -> width -> code (the default order), so clicking
 * "Width" groups equal widths together instead of leaving them in whatever
 * order the previous sort or the backend happened to serve them in.
 */
export function sortWiresByColumn(
  rows: WireStockRow[], key: WireSortKey, dir: SortDir = 'asc',
): WireStockRow[] {
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) =>
    sign * (a[key] - b[key]) ||
    (a.thickness_mm - b.thickness_mm) ||
    (a.width_mm - b.width_mm) ||
    a.code.localeCompare(b.code));
}

/** Every distinct width stocked, ascending — the table's "filter by width". */
export function widthsInStock(rows: WireStockRow[]): number[] {
  return Array.from(new Set(rows.map(r => r.width_mm))).sort((a, b) => a - b);
}

// ─── The passive hint for a wire input (Geometry / Configure) ──────────────

const SIZE_TOL_MM = 1e-3;

/** Is `(thickness_mm, width_mm)` one of the stocked sizes, within a µm-scale
 *  rounding tolerance (the geometry field's own step is 0.1 mm)? */
export function isSizeInStock(
  thickness_mm: number, width_mm: number, sizes: WireStockSize[],
): boolean {
  return sizes.some(s =>
    Math.abs(s.thickness_mm - thickness_mm) < SIZE_TOL_MM &&
    Math.abs(s.width_mm - width_mm) < SIZE_TOL_MM);
}

/** The `n` stocked sizes closest to `(thickness_mm, width_mm)` by plain
 *  Euclidean distance in mm — a handful of discrete sizes, so this is a hint,
 *  never a selection rule. */
export function nearestStockSizes(
  thickness_mm: number, width_mm: number, sizes: WireStockSize[], n = 3,
): WireStockSize[] {
  if (!sizes.length) return [];
  const dist = (s: WireStockSize) => {
    const dt = s.thickness_mm - thickness_mm;
    const dw = s.width_mm - width_mm;
    return Math.sqrt(dt * dt + dw * dw);
  };
  return [...sizes].sort((a, b) => dist(a) - dist(b)).slice(0, Math.max(0, n));
}

/** "0.5×7, 0.6×7" — the nearest-sizes half of the hint line. */
export function formatNearestSizes(sizes: WireStockSize[]): string {
  return sizes
    .map(s => `${trimTrailingZero(s.thickness_mm)}×${trimTrailingZero(s.width_mm)}`)
    .join(', ');
}

function trimTrailingZero(v: number): string {
  return Number(v.toFixed(2)).toString();
}

/**
 * "not in stock — nearest: 0.5×7, 0.6×7", or `null` when the pair IS stocked
 * (the caller then shows nothing — a passive hint, not a validation error).
 */
export function stockHint(
  thickness_mm: number, width_mm: number, sizes: WireStockSize[],
): string | null {
  if (!Number.isFinite(thickness_mm) || !Number.isFinite(width_mm)) return null;
  if (!sizes.length) return null;
  if (isSizeInStock(thickness_mm, width_mm, sizes)) return null;
  const nearest = nearestStockSizes(thickness_mm, width_mm, sizes, 2);
  return `not in stock — nearest: ${formatNearestSizes(nearest)}`;
}
