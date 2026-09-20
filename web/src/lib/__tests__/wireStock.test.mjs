// node --test — the pure helpers of lib/wireStock.ts, copied verbatim (the
// repo's convention for node tests: `node --test` cannot load the TS modules,
// so the pure functions under test are re-stated here and kept in sync — see
// turnsFactor.test.mjs / geometryApplyOutcome.test.mjs for the same pattern).
//
// What this pins: the "flat wire in stock" reference table's row formatting
// (owner, 2026-09-20 — "справочная таблица по доступным на складе проводам")
// and the passive "not in stock — nearest: …" hint for a wire_height /
// wire_width input, which does NOT restrict anything yet — it only hints.
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copies of the shipped helpers (lib/wireStock.ts) ─────────────── */

function formatMm(v) {
  return v.toFixed(2);
}

function formatSize(thickness_mm, width_mm) {
  return `${formatMm(thickness_mm)} × ${formatMm(width_mm)} mm`;
}

function formatInsulation(row) {
  const base = (row.insulation || '').trim();
  let label = base || '—';
  if (row.self_bonding && !/self.?bonding/i.test(label)) label += ' self-bonding';
  if (row.thermal_class_c && !new RegExp(String(row.thermal_class_c)).test(label)) {
    label += ` (${row.thermal_class_c} °C)`;
  }
  return label;
}

function formatStock(stock_kg) {
  return `${stock_kg.toFixed(stock_kg < 10 ? 2 : 1)} kg`;
}

function formatWireRow(row) {
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

function sortWires(rows) {
  return [...rows].sort((a, b) =>
    a.thickness_mm - b.thickness_mm ||
    a.width_mm - b.width_mm ||
    a.code.localeCompare(b.code));
}

function sortWiresByColumn(rows, key, dir = 'asc') {
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) =>
    sign * (a[key] - b[key]) ||
    (a.thickness_mm - b.thickness_mm) ||
    (a.width_mm - b.width_mm) ||
    a.code.localeCompare(b.code));
}

function widthsInStock(rows) {
  return Array.from(new Set(rows.map(r => r.width_mm))).sort((a, b) => a - b);
}

const SIZE_TOL_MM = 1e-3;

function isSizeInStock(thickness_mm, width_mm, sizes) {
  return sizes.some(s =>
    Math.abs(s.thickness_mm - thickness_mm) < SIZE_TOL_MM &&
    Math.abs(s.width_mm - width_mm) < SIZE_TOL_MM);
}

function nearestStockSizes(thickness_mm, width_mm, sizes, n = 3) {
  if (!sizes.length) return [];
  const dist = (s) => {
    const dt = s.thickness_mm - thickness_mm;
    const dw = s.width_mm - width_mm;
    return Math.sqrt(dt * dt + dw * dw);
  };
  return [...sizes].sort((a, b) => dist(a) - dist(b)).slice(0, Math.max(0, n));
}

function trimTrailingZero(v) {
  return Number(v.toFixed(2)).toString();
}

function formatNearestSizes(sizes) {
  return sizes
    .map(s => `${trimTrailingZero(s.thickness_mm)}×${trimTrailingZero(s.width_mm)}`)
    .join(', ');
}

function stockHint(thickness_mm, width_mm, sizes) {
  if (!Number.isFinite(thickness_mm) || !Number.isFinite(width_mm)) return null;
  if (!sizes.length) return null;
  if (isSizeInStock(thickness_mm, width_mm, sizes)) return null;
  const nearest = nearestStockSizes(thickness_mm, width_mm, sizes, 2);
  return `not in stock — nearest: ${formatNearestSizes(nearest)}`;
}

/* ── fixtures — a slice shaped like GET /api/wires/stock ───────────────────── */

