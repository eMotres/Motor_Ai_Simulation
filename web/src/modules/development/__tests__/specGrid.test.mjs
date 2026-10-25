// node --test: the specification's ONE grid (owner 2026-10-26, «не выровнено по полям ввода»).
// Source-level assertions on the renderer + pure tests of the helper.  What they pin:
//   - the group header and every field label are ONE line of a FIXED height (ellipsis, full text in the tooltip), so all
//     inputs of a row share one top edge;
//   - the label row never wraps and the grid aligns its items to the start;
//   - every select / autocomplete computes a min width from its options (EN and zh-CN), so the chosen value is not cut off;
//   - every card sets the one column width (--spec-col) that every group of the card uses; each group starts its own row;
//   - the maximum ambient temperature has its label ABOVE the input like every other field.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  SPEC_LABEL_HEIGHT, cardColumnMin, fieldOptionLabels, labelChars, longestOptionChars, optionLabels, optionsMinWidth,
  specFieldLabelSx, specGridSx, specGroupHeaderSx, visibleProperties,
} from '../specGrid.ts';

const page = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');
const schema = JSON.parse(readFileSync(new URL('../../../../../src/motor_ai_sim/development/schema.json', import.meta.url), 'utf8'));
const field = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const lineOf = (needle) => page.split('\n').find((l) => l.includes(needle)) ?? '';

test('labels are one line of a fixed height: ellipsis, no wrap', () => {
  for (const sx of [specGroupHeaderSx, specFieldLabelSx]) {
    assert.equal(sx.whiteSpace, 'nowrap');
    assert.equal(sx.overflow, 'hidden');
    assert.equal(sx.textOverflow, 'ellipsis');
    assert.equal(sx.height, SPEC_LABEL_HEIGHT);
    assert.equal(sx.lineHeight, `${SPEC_LABEL_HEIGHT}px`);
  }
  // two levels only: the group header above the field label
  assert.ok(specGroupHeaderSx.fontSize > specFieldLabelSx.fontSize);
  assert.equal(specGridSx.alignItems, 'start');
});

test('the renderer uses the single-line label style everywhere a label is drawn', () => {
  const labelRow = lineOf('const labelRow = (');
  assert.match(labelRow, /sx=\{specGroupHeaderSx\}/);
  assert.match(labelRow, /flexWrap: 'nowrap'/);
  assert.doesNotMatch(labelRow, /flexWrap: 'wrap'/);
  assert.match(lineOf('const propLabel = ('), /\.\.\.specFieldLabelSx/);
  // the nested object / array property labels too: no ad-hoc 11 px grey typography left in the spec renderers
  assert.doesNotMatch(page, /<Typography tabIndex=\{0\} sx=\{\{ fontSize: 11, color: 'var\(--text-3\)' \}\}>/);
  assert.equal((page.match(/sx=\{specFieldLabelSx\}/g) ?? []).length >= 2, true);
});

test('every group and every input row uses the one grid (equal columns, no ad-hoc minmax(120/140px))', () => {
  assert.doesNotMatch(page, /repeat\(auto-fill,\s*minmax\(1[24]0px/);
  assert.match(lineOf('const propertyGrid ='), /\.\.\.specGridSx/);
  assert.match(page, /\.\.\.specGridSx/);
  // each group starts its own row: the requirements fields are stacked in a one-column grid
  assert.match(lineOf('const grid = {'), /gridTemplateColumns: 'minmax\(0,1fr\)'/);
});

test('every card sets the one column width of the card (--spec-col)', () => {
  assert.match(page, /'--spec-col': cardColumnMin\(section\.fields\)/);
  assert.match(specGridSx.gridTemplateColumns, /var\(--spec-col/);
});

test('selects in the spec grid compute a min width from their options', () => {
  // the enum select, the yes/no select, the field-level options select and the suggestion autocomplete
  const withMin = page.split('\n').filter((l) => /<Select size="small"/.test(l) && /optionsMinWidth\(/.test(l));
  assert.ok(withMin.length >= 3, 'three Select renderers carry a min width');
  const selects = page.split('\n').filter((l) => /<Select size="small"/.test(l) && /(options|displayEmpty)/.test(l));
  for (const l of selects) {
    // the Select of the spec renderers (not the version / role pickers, which have no `fullWidth displayEmpty`)
    if (/fullWidth displayEmpty/.test(l)) assert.match(l, /optionsMinWidth\(/, l.slice(0, 120));
  }
  assert.match(page, /<SuggestField [^\n]*minWidth=\{optionsMinWidth\(/);
  assert.match(page, /function SuggestField\(\{[^}]*minWidth/);
  assert.match(page, /sx=\{\{ minWidth, maxWidth: '100%'/);
});

test('the maximum ambient temperature has its label above the input, like every other field', () => {
  const start = page.indexOf('const renderAmbient');
  const body = page.slice(start, page.indexOf('const renderLoads', start));
  assert.doesNotMatch(body, /direction="row"/);
  assert.match(body, /labelRow\(f, false, required, unknown/);
  assert.match(body, /<Box sx=\{propertyGrid\}>/);
});

test('min width grows with the longest option, in either language', () => {
  assert.equal(labelChars('abc'), 3);
  assert.equal(labelChars('螺旋桨滑流'), 10);                       // a CJK character is about two characters wide
  const options = [{ value: 'Still air', label: { en: 'Still air', 'zh-CN': '静止空气' } }, { value: 'Propeller wash', label: { en: 'Propeller wash', 'zh-CN': '螺旋桨滑流' } }];
  assert.equal(longestOptionChars(options), 14);                    // "Propeller wash" (EN) vs 螺旋桨滑流 (10)
  assert.equal(optionsMinWidth(options), 'calc(14ch + 60px)');
  assert.ok(longestOptionChars([{ value: 'x', label: { en: 'Fan', 'zh-CN': '这是一个非常非常长的中文选项文字' } }]) > 16);
  assert.equal(optionsMinWidth([]), undefined);
  assert.equal(optionsMinWidth([1, 2, 3]), undefined);               // numeric menus are short
  assert.equal(optionsMinWidth(['x'.repeat(200)]), 'calc(46ch + 60px)'); // capped
  assert.deepEqual(optionLabels(['a', 3, { value: 'v', label: 'L' }]), ['a', 'L', 'v']);
});

test('the Airflow mode select is wide enough for "Propeller wash" and the card column for its longest option', () => {
  const airflow = field('3_1').properties.find((p) => p.id === 'airflow').properties.find((p) => p.id === 'mode');
  assert.ok(longestOptionChars(airflow.suggestions) >= 'Propeller wash'.length);
  const housing = field('3_3');                                      // "Open (air flows through the motor)"
  assert.ok(fieldOptionLabels(housing).some((l) => l.startsWith('Open (air flows')));
  const col = cardColumnMin([field('3_1'), housing]);
  const chars = Number(/calc\((\d+)ch/.exec(col)?.[1]);
  assert.ok(chars >= 'Open (air flows through the motor)'.length, col);
  assert.equal(cardColumnMin([]), '180px');
});

test('properties marked ui_hidden are not asked, the others are', () => {
  assert.deepEqual(visibleProperties([{ id: 'a' }, { id: 'b', ui_hidden: true }, { id: 'c' }]).map((p) => p.id), ['a', 'c']);
  assert.deepEqual(visibleProperties(undefined), []);
});
