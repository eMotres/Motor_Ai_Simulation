// node --test - saved gearboxes: the 409 path, stale / flag chips, the archive toggle, unreadable files, open -> editor state.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const fx = (n) => JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/${n}.json`, import.meta.url)), 'utf8'));
let S = null, A = null, P = null;
try { S = await import('../saved.ts'); A = await import('../api.ts'); P = await import('../plain.ts'); } catch { /* old Node */ }
const t = S ? test : test.skip;

const withFetch = async (impl, fn) => {
  const real = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return impl(String(url), init); };
  try { await fn(calls); } finally { globalThis.fetch = real; }
};
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

t('409 stale_version is recognised with the current version', () => {
  const e = A.parseErrorBody(409, { detail: { error: 'stale_version', message: 'The record changed since it was loaded', current_version: 5 } });
  assert.equal(S.isStaleVersion(e), true);
  assert.equal(e.currentVersion, 5);
  assert.equal(e.code, 'stale_version');
  assert.equal(S.isStaleVersion(A.parseErrorBody(404, { detail: { error: 'not_found', message: 'No such gearbox record' } })), false);
  assert.equal(S.isStaleVersion(new Error('x')), false);
  const v = A.parseErrorBody(422, fx('error_422'));
  assert.equal(v.errors.length, 1);
  assert.equal(S.isStaleVersion(v), false);
});

t('the client sends version and scope, and a stale PUT rejects as stale_version', async () => {
  await withFetch(() => json(409, { detail: { error: 'stale_version', message: 'm', current_version: 7 } }), async (calls) => {
    await assert.rejects(() => A.boxesApi.update('proj_1', 'gb_0123456789abcdef', { version: 3, name: 'n', request: fx('point_forward.request') }),
      (e) => S.isStaleVersion(e) && e.currentVersion === 7);
    assert.match(calls[0].url, /\/api\/gears\/boxes\/gb_0123456789abcdef\?project=proj_1$/);
    assert.equal(calls[0].init.method, 'PUT');
    assert.equal(JSON.parse(calls[0].init.body).version, 3);
  });
  await withFetch(() => json(409, { detail: { error: 'stale_version', message: 'm', current_version: 4 } }), async (calls) => {
    await assert.rejects(() => A.boxesApi.archive(null, 'gb_0123456789abcdef', 2), (e) => S.isStaleVersion(e));
    assert.match(calls[0].url, /\/boxes\/gb_0123456789abcdef\?version=2$/);
    assert.equal(calls[0].init.method, 'DELETE');
  });
  await withFetch(() => json(200, { id: 'gb_0123456789abcdef', version: 3 }), async (calls) => {
    await A.boxesApi.restore('p', 'gb_0123456789abcdef', 2);
    assert.match(calls[0].url, /\/boxes\/gb_0123456789abcdef\/restore\?project=p&version=2$/);
    assert.equal(calls[0].init.method, 'POST');
    await A.boxesApi.list(null, 'all');
    assert.match(calls[1].url, /\/api\/gears\/boxes\?state=all$/);
    await A.boxesApi.create(null, { name: 'n', request: fx('point_forward.request') });
    assert.match(calls[2].url, /\/api\/gears\/boxes$/);
  });
});

const box = (id, state, extra = {}) => ({ id, name: id, scope: '_workspace', state, version: 1, flags: [], response_stale: false, has_response: true, ...extra });

t('archive toggle: archived records only behind it; unreadable files are counted, not listed', () => {
  const list = { scope: '_workspace', items: [box('a', 'active'), box('b', 'archived'), box('c', 'active')], unreadable: [{ file: 'gb_x.json', error: 'bad' }] };
  const off = S.visibleBoxes(list, false);
  assert.deepEqual(off.items.map((b) => b.id), ['a', 'c']);
  assert.equal(off.archived, 1);
  assert.equal(off.unreadable, 1);
  assert.deepEqual(S.visibleBoxes(list, true).items.map((b) => b.id), ['a', 'b', 'c']);
  assert.deepEqual(S.visibleBoxes(null, true), { items: [], archived: 0, unreadable: 0 });
});

