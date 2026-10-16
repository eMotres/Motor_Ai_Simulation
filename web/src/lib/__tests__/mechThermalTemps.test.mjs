// node --test — the pure rules of lib/mechThermalTemps.ts, copied verbatim
// (the repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure functions under test are re-stated here and kept in
// sync).  What they pin, in the order it matters:
//
//   • the AVERAGE is what a Solve sends and the MAX is only shown — a thermal
//     eigenstrain is a bulk strain, so the fit is set by the part's mean;
//   • a manual (or stale) request sends exactly the two fields it always did
//     and no new one, so the backend's cache key is untouched;
//   • a part the Thermal answer does not carry keeps the manual field.
import test from 'node:test';
import assert from 'node:assert/strict';

const REF_TEMP_C = 20;
const fin = (v) => (typeof v === 'number' && Number.isFinite(v)) ? v : null;

function tempOr(v, ref = REF_TEMP_C) {
  const t = (v ?? '').trim();
  const n = Number(t);
  return t !== '' && Number.isFinite(n) ? n : ref;
}

function partTempsFromComponents(components, at, stale) {
  const c = components ?? {};
  const one = (key) => {
    const e = c[key];
    return { avg: fin(e?.avg), max: fin(e?.max) };
  };
  const out = {
    magnet: one('magnet'), rotor: one('rotor'),
    shaft: one('shaft'), sleeve: one('sleeve'),
    at: at ?? null, stale: stale ?? null,
  };
  const any = [out.magnet, out.rotor, out.shaft, out.sleeve]
    .some((p) => p.avg !== null || p.max !== null);
  return any ? out : null;
}

function thermalTempsInUse(tempSource, temps) {
  return tempSource === 'thermal' && !!temps && temps.stale !== true;
}

function mechTempParams(tempSource, temps, manual, ref = REF_TEMP_C) {
  const manualRotor = tempOr(manual.rotorTempC, ref);
  const manualSleeve = tempOr(manual.sleeveTempC, ref);
  if (!thermalTempsInUse(tempSource, temps)) {
    return { rotor_temp_c: manualRotor, sleeve_temp_c: manualSleeve };
  }
  const t = temps;
  const out = {
    rotor_temp_c: t.rotor.avg ?? manualRotor,
    sleeve_temp_c: t.sleeve.avg ?? manualSleeve,
  };
  if (t.magnet.avg !== null) out.magnet_temp_c = t.magnet.avg;
  if (t.rotor.avg !== null) out.rotor_core_temp_c = t.rotor.avg;
  if (t.shaft.avg !== null) out.shaft_temp_c = t.shaft.avg;
  return out;
}

const ROWS = [['magnet', 'magnets'], ['rotor', 'core'], ['shaft', 'shaft'],
              ['sleeve', 'sleeve']];

function thermalTempsLine(temps, when = '') {
  if (!temps) return '';
  const parts = ROWS
    .map(([k, label]) => [label, temps[k].avg])
    .filter(([, v]) => v !== null)
    .map(([label, v]) => `${label} ${Math.round(v)}`);
  if (!parts.length) return '';
  return `from Thermal${when ? ` ${when}` : ''}: ${parts.join(' · ')} °C`;
}

const MANUAL = { rotorTempC: '150', sleeveTempC: '150' };
const FULL = {
  components: {
    magnet: { avg: 163.4, max: 171.2 }, rotor: { avg: 160.6, max: 168.0 },
    shaft: { avg: 158.2, max: 159.9 }, sleeve: { avg: 162.1, max: 166.4 },
    winding: { avg: 120, max: 140 }, stator: { avg: 90, max: 95 },
  },
  at: '2026-09-08T09:38:11+00:00',
};

test('the four rotor parts are read, and nothing else is', () => {
  const t = partTempsFromComponents(FULL.components, FULL.at, false);
  assert.deepEqual(Object.keys(t).sort(),
                   ['at', 'magnet', 'rotor', 'shaft', 'sleeve', 'stale']);
  assert.deepEqual(t.magnet, { avg: 163.4, max: 171.2 });
  assert.deepEqual(t.sleeve, { avg: 162.1, max: 166.4 });
  assert.equal(t.at, FULL.at);
  assert.equal(t.stale, false);
});