const ROW_A1 = {
  code: 'A1', spec: 'SFT-AIW 0.5*2.5', thickness_mm: 0.5, width_mm: 2.5,
  insulation: 'AIW', self_bonding: false, thermal_class_c: null,
  stock_kg: 6.0, warehouse: '', check: false,
};
const ROW_A2 = {
  code: 'A2', spec: 'SFT-AIW/SB 0.3*3.5', thickness_mm: 0.3, width_mm: 3.5,
  insulation: 'AIW', self_bonding: true, thermal_class_c: 200,
  stock_kg: 2.27, warehouse: '小象仓库', check: false,
};
const ROW_A3_AMBIGUOUS = {
  code: 'A3', spec: '0.6*7', thickness_mm: 0.6, width_mm: 7.0,
  insulation: '', self_bonding: false, thermal_class_c: null,
  stock_kg: 29.17, warehouse: '', check: true,
};

const SIZES = [
  { thickness_mm: 0.3, width_mm: 3.5, stock_kg: 2.27, codes: ['A2'] },
  { thickness_mm: 0.5, width_mm: 2.5, stock_kg: 6.0, codes: ['A1'] },
  { thickness_mm: 0.5, width_mm: 7.0, stock_kg: 1.25, codes: ['X'] },
  { thickness_mm: 0.6, width_mm: 7.0, stock_kg: 29.17, codes: ['A3'] },
];

/* ── formatting ──────────────────────────────────────────────────────────── */

test('formatSize pads to 2 decimals so the column does not jitter', () => {
  assert.equal(formatSize(0.5, 2.5), '0.50 × 2.50 mm');
  assert.equal(formatSize(1.44, 2.53), '1.44 × 2.53 mm');
});

test('formatInsulation adds self-bonding and thermal class only when missing from the label', () => {
  assert.equal(formatInsulation(ROW_A1), 'AIW');
  assert.equal(formatInsulation(ROW_A2), 'AIW self-bonding (200 °C)');
  // already spells out "self-bonding" and the class number — must not double up
  assert.equal(
    formatInsulation({ insulation: 'AIW self-bonding 200C', self_bonding: true, thermal_class_c: 200 }),
    'AIW self-bonding 200C');
});

test('formatInsulation falls back to an em dash rather than inventing a code', () => {
  assert.equal(formatInsulation(ROW_A3_AMBIGUOUS), '—');
});

test('formatStock keeps 2 decimals under 10 kg and 1 decimal at/above', () => {
  assert.equal(formatStock(2.27), '2.27 kg');
  assert.equal(formatStock(29.17), '29.2 kg');
  assert.equal(formatStock(321.383), '321.4 kg');
});

test('formatWireRow assembles the pre-formatted row the table renders', () => {
  const r = formatWireRow(ROW_A2);
  assert.deepEqual(r, {
    code: 'A2',
    thicknessLabel: '0.30',
    widthLabel: '3.50',
    insulationLabel: 'AIW self-bonding (200 °C)',
    stockLabel: '2.27 kg',
    warehouse: '小象仓库',
    check: false,
  });
});

test('formatWireRow carries the ambiguous-label flag through', () => {
  assert.equal(formatWireRow(ROW_A3_AMBIGUOUS).check, true);
});

/* ── sorting / filtering ─────────────────────────────────────────────────── */

test('sortWires orders by thickness, then width, then code', () => {
  const sorted = sortWires([ROW_A3_AMBIGUOUS, ROW_A1, ROW_A2]);
  assert.deepEqual(sorted.map(r => r.code), ['A2', 'A1', 'A3']);
});

test('widthsInStock is the ascending set of distinct widths', () => {
  assert.deepEqual(widthsInStock([ROW_A1, ROW_A2, ROW_A3_AMBIGUOUS, { ...ROW_A1, code: 'A1b' }]),
    [2.5, 3.5, 7.0]);
});

/* ── sortWiresByColumn — the owner's addendum (2026-09-20): each of the
   three columns sorts independently on click, defaulting to thickness then
   width (the same order sortWires gives). ─────────────────────────────── */

const ROW_A4 = {
  code: 'A4', spec: 'SFT-AIW 0.6*2.5', thickness_mm: 0.6, width_mm: 2.5,
  insulation: 'AIW', self_bonding: false, thermal_class_c: null,
  stock_kg: 1.0, warehouse: '', check: false,
};