t('chips: response_stale first, the five core flags in a fixed order, unknown flags kept', () => {
  assert.deepEqual(S.boxChips(box('a', 'active')), []);
  assert.deepEqual(S.boxChips(box('a', 'active', { response_stale: true, flags: ['catalogue_changed', 'core_stale', 'future_flag'] })),
    ['response_stale', 'core_stale', 'catalogue_changed', 'future_flag']);
  assert.deepEqual(S.BOX_FLAGS.length, 5);
  for (const lng of ['en', 'zh-CN']) {
    const loc = JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lng}/gears.json`, import.meta.url)), 'utf8'));
    for (const f of ['response_stale', ...S.BOX_FLAGS]) { assert.ok(loc.flag[f], `${lng} flag.${f}`); assert.ok(loc.flagTip[f], `${lng} flagTip.${f}`); }
  }
});

t('a stale or foreign response is not shown on Open', () => {
  const resp = fx('point_forward.response');
  assert.equal(S.responseToShow({ response: resp, response_stale: false }), resp);
  assert.equal(S.responseToShow({ response: resp, response_stale: true }), null);
  assert.equal(S.responseToShow({ response: null, response_stale: false }), null);
  assert.equal(S.responseToShow({ response: fx('assembly.response'), response_stale: false }), null);
});

t('open: a saved request becomes the editor state; saving it again gives the same plain body', () => {
  const ed = S.editorFromRequest(fx('point_forward.request'));
  const joint = P.humanoidJoint();
  assert.equal(ed.stages.length, 2);
  ed.stages.forEach((s, i) => {
    assert.equal(s.kind, 'planetary');
    assert.equal(s.name, joint.stages[i].name);
    assert.equal(s.n_planets, joint.stages[i].n_planets);
    for (const role of ['sun', 'planet', 'ring']) for (const f of ['z', 'm_n_mm', 'b_mm']) assert.equal(s.gears[role][f], joint.stages[i].gears[role][f]);
    assert.deepEqual(s.bearings, {});
  });
  assert.equal(ed.preset_name, 'humanoid_robot_joint');
  assert.ok(P.validateEditor(JSON.parse(JSON.stringify(ed))));
  const body = P.toPlainBody(ed, 'point');
  assert.deepEqual(body.stages.map((s) => s.stage_id), fx('point_forward.request').stages.map((s) => s.stage_id));
  const withBearings = S.editorFromRequest(fx('point_bearings.request'));
  assert.deepEqual(withBearings.stages[0].bearings, { input_bearing: '618/8-2Z', output_bearing: '618/8-2Z' });
  const sp = S.editorFromRequest(fx('spectrum.request'));
  assert.equal(sp.spectrum.bins.length, 2);
  assert.equal(sp.spectrum.peak_events, 1000);
});

t('open refuses what the editor cannot show, loudly and with the path', () => {
  const req = JSON.parse(JSON.stringify(fx('point_forward.request')));
  req.stages[1].kind = 'helical_pair';
  assert.throws(() => S.editorFromRequest(req), (e) => e instanceof S.EditorFromRequestError && e.path === '/stages/1/kind');
  req.stages = [];
  assert.throws(() => S.editorFromRequest(req), (e) => e.path === '/stages');
});

t('remembered save state: valid kept, junk dropped', () => {
  assert.deepEqual(S.validateSavedRef({ ref: { id: 'gb_1', version: 2, project: '', name: 'n' }, project: 'p', name: 'x', showArchived: true }).showArchived, true);
  assert.equal(S.validateSavedRef(null), null);
  assert.equal(S.validateSavedRef({ ref: { id: 5 }, project: 3 }).ref, null);
});
