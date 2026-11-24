import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { groupByRevision, isGroupOpen } from '../documentRevisions.ts';

const locale = (lang) => JSON.parse(readFileSync(new URL(`../../../locales/${lang}/development.json`, import.meta.url), 'utf8'));
const d = (id, revision) => ({ id, revision });

test('groups keep first-appearance order and server order inside a group', () => {
  const g = groupByRevision([d(1, 'R06'), d(2, 'R05'), d(3, 'R06'), d(4, ''), d(5, 'R05'), d(6, '  ')]);
  assert.deepEqual(g.map((x) => x.key), ['R06', 'R05', '']);
  assert.deepEqual(g[0].docs.map((x) => x.id), [1, 3]);
  assert.deepEqual(g[1].docs.map((x) => x.id), [2, 5]);
  assert.deepEqual(g[2].docs.map((x) => x.id), [4, 6]);
});

test('revision is trimmed; missing revision joins the empty group; empty list gives no group', () => {
  assert.deepEqual(groupByRevision([d(1, ' R1 '), d(2, 'R1'), { id: 3 }]).map((x) => [x.key, x.docs.length]), [['R1', 2], ['', 1]]);
  assert.deepEqual(groupByRevision([]), []);
});

test('first group open by default, others closed, explicit toggles win', () => {
  const g = groupByRevision([d(1, 'R06'), d(2, 'R05')]);
  assert.equal(isGroupOpen(g, 'R06', {}), true);
  assert.equal(isGroupOpen(g, 'R05', {}), false);
  assert.equal(isGroupOpen(g, 'R06', { R06: false }), false);
  assert.equal(isGroupOpen(g, 'R05', { R05: true }), true);
});

test('the no-revision label exists in EN and ZH', () => {
  assert.equal(locale('en').documents.noRevision, 'No revision');
  assert.equal(locale('zh-CN').documents.noRevision, '未标注版本');
});

test('the Documents tab wires the grouping with an accessible toggle', () => {
  const src = readFileSync(new URL('../components/ProjectDocuments.tsx', import.meta.url), 'utf8');
  assert.match(src, /groupByRevision\(shown\)/);
  assert.match(src, /groups\.length < 2 \? shown\.map\(renderRow\)/);
  assert.match(src, /data-testid="documents-revision-group"/);
  assert.match(src, /aria-expanded=\{open\}/);
});

test('unlabelled files on top do not close the newest named revision', () => {
  const g = groupByRevision([d(1, ''), d(2, 'R06'), d(3, 'R05')]);
  assert.equal(isGroupOpen(g, '', {}), true);
  assert.equal(isGroupOpen(g, 'R06', {}), true);
  assert.equal(isGroupOpen(g, 'R05', {}), false);
  assert.equal(isGroupOpen(g, 'R06', { R06: false }), false);
});

// ── releases (owner 2026-11-24) ────────────────────────────────────────────
import { orderByRelease, isReleaseGroupOpen } from '../documentRevisions.ts';

const rel = (revision, current, superseded) => ({ revision, current, superseded, count: 1, latest_upload: '' });

test('groups follow the release order of the server: current, general, superseded newest first', () => {
  const groups = groupByRevision([d(1, 'A'), d(2, ''), d(3, 'C'), d(4, 'B'), d(5, 'A')]);
  const out = orderByRelease(groups, [rel('B', true, false), rel('', false, false), rel('C', false, true), rel('A', false, true)]);
  assert.deepEqual(out.map((g) => [g.key, g.current, g.superseded]), [['B', true, false], ['', false, false], ['C', false, true], ['A', false, true]]);
  assert.deepEqual(out[3].docs.map((x) => x.id), [1, 5]);
});

test('a group the server does not list (archived only) goes last and is neither current nor superseded', () => {
  const out = orderByRelease(groupByRevision([d(1, 'OLD'), d(2, 'B')]), [rel('B', true, false)]);
  assert.deepEqual(out.map((g) => [g.key, g.current, g.superseded]), [['B', true, false], ['OLD', false, false]]);
});

test('without release information the first-appearance order stays', () => {
  assert.deepEqual(orderByRelease(groupByRevision([d(1, 'X'), d(2, 'Y')]), undefined).map((g) => g.key), ['X', 'Y']);
});

test('the current release and the general documents are open, a superseded group is closed until the user opens it', () => {
  assert.equal(isReleaseGroupOpen({ key: 'B', current: true, superseded: false }, {}), true);
  assert.equal(isReleaseGroupOpen({ key: '', current: false, superseded: false }, {}), true);
  assert.equal(isReleaseGroupOpen({ key: 'A', current: false, superseded: true }, {}), false);
  assert.equal(isReleaseGroupOpen({ key: 'A', current: false, superseded: true }, { A: true }), true);
  assert.equal(isReleaseGroupOpen({ key: 'B', current: true, superseded: false }, { B: false }), false);
});

test('the release strings exist in EN and ZH', () => {
  for (const lang of ['en', 'zh-CN']) {
    const s = locale(lang).documents;
    for (const k of ['releaseZip', 'releaseZipTip', 'allGroupsZip', 'moreDownloads', 'currentRelease', 'currentReleaseTip', 'superseded', 'supersededTip', 'makeCurrent', 'makeCurrentTip', 'releaseDefault', 'supersededConfirmTitle', 'supersededConfirm', 'supersededViewOnly', 'downloadAnyway', 'cancel', 'generalDocuments', 'pinCurrent', 'pinCurrentTip', 'releaseCleared']) assert.ok(s[k], `${lang} documents.${k}`);
  }
});
