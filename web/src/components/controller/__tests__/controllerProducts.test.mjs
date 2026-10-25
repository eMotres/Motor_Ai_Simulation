// node --test: the read-only list of complete commercial controllers in the Controller tab (owner 2026-10-26).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { controllerCells, controllerDetails, provTip } from '../controllerProductsModel.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const t = (key, opts) => (opts ? `${key} ${JSON.stringify(opts)}` : key);

const cell = (value, unit, basis, page, extra = {}) => ({ value, unit, basis, page, ...extra });
// the shape of GET /api/controllers/catalog rows (the DTI manual's numbers)
const HV850 = {
  id: 'dti-hv-850-rev4', brand: 'DTI', model: 'HV-850', revision: 'Rev 4', display_name: 'DTI HV-850 (Rev 4)', manufacturer: 'Drivetrain Innovation kft.',
  supply_voltage_V: { min: cell(30, 'V', 'stated', 9), nominal: null, max: cell(800, 'V', 'stated', 9), abs_max: cell(830, 'V', 'stated', 9) }, supply_max_V: 800,
  dc_input_current_max_A: cell(400, 'A', 'stated', 9),
  phase_current: {
    continuous: [cell(850, 'Apk', 'stated', 10, { dc_V: 400 }), cell(516, 'Apk', 'stated', 10, { dc_V: 800 })],
    continuous_Arms_at_supply_max: cell(364.9, 'Arms', 'derived', 10),
    peak: [cell(850, 'Apk', 'stated', 10), cell(600, 'Arms', 'stated', 10)], peak_duration_s: cell(10, 's', 'figure', 11),
  },
  switching_frequency_Hz: { min: cell(8000, 'Hz', 'stated', 10), max: cell(14000, 'Hz', 'stated', 10), rating_point: cell(8000, 'Hz', 'stated', 10) },
  power_devices: { device_type: null, voltage_class_V: null, part_number: null, note: 'NOT STATED in the manual' }, power_devices_known: false,
  position_sensors: [{ type: 'RLS RM44SI encoder', spec_choice: 'Absolute encoder', basis: 'stated', page: 17 }, { type: 'Resolver', spec_choice: 'Resolver', basis: 'stated', page: 17 }],
  communication: [{ name: 'CAN (ISO 11898-2)', basis: 'stated', page: 25 }],
  mass_kg: cell(6.8, 'kg', 'stated', 12), dimensions_mm: { height: 77, width: 213, length: 420, page: 12 }, protection: { ip: 'IP65' },
  cooling: { type: 'liquid', coolant: 'glycol - distilled water mixture', max_inlet_C: cell(60, '', 'stated', 12), flow_L_per_min: cell(25.3, '', 'stated', 12) },
  source: { document: 'HV-550/HV-850 User manual, version 1.2' }, notes: ['Table 4 and Figure 3 disagree.'],
};
const HV550 = { ...HV850, id: 'dti-hv-550-rev4', model: 'HV-550', phase_current: { ...HV850.phase_current, peak: [cell(550, 'Apk', 'stated', 10), cell(390, 'Arms', 'stated', 10)], peak_duration_s: null }, dc_input_current_max_A: cell(280, 'A', 'stated', 9) };

test('the list shows ONE value per cell (no min-max range on screen); the rest is in the tooltip with its page', () => {
  const c = controllerCells(HV850, t);
  assert.equal(c.brand.text, 'DTI'); assert.equal(c.model.text, 'HV-850'); assert.equal(c.revision.text, 'Rev 4');
  assert.equal(c.supplyMax.text, '800 V');
  assert.match(c.supplyMax.tip, /830 V/); assert.match(c.supplyMax.tip, /30 V/); assert.match(c.supplyMax.tip, /"page":9/);
  assert.equal(c.continuous.text, '364.9 A rms');
  assert.match(c.continuous.tip, /850 Apk @ 400 V/); assert.match(c.continuous.tip, /516 Apk @ 800 V/);
  assert.equal(c.peak.text, '600 A rms');
  assert.equal(c.peakTime.text, '10 s'); assert.match(c.peakTime.tip, /"page":11/); assert.match(c.peakTime.tip, /controllers\.basis\.figure/);
  assert.equal(c.switching.text, '8 kHz'); assert.match(c.switching.tip, /8000 - 14000 Hz/);
  assert.equal(c.mass.text, '6.8 kg');
  for (const text of Object.values(c).map((x) => x.text)) assert.doesNotMatch(text, /\d\s*[-–]\s*\d/, text);   // no range in a visible cell
});

