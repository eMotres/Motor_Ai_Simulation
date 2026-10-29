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
