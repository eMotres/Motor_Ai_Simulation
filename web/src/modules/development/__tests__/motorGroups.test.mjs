// node --test: the Motors & results table (owner 2026-10-26) - one group per geometry variant, a colour per die, history under
// its variant, the Latest / All versions view.  Synthetic motors only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  DIE_PALETTE, averageEfficiency, dieColor, groupMotorVersions, headerFlags, latestAndBest, motorColor, orderByGroup, variantKey, versionsForView,
} from '../motorGroups.ts';
import { developmentComparisonRows, developmentComparisonColumns } from '../comparisonRows.ts';
import { visibleProjectVersions } from '../motorStatus.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const page = read('../components/DevelopmentPage.tsx');
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);

const result = (duty, eff) => ({ duty, has_result: eff !== undefined, comparison: eff === undefined ? {} : { efficiency_pct: eff, torque_nm: 10 } });
const motor = (die, config, over = {}) => ({ die, config, ...over });
const A1 = motor('DieA', 'opt1', { results: [result('rated', 90), result('peak', 88)], note: 'baseline' });
const A2 = motor('DieA', 'opt2', { results: [result('rated', 93), result('peak', 91)], note: 'optimised' });
const A3 = motor('DieA', 'opt3', { results: [result('rated', 92)] });
const B1 = motor('DieB', 'x1', { status: 'trial', results: [result('rated', 99)] });
const C1 = motor('DieC', 'new', { results: [] });                                  // no result yet
const H = (m, id) => ({ ...m, kind: 'history', snapshot_id: id, results: m.results, note: `snap ${id}` });

test('every geometry has one stable accent colour from a fixed palette, the same wherever it appears', () => {
  assert.equal(dieColor('DieA'), dieColor('diea'));                                 // case-insensitive
  assert.ok(DIE_PALETTE.includes(dieColor('DieA')));
  assert.equal(motorColor(A1), motorColor(A2));                                     // two variants of one die share it
  assert.equal(motorColor({ die: 'alias', canonical_die: 'DieA' }), dieColor('DieA'));  // the canonical die decides
  const colors = new Set(['DieA', 'DieB', 'DieC', 'DieD', 'DieE'].map(dieColor));
  assert.ok(colors.size >= 3, 'different dies mostly differ');
  assert.equal(dieColor(''), 'var(--text-4)');
  assert.equal(new Set(DIE_PALETTE).size, DIE_PALETTE.length);
  // the same helper colours the points-only Configure step selector
  const points = read('../../../components/compare/PointsConfigure.tsx');
  assert.match(points, /motorColor\(v\)/);
  assert.match(points, /data-testid="step-accent"/);
});

test('history snapshots are grouped under their variant, in table order', () => {
  const versions = [A1, B1, A2, H(A1, 'h1'), H(A2, 'h2'), H(A1, 'h3'), H(C1, 'h4')];
  const groups = groupMotorVersions(versions);
  assert.deepEqual(groups.map((g) => g.key), ['diea/opt1', 'dieb/x1', 'diea/opt2', 'diec/new']);
  assert.deepEqual(groups[0].history.map((h) => h.snapshot_id), ['h1', 'h3']);
  assert.equal(groups[3].live, null);                                                // a snapshot of a variant no longer linked
  const ordered = orderByGroup(versions);
  assert.deepEqual(ordered.map((m) => m.snapshot_id ?? m.config), ['opt1', 'h1', 'h3', 'x1', 'opt2', 'h2', 'h4']);
});

test('header flags: a variant header above its first row, a history header above each snapshot, none between duty rows', () => {
  const ordered = orderByGroup([A1, H(A1, 'h1'), H(A1, 'h3'), A2]);
  // rows as the table builds them: one entry per duty row (A1 has 2 duties, h1 1, h3 2, A2 2)
  const rows = [A1, A1, ordered[1], ordered[2], ordered[2], A2, A2];
  const flags = rows.map((_, i) => headerFlags(rows, i));
  assert.deepEqual(flags.map((f) => f.group), [true, false, false, false, false, true, false]);
  assert.deepEqual(flags.map((f) => f.history), [false, false, true, true, false, false, false]);
  assert.deepEqual(headerFlags([], 0), { group: false, history: false });
});

