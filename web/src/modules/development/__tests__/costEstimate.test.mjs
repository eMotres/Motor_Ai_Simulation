import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  buildCostSteps, editLine, formatMoney, langOf, originChip, reviewRows, setLineStatus, setSetting, sourceCounts,
  stepIndexOfMissing, stepLines, stepState,
} from '../costEstimate.ts';

// the real line catalogue the server serves
const schema = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../src/motor_ai_sim/development/cost_estimate_schema.json', import.meta.url)), 'utf8'));
const t = (key, args) => (args ? `${key}|${JSON.stringify(args)}` : key);

const blank = (unit) => ({ status: 'missing', qty: null, unit, unit_cost: null, currency: 'EUR', source: null, source_ref: null, scale: null, derive: null, date: null, note: '', by: null });
function estimate(iterations = 2) {
  const iterItems = schema.groups.filter((g) => g.scope === 'iteration').flatMap((g) => g.sections.flatMap((s) => s.items));
  const projItems = schema.groups.filter((g) => g.scope === 'project').flatMap((g) => g.sections.flatMap((s) => s.items));
  return {
    version: 1, schema_version: 1, project_id: 'alpha', revision: 3,
    settings: { complex: false, iterations, motors_per_iteration: 2, currency: 'EUR', contingency_pct: 10, liquid_cooling: false, high_voltage: false, target_motor: null, analogs: [], basis_overrides: {} },
    iterations: Array.from({ length: iterations }, (_, i) => ({ index: i + 1, settings: { bench: 'dyno', milestone_pct: null }, lines: Object.fromEntries(iterItems.map((it) => [it.id, blank(it.unit)])) })),
    project_lines: Object.fromEntries(projItems.map((it) => [it.id, blank(it.unit)])), basis: {}, updated_at: null, updated_by: null,
  };
}

test('steps: settings, then manufacturing / testing / logistics per iteration, project-wide, review', () => {
  const steps = buildCostSteps(schema, estimate(3));
  assert.deepEqual(steps.map((s) => s.id), ['settings', 'it1-manufacturing', 'it1-testing', 'it1-logistics', 'it2-manufacturing', 'it2-testing',
    'it2-logistics', 'it3-manufacturing', 'it3-testing', 'it3-logistics', 'project', 'review']);
  const rows = stepLines(steps[1], estimate(3));
  assert.ok(rows.length > 30 && rows[0].item.id === 'tool_main' && rows[0].section.id === 'tooling');
  assert.equal(stepState(steps[1], estimate(3)).state, 'empty');
  assert.equal(stepIndexOfMissing(steps, { scope: 'iteration', iteration: 2, group: 'testing', item: 'test_bench' }), 5);
  assert.equal(stepIndexOfMissing(steps, { scope: 'project', iteration: null, group: 'project', item: 'proj_travel_factory' }), 10);
});

test('typing a number makes the line manual, filled only with quantity and unit cost; the source is never lost silently', () => {
  let e = estimate();
  const ref = { iteration: 1, item: 'mech_shaft' };
  e = editLine(e, ref, { qty: 2 }, 'owner@example.test', '2026-11-02');
  let line = e.iterations[0].lines.mech_shaft;
  assert.equal(line.status, 'missing');
  assert.equal(line.source, 'manual');
  e = editLine(e, ref, { unit_cost: 120 }, 'owner@example.test', '2026-11-02');
  line = e.iterations[0].lines.mech_shaft;
  assert.equal(line.status, 'filled');
  assert.equal(line.by, 'owner@example.test');
  assert.equal(line.date, '2026-11-02');
  // a note alone keeps the suggested source
  const sug = estimate();
  Object.assign(sug.iterations[0].lines.mech_housing, { status: 'filled', qty: 2, unit_cost: 450, source: 'analog', source_ref: { production: 'MID/b' }, scale: { rule: 'active_mass', factor: 1.5 } });
  const noted = editLine(sug, { iteration: 1, item: 'mech_housing' }, { note: 'quote 2026-10' }, 'o', '2026-11-02');
  assert.equal(noted.iterations[0].lines.mech_housing.source, 'analog');
  // a derived "modifications" line keeps its derive when only the % is edited
  const der = estimate();
  Object.assign(der.iterations[1].lines.tool_main, { status: 'filled', source: 'rule', derive: { pct: 20, from_iteration: 1 } });
  const pct = editLine(der, { iteration: 2, item: 'tool_main' }, { derivePct: 35 }, 'o', '2026-11-02');
  assert.deepEqual(pct.iterations[1].lines.tool_main.derive, { pct: 35, from_iteration: 1 });
  assert.equal(pct.iterations[1].lines.tool_main.status, 'filled');
  // a percentage line needs only its %
  const p2 = editLine(estimate(), { iteration: 1, item: 'scrap_allowance' }, { qty: 5 }, 'o', '2026-11-02', true);
  assert.equal(p2.iterations[0].lines.scrap_allowance.status, 'filled');
  // the input is not mutated
  assert.equal(estimate().iterations[0].lines.mech_shaft.qty, null);
});

