// node --test — the PWM picker is TWO dropdowns: transistor, then PWM frequency (owner 2026-10-05).
// The (device, frequency) pair names exactly one computed variant; the frequency menu lists only the
// carriers computed for the chosen device; changing the device keeps the frequency if that pair exists,
// else the nearest computed one.  Persistence, presets and "modified from preset" speak the PAIR.
//
// Data: the REAL pilot record of L12 (config/passports/CIANO14 40 new/L12.json), not a mock.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let D = null, R = null;
try {
  D = await import('../configuratorDrive.ts');
  R = await import('../configuratorPresets.ts');
} catch { /* old Node */ }
const t = D ? test : test.skip;

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
const L12 = JSON.parse(readFileSync(join(ROOT, 'config', 'passports', 'CIANO14 40 new', 'L12.json'), 'utf8'));
const VS = D ? D.usableVariants(L12.pwm_variants) : [];

t('the real L12 record: five variants, four transistors-or-fewer, each pair once', () => {
  assert.ok(VS.length >= 4);
  const pairs = VS.map((v) => D.pairKey(v));
  assert.equal(new Set(pairs).size, pairs.length, 'a (device, frequency) pair is one variant');
  const devs = D.variantDevices(VS);
  assert.equal(new Set(devs.map((d) => d.device)).size, devs.length);
  assert.ok(devs.every((d) => typeof d.device === 'string'));
});

t('the frequency menu lists only the carriers computed for the chosen transistor, ascending', () => {
  for (const { device } of D.variantDevices(VS)) {
    const cs = D.variantCarriers(VS, device);
    assert.deepEqual(cs, VS.filter((v) => v.device === device).map((v) => Number(v.carrier_hz)).sort((a, b) => a - b), device);
    assert.ok(cs.length >= 1);
  }
  // a device computed at one carrier only offers that one
  const single = D.variantDevices(VS).find((d) => D.variantCarriers(VS, d.device).length === 1);
  assert.ok(single, 'the real record has a transistor computed at one frequency');
  assert.equal(D.variantCarriers(VS, 'no such device').length, 0);
  assert.equal(D.carrierLabel(48000), '48 kHz');
  assert.equal(D.carrierLabel(100000), '100 kHz');
});

t('the pair selects exactly one variant', () => {
  for (const v of VS) assert.equal(D.variantFor(VS, v.device, Number(v.carrier_hz)), v);
  assert.equal(D.variantFor(VS, 'IQE018N06NM6SC', 1234), undefined);
  assert.equal(D.variantFor(VS, 'nobody', 48000), undefined);
});

t('changing the transistor keeps the frequency when that pair exists, else the NEAREST computed one', () => {
  const two = D.variantDevices(VS).filter((d) => D.variantCarriers(VS, d.device).length >= 2);
  const single = D.variantDevices(VS).find((d) => D.variantCarriers(VS, d.device).length === 1);
  // a device with several carriers: from its highest carrier to a device that has it -> kept
  const a = VS.find((v) => v.device === two[0].device && Number(v.carrier_hz) === Math.max(...D.variantCarriers(VS, two[0].device)));
  for (const d of two.slice(1)) {
    const hasIt = D.variantCarriers(VS, d.device).includes(Number(a.carrier_hz));
    const got = D.switchDevice(VS, a, d.device);
    assert.equal(got.device, d.device);
    if (hasIt) assert.equal(Number(got.carrier_hz), Number(a.carrier_hz));
  }
  // to the single-carrier device: its only carrier, whatever was current
  assert.equal(Number(D.switchDevice(VS, a, single.device).carrier_hz), D.variantCarriers(VS, single.device)[0]);
  // nearest on a synthetic ladder, ties to the lower one
  const mk = (device, hz) => ({ id: `${device}${hz}`, device, carrier_hz: hz, points: { p: { rpm: 1, I_A: 1 } } });
  const syn = [mk('A', 48000), mk('B', 24000), mk('B', 72000), mk('C', 20000), mk('C', 100000)];
  assert.equal(D.switchDevice(syn, syn[0], 'B').carrier_hz, 24000);            // 48k is equidistant: the lower wins
  assert.equal(D.switchDevice(syn, mk('A', 60000), 'B').carrier_hz, 72000);    // nearer to 72k
  assert.equal(D.switchDevice(syn, syn[0], 'C').carrier_hz, 20000);            // |48-20| < |100-48|
  assert.equal(D.switchDevice(syn, syn[0], 'nobody'), undefined);
  assert.equal(D.switchDevice(syn, null, 'B').carrier_hz, 24000);              // nothing current: the lowest
});

t('changing the frequency stays on the same transistor', () => {
  const dev = D.variantDevices(VS).find((d) => D.variantCarriers(VS, d.device).length >= 2).device;
  const [lo, hi] = D.variantCarriers(VS, dev);
  const cur = D.variantFor(VS, dev, lo);
  const got = D.switchCarrier(VS, cur, hi);
  assert.equal(got.device, dev); assert.equal(Number(got.carrier_hz), hi);
  assert.equal(D.switchCarrier(VS, cur, 12345), undefined);
  assert.equal(D.switchCarrier(VS, null, hi), undefined);
});

t('the variant labels print the device and the frequency only', () => {
  for (const v of VS) assert.equal(D.variantLabel(v), `${v.device} · ${D.carrierLabel(v.carrier_hz)}`);
});

