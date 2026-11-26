// node --test - "With motor": request building, row display logic, the no-zero rule for losses, the two efficiency parts, errors.
// Fixtures are real answers of POST /api/gears/drivetrain (gear_drivetrain-1), see fixtures/make_drivetrain_fixtures.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const fx = (n) => JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/${n}.json`, import.meta.url)), 'utf8'));
let D = null, A = null, P = null;
try { D = await import('../drivetrain.ts'); A = await import('../api.ts'); P = await import('../plain.ts'); } catch { /* old Node */ }
const t = D ? test : test.skip;

t('request: inline gearbox, row names are the modes, a picked duty goes to motor.duties', () => {
  const inline = fx('drivetrain_ok.request').gearbox.request;
  const rows = [{ name: 'continuous', torque_out_Nm: 9.8, speed_out_rpm: 247, duty: '' }, { name: 'peak', torque_out_Nm: 5, speed_out_rpm: 247, duty: 'pull' }];
  const body = D.buildDrivetrainRequest({ die: 'D1', configuration: 'C1' }, rows, { kind: 'inline', request: inline });
  assert.deepEqual(body, fx('drivetrain_ok.request'));
  const rec = D.buildDrivetrainRequest({ die: 'D1', configuration: 'C1' }, rows.slice(0, 1), { kind: 'record', record_id: 'gb_0123456789abcdef', project: '' });
  assert.deepEqual(rec.gearbox, { record_id: 'gb_0123456789abcdef' });
  assert.equal(rec.motor.duties, undefined);
  assert.deepEqual(D.buildDrivetrainRequest({ die: 'D', configuration: 'C' }, rows, { kind: 'record', record_id: 'gb_1', project: 'p1' }).gearbox, { record_id: 'gb_1', project: 'p1' });
  assert.equal(D.defaultRows().length, 2);
  assert.deepEqual(D.defaultRows().map((r) => r.name), ['continuous', 'peak']);
});

t('ok rows: required motor point, margin, covered, the two efficiency parts stay separate, no chain number', () => {
  const j = fx('drivetrain_ok.response');
  assert.equal(j.schema, D.DRIVETRAIN_SCHEMA);
  assert.equal(j.motoring_only, true); assert.equal(j.reverse_not_computed, true);
  const row = j.rows[0];
  assert.equal(D.rowTone(row), 'ok');
  const parts = D.partCells(row);
  assert.ok(parts.gearbox.efficiency.text !== '-' && parts.motor.efficiency.text === '0.85');
  assert.match(parts.motor.at, /^1 N\*m, 3500 rpm$/);
  assert.notEqual(parts.gearbox.at, parts.motor.at);
  const chain = D.chainCell(row);
  assert.equal(chain.missing, true); assert.equal(chain.text, '-'); assert.equal(chain.reason, 'motor_point_differs');
  assert.equal(row.motor_saved_point.torque_basis, 'field_legacy');
  assert.ok(row.margin.torque_Nm > 0 && row.margin.speed_rpm > 0);
  assert.ok(Number(D.cell(row.output_torque_per_total_mass_Nm_per_kg, 2).text) > 0);
  assert.ok(Math.abs(row.mass.total_kg - (row.mass.motor_kg + row.mass.gearbox_kg)) < 1e-12);
});

t('losses: null is a dash naming the missing inputs, never 0; the total is partial', () => {
  const cells = D.lossCells(fx('drivetrain_ok.response').rows[0].gearbox);
  assert.equal(cells.length, 2);
  for (const l of cells) {
    assert.equal(l.bearings.text, '-'); assert.equal(l.churning.text, '-');
    assert.equal(l.bearings.reason, 'bearing_definitions, grease_drag_torque_Nm');
    assert.equal(l.mesh.missing, false); assert.equal(l.partial, true);
  }
  // a computed loss is a number (modified copy of the real answer)
  const g = JSON.parse(JSON.stringify(fx('drivetrain_ok.response').rows[0].gearbox));
  g.loss_by_stage[0].bearings_W = 0.25; g.loss_by_stage[0].loss_inputs_missing = ['grease_drag_torque_Nm'];
  assert.equal(D.lossCells(g)[0].bearings.text, '0.25');
  assert.equal(D.lossCells(g)[0].churning.text, '-');
  assert.equal(D.cell(0, 3).text, '0');                 // a true zero is shown as zero, only null is a dash
  assert.equal(D.lossCells(undefined).length, 0);
});

