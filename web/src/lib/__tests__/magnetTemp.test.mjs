/**
 * node --test — the Simulation tab's MAGNET TEMPERATURE field (owner
 * 2026-10-29: «сделать так, чтобы можно было задавать температуру магнитов в
 * расчёте … а это не работает»).  Pinned:
 *   * blank = the card as quoted (nothing sent); a number is sent as typed;
 *     a decimal comma is a decimal point — "90,5" used to vanish silently;
 *   * text that is not a number is an ERROR (the field goes red, Run waits),
 *     never a silent fall-back to the card;
 *   * the value is part of a duty's operating point: kept per duty, saved
 *     with it, restored on load (lib/dutySettings DUTY_OP_KEYS).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { magnetTempError, parseMagnetTempC } from '../magnetTemp.ts';

const LIB = join(dirname(fileURLToPath(import.meta.url)), '..');

test('blank is the card, numbers are sent, a comma is a decimal point', () => {
  assert.equal(parseMagnetTempC(''), null);
  assert.equal(parseMagnetTempC('   '), null);
  assert.equal(parseMagnetTempC(null), null);
  assert.equal(parseMagnetTempC('90'), 90);
  assert.equal(parseMagnetTempC(' 90.5 '), 90.5);
  assert.equal(parseMagnetTempC('90,5'), 90.5);
  assert.equal(parseMagnetTempC(68), 68);
});

test('what cannot be used is said, not dropped', () => {
  assert.equal(magnetTempError(''), null);
  assert.equal(magnetTempError('90'), null);
  assert.equal(magnetTempError('90,5'), null);
  assert.equal(magnetTempError('90 C'), 'not a number');
  assert.equal(magnetTempError('hot'), 'not a number');
  assert.match(magnetTempError('900'), /outside/);
});

test('the payload sends it through the same parser; the duty keeps it', () => {
  const payload = readFileSync(join(LIB, 'emRunPayload.ts'), 'utf8');
  assert.match(payload, /parseMagnetTempC\(readSimSetting<string>\('magnetTempC'/);
  const duty = readFileSync(join(LIB, 'dutySettings.ts'), 'utf8');
  assert.match(duty, /'sim\.magnetTempC'/);
});

// ── the grade's limit (owner 2026-10-30: «не больше той, для которой они предназначены») ──
import { magnetTempOverLimit } from '../magnetTemp.ts';
import { legacyMagnetPick, magnetFamily, magnetVariants, effectiveMagnet } from '../magnetVariants.ts';

test('a typed temperature above the grade limit is caught; at the limit it is not', () => {
  assert.equal(magnetTempOverLimit('181', 180), 180);
  assert.equal(magnetTempOverLimit('180', 180), null);
  assert.equal(magnetTempOverLimit('150,5', 150), 150);
  assert.equal(magnetTempOverLimit('', 180), null, 'blank = the 20 °C card, never over');
  assert.equal(magnetTempOverLimit('abc', 180), null, 'not a number is magnetTempError, not this');
  assert.equal(magnetTempOverLimit('500', undefined), null, 'no limit known, nothing judged here');
});

test('one 20 °C card per grade: a former <grade>_<T>C pick becomes grade + temperature', () => {
  const lib = ['N52UH', 'F52SH', 'Fe16N2_lab_best'];
  assert.deepEqual(legacyMagnetPick('F52SH_80C', lib), { grade: 'F52SH', tempC: 80 });
  assert.equal(legacyMagnetPick('F52SH', lib), null, 'a current card is not a former pick');
  assert.equal(legacyMagnetPick('N42SH_120C', lib), null, 'unknown grade: left alone');
  assert.equal(magnetFamily('N52UH_150C'), 'N52UH');
  assert.equal(magnetFamily('N52UH'), 'N52UH');
  assert.deepEqual(magnetVariants('N52UH', lib), ['N52UH'], 'one card: no temperature picker');
  // a former pick that left the library is never sent to the solver
  assert.equal(effectiveMagnet('F52SH', 'F52SH_80C', lib), 'F52SH');
});

// ── the field always shows the temperature the run uses (owner 2026-10-30: «меню так и не работает») ──
import { magnetTempShown, dutyMagnetTempC } from '../magnetTemp.ts';

test('an empty field shows the card temperature (grey); typed text wins', () => {
  assert.deepEqual(magnetTempShown('', 150), { text: '150', isCard: true }, 'former N52UH_150C');
  assert.deepEqual(magnetTempShown('', 20), { text: '20', isCard: true }, 'a 20 °C library card');
  assert.deepEqual(magnetTempShown('  ', 20), { text: '20', isCard: true });
  assert.deepEqual(magnetTempShown('90', 150), { text: '90', isCard: false });
  assert.deepEqual(magnetTempShown('abc', 150), { text: 'abc', isCard: false }, 'an invalid entry stays visible (red)');
  assert.deepEqual(magnetTempShown('', null), { text: '', isCard: false }, 'card not loaded yet');
});

test('a duty load takes the duty\'s own magnet temperature, else the card (never the previous duty\'s)', () => {
  assert.equal(dutyMagnetTempC({ mesh: { 'sim.magnetTempC': '120' } }), '120');
  assert.equal(dutyMagnetTempC({ mesh: { 'sim.magnetTempC': 87.5 } }), '87.5');
  assert.equal(dutyMagnetTempC({ mesh: { 'sim.stepsPP': 60 } }), '', 'no key: the card as quoted');
  assert.equal(dutyMagnetTempC({}), '');
  assert.equal(dutyMagnetTempC(null), '');
  // the duty-load sequence writes it BEFORE the per-duty overlay restore
  const src = readFileSync(join(LIB, 'dutyLocalApply.ts'), 'utf8');
  const a = src.indexOf("set('magnetTempC', dutyMagnetTempC(dd))");
  const b = src.indexOf('restoreDutyOp(opKey)');
  assert.ok(a > 0 && b > a, 'magnet temperature reset must precede the overlay restore');
});