test('what the manual does not state stays empty or says unknown - nothing is invented', () => {
  const c = controllerCells(HV550, t);
  assert.equal(c.peakTime.text, '');                                      // Figure 4 states no separate peak time for the HV-550
  assert.equal(c.devices.text, 'controllers.unknown');                    // the transistors are not named
  assert.match(c.devices.tip, /NOT STATED/);
  assert.equal(c.peak.text, '390 A rms');
  const bare = controllerCells({ ...HV850, mass_kg: null, supply_voltage_V: {}, phase_current: { continuous: [], peak: [] }, switching_frequency_Hz: {}, position_sensors: [] }, t);
  for (const k of ['supplyMax', 'continuous', 'peak', 'peakTime', 'switching', 'mass', 'sensors']) assert.equal(bare[k].text, '', k);
  // a named device shows
  const named = controllerCells({ ...HV850, power_devices: { device_type: 'SiC MOSFET', voltage_class_V: 1200 }, power_devices_known: true }, t);
  assert.equal(named.devices.text, 'SiC MOSFET, 1200 V');
});

test('provenance text carries the page and the basis', () => {
  assert.match(provTip(cell(516, 'Apk', 'stated', 10), t), /"page":10/);
  assert.equal(provTip(null, t), '');
  assert.match(provTip({ value: 1, page: 3, basis: 'derived', note: 'x / sqrt(2)' }, t), /x \/ sqrt\(2\)/);
});

test('the open row lists every stated value with its page and the manual\'s own notes', () => {
  const rows = Object.fromEntries(controllerDetails(HV850, t).map((d) => [d.label, d.value]));
  assert.match(rows['controllers.detail.supply'], /830 V/);
  assert.match(rows['controllers.detail.dcCurrent'], /400 A/);
  assert.match(rows['controllers.detail.peak'], /850 Apk.*600 Arms/);
  assert.equal(rows['controllers.detail.devices'], 'controllers.unknown');
  assert.match(rows['controllers.detail.dimensions'], /420 x 213 x 77 mm/);
  assert.match(rows['controllers.detail.cooling'], /liquid/);
  assert.match(rows['controllers.detail.notes'], /disagree/);
  assert.match(rows['controllers.detail.source'], /User manual/);
});

test('EN and zh-CN carry every controllers.* key the list uses', () => {
  const en = JSON.parse(read('../../../locales/en/controller.json'));
  const zh = JSON.parse(read('../../../locales/zh-CN/controller.json'));
  const sources = read('../controllerProductsModel.ts') + read('../ControllerProducts.tsx');
  const keys = new Set([...sources.matchAll(/'(controllers\.[A-Za-z.]+)'/g)].map((m) => m[1]));
  for (const k of ['min', 'nominal', 'max', 'absMax']) keys.add(`controllers.${k}`);
  for (const id of ['brand', 'model', 'revision', 'supplyMax', 'continuous', 'peak', 'peakTime', 'switching', 'devices', 'sensors', 'mass']) keys.add(`controllers.col.${id}`);
  for (const id of ['manufacturer', 'supply', 'dcCurrent', 'continuous', 'peak', 'peakTime', 'switching', 'devices', 'sensors', 'communication', 'cooling', 'dimensions', 'mass', 'protection', 'source', 'notes']) keys.add(`controllers.detail.${id}`);
  for (const b of ['stated', 'derived', 'figure', 'inferred']) keys.add(`controllers.basis.${b}`);
  for (const k of keys) { assert.ok(get(en, k), `EN ${k}`); assert.ok(get(zh, k), `zh-CN ${k}`); }
  assert.ok(keys.size > 25);
});

test('the view is read-only: no form, no write call, no caption', () => {
  const view = read('../ControllerProducts.tsx');
  assert.doesNotMatch(view, /method: 'POST'|TextField|<Button/);
  assert.doesNotMatch(view, /variant="caption"|helperText=/);
  assert.match(read('../ControllerPanel.tsx'), /<ControllerProducts \/>/);
});
