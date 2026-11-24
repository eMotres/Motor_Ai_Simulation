// node --test — the Documents tab by topic, the links between documents and assembly steps, the ONE superseded rule, and the shared
// documents link that narrows the list (documentTopics.ts, lib/productionLink.ts, lib/appLink.ts).  Node >= 22.18 (else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

let D = null; let PL = null; let AL = null;
try { D = await import('../documentTopics.ts'); PL = await import('../../../lib/productionLink.ts'); AL = await import('../../../lib/appLink.ts'); } catch { /* old Node */ }
const t = D ? test : test.skip;

const bi = (en) => ({ en, zh: en });
let n = 0;
const doc = (name, extra = {}) => ({ id: `doc_${++n}`, uploader_email: 'a@b.c', uploaded_at: '2026-11-0' + (1 + (n % 9)) + 'T10:00:00Z', part_id: null, file_id: 'f'.repeat(31) + (n % 10), name, ...extra });
const step = (id, num, title, kind = 'operation', extra = {}) => ({ id, n: num, title: bi(title), kind, parts: [], after: [], note: null, parts_resolved: [], ...extra });
const parts = [{ id: 'p_stator', name: bi('Stator') }, { id: 'p_jig', name: bi('Magnet insertion jig') }, { id: 'p_box', name: bi('Shipping box') }, { id: 'p_shaft', name: bi('Shaft') }];
const ctxOf = (steps = []) => D.topicContext(parts, ['p_jig'], steps);

// -- the superseded rule ------------------------------------------------------------------------------------------------------
t('superseded: status OR a name that starts with [superseded - one rule', () => {
  assert.equal(D.isSupersededDoc({ name: 'a.pdf', status: 'superseded' }), true);
  assert.equal(D.isSupersededDoc({ name: '[superseded 2026-10] a.pdf' }), true);
  assert.equal(D.isSupersededDoc({ name: 'a.pdf' }), false);
  assert.equal(D.isSupersededDoc({ name: 'a [superseded].pdf' }), false);
});

t('BUG: a superseded file linked through a part is no longer shown in the step (status and name rule alike)', () => {
  const cur = doc('stator_v2.pdf', { part_id: 'p_stator' });
  const oldStatus = doc('stator_v1.pdf', { part_id: 'p_stator', status: 'superseded' });
  const oldName = doc('[superseded] stator_v0.pdf', { part_id: 'p_stator' });
  const own = doc('test_sheet_old.xlsx', { status: 'superseded' });
  const s = step('s1', 1, 'Wind', 'operation', { parts_resolved: [{ id: 'p_stator', name: bi('Stator') }], documents: [own.id] });
  assert.deepEqual(D.stepDocsOf(s, [cur, oldStatus, oldName, own]).map((d) => d.name), ['stator_v2.pdf']);
});

t('stepDocsOf: own and part documents, no step pictures, a drawing linked to two parts once', () => {
  const a = doc('a.pdf', { part_id: 'p_stator' }); const b = doc('a.pdf', { part_id: 'p_shaft' });
  const pic = doc('s1.png', { part_id: 'p_stator', step_id: 's1' }); const own = doc('label.svg');
  const s = step('s1', 1, 'Label', 'operation', { parts_resolved: [{ id: 'p_stator', name: bi('S') }, { id: 'p_shaft', name: bi('S') }], documents: [own.id] });
  assert.deepEqual(D.stepDocsOf(s, [a, b, pic, own]).map((d) => d.id), [a.id, own.id]);
});

// -- links both ways ----------------------------------------------------------------------------------------------------------
t('stepsOfDocuments is the inverse of stepDocsOf, steps in step order', () => {
  const lab = doc('label_front.svg'); const dr = doc('stator.pdf', { part_id: 'p_stator' }); const unused = doc('misc.pdf');
  const s5 = step('s5', 5, 'Insert the magnets', 'operation', { documents: [lab.id] });
  const s2 = step('s2', 2, 'Wind', 'operation', { parts_resolved: [{ id: 'p_stator', name: bi('Stator') }] });
  const s9 = step('s9', 9, 'Stick the label', 'operation', { documents: [lab.id] });
  const m = D.stepsOfDocuments([s9, s5, s2], [lab, dr, unused]);
  assert.deepEqual(m.get(lab.id).map((s) => s.id), ['s5', 's9']);
  assert.deepEqual(m.get(dr.id).map((s) => s.id), ['s2']);
  assert.equal(m.has(unused.id), false);
  for (const s of [s5, s2, s9]) for (const d of D.stepDocsOf(s, [lab, dr, unused])) assert.ok(m.get(d.id).some((x) => x.id === s.id));
  assert.equal(D.stepChipLabel(s5, 'en'), '5. Insert the magnets');
});

