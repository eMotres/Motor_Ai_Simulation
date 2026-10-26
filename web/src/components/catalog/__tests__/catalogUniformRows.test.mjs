// node --test - the Motors list is uniform (owner 2026-10-27): no coloured card / older-build chip
// and no red "retire" link in a row; the ONLY per-row distinction is the open / closed marker.
// Imports lib/catalogOpenness.ts itself (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { cardStatusTip, dieOpenness, OPENNESS_COLOR, OPENNESS_TIP_KEY } from '../../../lib/catalogOpenness.ts';

const family = readFileSync(new URL('../FamilyCatalog.tsx', import.meta.url), 'utf8');
const loc = (lng) => JSON.parse(readFileSync(new URL(`../../../locales/${lng}/motors.json`, import.meta.url), 'utf8'));

test('only a die the server stamps source "open" is open; everything else reads closed', () => {
  assert.equal(dieOpenness({ source: 'open' }), 'open');
  assert.equal(dieOpenness({ source: 'private' }), 'closed');
  assert.equal(dieOpenness({}), 'closed');
  assert.equal(dieOpenness({ source: null }), 'closed');
  assert.equal(dieOpenness({ source: 'shared' }), 'closed');
  assert.equal(dieOpenness(undefined), 'closed');
});

test('open and closed have their own quiet colour and tooltip', () => {
  assert.notEqual(OPENNESS_COLOR.open, OPENNESS_COLOR.closed);
  assert.notEqual(OPENNESS_TIP_KEY.open, OPENNESS_TIP_KEY.closed);
});

test('card / older-build tooltip line: date, older build, nothing without a card', () => {
  const t = (key, o) => `${key}|${JSON.stringify(o ?? {})}`;
  assert.equal(cardStatusTip(t, { date: null }), '');
  assert.equal(cardStatusTip(t, {}), '');
  assert.equal(cardStatusTip(t, { date: '2026-10-20' }), 'card.tip|{"date":"2026-10-20"}');
  assert.equal(cardStatusTip(t, { date: '-' }), 'card.tipNoDate|{}');
  assert.equal(cardStatusTip(t, { date: '2026-10-20', staleDuties: ['rated', 'peak'] }),
    'card.olderBuildTip|{"duties":"rated, peak"}');
});

test('a die row carries the marker for open AND closed, drawn for every row', () => {
  assert.match(family, /<OpennessMarker die=\{die\} \/>/);
  assert.match(family, /kind === 'open' \? PublicOutlinedIcon : LockOutlinedIcon/);
  assert.match(family, /data-openness=\{kind\}/);
  // not behind canWrite / isAdmin: clients see it too
  const at = family.indexOf('<OpennessMarker die={die} />');
  assert.doesNotMatch(family.slice(Math.max(0, at - 60), at), /canWrite/);
});

test('no coloured card chip, no older-build chip and no red retire link in the rows', () => {
  assert.doesNotMatch(family, /CardBadge|CardSummary/);
  assert.doesNotMatch(family, /retireDieButton/);
  // no per-row lock emoji prefix in front of the die name (the lock toggle keeps its own control)
  assert.doesNotMatch(family, /die\.locked \? '🔒 ' : ''/);
});

test('"Retire for all users" is reachable for admins from the die row menu with the same confirm flow', () => {
  assert.match(family, /canWrite && isAdmin && die\.layer === 'shared'[\s\S]{0,400}data-testid="die-menu-button"/);
  assert.match(family, /data-testid="retire-die-item"[\s\S]{0,300}retireDie\(d\)/);
  assert.match(family, /const retireDie = \(name: string\) =>/);
  assert.match(family, /\/retire`, \{ confirm: name \}/);
});

test('EN and ZH carry the new strings; the dead link label is gone from both', () => {
  for (const lng of ['en', 'zh-CN']) {
    const m = loc(lng);
    for (const k of ['openMotorTip', 'closedMotorTip', 'dieMenuTip', 'retireDieConfirm', 'retireDieTip']) {
      assert.ok(m[k] && typeof m[k] === 'string', `${lng}:${k}`);
    }
    assert.equal(m.retireDieButton, undefined, lng);
  }
  assert.equal(loc('en').openMotorTip, 'Open motor (public data)');
  assert.equal(loc('en').closedMotorTip, 'Closed motor (private, NDA)');
});