test('a part the mesh did not resolve is null, not zero', () => {
  const t = partTempsFromComponents(
    { rotor: { avg: 160, max: 168 }, sleeve: null }, FULL.at, false);
  assert.deepEqual(t.sleeve, { avg: null, max: null });
  assert.deepEqual(t.shaft, { avg: null, max: null });
  assert.equal(t.rotor.avg, 160);
});

test('a result carrying none of the four parts is no source at all', () => {
  assert.equal(partTempsFromComponents({ winding: { avg: 120, max: 140 } },
                                       FULL.at, false), null);
  assert.equal(partTempsFromComponents(null, null, null), null);
  assert.equal(partTempsFromComponents({}, null, null), null);
});

test('unknown staleness is UNKNOWN, and counts as usable', () => {
  const t = partTempsFromComponents(FULL.components, FULL.at, null);
  assert.equal(t.stale, null);
  assert.equal(thermalTempsInUse('thermal', t), true);
});

test('manual sends the two fields it always did and nothing new', () => {
  const t = partTempsFromComponents(FULL.components, FULL.at, false);
  assert.deepEqual(mechTempParams('manual', t, MANUAL),
                   { rotor_temp_c: 150, sleeve_temp_c: 150 });
});

test('a stale thermal result falls back to the manual pair', () => {
  const t = partTempsFromComponents(FULL.components, FULL.at, true);
  assert.equal(thermalTempsInUse('thermal', t), false);
  assert.deepEqual(mechTempParams('thermal', t, MANUAL),
                   { rotor_temp_c: 150, sleeve_temp_c: 150 });
});

test('an empty field is the REFERENCE, never Number("") === 0', () => {
  assert.equal(tempOr(''), 20);
  assert.equal(tempOr('  '), 20);
  assert.equal(tempOr('abc'), 20);
  assert.equal(tempOr('0'), 0);
  assert.deepEqual(mechTempParams('manual', null, { rotorTempC: '', sleeveTempC: '' }),
                   { rotor_temp_c: 20, sleeve_temp_c: 20 });
});

test('coupled sends every part its own AVERAGE, never its maximum', () => {
  const t = partTempsFromComponents(FULL.components, FULL.at, false);
  assert.deepEqual(mechTempParams('thermal', t, MANUAL), {
    rotor_temp_c: 160.6, sleeve_temp_c: 162.1,
    magnet_temp_c: 163.4, rotor_core_temp_c: 160.6, shaft_temp_c: 158.2,
  });
});

test('a part the thermal answer lacks keeps the manual field, and sends no key', () => {
  const t = partTempsFromComponents(
    { magnet: { avg: 163, max: 171 }, rotor: { avg: 161, max: 168 } },
    FULL.at, false);
  const p = mechTempParams('thermal', t, MANUAL);
  // no sleeve and no shaft in that result: the sleeve keeps the manual number
  // (the API's own fallback), and `shaft_temp_c` is simply not sent.
  assert.deepEqual(p, {
    rotor_temp_c: 161, sleeve_temp_c: 150,
    magnet_temp_c: 163, rotor_core_temp_c: 161,
  });
  assert.equal('shaft_temp_c' in p, false);
});

test('the one line names the four parts in order, rounded', () => {
  const t = partTempsFromComponents(FULL.components, FULL.at, false);
  assert.equal(thermalTempsLine(t, '09:38'),
               'from Thermal 09:38: magnets 163 · core 161 · shaft 158 · sleeve 162 °C');
});

test('the line drops the parts that are missing rather than printing dashes', () => {
  const t = partTempsFromComponents(
    { magnet: { avg: 163, max: 171 }, rotor: { avg: 161, max: 168 } },
    null, false);
  assert.equal(thermalTempsLine(t), 'from Thermal: magnets 163 · core 161 °C');
  assert.equal(thermalTempsLine(null), '');
});