t('a matching saved point with the shaft marker gives the chain number as motor x gearbox', () => {
  const row = fx('drivetrain_match.response').rows[0];
  assert.equal(row.motor_saved_point.torque_basis, 'shaft');
  const chain = D.chainCell(row);
  assert.equal(chain.missing, false);
  assert.ok(Math.abs(Number(chain.text) - row.efficiency_parts.motor.efficiency * row.efficiency_parts.gearbox.efficiency) < 1e-4);
  assert.equal(row.chain_efficiency_partial_losses, true);
});

t('not covered and missing rows: limited_by named, requirement not clamped, reason shown', () => {
  const j = fx('drivetrain_missing.response');
  const [a, b] = j.rows;
  assert.equal(D.rowTone(a), 'warn'); assert.equal(a.covered_by_saved_point, false); assert.equal(a.limited_by, 'motor_torque');
  assert.ok(a.motor_required.torque_Nm > a.motor_saved_point.torque_Nm);
  assert.equal(D.rowTone(b), 'missing'); assert.equal(b.status, 'missing');
  assert.match(b.reason, /short-time|peak/);
  assert.equal(D.partCells(b).motor, null);                 // no motor part without a saved point; the gearbox part stays
  assert.ok(D.partCells(b).gearbox);
});

t('a marked record without a recognised shaft key is a normal missing row with a translated reason', () => {
  const row = fx('drivetrain_shaftkey.response').rows[0];
  assert.equal(row.status, 'missing'); assert.equal(row.reason, 'shaft_key_unrecognised');
  assert.equal(D.rowTone(row), 'missing');
  assert.equal(D.chainCell(row).missing, true);
  for (const lng of ['en', 'zh-CN']) {
    const loc = JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lng}/gears.json`, import.meta.url)), 'utf8'));
    assert.ok(loc.dtMissing.shaft_key_unrecognised, lng); assert.ok(loc.dtMissingTip.shaft_key_unrecognised, lng);
    for (const k of ['ok', 'missing', 'gearbox_failed']) { assert.ok(loc.dtStatus[k] && loc.dtStatusTip[k], `${lng} ${k}`); }
    for (const k of ['shaft', 'field_legacy']) assert.ok(loc.basis[k] && loc.basisTip[k], `${lng} ${k}`);
    for (const k of ['motor_torque', 'motor_speed']) assert.ok(loc.limitedBy[k], `${lng} ${k}`);
    for (const k of ['motor_point_differs', 'no_saved_point', 'not_covered', 'efficiency_unavailable', 'gearbox_failed']) assert.ok(loc.dtReason[k], `${lng} ${k}`);
    assert.ok(loc.motoringOnly && loc.reverseNotComputed, lng);
  }
});

t('a failed gearbox row keeps the core diagnostics and compares nothing', () => {
  const row = fx('drivetrain_failed.response').rows[0];
  assert.equal(row.status, 'gearbox_failed'); assert.equal(D.rowTone(row), 'failed');
  assert.equal(row.covered_by_saved_point, null); assert.equal(row.gearbox, undefined);
  assert.equal(row.gearbox_state, 'failed');
  assert.ok(Array.isArray(row.gearbox_flags) && Array.isArray(row.missing_inputs));
  assert.equal(D.chainCell(row).reason, 'gearbox_failed');
  assert.equal(D.lossCells(row.gearbox).length, 0);
});

t('422: the route paths land next to the load rows and pickers; gearbox paths collapse to one', () => {
  const err = A.parseErrorBody(422, fx('drivetrain_422.response'));
  assert.equal(err.errors[0].path, '/load/0/torque_out_Nm');
  const map = P.errorMap(err.errors.map((e) => ({ ...e, path: D.drivetrainErrorKey(e.path) })));
  assert.ok(map['/load/0/torque_out_Nm']);
  assert.equal(D.drivetrainErrorKey('/gearbox/request/stages/0/sun/z'), '/gearbox');
  assert.equal(D.drivetrainErrorKey('/gearbox/record_id'), '/gearbox');
  assert.equal(D.drivetrainErrorKey('/motor/die'), '/motor/die');
});

t('motor options from the family tree: only well-formed dies, configurations and duties', () => {
  const o = D.motorOptions({ dies: [{ name: 'D1', configs: [{ name: 'C1', duties: [{ name: 'rated', kind: 'continuous' }, { name: 'pull', kind: null }, { nope: 1 }] }, { nope: 1 }] }, { nope: 1 }, 'x'] });
  assert.deepEqual(o, { dies: [{ name: 'D1', configs: [{ name: 'C1', duties: [{ name: 'rated', kind: 'continuous' }, { name: 'pull', kind: null }] }] }] });
  assert.deepEqual(D.motorOptions(null), { dies: [] });
  assert.deepEqual(D.motorOptions({ dies: 5 }), { dies: [] });
});

t('remembered motor state: valid kept, junk dropped', () => {
  const ok = { die: 'D1', configuration: 'C1', rows: D.defaultRows(), source: 'inline' };
  assert.ok(D.validateMotorMemory(JSON.parse(JSON.stringify(ok))));
  assert.equal(D.validateMotorMemory(null), null);
  assert.equal(D.validateMotorMemory({ ...ok, source: 'x' }), null);
  assert.equal(D.validateMotorMemory({ ...ok, rows: [] }), null);
  assert.equal(D.validateMotorMemory({ ...ok, rows: [ok.rows[0], ok.rows[0]] }), null);   // duplicate row names are refused by the route
});

t('pick the configuration, then Calculate once: the first click is enabled, uses the picked configuration, and the pickers have no exit layer', () => {
  const options = D.motorOptions({ dies: [{ name: 'D1', configs: [{ name: 'C1', duties: [{ name: 'rated', kind: 'continuous' }] }, { name: 'C2', duties: [] }] }] });
  let mem = { die: '', configuration: '', rows: D.defaultRows(), source: 'inline' };
  assert.equal(D.canCalculate(mem, options, false), false);                    // nothing picked
  mem = D.pickDie(mem, 'D1');
  assert.equal(D.canCalculate(mem, options, false), false);                    // a die alone is not enough
  mem = D.pickConfiguration(mem, 'C2');
  assert.equal(D.canCalculate(mem, options, false), true);                     // enabled right after the pick: no second pick or click needed
  const first = D.buildDrivetrainRequest({ die: mem.die, configuration: mem.configuration }, mem.rows, { kind: 'record', record_id: 'gb_1', project: '' });
  assert.deepEqual(first.motor, { die: 'D1', configuration: 'C2' });           // the request carries the configuration just picked
  mem = D.pickConfiguration(mem, 'C1');
  assert.equal(D.buildDrivetrainRequest({ die: mem.die, configuration: mem.configuration }, mem.rows, { kind: 'record', record_id: 'gb_1', project: '' }).motor.configuration, 'C1');
  assert.equal(D.canCalculate(mem, options, true), false);                     // busy disables, as before
  assert.equal(D.canCalculate(D.pickDie(mem, 'D1'), options, false), false);   // re-picking the die clears the configuration
  assert.equal(D.canCalculate({ ...mem, configuration: 'gone' }, options, false), false);
  assert.equal(D.PICKER_MENU_PROPS.transitionDuration, 0);
  // the panel really hands that to its three pickers
  const src = readFileSync(fileURLToPath(new URL('../components/MotorPanel.tsx', import.meta.url)), 'utf8');
  assert.equal((src.match(/MenuProps: PICKER_MENU_PROPS/g) ?? []).length, 3);
});
