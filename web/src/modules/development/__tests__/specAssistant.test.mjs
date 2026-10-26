// Specification assistant panel logic: proposal cards (field label, value with unit, reason, recalculations), who may
// Apply, the chat history sent to the server, the Apply error mapping (synthetic data only).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  applyFailure, cardAction, cardView, formatProposalValue, historyForServer, langOf, openProposals, recalcText,
} from '../specAssistant.ts';

const schema = {
  sections: [{ id: '4', title: 'Mechanical', fields: [
    { id: '4_8', label: { en: 'Balance grade and runout', 'zh-CN': '平衡等级和跳动' }, short_label: { en: 'Balance and runout', 'zh-CN': '平衡与跳动' }, type: 'object',
      properties: [
        { id: 'balance_grade', label: { en: 'Balance grade', 'zh-CN': '平衡等级' }, type: 'string' },
        { id: 'runout_mm', label: { en: 'Runout', 'zh-CN': '跳动' }, type: 'number', unit: 'mm' },
        { id: 'note', label: 'Earlier text', type: 'string', ui_hidden: true }] },
    { id: 'load_type', label: { en: 'Load type' }, type: 'string', options: [{ value: 'air_propeller', label: { en: 'Air propeller', 'zh-CN': '空气螺旋桨' } }] },
    { id: '2_6', label: 'Phase current', type: 'number', unit: 'A', allow_range: true },
    { id: 'key_points', label: { en: 'Key operating points' }, type: 'array', items: { type: 'object', properties: [
      { id: 'name', label: { en: 'Point' }, type: 'string' }, { id: 'torque_Nm', label: { en: 'Torque' }, type: 'number', unit: 'N·m' },
      { id: 'speed_rpm', label: { en: 'Speed' }, type: 'number', unit: 'rpm' }] } },
  ] }],
};

const recalc = (kind) => ({
  needed: kind !== 'none', kind,
  text: kind === 'none' ? { en: 'none (documentation only)', 'zh-CN': '无（仅文档）' }
    : { en: 'coupled thermal + mechanics of the key points', 'zh-CN': '关键工作点的热-机械耦合重算' } });
const proposal = (extra = {}) => ({ id: 'p-1', field_id: '4_8', value: { balance_grade: 'G2.5', runout_mm: 0.05 }, reason: 'typical (assumed, to confirm)',
  recalc: recalc('thermal_mechanics'), by: 'assistant', created_at: '2026-10-27T10:00:00Z', status: 'open', ...extra });

test('a structured value shows labelled properties with units and hides ui_hidden ones', () => {
  assert.equal(formatProposalValue(schema, '4_8', { balance_grade: 'G2.5', runout_mm: 0.05, note: 'x' }, 'en'), 'Balance grade: G2.5; Runout: 0.05 mm');
  assert.equal(formatProposalValue(schema, '4_8', { balance_grade: 'G2.5', runout_mm: 0.05 }, 'zh-CN'), '平衡等级: G2.5; 跳动: 0.05 mm');
});

test('options show their label, ranges and rows read plainly, an unknown field falls back to its value', () => {
  assert.equal(formatProposalValue(schema, 'load_type', 'air_propeller', 'en'), 'Air propeller');
  assert.equal(formatProposalValue(schema, 'load_type', 'air_propeller', 'zh-CN'), '空气螺旋桨');
  assert.equal(formatProposalValue(schema, '2_6', { minimum: 40, maximum: 55 }, 'en'), '40–55 A');
  assert.equal(formatProposalValue(schema, '2_6', 42, 'en'), '42 A');
  assert.equal(formatProposalValue(schema, 'key_points', [{ name: 'hover', torque_Nm: 1.2, speed_rpm: 900 }, { name: 'peak', torque_Nm: 2, speed_rpm: 1200 }], 'en'),
    'Point: hover; Torque: 1.2 N·m; Speed: 900 rpm | Point: peak; Torque: 2 N·m; Speed: 1200 rpm');
  assert.equal(formatProposalValue(schema, 'nope', 'abc', 'en'), 'abc');
  assert.equal(formatProposalValue(schema, '4_8', null, 'en'), '-');
});

