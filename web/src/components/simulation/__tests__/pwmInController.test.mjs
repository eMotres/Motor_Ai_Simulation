// node --test — PWM IS GONE FROM THE ELECTROMAGNETIC TAB (2026-09-24).
//
// Owner, 2026-09-22: «как отладим каплинг с контроллером, нам не нужен будет
// PWM в электромагнитном моделировании — всё будет задаваться в меню
// Controller».  And 2026-09-24, on a screenshot of the Controller tab's greyed
// "Carrier 20,000 Hz" placeholder: «Это значение нужно задавать в контроллере;
// PWM нужно выкинуть из Electromagnetic.»
//
// This test reads the PANEL SOURCE rather than rendering it, for the reason the
// repo's other node tests state: `node --test` cannot load the TS/TSX modules.
// What it pins is a contract in the source, and the failure it guards is
// silent: a future edit that brings back a carrier picker or a V_bus field
// gives the machine a second place to describe its inverter and nothing
// visibly breaks.
//
// FOUR RULES:
//   1. the drive selector offers Sine current and Target T / P (plus the two
//      non-PWM current sources) — no "PWM inverter" button;
//   2. no carrier, V_bus or controller-class state lives in the panel, and the
//      shared-config PATCH no longer writes v_bus / f_switch;
//   3. a STORED pwm_voltage run still restores (old records stay readable and
//      re-runnable) and shows ONE line + a way to the Controller + a HelpTip;
//   4. what the panel still needs about the bridge is READ from the
//      Controller's resolved point, never typed here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, '..', 'SimulationPanel.tsx'), 'utf8');
const PAYLOAD = readFileSync(join(HERE, '..', '..', '..', 'lib', 'emRunPayload.ts'), 'utf8');

test('the drive selector keeps Sine current and Target T / P, and no PWM button', () => {
  assert.ok(SRC.includes('Sine current'), 'the ideal reference stays');
  assert.ok(SRC.includes('Target T / P'), 'the target drive stays');
  assert.ok(!/\['pwm_voltage',\s*'PWM inverter'\]/.test(SRC),
            'the PWM inverter button must not come back');
  assert.ok(!SRC.includes('setPwmMoved'), 'the old redirect notice state is gone');
});

test('no carrier, V_bus or controller class lives in the panel', () => {
  for (const k of ["usePersisted('fSwitch'", "usePersisted('vBus'",
                   "usePersisted('fSwGroup'", "usePersisted('fSwCustom'",
                   'FSW_GROUPS', 'seedBusFromBattery']) {
    assert.ok(!SRC.includes(k), `${k} must not be in the Electromagnetic panel`);
  }
  // …and the shared-config PATCH no longer writes the retired pair.
  const patch = SRC.slice(SRC.indexOf('const simPhysicsPatch'),
                          SRC.indexOf('});', SRC.indexOf('const simPhysicsPatch')));
  assert.ok(patch.length > 0);
  assert.ok(!/v_bus\s*:/.test(patch) && !/f_switch\s*:/.test(patch),
            'simPhysicsPatch must not send v_bus / f_switch');
  // …and the run body does not carry them either.
  assert.ok(!/v_bus:\s*inp\./.test(PAYLOAD) && !/f_switch:\s*inp\./.test(PAYLOAD),
            'emRunPayload must not send v_bus / f_switch');
});

test('a stored pwm_voltage run still restores, with one line and a way there', () => {
  assert.match(SRC, /_drv === 'pwm_voltage'/,
               'restoring a stored pwm_voltage run must still set the drive');
  const at = SRC.indexOf("{drive === 'pwm_voltage' && (");
  assert.ok(at > 0, 'a restored PWM run gets its notice');
  const block = SRC.slice(at, SRC.indexOf("{drive === 'bldc_current'", at));
  assert.equal((block.match(/<Typography/g) || []).length, 1, 'exactly one line of text');
  assert.match(block, /from Controller/, 'the line names the Controller');
  assert.match(block, /goToTab\('controller'\)/, 'and offers to open it');
  assert.match(block, /<HelpTip/, 'the explanation belongs in the tooltip');
  assert.ok(!/<TextField/.test(block), 'no PWM input comes back with it');
});

test('what the panel needs about the bridge is read from the Controller', () => {
  assert.match(SRC, /getResolvedPoint\(/, 'the Controller\'s resolved point is read');
  assert.match(SRC, /ctrlFsw \/ frequency/, 'the step rule uses the Controller\'s carrier');
});