t('a step link in the address bar: /assembly?item=<step id> round-trips and opens the Assembly tab', () => {
  const p = AL.appLinkPath({ kind: 'production', die: 'CIANO14 40 new', config: 'L12', tab: 'assembly', item: 's5' });
  assert.equal(p, '/production/CIANO14%2040%20new/L12/assembly?item=s5');
  const [path, search] = p.split('?');
  assert.deepEqual(AL.parseAppLink(path, '?' + search), { kind: 'production', die: 'CIANO14 40 new', config: 'L12', tab: 'assembly', item: 's5' });
  assert.equal(AL.parseAppLink(path, '?item=../x'), null);
});

// -- the topic ------------------------------------------------------------------------------------------------------------------
t('topic order and the superseded group at the bottom', () => {
  assert.deepEqual([...D.TOPIC_GROUPS], ['process', 'drawings', 'tooling', 'tests', 'packaging', 'photos', 'general', 'superseded']);
  const docs = [doc('z.pdf', { status: 'superseded' }), doc('misc.txt'), doc('[superseded] a.step'), doc('IMG_1.jpg')];
  assert.deepEqual(D.groupDocuments(docs, ctxOf()).map((g) => [g.group, g.docs.length]), [['photos', 1], ['general', 1], ['superseded', 2]]);
});

t('derived topic: step picture, tooling part, packaging, check step, test name, operation step, file type', () => {
  const chk = doc('hipot_sheet.xlsx'); const op = doc('insert_magnets.pdf'); const stepPic = doc('s1.jpg', { step_id: 's1' });
  const steps = [step('s1', 1, 'Insert', 'operation', { documents: [op.id] }), step('s2', 2, 'Check', 'check', { documents: [chk.id] })];
  const c = ctxOf(steps);
  const topic = (d) => D.topicOf(d, c);
  assert.equal(topic(stepPic), 'photos');
  assert.equal(topic(doc('jig.step', { part_id: 'p_jig' })), 'tooling');
  assert.equal(topic(doc('jig_drawing.pdf', { part_id: 'p_jig' })), 'tooling');
  assert.equal(topic(doc('anything.pdf', { part_id: 'p_box' })), 'packaging');
  assert.equal(topic(doc('Label_front_v2.svg')), 'packaging');
  assert.equal(topic(doc('latest_notes.txt')), 'general');                       // "latest" is not a test
  assert.equal(topic(chk), 'tests');                                              // named by a check step
  assert.equal(topic(doc('Insulation test report.pdf')), 'tests');
  assert.equal(topic(op), 'process');                                             // named by an operation step
  assert.equal(topic(doc('stator.step')), 'drawings');
  assert.equal(topic(doc('shaft.pdf', { part_id: 'p_shaft' })), 'drawings');
  assert.equal(topic(doc('shaft.png', { part_id: 'p_shaft' })), 'drawings');
  assert.equal(topic(doc('bench.png')), 'photos');
  assert.equal(topic(doc('Assembly instruction.pdf')), 'process');
  assert.equal(topic(doc('notes.txt')), 'general');
});

t('a part drawing is NOT "process" only because its part is used by a step (only a step that names the file counts)', () => {
  const d = doc('stator.pdf', { part_id: 'p_stator' });
  const s = step('s1', 1, 'Wind', 'operation', { parts_resolved: [{ id: 'p_stator', name: bi('Stator') }] });
  assert.equal(D.topicOf(d, ctxOf([s])), 'drawings');
});

t('a stored topic wins over the derivation; an unknown stored topic is ignored; superseded is never a stored topic', () => {
  const c = ctxOf();
  assert.equal(D.topicOf(doc('stator.step', { topic: 'tests' }), c), 'tests');
  assert.equal(D.topicOf(doc('stator.step', { topic: 'nonsense' }), c), 'drawings');
  assert.equal(D.topicOf(doc('x.pdf', { topic: 'superseded' }), c), 'general');
  assert.equal(D.groupOf(doc('x.pdf', { topic: 'tests', status: 'superseded' }), c), 'superseded');
  assert.equal(D.isDocumentTopic('superseded'), false);
});

