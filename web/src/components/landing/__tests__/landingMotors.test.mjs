// node --test - the landing's public motor preview and the "open in Configure after sign-up" intent.
// Imports lib/landingMotors.ts itself (pure TypeScript; Node >= 22.18 / 24, else skipped), and reads
// the components / locales as text for the structural promises (no geometry on the page, same keys
// in both locales, the section sits above the concept diagram).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, '..', '..', '..', '..');
const read = (...p) => fs.readFileSync(path.join(WEB, ...p), 'utf8');

let M = null;
try { M = await import('../../../lib/landingMotors.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

const memory = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); }, _m: m };
};

const ROW = { die: 'CIANO14 40 new', configuration: 'L12', application: 'propeller',
  rated: { power_kw: 0.74, torque_nm: 0.55, speed_rpm: 13000, efficiency_pct: 91.1, mass_kg: 0.1229,
    mass_is_partial: false, continuous: true } };

// ── the parser ──────────────────────────────────────────────────────────────────────────────
t('the parser keeps the allow-listed fields and nothing else', () => {
  const evil = { ...ROW, geometry: { slot_height: 9 }, thumb_svg: '<svg/>', cost_eur: 5,
    rated: { ...ROW.rated, thermal_path: 1, p_core_w: 4 } };
  const [m] = M.parsePublicMotors({ motors: [evil] });
  assert.deepEqual(Object.keys(m).sort(), ['application', 'configuration', 'die', 'rated']);
  assert.deepEqual(Object.keys(m.rated).sort(),
    ['continuous', 'efficiency_pct', 'mass_is_partial', 'mass_kg', 'power_kw', 'speed_rpm', 'torque_nm']);
});

t('a malformed answer gives no cards, never a throw', () => {
  for (const bad of [null, undefined, 5, 'x', {}, { motors: 'x' }, { motors: [null, 3, {}] }, { motors: [{ die: 'A' }] }]) {
    assert.deepEqual(M.parsePublicMotors(bad), []);
  }
});

t('numbers must be finite numbers; text and non-finite become null', () => {
  const [m] = M.parsePublicMotors({ motors: [{ ...ROW, rated: { power_kw: '1', torque_nm: NaN, speed_rpm: Infinity,
    efficiency_pct: null, mass_kg: true } }] });
  assert.deepEqual(m.rated, { power_kw: null, torque_nm: null, speed_rpm: null, efficiency_pct: null,
    mass_kg: null, mass_is_partial: false, continuous: false });
});

t('a name that is not a catalogue name is dropped, never shown altered', () => {
  const rows = [{ ...ROW, die: '<b>CIANO</b>14 40' }, { ...ROW, configuration: 'L12"><img>' },
    { ...ROW, die: 'CIANO20 150_35 new', configuration: 'L35' },
    { ...ROW, die: '100 mm (24s-28p), #2+', configuration: 'L15' }];
  const out = M.parsePublicMotors({ motors: rows });
  assert.deepEqual(out.map((m) => `${m.die}/${m.configuration}`),
    ['CIANO20 150_35 new/L35', '100 mm (24s-28p), #2+/L15']);
});

t('at most 24 cards', () => {
  const many = Array.from({ length: 40 }, (_, i) => ({ ...ROW, configuration: `L${i}` }));
  assert.equal(M.parsePublicMotors({ motors: many }).length, 24);
});

// ── the numbers on the card ────────────────────────────────────────────────────────────────
t('headline rows: power, torque, speed, mass, efficiency, in this order and units', () => {
  const [m] = M.parsePublicMotors({ motors: [ROW] });
  assert.deepEqual(M.headlineRows(m), [
    { key: 'power', value: '0.74', unit: 'kW' },
    { key: 'torque', value: '0.55', unit: 'N·m' },
    { key: 'speed', value: '13000', unit: 'rpm' },
    { key: 'mass', value: '0.123', unit: 'kg' },
    { key: 'efficiency', value: '91.1', unit: '%' },
  ]);
});

t('a missing number is a dash, never "null" or NaN', () => {
  const [m] = M.parsePublicMotors({ motors: [{ ...ROW, rated: {} }] });
  for (const r of M.headlineRows(m)) assert.equal(r.value, '–');
});

t('fmtNum keeps three significant digits', () => {
  assert.equal(M.fmtNum(0.7400), '0.74');
  assert.equal(M.fmtNum(12.345), '12.3');
  assert.equal(M.fmtNum(1234.5), '1235');
  assert.equal(M.fmtNum(0), '0');
  assert.equal(M.fmtNum(null), '–');
});

// ── the intent ─────────────────────────────────────────────────────────────────────────────
t('a remembered motor applies once', () => {
  const s = memory();
  assert.equal(M.rememberLandingMotor({ die: 'CIANO14 40 new', config: 'L12' }, 1000, s), true);
  assert.deepEqual(M.takeLandingMotor(2000, s), { die: 'CIANO14 40 new', config: 'L12' });
  assert.equal(M.takeLandingMotor(2000, s), null);
});