test('Latest shows the latest current variant plus the best one if it is another; trials, older variants and history are hidden', () => {
  assert.equal(averageEfficiency(A1), 89);
  assert.equal(averageEfficiency(C1), null);
  // A2 is both the best and (as the last linked current one) ... A3 is the latest, A2 the best
  const shown = latestAndBest([A1, A2, A3, B1]);
  assert.deepEqual(shown.map((m) => m.config), ['opt2', 'opt3']);                 // B1 is a trial (99 %) and hidden; A1 is older
  assert.deepEqual(latestAndBest([A1, A2]).map((m) => m.config), ['opt2']);       // the latest IS the best: one variant
  assert.deepEqual(latestAndBest([A2, A1]).map((m) => m.config), ['opt2', 'opt1']);   // the best first-linked, the latest second (table order kept)
  assert.deepEqual(latestAndBest([A1, C1]).map((m) => m.config), ['opt1', 'new']);    // the latest has no result: it is still shown
  assert.deepEqual(latestAndBest([B1]), []);                                          // only a trial: nothing current
  assert.deepEqual(latestAndBest([]), []);
  assert.deepEqual(latestAndBest([A1, H(A1, 'h1')]).map((m) => m.kind ?? 'live'), ['live']);
});

test('All versions shows everything grouped; the view helper picks the filter', () => {
  const all = [A1, B1, A2, H(A1, 'h1')];
  assert.deepEqual(versionsForView(all, 'all').map((m) => m.snapshot_id ?? m.config), ['opt1', 'h1', 'x1', 'opt2']);
  assert.deepEqual(versionsForView(all, 'latest').map((m) => m.config), ['opt2']);
  // the existing visibility rule feeds it: without "All" the history snapshots and trials never arrive
  assert.deepEqual(visibleProjectVersions([A1, B1], [], false).map((m) => m.config), ['opt1']);
  assert.equal(visibleProjectVersions([A1, B1], [{ id: 's1', die: 'DieA', config: 'opt1' }], true).length, 3);
});

test('the table rows: a variant without results is ONE "not computed" row', () => {
  const rows = developmentComparisonRows([C1]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].hasResult, false);
  const t = (key) => key;
  const duty = developmentComparisonColumns(t, rows).find((c) => c.key === 'duty');
  assert.equal(duty.format(duty.value(rows[0]), rows[0]), 'notComputed');
  const withDuty = developmentComparisonRows([A1]);
  assert.equal(duty.format(duty.value(withDuty[0]), withDuty[0]), 'rated');
  assert.equal(variantKey({ die: 'Alias', canonical_die: 'DieA', config: 'Opt1' }), 'diea/opt1');
});

test('the page: one group header per variant, left column in one style, no blue link buttons, two-state view', () => {
  assert.match(page, /renderGroupHeader=\{\(r, index, _all, colSpan\) =>/);
  assert.match(page, /rowAccent=\{\(r\) => motorColor\(r\.motor\)\}/);
  assert.match(page, /data-testid="variant-name"/);
  assert.match(page, /showAllVersions \? orderByGroup\(versions\) : latestAndBest\(versions\)/);
  // the old per-row left column (name + note on EVERY row, uppercase blue "history" link button) is gone
  assert.doesNotMatch(page, /\{tr\('snapshots\.history'\)\} · \{r\.variant\}/);
  assert.doesNotMatch(page, /rowLabel=\{\(r\) => r\.history/);
  // history opens through a small icon button with a tooltip, normal-case typography in the group
  assert.match(page, /aria-label=\{tr\('snapshots\.details'\)\} onClick=\{\(\) => setHistoryDetail\(r\.motor\)\}/);
  assert.match(page, /data-testid="history-header"/);
  // the "Show all versions" checkbox became a one-line two-state switch with a tooltip
  assert.doesNotMatch(page, /tr\('showAllVersions'\)/);
  assert.match(page, /<ToggleButtonGroup exclusive size="small" value=\{showAllVersions \? 'all' : 'latest'\}/);
  assert.match(page, /<Tooltip title=\{tr\('versionView\.tip'\)\}>/);
  // note and status controls are in the group header (once), not repeated on every duty row
  const rowEnd = page.slice(page.indexOf('renderRowEnd={(r) =>'), page.indexOf('renderRowEnd={(r) =>') + 700);
  assert.doesNotMatch(rowEnd, /MotorNoteField|changeMotorStatus|setUnlinkTarget/);
  for (const key of ['versionView.label', 'versionView.latest', 'versionView.all', 'versionView.tip', 'snapshots.details']) {
    assert.ok(get(en, key), `EN ${key}`); assert.ok(get(zh, key), `zh-CN ${key}`);
  }
  assert.equal(en.showAllVersions, undefined); assert.equal(zh.showAllVersions, undefined);
});

test('the table skin supports group headers and an accent without changing the other tables', () => {
  const table = read('../../../components/compare/SharedComparisonTable.tsx');
  assert.match(table, /renderGroupHeader\?: \(row: T, index: number, rows: T\[\], columnCount: number\) => React\.ReactNode/);
  assert.match(table, /rowAccent\?: \(row: T\) => string \| undefined/);
  assert.match(table, /boxShadow: `inset 4px 0 0 \$\{rowAccent\(row\)\}`/);
});