test('the card carries the label, the value, the reason, the recalculation line and what it replaces', () => {
  const c = cardView(proposal(), schema, 'en');
  assert.equal(c.title, 'Balance and runout');
  assert.equal(c.proposed, 'Balance grade: G2.5; Runout: 0.05 mm');
  assert.equal(c.current, null);
  assert.equal(c.reason, 'typical (assumed, to confirm)');
  assert.equal(c.recalc, 'coupled thermal + mechanics of the key points');
  assert.equal(c.recalcNeeded, true);
  const changed = cardView(proposal({ current: { status: 'filled', value: { balance_grade: 'G6.3' } } }), schema, 'zh-CN');
  assert.equal(changed.title, '平衡与跳动');
  assert.equal(changed.current, '平衡等级: G6.3');
  assert.equal(changed.recalc, '关键工作点的热-机械耦合重算');
  const missing = cardView(proposal({ current: { status: 'missing', value: null } }), schema, 'en');
  assert.equal(missing.current, null);
});

test('a documentation-only field says none and needs no recalculation', () => {
  const p = proposal({ field_id: 'load_type', value: 'air_propeller', recalc: recalc('none') });
  const c = cardView(p, schema, 'en');
  assert.equal(c.recalc, 'none (documentation only)');
  assert.equal(c.recalcNeeded, false);
  assert.equal(recalcText({ recalc: { needed: false, kind: 'none', text: { en: 'none' } } }, 'zh-CN'), 'none');
});

test('only spec editors get Apply; everybody else sees the owner hint; closed cards offer nothing', () => {
  assert.equal(cardAction({ status: 'open' }, true), 'apply');
  assert.equal(cardAction({ status: 'open' }, false), 'owner');
  assert.equal(cardAction({ status: 'applied' }, true), 'none');
  assert.equal(cardAction({ status: 'dismissed' }, false), 'none');
});

test('only open proposals show, oldest first', () => {
  const rows = [proposal({ id: 'b', created_at: '2026-10-27T11:00:00Z' }), proposal({ id: 'x', status: 'applied' }),
    proposal({ id: 'a', created_at: '2026-10-27T09:00:00Z' }), proposal({ id: 'y', status: 'dismissed' })];
  assert.deepEqual(openProposals(rows).map((p) => p.id), ['a', 'b']);
});

test('the history sent to the server starts with a user turn, drops empty turns and is bounded', () => {
  const h = historyForServer([{ role: 'assistant', content: 'hi' }, { role: 'user', content: 'a' }, { role: 'assistant', content: ' ' },
    { role: 'assistant', content: 'b' }, { role: 'user', content: 'c' }]);
  assert.deepEqual(h, [{ role: 'user', content: 'a' }, { role: 'assistant', content: 'b' }, { role: 'user', content: 'c' }]);
  const many = Array.from({ length: 50 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `m${i}` }));
  assert.ok(historyForServer(many).length <= 20);
  assert.equal(historyForServer(many)[0].role, 'user');
  assert.deepEqual(historyForServer([]), []);
});

test('an Apply failure maps to a message key; a stale revision asks for a reload', () => {
  assert.deepEqual(applyFailure(409, { message: 'Draft changed; reload before applying', revision: 7 }), { key: 'specAssistant.stale', reload: true });
  assert.deepEqual(applyFailure(409, { message: 'proposal is already applied' }), { key: 'specAssistant.alreadyClosed', reload: false });
  assert.equal(applyFailure(403, 'x').key, 'specAssistant.ownerOnly');
  assert.equal(applyFailure(404, 'x').key, 'specAssistant.gone');
  assert.equal(applyFailure(422, 'x').key, 'specAssistant.invalid');
  assert.equal(applyFailure(500, null).key, 'specAssistant.applyFailed');
});

test('the UI language is read from the i18n code', () => {
  assert.equal(langOf('zh-CN'), 'zh-CN');
  assert.equal(langOf('zh'), 'zh-CN');
  assert.equal(langOf('en-GB'), 'en');
  assert.equal(langOf(undefined), 'en');
});