t('an old choice expires and is removed', () => {
  const s = memory();
  M.rememberLandingMotor({ die: 'A', config: 'L1' }, 0, s);
  assert.equal(M.takeLandingMotor(M.LANDING_MOTOR_TTL_MS + 1, s), null);
  assert.equal(s._m.size, 0);
});

t('a generic sign-up forgets the choice', () => {
  const s = memory();
  M.rememberLandingMotor({ die: 'A', config: 'L1' }, 5, s);
  M.forgetLandingMotor(s);
  assert.equal(M.takeLandingMotor(6, s), null);
});

t('a tampered or malformed value is refused', () => {
  const s = memory();
  for (const raw of ['not json', '{}', JSON.stringify({ die: '../x', config: 'L1', at: 1 }),
    JSON.stringify({ die: 'A', config: 'L1' }), JSON.stringify({ die: 'A', config: 'x'.repeat(200), at: 1 }),
    JSON.stringify({ die: 'A', config: 'L1', at: 1e15 })]) {
    s.setItem(M.LANDING_MOTOR_KEY, raw);
    assert.equal(M.takeLandingMotor(2, s), null, raw);
  }
  assert.equal(M.rememberLandingMotor({ die: '', config: 'L1' }, 1, s), false);
});

t('blocked storage never throws', () => {
  const dead = { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); }, removeItem() { throw new Error('x'); } };
  assert.equal(M.rememberLandingMotor({ die: 'A', config: 'L1' }, 1, dead), false);
  assert.equal(M.takeLandingMotor(1, dead), null);
  M.forgetLandingMotor(dead);
  assert.equal(M.consumeOpenInConfigure(dead), false);
  M.markOpenInConfigure(dead);
});

t('the selection handed to the app is the app\'s own last-motor shape, with no reference id', () => {
  assert.deepEqual(M.selectionOfIntent({ die: 'D', config: 'L1' }), { ref_id: null, die: 'D', config: 'L1', duty: null });
});

t('the land-on-Configure flag is one-shot', () => {
  const s = memory();
  assert.equal(M.consumeOpenInConfigure(s), false);
  M.markOpenInConfigure(s);
  assert.equal(M.consumeOpenInConfigure(s), true);
  assert.equal(M.consumeOpenInConfigure(s), false);
});

// ── the page ───────────────────────────────────────────────────────────────────────────────
test('the preview sits above the five-steps diagram and reads one open route', () => {
  const landing = read('src', 'components', 'landing', 'Landing.tsx');
  assert.ok(landing.indexOf('<PublicMotors />') > 0, 'PublicMotors is rendered');
  assert.ok(landing.indexOf('<PublicMotors />') < landing.indexOf('id="concept"'), 'above the concept section');
  const comp = read('src', 'components', 'landing', 'PublicMotors.tsx');
  assert.match(comp, /\/api\/public\/motors/);
  assert.equal((comp.match(/\/api\//g) || []).length, 2, 'only the one route (once in the header comment, once in the code)');
});

test('no geometry or construction on the page: no thumbnail, no CAD or winding words', () => {
  const comp = read('src', 'components', 'landing', 'PublicMotors.tsx').replace(/\/\*\*[\s\S]*?\*\//, '');
  assert.doesNotMatch(comp, /MotorThumbnail|thumb_svg|motor-thumbs/);
  assert.doesNotMatch(comp, /geometry|winding|slot|magnet|cad_|thermal_path|bearing/i);
  const logic = read('src', 'lib', 'landingMotors.ts');
  assert.doesNotMatch(logic, /geometry|thumb_svg|winding/);
});

test('the open button starts the sign-up and remembers the motor', () => {
  const comp = read('src', 'components', 'landing', 'PublicMotors.tsx');
  assert.match(comp, /rememberLandingMotor\(/);
  assert.match(comp, /signUp\.primary\(\)/);
  assert.match(comp, /motors\.open/);
});

test('both locales carry the same motors keys; the button reads as asked', () => {
  const load = (l) => JSON.parse(read('src', 'locales', l, 'landing.json')).motors;
  const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) =>
    (v && typeof v === 'object') ? keys(v, `${p}${k}.`) : [`${p}${k}`]).sort();
  assert.deepEqual(keys(load('zh-CN')), keys(load('en')));
  assert.equal(load('en').open, 'Open in Configure — free account');
  for (const l of ['en', 'zh-CN']) {
    const m = load(l);
    assert.ok(m.sub.length <= 90, `${l}: one short line`);
    assert.ok(m.ratedHint && m.openHint && m.massPartialHint, `${l}: tooltips`);
  }
});

test('sign-in hands the remembered motor to the app before the workspace boots', () => {
  const ctx = read('src', 'contexts', 'AuthContext.tsx');
  const i = ctx.indexOf('applyLandingMotor(u.email)');
  assert.ok(i > ctx.indexOf('storeSession(token, u)') && i < ctx.indexOf('setUser(toAuthUser(u))'));
  const apply = read('src', 'lib', 'landingMotorApply.ts');
  assert.match(apply, /writeLocalLastMotor/);
  assert.match(apply, /rememberLastMotor/);
});