t('the topic list matches the server list (models.DOCUMENT_TOPICS)', () => {
  const py = fs.readFileSync(new URL('../../../../../src/motor_ai_sim/production/models.py', import.meta.url), 'utf8');
  const m = /DOCUMENT_TOPICS = \(([^)]*)\)/.exec(py);
  assert.ok(m, 'DOCUMENT_TOPICS not found');
  assert.deepEqual(m[1].split(',').map((x) => x.trim().replace(/"/g, '')).filter(Boolean), [...D.DOCUMENT_TOPICS]);
});

t('search by name and by part, every word must match; counts are of the matches', () => {
  const a = doc('drawing_A.pdf', { part_id: 'p_stator' }); const b = doc('drawing_B.pdf', { part_id: 'p_shaft' }); const c2 = doc('notes.txt');
  const c = ctxOf();
  assert.deepEqual(D.groupDocuments([a, b, c2], c, 'stator').flatMap((g) => g.docs.map((d) => d.id)), [a.id]);
  assert.deepEqual(D.groupDocuments([a, b, c2], c, 'DRAWING shaft').flatMap((g) => g.docs.map((d) => d.id)), [b.id]);
  assert.deepEqual(D.groupDocuments([a, b, c2], c, 'zzz'), []);
  assert.equal(D.groupDocuments([a, b, c2], c, '  ').flatMap((g) => g.docs).length, 3);
});

t('inside a group the names are in natural order', () => {
  const docs = ['s10.pdf', 's2.pdf', 's1.pdf'].map((x) => doc(x, { part_id: 'p_shaft' }));
  assert.deepEqual(D.groupDocuments(docs, ctxOf())[0].docs.map((d) => d.name), ['s1.pdf', 's2.pdf', 's10.pdf']);
});

t('remembered closed groups: only known ids survive; the default closes the superseded group', () => {
  assert.deepEqual(D.validClosedGroups(['tooling', 'bogus', 7]), ['tooling']);
  assert.equal(D.validClosedGroups('tooling'), null);
  assert.deepEqual([...D.DEFAULT_CLOSED], ['superseded']);
});

// -- the shared link that narrows the list -------------------------------------------------------------------------------------
const split = (p) => { const i = p.indexOf('?'); return i < 0 ? [p, ''] : [p.slice(0, i), p.slice(i)]; };

t('shared link documents?ids=: the whole chain from the address bar to the list narrows to the named document', () => {
  const docs = Array.from({ length: 84 }, (_, i) => doc(`file_${i}.pdf`));
  const pick = docs[40];
  const url = `/production/CIANO14%2040%20new/L12/documents?ids=${pick.id}`;
  const [path, search] = split(url);
  const app = AL.parseAppLink(path, search); const link = PL.parseProductionLink(path, search);
  assert.deepEqual(app.ids, [pick.id]); assert.deepEqual(link.ids, [pick.id]);
  const v = PL.documentsForLink(docs, link);
  assert.equal(v.narrowed, true);
  assert.deepEqual(v.shown.map((d) => d.id), [pick.id]);
  // ... and the grouped view of that list holds only that file, in one group
  assert.deepEqual(D.groupDocuments(v.shown, ctxOf()).map((g) => g.docs.map((d) => d.id)), [[pick.id]]);
});

t('shared link: a superseded file named by the link is shown (its group opens), it is not dropped', () => {
  const old = doc('a_v1.pdf', { status: 'superseded' }); const cur = doc('a_v2.pdf');
  const v = PL.documentsForLink([old, cur], { ids: [old.id] });
  assert.deepEqual(D.groupDocuments(v.shown, ctxOf()).map((g) => g.group), ['superseded']);
});

t('REPRODUCED: a link with tracking keys appended was refused as a whole (length cap counted keys nobody reads) - now it narrows', () => {
  const ids = ['doc_Ab3-dE5_gH7jK9mN', 'doc_Xy2-zW4_vU6tS8rQ', 'doc_Pq1-oN3_mL5kJ7iH', 'doc_Fe9-dC7_bA5zY3xW'];
  const base = `/production/CIANO14%2040%20new/L12/documents`;
  const clean = `?ids=${ids.join(',')}`;
  const tracked = `${clean}&utm_source=mail&utm_medium=email&utm_campaign=production_digest_2026-11-23&fbclid=IwAR0abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz`;
  assert.ok(base.length + tracked.length > 280, 'the address is over the old cap');
  for (const s of [clean, tracked]) {
    const link = PL.parseProductionLink(base, s);
    assert.ok(link && link.ids.length === 4, `link parsed for ${s.slice(0, 30)}`);
    const app = AL.parseAppLink(base, s);
    assert.ok(app && app.kind === 'production' && app.tab === 'documents' && app.ids.length === 4);
  }
  // the cap still holds for what the parser reads
  const longIds = Array.from({ length: 14 }, (_, i) => `doc_${String(i).padStart(2, '0')}Ab3-dE5_gH7jK9mN`).join(',');
  assert.equal(PL.parseProductionLink(base, `?ids=${longIds}`), null);
});