test('not applicable is the owner\'s decision; back to missing clears the source', () => {
  let e = setLineStatus(estimate(), { iteration: 1, item: 'mech_cooling' }, 'not_applicable', 'o', '2026-11-02');
  assert.equal(e.iterations[0].lines.mech_cooling.source, 'manual');
  e = setLineStatus(e, { iteration: 1, item: 'mech_cooling' }, 'missing', 'o', '2026-11-02');
  assert.equal(e.iterations[0].lines.mech_cooling.source, null);
  assert.equal(setSetting(estimate(2), 'complex', true).settings.iterations, 3);
});

test('source chips say where the number came from', () => {
  const line = { ...blank('motor'), status: 'filled', qty: 2, unit_cost: 450, source: 'analog', source_ref: { production: 'MID/b', label: 'Housing' }, scale: { rule: 'active_mass', factor: 4.2 } };
  const chip = originChip(line, schema, 'en', t);
  assert.equal(chip.kind, 'analog');
  assert.match(chip.tip, /analogProduction/);
  assert.match(chip.tip, /MID\/b/);
  assert.match(chip.tip, /4\.2/);
  const manual = originChip({ ...line, source: 'manual', scale: null, by: 'owner@example.test', date: '2026-11-02' }, schema, 'en', t);
  assert.match(manual.tip, /owner@example.test/);
  assert.equal(originChip(blank('lot'), schema, 'en', t).kind, 'missing');
  assert.equal(originChip({ ...blank('lot'), status: 'not_applicable' }, schema, 'en', t).kind, 'not_applicable');
  assert.equal(langOf('zh-CN'), 'zh-CN');
  assert.equal(langOf('en-US'), 'en');
});

test('money stays per currency in the review table', () => {
  assert.equal(formatMoney({ EUR: 1200, CNY: 300.5 }), 'EUR 1 200.00 · CNY 300.50');
  assert.equal(formatMoney({}), '—');
  const rows = reviewRows({ iterations: [{ index: 1, manufacturing: { EUR: 10 }, testing: {}, logistics: { USD: 5 }, subtotal: { EUR: 10, USD: 5 }, lines: {} }],
    project: { total: { EUR: 1 }, lines: {} }, subtotal: {}, contingency_pct: 0, contingency: {}, total: {}, currencies: [], missing: [], missing_count: 0, milestones: [], warnings: [] });
  assert.equal(rows[0].subtotal, 'EUR 10.00 · USD 5.00');
  assert.equal(rows[1].key, 'project');
  const counts = sourceCounts(setLineStatus(estimate(1), { iteration: 1, item: 'mech_cooling' }, 'not_applicable', 'o', 'd'));
  assert.equal(counts.not_applicable, 1);
  assert.ok(counts.missing > 50);
});

test('every catalogue item has both locales, a known unit and a known scale rule', () => {
  for (const g of schema.groups) for (const s of g.sections) for (const it of s.items) {
    assert.ok(it.label.en && it.label['zh-CN'] && it.help.en && it.help['zh-CN'], it.id);
    assert.ok(schema.units[it.unit], it.id);
    if (it.analog) assert.ok(schema.scale_rules[it.analog.scale], it.id);
  }
});
