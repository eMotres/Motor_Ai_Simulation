// node --test - gear page logic (pure modules): plain body, remembered state, field errors, result view-models, 3-D meshes.
// Fixtures are real core answers (fixtures/make_fixtures.py): the humanoid joint, point / spectrum / assembly / pose / 422.
// Imports .ts files directly (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const fx = (n) => JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/${n}.json`, import.meta.url)), 'utf8'));
let P = null, V = null, M = null, A = null, Pose = null;
try { P = await import('../plain.ts'); V = await import('../view.ts'); M = await import('../mesh.ts'); A = await import('../api.ts'); Pose = await import('../pose.ts'); } catch (e) { /* old Node */ }
const t = P ? test : test.skip;

t('the TS humanoid default mirrors the server template default (no drift)', () => {
  const req = fx('point_forward.request');
  const ed = P.humanoidJoint();
  assert.equal(req.preset_name, ed.preset_name);
  assert.equal(req.lubricant, ed.lubricant);
  assert.equal(req.stages.length, ed.stages.length);
  req.stages.forEach((s, i) => {
    for (const role of ['sun', 'planet', 'ring']) {
      for (const f of ['z', 'm_n_mm', 'b_mm']) assert.equal(s[role][f], ed.stages[i].gears[role][f], `${i}.${role}.${f}`);
    }
    assert.equal(s.n_planets, ed.stages[i].n_planets);
    for (const slot of ['input_bearing', 'planet_bearing', 'output_bearing']) assert.equal(s[slot].name, ed.stages[i].bearings[slot], `${i}.${slot}`);
  });
  assert.equal(req.torque_Nm, ed.torque_Nm);
  assert.equal(req.torque_at, ed.torque_at);
});

t('plain body: stable stage ids, spectrum only for the spectrum operation, no empty fields', () => {
  const ed = P.humanoidJoint();
  const point = P.toPlainBody(ed, 'point');
  assert.equal(point.spectrum, undefined);
  assert.deepEqual(point.stages.map((s) => s.stage_id), ed.stages.map((s) => s.uid));
  assert.equal(point.operation, 'point');
  const sp = P.toPlainBody(ed, 'spectrum');
  assert.equal(sp.spectrum.bins.length, 2);
  ed.stages[0].bearings = { input_bearing: '618/8-2Z', output_bearing: '' };
  const body = P.toPlainBody(ed, 'point').stages[0];
  assert.deepEqual(body.bearings, { input_bearing: '618/8-2Z' });
  const pair = P.toPlainBody({ ...ed, stages: [P.defaultStage('pair', 'p')] }, 'point').stages[0];
  assert.equal(pair.n_planets, undefined);
  assert.deepEqual(Object.keys(pair.gears), ['gear1', 'gear2']);
});

t('every stage kind has the roles the template route accepts', () => {
  assert.deepEqual(P.ROLES.planetary, ['sun', 'planet', 'ring']);
  assert.deepEqual(P.ROLES.compound_planetary, ['sun', 'planet', 'planet2', 'ring']);
  assert.deepEqual(P.ROLES.pair, ['gear1', 'gear2']);
  for (const k of Object.keys(P.ROLES)) { const s = P.defaultStage(k, 'x'); for (const r of P.ROLES[k]) assert.ok(s.gears[r].z > 0); }
});

t('remembered editor state: valid is kept, anything malformed is dropped', () => {
  const ed = JSON.parse(JSON.stringify(P.humanoidJoint()));
  assert.ok(P.validateEditor(ed));
  assert.equal(P.validateEditor(null), null);
  assert.equal(P.validateEditor({}), null);
  const bad = JSON.parse(JSON.stringify(ed)); bad.stages[0].gears.sun.z = 'x';
  assert.equal(P.validateEditor(bad), null);
  const dup = JSON.parse(JSON.stringify(ed)); dup.stages[1].uid = dup.stages[0].uid;
  assert.equal(P.validateEditor(dup), null);
  const none = JSON.parse(JSON.stringify(ed)); none.spectrum.bins = [];
  assert.equal(P.validateEditor(none), null);
});

t('422 body: field paths survive and land on the editor fields', () => {
  const err = A.parseErrorBody(422, fx('error_422'));
  assert.equal(err.status, 422);
  assert.equal(err.errors[0].path, '/stages/0/sun/z');
  const map = P.errorMap(err.errors);
  assert.ok(map[P.gearPath(0, 'sun', 'z')]);
  assert.equal(P.normalisePath('/stages/3/planet2/z'), '/stages/3/gears/planet2/z');
  assert.equal(P.normalisePath('/stages/1/gears/ring/z'), '/stages/1/gears/ring/z');
  assert.equal(P.normalisePath('/stages/1/name'), '/stages/1/name');
  assert.equal(A.parseErrorBody(403, { detail: 'The gear module is open to MOTRES staff only.' }).message, 'The gear module is open to MOTRES staff only.');
});