t('persistence stores BOTH the id and the pair; a renumbered id is still found by its pair', () => {
  const v = VS[1];
  const choice = { drive: 'pwm', drive_variant: v.id, drive_device: v.device, drive_carrier_hz: Number(v.carrier_hz) };
  assert.deepEqual(D.pickDrive({ ...choice, other: 1 }), choice);
  const raw = D.writeDriveChoice(null, 'cat:m', choice);
  assert.deepEqual(D.readDriveChoice(raw, 'cat:m'), choice);
  // the passport was regenerated and the ids changed: the pair still names the variant
  const renumbered = VS.map((x, i) => ({ ...x, id: `new_${i}` }));
  const got = D.resolveVariant(renumbered, D.readDriveChoice(raw, 'cat:m'));
  assert.equal(D.pairKey(got), D.pairKey(v));
  // an unknown choice falls back to the first variant, never to nothing
  assert.equal(D.resolveVariant(VS, { drive_variant: 'gone' }), VS[0]);
  // Sine forgets the machine
  assert.equal(D.readDriveChoice(D.writeDriveChoice(raw, 'cat:m', {}), 'cat:m'), null);
  // old stored choices (id only) still read
  assert.deepEqual(D.readDriveChoice(JSON.stringify({ 'cat:m': { drive: 'pwm', drive_variant: v.id } }), 'cat:m'), { drive: 'pwm', drive_variant: v.id });
});

// ── presets ──────────────────────────────────────────────────────────────────────────────────
const knobsOf = { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1, I_A: 45, rpm: 13000, split: 1 };
const preset = (v) => ({
  config: 'L12', die: 'CIANO14 40 new', duty: 'rated', battery: null, device: null,
  knobs: { L_mm: 12, N: 7, wireH_mm: 0.6, split: 1, nP: 1, I_A: 45, rpm: 13000 },
  pwm_variants: VS.map((x) => ({ id: x.id, device: x.device, carrier_hz: Number(x.carrier_hz) })),
  drive_variant: v ? v.id : null,
});

t('a preset opens on its variant AS A PAIR (the two dropdowns show it)', () => {
  const v = VS[2];
  const k = R.presetKnobs(knobsOf, preset(v));
  assert.equal(k.drive, 'pwm'); assert.equal(k.drive_variant, v.id);
  assert.equal(k.drive_device, v.device); assert.equal(k.drive_carrier_hz, Number(v.carrier_hz));
  const s = R.presetKnobs({ ...k }, preset(null));
  assert.equal(s.drive, 'sine');
  assert.ok(!('drive_variant' in s) && !('drive_device' in s) && !('drive_carrier_hz' in s));
});

t('"modified from preset" compares the PAIR: two ids of one pair are the same drive; another frequency is a change', () => {
  const v = VS[2];
  const pr = preset(v);
  const same = R.presetKnobs(knobsOf, pr);
  assert.deepEqual(R.presetDiff(pr, same, null, null), []);
  // the same pair under another id (a regenerated passport)
  assert.deepEqual(R.presetDiff(pr, { ...same, drive_variant: 'other_id' }, null, null), []);
  // another frequency of the same transistor
  const other = VS.find((x) => x.device === v.device && Number(x.carrier_hz) !== Number(v.carrier_hz));
  if (other) {
    assert.deepEqual(R.presetDiff(pr, { ...same, drive_variant: other.id, drive_carrier_hz: Number(other.carrier_hz) }, null, null), ['drive']);
  }
  // another transistor
  const dev2 = VS.find((x) => x.device !== v.device);
  assert.deepEqual(R.presetDiff(pr, { ...same, drive_variant: dev2.id, drive_device: dev2.device, drive_carrier_hz: Number(dev2.carrier_hz) }, null, null), ['drive']);
  // Sine against a PWM preset, and PWM against a Sine preset
  assert.deepEqual(R.presetDiff(pr, { ...same, drive: 'sine' }, null, null), ['drive']);
  assert.deepEqual(R.presetDiff(preset(null), same, null, null), ['drive']);
  // knobs that only carry the id (stored before the pair existed) still compare by id
  assert.deepEqual(R.presetDiff(pr, { ...knobsOf, drive: 'pwm', drive_variant: v.id }, null, null), []);
});

t('the panel is wired to the two dropdowns and to the pair', () => {
  const panel = readFileSync(join(ROOT, 'web', 'src', 'components', 'compare', 'ConfiguratorPanel.tsx'), 'utf8');
  assert.match(panel, /aria-label=\{tx\('configureDrive\.transistor'\)\}/);
  assert.match(panel, /aria-label=\{tx\('configureDrive\.frequency'\)\}/);
  assert.match(panel, /switchDevice\(variants, variant, e\.target\.value\)/);
  assert.match(panel, /switchCarrier\(variants, variant, Number\(e\.target\.value\)\)/);
  assert.match(panel, /variantCarriers\(variants, variant\.device\)/);
  assert.doesNotMatch(panel, /variantLabel\(v\)/);                      // no combined "device · kHz" option any more
  assert.match(panel, /drive_device: v\.device, drive_carrier_hz: Number\(v\.carrier_hz\)/);
  assert.match(panel, /pairKey\(resolveVariant\(variants, k\)\)/);       // the "modified" check speaks the pair
});

for (const l of ['en', 'zh-CN']) {
  t(`both dropdowns have their labels and tooltips (${l})`, () => {
    const d = JSON.parse(readFileSync(join(ROOT, 'web', 'src', 'locales', l, 'controller.json'), 'utf8')).configureDrive;
    for (const k of ['transistor', 'transistorTip', 'frequency', 'frequencyTip']) assert.ok(d[k] && d[k].length > 2, k);
  });
}
