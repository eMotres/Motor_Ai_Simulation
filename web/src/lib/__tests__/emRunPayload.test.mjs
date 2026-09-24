// node --test — the rules of lib/emRunPayload.ts, copied verbatim (the repo's
// convention for node tests: `node --test` cannot load the TS modules, so the
// pure functions under test are re-stated here and kept in sync).
//
// WHAT THIS PINS, and why it is worth pinning (2026-09-08).  The Thermal tab may
// now make the Electromagnetic run its solve could not find — through the
// orchestrator, with the SAME body the Electromagnetic tab's coupled Run sends.
// Two bodies would be two ways for the run that gets MADE to stop being the run
// that gets LOOKED FOR, and that failure is silent: the same 422, six minutes
// later.  So:
//
//   • the two callers hand the ONE builder the same inputs — the Electromagnetic
//     panel from its React state, the Thermal tab from where that panel persists
//     it — and the blocks they produce are identical;
//   • each excitation source sends ITS OWN parameters and no others (the backend
//     refuses a v_bus on a current drive rather than ignore it);
//   • the pack rides only an imposed-VOLTAGE run of a machine that has one;
//   • the operating point the Thermal tab is ASKING about wins over the stored
//     one, so the run it makes is the run its own request names.
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── the fake browser storage the readers below use ─────────────────────── */
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
};
const put = (obj) => {
  store.clear();
  for (const [k, v] of Object.entries(obj)) store.set(k, JSON.stringify(v));
};

/* ── copied verbatim from lib/emRunPayload.ts ───────────────────────────── */
const DRIVES = ['current', 'voltage', 'pwm_voltage', 'custom_current', 'bldc_current'];

function readSimSetting(key, def) {
  try {
    const raw = localStorage.getItem(`sim.${key}`);
    return raw == null ? def : JSON.parse(raw);
  } catch { return def; }
}

const numOr = (v, def) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : def;
};

function driveFields(inp) {
  const { drive, battery } = inp;
  const imposedV = drive === 'voltage' || drive === 'pwm_voltage';
  return {
    drive,
    ...(imposedV
      ? { v_phase_peak: inp.vPeak, v_delta_deg: inp.vDelta, harm_ref: true }
      : { v_phase_peak: 0, v_delta_deg: 0, harm_ref: false }),
    // pwm_voltage sends NO v_bus / f_switch (2026-09-24): the PWM drive is
    // the Controller's, and the route resolves both from it.
    ...(drive === 'bldc_current' ? { i_block: inp.iBlock } : {}),
    ...(drive === 'custom_current' ? { waveform: inp.waveform } : {}),
    ...((imposedV && battery)
      ? { battery: JSON.stringify(battery),
          ...(inp.busCouple ? { bus_couple: true } : {}),
          ...(inp.chargeMax ? { charge_max: true } : {}) }
      : {}),
  };
}

function emRunInputsFromSettings(over = {}) {
  const drive = readSimSetting('drive', 'current');
  return {
    restore: false,
    steps: numOr(readSimSetting('stepsPP', 40), 40),
    gamma_deg: numOr(readSimSetting('gamma', 0), 0),
    I_phase_rms: Math.max(0, numOr(readSimSetting('current', 0), 0)),
    drive: DRIVES.includes(drive) ? drive : 'current',
    vPeak: numOr(readSimSetting('vPeak', 30.0), 30.0),
    vDelta: numOr(readSimSetting('vDelta', 0), 0),
    iBlock: numOr(readSimSetting('iBlock', 0), 0),
    waveform: String(readSimSetting('waveform', '') ?? ''),
    battery: null,
    busCouple: readSimSetting('busCouple', true) === true,
    chargeMax: false,
    fieldLosses: true,
    eddyCoupled: true,
    demag: readSimSetting('demag', true) === true,
    torqueFilter: false,
    fresh: false,
    run_id: '',
    ...over,
  };
}

/* ═══════════════════════════════════════════════════════════════════════════
 * ONE builder, two callers
 * ═══════════════════════════════════════════════════════════════════════════ */

