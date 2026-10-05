// node --test — regression: the live L12 showed "NMC 100 cells / 370 V" beside a 6S machine
// (owner 2026-10-05, deploy e7b1ba2).
//
// ROOT CAUSE: two reference cards share L12's cross-section AND build — the real
// "CIANO14 40 new" (has the pack, the controller, the variants) and an older duplicate
// "CIANO14 40_12" (has none).  Configure took the first of a TIE, which on the live catalogue
// is the duplicate, so its context carried no pack and the stock 100-cell default stayed.
//
// Data: web/src/lib/__tests__/fixtures/live_l12_reference_cards.json — the two REAL cards'
// passports, copied from the live catalogue.  Imports the .ts modules themselves (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let G = null, B = null, M = null;
try {
  G = await import('../configuratorGuard.ts');
  B = await import('../configuratorBattery.ts');
  M = await import('../motorScaling.ts');
} catch { /* old Node */ }
const t = G ? test : test.skip;

const CARDS = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'fixtures',
  'live_l12_reference_cards.json'), 'utf8'));
const ref = (id, hasMachine) => ({ id, hasMachine, name: CARDS[id].name, geo: CARDS[id].passport.geo,
  passport: CARDS[id].passport.passport, poles: CARDS[id].passport.poles });

// the loaded L12 (what the store holds): geometry of the real machine
const LOADED = { motor_length: 12, num_wires_per_slot: 7, wire_height: 0.6,
                 stator_outer_radius: 20, magnet_height: 5.7 };
const dist = (r) => {
  const rel = (a, b) => (a > 0 && b > 0 ? Math.abs(Math.log(a / b)) : 5);
  const p0 = r.passport;
  return rel(p0.L0_mm, LOADED.motor_length) + rel(p0.N0, LOADED.num_wires_per_slot) + rel(p0.wireH0_mm, LOADED.wire_height);
};
const geoDist = (r) => Math.abs(r.geo.magnetHeight_mm - LOADED.magnet_height) + Math.abs(r.geo.statorOR_mm - LOADED.stator_outer_radius);

t('the two live cards really tie on build (the trap)', () => {
  const a = ref('cat_my_40mm_last', false), b = ref('cat_ciano14_40_new', true);
  assert.ok(Math.abs(dist(a) - dist(b)) < 1e-9, 'same L0 / N0 / wire');
  assert.equal(CARDS.cat_my_40mm_last.name, 'CIANO14 40_12');
});

t('the loaded L12 gets the real "CIANO14 40 new" card, whatever the catalogue order', () => {
  for (const order of [['cat_my_40mm_last', 'cat_ciano14_40_new'], ['cat_ciano14_40_new', 'cat_my_40mm_last']]) {
    // flags as the server sends them: only the real machine is one
    const cands = order.map((id) => ref(id, id === 'cat_ciano14_40_new'));
    assert.equal(G.pickReference(cands, dist, geoDist).id, 'cat_ciano14_40_new', order.join(','));
    // and even on an OLD server without the flag (geometry alone settles it)
    const noFlag = order.map((id) => ref(id, undefined));
    assert.equal(G.pickReference(noFlag, dist, geoDist).id, 'cat_ciano14_40_new', 'no flag: ' + order.join(','));
  }
});

t('pickReference: build first, then geometry, then "is a machine", then catalogue order', () => {
  const mk = (id, d, g, m) => ({ id, d, g, hasMachine: m });
  const pick = (c) => G.pickReference(c, (r) => r.d, (r) => r.g).id;
  assert.equal(pick([mk('far', 1, 0, true), mk('near', 0.1, 9, false)]), 'near');            // build wins
  assert.equal(pick([mk('a', 0, 0.2, true), mk('b', 0, 0, false)]), 'b');                    // then geometry
  assert.equal(pick([mk('dup', 0, 0, false), mk('real', 0, 0, true)]), 'real');             // then machine
  assert.equal(pick([mk('first', 0, 0, true), mk('second', 0, 0, true)]), 'first');          // then order
  assert.equal(pick([mk('x', 0, 0, false), mk('y', 0, 0, false)]), 'x');
  assert.equal(G.pickReference([], () => 0, () => 0), undefined);
});