t('point result: values, efficiency both ways, no min life at a point, never a green pass', () => {
  const r = fx('point_forward.response').result;
  assert.equal(r.passed, false);
  assert.ok(r.pass_reasons.includes('parts_not_evaluated'));
  const eff = V.showField(r, 'overall_efficiency', 4);
  assert.ok(eff.available && Number(eff.text) > 0.9 && Number(eff.text) < 1);
  assert.ok(V.showField(r, 'reverse_efficiency', 4).available);
  const life = V.showField(r, 'min_gear_life_h');
  assert.equal(life.available, false);
  assert.equal(life.text, '-');
  assert.equal(life.reason, 'requires_spectrum_evaluation');
  assert.equal(V.resultTone(r), 'failed');   // the default 618/8 bearings fail the core's checks at the joint's small loads
});

t('ratings: internal-gear root is unsupported and has no S_F; pitting S_H is shown and estimated', () => {
  const rows = V.ratingRows(fx('point_forward.response').result);
  assert.equal(rows.length, 8);
  const ring = rows.filter((x) => x.member === 'ring');
  assert.equal(ring.length, 2);
  for (const x of ring) { assert.equal(x.S_F, null); assert.equal(x.bending, 'unsupported'); assert.ok(x.S_H > 0); assert.equal(x.internal, true); }
  assert.deepEqual([...new Set(rows.map((x) => x.member))].sort(), ['planet', 'ring', 'sun']);
  assert.ok(rows.some((x) => x.pitting === 'estimated'));
});

t('bearings: failed bearing records are shown with their state, lives stay labelled', () => {
  const r = fx('point_bearings.response').result;
  const rows = V.bearingRows(r);
  assert.equal(rows.length, 2);
  assert.ok(rows.every((b) => b.state === 'failed' && b.name === '618/8-2Z'));
  assert.equal(r.min_bearing_life_h, null);
  assert.equal(r.passed, false);
  // the preset default carries catalogue bearings on planets, input and output of both stages
  assert.equal(V.bearingRows(fx('point_forward.response').result).length, 6);
  assert.equal(P.defaultStage('planetary', 'x').bearings && Object.keys(P.defaultStage('planetary', 'x').bearings).length, 0);
});

t('spectrum result: unsupported / failed checks never pass, miner rows keep null as unavailable', () => {
  const r = fx('spectrum.response').result;
  assert.equal(r.passed, false);
  assert.equal(r.duty_source, 'spectrum');
  const checks = r.preset_evaluation.checks;
  for (const c of checks) if (c.state === 'unsupported' || c.state === 'failed') assert.equal(c.passed, false, c.criterion);
  assert.ok(V.minerRows(r).length > 0);
  const sh = checks.find((c) => c.criterion === 'S_F_duty');
  assert.equal(sh.state, 'unsupported');
  assert.ok(V.reasonParts(r.pass_reasons.find((x) => x.startsWith('preset:S_F_duty'))).state === 'unsupported');
  const life = V.showField(r, 'min_gear_life_h');
  assert.equal(life.available, false);
});

t('show(): endurance is named, null is a dash with its reason, large numbers use exponents', () => {
  const e = V.show(null, { status: 'endurance', reason: 'zero_damage_in_supported_model' });
  assert.equal(e.endurance, true); assert.equal(e.text, 'endurance');
  assert.equal(V.show(null, { status: 'unavailable', reason: 'failed_upstream' }).text, '-');
  assert.equal(V.show(0, null).text, '0');
  assert.equal(V.show(1.33e52, null).text, '1.33e+52');
  assert.equal(V.show(Number.NaN, null).available, false);
});