test('the Thermal fallback and the Simulation panel build the same body', () => {
  // What the Electromagnetic panel has persisted — i.e. what its React state
  // holds, because every one of these fields is a `usePersisted`.
  put({
    'sim.drive': 'current', 'sim.stepsPP': 36, 'sim.gamma': 12.5,
    'sim.current': 461.7, 'sim.vPeak': 30, 'sim.vDelta': 0, 'sim.vBus': 750,
    'sim.fSwitch': 24000, 'sim.iBlock': 0, 'sim.waveform': '',
    'sim.busCouple': true, 'sim.demag': true,
  });
  // The Electromagnetic panel's own props, as `TransientCharts` receives them.
  const panel = {
    restore: false, steps: 36, gamma_deg: 12.5, I_phase_rms: 461.7,
    drive: 'current', vPeak: 30, vDelta: 0,
    iBlock: 0, waveform: '', battery: null, busCouple: true, chargeMax: false,
    fieldLosses: true, eddyCoupled: true, demag: true, torqueFilter: false,
    fresh: false, run_id: '',
  };
  assert.deepEqual(emRunInputsFromSettings(), panel);
  assert.deepEqual(driveFields(emRunInputsFromSettings()), driveFields(panel));
});

test('the operating point the Thermal tab asks about wins over the stored one', () => {
  put({ 'sim.stepsPP': 36, 'sim.gamma': 12.5, 'sim.current': 461.7 });
  // The thermal request was refused for I = 300 A at 48 steps; the run made for
  // it must be that run, not the one the panel happens to hold now.
  const inp = emRunInputsFromSettings({
    I_phase_rms: 300, gamma_deg: 0, steps: 48, run_id: 'therm-em-1',
  });
  assert.equal(inp.I_phase_rms, 300);
  assert.equal(inp.gamma_deg, 0);
  assert.equal(inp.steps, 48);
  assert.equal(inp.run_id, 'therm-em-1');
});

test('a browser that has never opened the panel still names a real machine', () => {
  put({});
  const inp = emRunInputsFromSettings();
  assert.equal(inp.drive, 'current');   // never a voltage drive by accident
  assert.equal(inp.I_phase_rms, 0);     // 0 A, not NaN — the backend refuses it loudly
  assert.equal(inp.demag, true);
  assert.equal(inp.chargeMax, false);   // a one-shot press is never inherited
  assert.equal(inp.fresh, false);       // …and neither is "discard the cache"
});

test('a corrupt drive falls back to current rather than to nothing', () => {
  put({ 'sim.drive': 'plasma' });
  assert.equal(emRunInputsFromSettings().drive, 'current');
});

/* ═══════════════════════════════════════════════════════════════════════════
 * Each source sends its OWN parameters and no others
 * ═══════════════════════════════════════════════════════════════════════════ */

const base = {
  drive: 'current', vPeak: 30, vDelta: 5,
  iBlock: 120, waveform: '[[0,1]]', battery: null, busCouple: true,
  chargeMax: false,
};

test('a current drive carries no voltage, no carrier and no pack', () => {
  assert.deepEqual(driveFields(base), {
    drive: 'current', v_phase_peak: 0, v_delta_deg: 0, harm_ref: false });
});

test('a PWM run carries NO bus and NO carrier — they are the Controller\'s', () => {
  // 2026-09-24: «PWM нужно выкинуть из Electromagnetic» — the route resolves
  // both from the Controller settings (inverter.drive_source).
  const d = driveFields({ ...base, drive: 'pwm_voltage' });
  assert.equal('v_bus' in d, false);
  assert.equal('f_switch' in d, false);
  assert.equal(d.v_phase_peak, 30);
  assert.equal(d.harm_ref, true);
});

test('the block amplitude rides only the BLDC source', () => {
  assert.equal(driveFields({ ...base, drive: 'bldc_current' }).i_block, 120);
  assert.equal('i_block' in driveFields(base), false);
});

test('the sampled waveform rides only the custom source', () => {
  assert.equal(driveFields({ ...base, drive: 'custom_current' }).waveform, '[[0,1]]');
  assert.equal('waveform' in driveFields(base), false);
});

test('the pack rides an imposed-voltage run only', () => {
  const pack = { v_oc: 750, r_int_mohm: 24.7 };
  const v = driveFields({ ...base, drive: 'pwm_voltage', battery: pack });
  assert.equal(v.battery, JSON.stringify(pack));
  assert.equal(v.bus_couple, true);
  assert.equal('charge_max' in v, false);          // the search was not pressed
  // …and never a current run: there is no bridge for it to be behind.
  assert.equal('battery' in driveFields({ ...base, battery: pack }), false);
});