t('a stored "edit" equal to the stock 100-cell default is never an edit', () => {
  const stock = { type: 'NMC', cells: 100, nom: 3.7, max: 4.2, min: 3.0 };
  assert.ok(B.isStockBattery(stock) && !B.isStockBattery({ ...stock, cells: 6 }));
  const raw = JSON.stringify({ 'cat:cat_ciano14_40_new': stock });
  assert.equal(B.readBatteryEdit(raw, 'cat:cat_ciano14_40_new'), null);
  // so the machine's own pack wins
  const pack = B.batteryFromPack({ chemistry: 'NMC', cells: 6, v_cell_min: 3, v_cell_nom: 3.7, v_cell_max: 4.2 });
  assert.equal(B.wantedBattery(B.readBatteryEdit(raw, 'cat:cat_ciano14_40_new'), pack).cells, 6);
});

t('the one-time cleanup drops stock entries and keeps real edits', () => {
  const stock = { type: 'NMC', cells: 100, nom: 3.7, max: 4.2, min: 3.0 };
  const mine = { type: 'NMC', cells: 7, nom: 3.7, max: 4.2, min: 3.0 };
  const raw = JSON.stringify({ a: stock, b: mine, c: { ...stock, type: 'LFP' } });
  const clean = JSON.parse(B.dropStockEdits(raw));
  assert.deepEqual(Object.keys(clean).sort(), ['b', 'c']);        // LFP 100 cells is not the stock default
  assert.deepEqual(clean.b, mine);
  assert.equal(B.dropStockEdits(null), null);
  assert.equal(B.dropStockEdits('garbage'), 'garbage');
  const same = JSON.stringify({ b: mine });
  assert.equal(B.dropStockEdits(same), same);                      // nothing to drop: untouched
});

// ── the motor marker vs the catalogue table ────────────────────────────────────────────────
// The marker is the DC bus the machine needs at the CURRENT knobs: sqrt(3) x the phase peak
// voltage = the line peak.  At the catalogue's own duty points it must agree with the
// catalogue's V L-L: the owner reads ~20 V (peak 48.8 A, 14400 rpm) and ~18 V (rated 42.78 A, 13000 rpm).
t('the marker at the catalogue duty points agrees with its V L-L table (20 V peak / 18 V rated)', () => {
  const c = CARDS.cat_ciano14_40_new.passport;
  const at = (I, rpm, extra = {}) => M.scaleMotor(c.passport,
    { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1, split: 1, I_A: I, rpm, ...extra }, c.poles);
  const rated = at(42.78, 13000), peak = at(48.79, 14400);
  const bus = (r) => r.Vphase_peak_V * Math.sqrt(3);
  assert.ok(Math.abs(bus(rated) - 18) / 18 < 0.06, `rated ${bus(rated)}`);
  assert.ok(Math.abs(bus(peak) - 20) / 20 < 0.06, `peak ${bus(peak)}`);
  assert.ok(Math.abs(bus(rated) - rated.Vline_peak_V) < 1e-9);       // the same quantity, no hidden factor
  // The marker reads ~69 V only for a knob state that is NOT one of those duty points — e.g.
  // L20's 25000 rpm with the strips of the other connection: that is a stale/foreign knob state,
  // which is why the marker now says which operating point it is for.
  const foreign = at(80.61, 25000, { split: 2 });
  assert.ok(bus(foreign) > 60, `foreign state ${bus(foreign)}`);
});

// ── the charts are hidden behind ONE flag (owner 2026-10-05, «графики пока убери») ──────────
test('the speed / efficiency charts sit behind the single SHOW_CONFIGURE_CHARTS flag, off', () => {
  const dir = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
  const src = (f) => readFileSync(join(dir, f), 'utf8');
  assert.match(src('lib/configuratorFlags.ts'), /export const SHOW_CONFIGURE_CHARTS = false;/);
  const panel = src('components/compare/ConfiguratorPanel.tsx');
  assert.match(panel, /\{SHOW_CONFIGURE_CHARTS && \(\s*<Box[^>]*>\s*<PerformanceCharts/);
  assert.equal((panel.match(/<PerformanceCharts/g) || []).length, 1);
  assert.match(src('components/compare/ChargePanel.tsx'), /SHOW_CONFIGURE_CHARTS && chartData\.length > 1/);
  // the geometry pictures are NOT behind it
  assert.doesNotMatch(panel, /SHOW_CONFIGURE_CHARTS && \(\s*<Box[^>]*>\s*<GeometryProjections/);
});
