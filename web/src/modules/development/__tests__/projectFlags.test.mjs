// The staff marks of a project variant: report access, the recommended variant and its fallback (owner 2026-10-26).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { flagWho, isRecommended, recommendedMotor, reportAccess } from '../projectFlags.ts';

const m = (config, extra = {}) => ({ die: 'D', config, status: 'current', ...extra });
const flag = { by: 'staff@example.test', at: '2026-10-26T10:00:00' };

test('the report: staff always, a member only for a reviewed variant, the button says why when closed', () => {
  assert.deepEqual(reportAccess(m('a'), true), { enabled: true, reason: 'staff' });
  assert.deepEqual(reportAccess(m('a'), false), { enabled: false, reason: 'not_reviewed' });
  assert.deepEqual(reportAccess(m('a', { report_reviewed: flag }), false), { enabled: true, reason: 'ok' });
  assert.deepEqual(reportAccess(null, false), { enabled: false, reason: 'not_reviewed' });
});

test('the recommended variant is the marked one; none marked falls back to the latest current variant and says so', () => {
  const list = [m('opt1'), m('opt2', { recommended: flag }), m('opt3'), m('trial', { status: 'trial' })];
  assert.deepEqual([recommendedMotor(list).motor.config, recommendedMotor(list).explicit], ['opt2', true]);
  const none = [m('opt1'), m('opt3'), m('trial', { status: 'trial' })];
  assert.deepEqual([recommendedMotor(none).motor.config, recommendedMotor(none).explicit], ['opt3', false]);
  assert.equal(recommendedMotor([m('t', { status: 'trial' })]), null);
  assert.equal(recommendedMotor([m('h', { kind: 'history', recommended: flag })]), null);               // a snapshot is not a variant
  assert.equal(isRecommended(m('a', { recommended: flag })), true);
  assert.equal(isRecommended(m('a')), false);                                                          // the fallback is never drawn as a mark
  assert.equal(flagWho(flag), 'staff@example.test · 2026-10-26 10:00');
  assert.equal(flagWho(null), '');
});

test('wiring: the group header carries the controls and a stronger accent for the recommended variant', () => {
  const page = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /<VariantControls projectId=\{selected\.id\} motor=\{live\} staff=\{canManageGlobally\}/);
  assert.match(page, /isRecommended\(live\)\);\n?\s*\};/);
  assert.match(page, /strong \? `inset 9px 0 0/);
  const controls = readFileSync(new URL('../components/VariantControls.tsx', import.meta.url), 'utf8');
  assert.match(controls, /\/motors\/report\?/);                                                         // the project route (its own gate)
  assert.match(controls, /disabled=\{!access\.enabled/);                                                // closed for a member until reviewed
  assert.match(controls, /t\('report\.notReviewedTip'\)/);
  const api = readFileSync(new URL('../api.ts', import.meta.url), 'utf8');
  assert.match(api, /motors\/report_reviewed/);
  assert.match(api, /motors\/recommended/);
});