t('limitations: the core list is complete and the two primary ones are named', () => {
  assert.ok(V.LIMITATIONS.includes('internalRoot'));
  assert.ok(V.LIMITATIONS.includes('reversal'));
  assert.deepEqual([...V.PRIMARY_LIMITATIONS], ['internalRoot', 'reversal']);
  for (const lng of ['en', 'zh-CN']) {
    const loc = JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lng}/gears.json`, import.meta.url)), 'utf8'));
    for (const id of V.LIMITATIONS) { assert.ok(loc.limit[id], `${lng} limit.${id}`); assert.ok(loc.limitTip[id], `${lng} limitTip.${id}`); }
  }
});

t('assembly: one mesh per gear, ring is a ring, pose moves and turns every member', () => {
  const asm = fx('assembly.response').result;
  const pose = fx('pose.response').result;
  const bare = M.buildParts(asm, null);
  assert.equal(bare.length, asm.members.length);
  assert.equal(bare.length, 11);
  const posed = M.buildParts(asm, pose.members);
  const planet = posed.find((p) => p.key === 'stage0_s1.planet_0');
  const bareP = bare.find((p) => p.key === 'stage0_s1.planet_0');
  const centre = (p) => { let x = 0, y = 0, n = p.vertices.length / 3; for (let i = 0; i < p.vertices.length; i += 3) { x += p.vertices[i]; y += p.vertices[i + 1]; } return [x / n, y / n]; };
  const c = pose.members['stage0_s1.planet_0'].center_mm;
  const got = centre(planet), want = centre(bareP);
  assert.notDeepEqual(got.map((v) => v.toFixed(3)), want.map((v) => v.toFixed(3)));
  // the centroid of the tooth ring sits near the gear axis (the tooth outline is almost symmetric), within a module
  assert.ok(Math.hypot(got[0] - c[0], got[1] - c[1]) < 1.5, 'planet near its pose centre');
  const ring = posed.find((p) => p.key === 'stage1_s2.ring');
  assert.ok(ring.vertices.length / 3 > asm.outlines[asm.members.find((m) => m.name === 'stage1_s2.ring').outline_id].outline_mm.length * 3);
  // stage 2 starts at z = 15 (stage 1 face 10 + gap 5) and is 12 wide
  const zs = posed.find((p) => p.key === 'stage1_s2.sun').vertices.filter((_, i) => i % 3 === 2);
  assert.equal(Math.min(...zs), 15); assert.equal(Math.max(...zs), 27);
  for (const p of posed) { const n = p.vertices.length / 3; assert.ok(p.faces.every((f) => f.every((i) => i >= 0 && i < n))); }
});

t('extrusions are closed: every edge is shared by two triangles', () => {
  const sq = [[1, 0], [0, 1], [-1, 0], [0, -1], [1, 0]];
  for (const solid of [M.extrudeSolid(sq, 0, 2), M.extrudeRing(sq, 4, 0, 2)]) {
    const edges = new Map();
    for (const f of solid.faces) for (let k = 0; k < 3; k++) {
      const a = f[k], b = f[(k + 1) % 3]; const key = a < b ? `${a}-${b}` : `${b}-${a}`; edges.set(key, (edges.get(key) ?? 0) + 1);
    }
    assert.ok([...edges.values()].every((c) => c === 2));
  }
});

t('pose fixture: the sun of stage 2 follows the carrier of stage 1', () => {
  const m = fx('pose.response').result.members;
  assert.ok(Math.abs(m['stage1_s2.sun'].rotation_deg - 37 * (1 / 3.6)) < 1e-6);
});

t('client pose equals the pose operation (fixture) for every member, at 37 degrees', () => {
  const asm = fx('assembly.response').result;
  const want = fx('pose.response').result;
  const got = Pose.poseAt(asm, want.input_angle_deg);
  assert.deepEqual(Object.keys(got).sort(), Object.keys(want.members).sort());
  for (const [name, m] of Object.entries(want.members)) {
    assert.ok(Math.abs(got[name].rotation_deg - m.rotation_deg) < 1e-6, `${name} rotation`);
    for (let i = 0; i < 3; i++) assert.ok(Math.abs(got[name].center_mm[i] - m.center_mm[i]) < 1e-6, `${name} centre ${i}`);
  }
});

t('client pose at angle 0 is the assembly itself, and one input turn advances every member by its ratio', () => {
  const asm = fx('assembly.response').result;
  const zero = Pose.poseAt(asm, 0);
  for (const p of asm.members) {
    assert.ok(Math.abs(zero[p.name].rotation_deg - p.reference_angle_deg) < 1e-9);
    for (let i = 0; i < 3; i++) assert.ok(Math.abs(zero[p.name].center_mm[i] - p.center_mm[i]) < 1e-9, p.name);
  }
  const turn = Pose.poseAt(asm, 360);
  const sun = asm.members.find((m) => m.name === 'stage0_s1.sun');
  assert.ok(Math.abs(turn[sun.name].rotation_deg - 360) < 1e-9);
  assert.ok(Math.abs(asm.overall_ratio - 12.884) < 1e-3);
});

t('hidden stages leave the scene', () => {
  const asm = fx('assembly.response').result;
  assert.equal(M.buildParts(asm, null, new Set([1])).length, 5);
  assert.equal(M.buildParts(asm, null, new Set([0, 1])).length, 0);
  assert.ok(M.buildParts(asm, null, new Set([0])).every((p) => p.key.startsWith('stage1_')));
});

t('a point has no verdict: the page keeps the core reasons and shows no pass flag for it', () => {
  const r = fx('point_forward.response').result;
  assert.equal(r.passed, false);
  assert.deepEqual(r.pass_reasons.filter((x) => x === 'parts_not_evaluated' || x === 'preset_not_evaluated').length, 2);
  const src = readFileSync(fileURLToPath(new URL('../components/Results.tsx', import.meta.url)), 'utf8');
  assert.match(src, /isPoint \? \(/);
  assert.match(src, /data-verdict="none"/);
});