test('sortWiresByColumn on thickness (asc) matches the default sortWires order', () => {
  const byColumn = sortWiresByColumn([ROW_A3_AMBIGUOUS, ROW_A1, ROW_A2], 'thickness_mm', 'asc');
  const byDefault = sortWires([ROW_A3_AMBIGUOUS, ROW_A1, ROW_A2]);
  assert.deepEqual(byColumn.map(r => r.code), byDefault.map(r => r.code));
});

test('sortWiresByColumn on thickness (desc) reverses the primary key only', () => {
  const sorted = sortWiresByColumn([ROW_A2, ROW_A1, ROW_A3_AMBIGUOUS], 'thickness_mm', 'desc');
  assert.deepEqual(sorted.map(r => r.code), ['A3', 'A1', 'A2']);
});

test('sortWiresByColumn on width groups equal widths and breaks ties by thickness', () => {
  // A1 (0.5, 2.5), A4 (0.6, 2.5): same width — thickness breaks the tie.
  // A2 (0.3, 3.5), A3 (0.6, 7.0).
  const sorted = sortWiresByColumn([ROW_A3_AMBIGUOUS, ROW_A2, ROW_A4, ROW_A1], 'width_mm', 'asc');
  assert.deepEqual(sorted.map(r => r.code), ['A1', 'A4', 'A2', 'A3']);
});

test('sortWiresByColumn on width descending puts the widest first', () => {
  const sorted = sortWiresByColumn([ROW_A1, ROW_A2, ROW_A3_AMBIGUOUS, ROW_A4], 'width_mm', 'desc');
  assert.deepEqual(sorted.map(r => r.code), ['A3', 'A2', 'A1', 'A4']);
});

test('sortWiresByColumn on stock_kg orders by the summed shelf weight', () => {
  // stock_kg: A2=2.27, A4=1.0, A1=6.0, A3=29.17
  const asc = sortWiresByColumn([ROW_A1, ROW_A2, ROW_A3_AMBIGUOUS, ROW_A4], 'stock_kg', 'asc');
  assert.deepEqual(asc.map(r => r.code), ['A4', 'A2', 'A1', 'A3']);
  const desc = sortWiresByColumn([ROW_A1, ROW_A2, ROW_A3_AMBIGUOUS, ROW_A4], 'stock_kg', 'desc');
  assert.deepEqual(desc.map(r => r.code), ['A3', 'A1', 'A2', 'A4']);
});

/* ── the passive stock hint ─────────────────────────────────────────────── */

test('a stocked size gets no hint at all', () => {
  assert.equal(stockHint(0.5, 2.5, SIZES), null);
});

test('an off-catalogue size names the nearest sizes, closest first', () => {
  const hint = stockHint(0.52, 7.0, SIZES);
  assert.equal(hint, 'not in stock — nearest: 0.5×7, 0.6×7');
});

test('nearestStockSizes orders by Euclidean distance and respects n', () => {
  const nearest = nearestStockSizes(0.5, 6.0, SIZES, 2);
  assert.deepEqual(nearest.map(s => [s.thickness_mm, s.width_mm]),
    [[0.5, 7.0], [0.6, 7.0]]);
});

test('isSizeInStock tolerates float rounding but not a real mismatch', () => {
  assert.equal(isSizeInStock(0.5000001, 2.5, SIZES), true);
  assert.equal(isSizeInStock(0.4, 2.5, SIZES), false);
});

test('stockHint is silent (null) with no data yet, or non-finite input', () => {
  assert.equal(stockHint(0.5, 2.5, []), null);
  assert.equal(stockHint(NaN, 2.5, SIZES), null);
});

test('formatNearestSizes trims trailing zeros ("0.5×7", not "0.50×7.00")', () => {
  assert.equal(formatNearestSizes([{ thickness_mm: 0.5, width_mm: 7 }, { thickness_mm: 0.6, width_mm: 7 }]),
    '0.5×7, 0.6×7');
});
