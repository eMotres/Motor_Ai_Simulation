// The points-limited Configure draws the ordinary Configure's result grid from a stored point (owner 2026-10-26).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { configureGrid } from '../configureGrid.ts';
import { highlightIndex, isConfigRecommended } from '../projectFocus.ts';

const point = (comparison) => ({ duty: 'cruise', has_result: true, comparison });
const ids = (grid) => grid.map((row) => row.map((c) => c.id));

test('the rows and their order are the Configure grid; a cell that cannot be computed is simply not drawn', () => {
  const grid = configureGrid(point({
    torque_nm: 818.84, power_w: 250000, mass_kg: 29.14, mass_partial: true, efficiency_pct: 95.1, ripple_pct: 2.1,
    p_loss_total_w: 12000, p_fe_w: 3000, p_cu_w: 8000, v_phase_peak_v: 300, v_line_peak_v: 520, current_density_a_mm2: 7.5,
  }));
  assert.deepEqual(ids(grid), [
    ['torque', 'power', 'mass', 'efficiency', 'ripple', 'torqueMass', 'powerMass'],
    ['losses', 'pFe', 'pCu', 'lossDensity'],                                  // no magnet loss stored: no cell
    ['dcBus', 'vLine', 'vPhase', 'vLineRms', 'vPhaseRms', 'currDensity'],
  ]);
  const byId = Object.fromEntries(grid.flat().map((c) => [c.id, c]));
  assert.equal(byId.power.value, 250);                                         // kW, as in Configure
  assert.ok(Math.abs(byId.torqueMass.value - 818.84 / 29.14) < 1e-9);          // derived with the Configure formula
  assert.ok(Math.abs(byId.dcBus.value - 300 * Math.sqrt(3)) < 1e-9);            // DC bus (min) = phase peak x sqrt(3)
  assert.ok(Math.abs(byId.vLineRms.value - 520 / Math.SQRT2) < 1e-9);
  assert.equal(byId.currDensity.unit, 'A/mm²');
});

test('labels and tips are the Configure keys; the mass names follow the stamp (active unless stamped full)', () => {
  const a = Object.fromEntries(configureGrid(point({ torque_nm: 1, mass_kg: 10, power_w: 1000, mass_partial: true })).flat().map((c) => [c.id, c]));
  assert.equal(a.torque.labelKey, 'configure.torque');
  assert.equal(a.mass.labelKey, 'configure.massPartial');
  assert.equal(a.torqueMass.labelKey, 'configure.tPerMassActive');
  assert.equal(a.powerMass.labelKey, 'configure.pPerMassActive');
  const f = Object.fromEntries(configureGrid(point({ torque_nm: 1, mass_kg: 10, power_w: 1000, mass_partial: false })).flat().map((c) => [c.id, c]));
  assert.equal(f.mass.labelKey, 'configure.mass');
  assert.equal(f.torqueMass.labelKey, 'configure.tPerMass');
  assert.equal(f.mass.tipKey, 'configure.massFullTip');
});

test('a value of exactly 0 is not drawn, an empty row disappears, an empty point gives no grid', () => {
  const grid = configureGrid(point({ torque_nm: 0, power_w: 0, efficiency_pct: 90, p_loss_total_w: 0 }));
  assert.deepEqual(ids(grid), [['efficiency']]);
  assert.deepEqual(configureGrid(point({})), []);
  assert.deepEqual(configureGrid(null), []);
  assert.deepEqual(configureGrid(point({ torque_nm: 'x', power_w: Number.NaN })), []);          // not a number: not drawn
});

test('no per-mass cell without a positive mass; DC bus prefers the stored requirement', () => {
  assert.deepEqual(ids(configureGrid(point({ torque_nm: 5, power_w: 100 }))), [['torque', 'power']]);
  const bus = configureGrid(point({ v_dc_required_v: 650, v_phase_peak_v: 100 })).flat().find((c) => c.id === 'dcBus');
  assert.equal(bus.value, 650);
});

test('the Motors tab marks the recommended variant of the highlighted project', () => {
  const idx = highlightIndex([{ die: 'A', config: 'c1', recommended: { by: 'x' } }, { die: 'A', config: 'c2' }]);
  assert.equal(isConfigRecommended(idx, 'a', 'C1'), true);
  assert.equal(isConfigRecommended(idx, 'A', 'c2'), false);
});

test('wiring: the points-limited Configure uses the grid and the battery block, the custom DC bus field is gone, stars are drawn', () => {
  const src = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  const panel = src('../../../components/compare/PointsConfigure.tsx');
  assert.match(panel, /configureGrid\(current\)/);
  assert.match(panel, /<BatteryPanel vDc=\{dcBus\}/);
  assert.match(panel, /specBatteryView\(/);
  assert.doesNotMatch(panel, /busText|busHeadroom|points\.bus/);
  assert.match(panel, /<VariantControls projectId=\{focus\.id\}/);
  assert.match(panel, /data-testid="step-star"/);
  assert.match(src('../../../components/compare/StepsChart.tsx'), /data-testid="steps-chart-star"/);
  assert.match(src('../../../components/catalog/FamilyCatalog.tsx'), /isConfigRecommended\(projectIndex, die\.name, b\.config\)/);
});
