// node --test — the Simulation tab's PWM controls are REDIRECTED (Stage 2).
//
// Owner, 2026-09-22: «как отладим каплинг с контроллером, нам не нужен будет
// PWM в электромагнитном моделировании — всё будет задаваться в меню
// Controller».  A machine's carrier, bus, dead time and device are a property
// of its CONTROLLER, and two places to type them is how one duty ends up with
// two answers.
//
// This test reads the PANEL SOURCE rather than rendering it, for the reason the
// repo's other node tests state: `node --test` cannot load the TS/TSX modules.
// What it pins is therefore a contract in the source, and it is the right one
// to pin, because the failure mode is silent: a future edit that restores
// `setDrive('pwm_voltage')` to that button gives the machine a second place to
// describe its inverter and nothing visibly breaks.
//
// THREE RULES, and the third matters as much as the first two:
//   1. pressing "PWM inverter" does NOT switch the drive — it says where the
//      setting lives;
//   2. the line it shows is ONE line, the words the owner asked for;
//   3. a run ALREADY on `pwm_voltage` (a stored record restored into the panel)
//      keeps its controls — old records stay readable and re-runnable, which is
//      why the drive itself was left alone.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, '..', 'SimulationPanel.tsx'), 'utf8');

test('the PWM button does not switch the drive any more', () => {
  // The guard is the whole mechanism: the click returns before setDrive.
  assert.match(
    SRC,
    /if \(m === 'pwm_voltage' && drive !== 'pwm_voltage'\) \{\s*\n\s*setPwmMoved\(true\);[^\n]*\n\s*return;/,
    'clicking "PWM inverter" must set the notice and return, not setDrive');
  // …and the old seeding line that only made sense when it DID switch is gone.
  assert.ok(!/m === 'pwm_voltage' && !\(vPeak > 0\)/.test(SRC),
            'the vPeak seed belonged to the old switch and must not survive it');
});

test('it shows one line naming the Controller, and a way there', () => {
  assert.ok(SRC.includes('PWM is defined in Controller'),
            'the one line the owner asked for');
  assert.match(SRC, /goToTab\('controller'\)/,
               'the line must offer to open the Controller tab');
  // One line + a HelpTip, never a paragraph on the panel (the UI rule).
  const block = SRC.slice(SRC.indexOf('{pwmMoved && ('),
                          SRC.indexOf("{drive === 'pwm_voltage' && (<>"));
  assert.ok(block.length > 0, 'the notice must sit above the PWM controls');
  assert.equal((block.match(/<Typography/g) || []).length, 1,
               'exactly one line of text');
  assert.match(block, /<HelpTip/, 'the explanation belongs in the tooltip');
});

test('a stored pwm_voltage run still gets its controls', () => {
  // The panel still RENDERS the PWM block for a run that is on that drive, and
  // the restore path still accepts the drive name — both are what keeps an old
  // record readable and re-runnable.
  assert.ok(SRC.includes("{drive === 'pwm_voltage' && (<>"),
            'the PWM controls must still render for a run already on it');
  assert.match(SRC, /_drv === 'pwm_voltage'/,
               'restoring a stored pwm_voltage run must still set the drive');
});
