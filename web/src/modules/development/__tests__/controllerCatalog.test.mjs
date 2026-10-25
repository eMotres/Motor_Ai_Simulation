// node --test: the controller picker of the specification (owner 2026-10-26).  Synthetic catalogue entries in the shape
// GET /api/controllers/catalog returns (the DTI numbers are the manual's, but any numbers would do).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  OWN_DESIGN, applyControllerChoice, batteryMaxV, batteryVsController, catalogBrands, linkedController, matchCatalog, powerDevicesUnknown,
  prefillProvenance, prefillSource, statedPowerDevices, withCatalogLink,
} from '../controllerCatalog.ts';

const cell = (value, basis = 'stated', page = 10) => ({ value, basis, page });
const HV850 = {
  id: 'dti-hv-850-rev4', brand: 'DTI', model: 'HV-850', revision: 'Rev 4', display_name: 'DTI HV-850 (Rev 4)', aliases: ['DTI HV-850', 'HV-850'],
  supply_max_V: 800, power_devices_known: false,
  prefill: { '2_6.continuous_Arms': cell(364.9, 'derived'), '2_6.peak_Arms': cell(600), '2_6.peak_duration_s': cell(10, 'figure', 11), '2_7.switching_frequency_Hz': cell(8000), '2_8': cell('Absolute encoder', 'inferred', 17) },
};
const HV550 = {
  id: 'dti-hv-550-rev4', brand: 'DTI', model: 'HV-550', revision: 'Rev 4', display_name: 'DTI HV-550 (Rev 4)', aliases: ['DTI HV-550', 'HV-550'],
  supply_max_V: 800, power_devices_known: false,
  prefill: { '2_6.continuous_Arms': cell(364.9, 'derived'), '2_6.peak_Arms': cell(390), '2_7.switching_frequency_Hz': cell(8000), '2_8': cell('Absolute encoder', 'inferred', 17) },
};
const FULL = { // an entry that names its devices
  id: 'x-1', brand: 'Example Drives', model: 'X1', revision: '1', display_name: 'Example Drives X1 (1)', aliases: [], supply_max_V: 1000, power_devices_known: true,
  prefill: { '2_5.device_type': cell('SiC MOSFET'), '2_5.voltage_class_V': cell(1200), '2_6.continuous_Arms': cell(200), '2_6.peak_Arms': cell(300) },
};
const CATALOG = [HV550, HV850, FULL];
const filled = (value) => ({ status: 'filled', value });
const draft = (fields = {}) => ({ schema_version: 2, fields, mission_profile: [], production: {} });
const val = (d, id, prop) => { const v = d.fields?.[id]?.value; return prop ? v?.[prop] : v; };

test('choosing a catalogue controller fills brand, model and the dependent fields with provenance', () => {
  const d = applyControllerChoice(draft(), { kind: 'catalog', entry: HV850 }, CATALOG);
  assert.equal(val(d, '2_4', 'manufacturer_model'), 'DTI HV-850 (Rev 4)');
  assert.equal(val(d, '2_4', 'catalog_id'), 'dti-hv-850-rev4');
  assert.equal(val(d, '2_4', 'supplier'), 'DTI');                       // the brand, automatically
  assert.equal(val(d, '2_6', 'continuous_Arms'), 364.9);
  assert.equal(val(d, '2_6', 'peak_Arms'), 600);
  assert.equal(val(d, '2_6', 'peak_duration_s'), 10);
  assert.equal(val(d, '2_7', 'switching_frequency_Hz'), 8000);
  assert.equal(val(d, '2_8'), 'Absolute encoder');
  assert.equal(d.fields['2_6'].status, 'filled');
  // the manual does not name the transistors: nothing is invented
  assert.equal(val(d, '2_5', 'device_type'), undefined);
  // provenance "from catalogue <id>" on every value that still holds the catalogue value
  const prov = prefillProvenance(d, CATALOG);
  assert.deepEqual(Object.keys(prov).sort(), ['2_6.continuous_Arms', '2_6.peak_Arms', '2_6.peak_duration_s', '2_7.switching_frequency_Hz', '2_8']);
  assert.equal(prov['2_6.peak_duration_s'].page, 11);
  assert.equal(prefillSource(d, '2_6.peak_Arms', CATALOG), 'dti-hv-850-rev4');
});

test('an edited value stops being "from catalogue" and survives a later controller change', () => {
  let d = applyControllerChoice(draft(), { kind: 'catalog', entry: HV850 }, CATALOG);
  d = { ...d, fields: { ...d.fields, '2_6': filled({ ...val(d, '2_6'), peak_Arms: 500 }) } };       // the customer lowers the peak current
  assert.equal(prefillSource(d, '2_6.peak_Arms', CATALOG), null);
  const swapped = applyControllerChoice(d, { kind: 'catalog', entry: HV550 }, CATALOG);
  assert.equal(val(swapped, '2_6', 'peak_Arms'), 500);                  // typed by the customer: kept
  assert.equal(val(swapped, '2_6', 'continuous_Arms'), 364.9);
  assert.equal(val(swapped, '2_6', 'peak_duration_s'), '');             // still held the HV-850 value, HV-550 states none: cleared
  assert.equal(val(swapped, '2_4', 'catalog_id'), 'dti-hv-550-rev4');
  assert.equal(val(swapped, '2_4', 'manufacturer_model'), 'DTI HV-550 (Rev 4)');
});

test('changing the controller replaces only the fields that still hold the old catalogue values', () => {
  const start = applyControllerChoice(draft({ '2_6': filled({ continuous_Arms: 111 }) }), { kind: 'catalog', entry: HV850 }, CATALOG);
  assert.equal(val(start, '2_6', 'continuous_Arms'), 111);              // the customer's own limit was not overwritten by the first choice
  const next = applyControllerChoice(start, { kind: 'catalog', entry: FULL }, CATALOG);
  assert.equal(val(next, '2_6', 'continuous_Arms'), 111);               // still the customer's
  assert.equal(val(next, '2_6', 'peak_Arms'), 300);                     // was the HV-850 value: replaced
  assert.equal(val(next, '2_5', 'device_type'), 'SiC MOSFET');          // the new entry names its devices
  assert.equal(val(next, '2_7', 'switching_frequency_Hz'), '');         // held the HV-850 value, FULL states none: cleared
  assert.equal(val(next, '2_4', 'supplier'), 'Example Drives');         // the automatic supplier follows the brand
});

test('a supplier the customer typed is never replaced; "own design" clears the old catalogue values only', () => {
  let d = applyControllerChoice(draft({ '2_4': filled({ manufacturer_model: '', supplier: 'Erium GmbH' }) }), { kind: 'catalog', entry: HV850 }, CATALOG);
  assert.equal(val(d, '2_4', 'supplier'), 'Erium GmbH');
  d = applyControllerChoice(d, { kind: 'own' }, CATALOG);
  assert.equal(val(d, '2_4', 'manufacturer_model'), OWN_DESIGN);
  assert.equal(val(d, '2_4', 'catalog_id'), undefined);
  assert.equal(val(d, '2_4', 'supplier'), 'Erium GmbH');
  assert.equal(val(d, '2_6', 'peak_Arms'), '');
  assert.equal(val(d, '2_6', 'continuous_Arms'), '');
  assert.equal(linkedController(d, CATALOG), undefined);
});

test('free typing unlinks without touching anything else; typing an exact catalogue name links and pre-fills', () => {
  const linked = applyControllerChoice(draft(), { kind: 'catalog', entry: HV850 }, CATALOG);
  const typed = applyControllerChoice(linked, { kind: 'text', text: 'DTI HV-85' }, CATALOG);
  assert.equal(val(typed, '2_4', 'manufacturer_model'), 'DTI HV-85');
  assert.equal(val(typed, '2_4', 'catalog_id'), undefined);
  assert.equal(val(typed, '2_6', 'peak_Arms'), 600);                    // nothing is lost while typing
  const back = applyControllerChoice(typed, { kind: 'text', text: 'dti hv-850' }, CATALOG);
  assert.equal(val(back, '2_4', 'catalog_id'), 'dti-hv-850-rev4');
  assert.equal(val(back, '2_4', 'manufacturer_model'), 'DTI HV-850 (Rev 4)');
  const fresh = applyControllerChoice(draft(), { kind: 'text', text: 'HV-850' }, CATALOG);
  assert.equal(val(fresh, '2_6', 'peak_Arms'), 600);
});

test('a field marked "not known yet" is not pre-filled', () => {
  const d = applyControllerChoice(draft({ '2_8': { status: 'not_known_yet', value: null } }), { kind: 'catalog', entry: HV850 }, CATALOG);
  assert.equal(d.fields['2_8'].status, 'not_known_yet');
  assert.equal(val(d, '2_6', 'peak_Arms'), 600);
});

test('an existing free-text controller is recognised and linked, with no data loss', () => {
  const stored = draft({ '2_4': filled({ manufacturer_model: 'DTI HV-850 (Rev 4)', supplier: 'Customer', count: 2 }), '2_6': filled({ continuous_Arms: 111 }) });
  const linked = withCatalogLink(stored, CATALOG);
  assert.deepEqual(val(linked, '2_4'), { manufacturer_model: 'DTI HV-850 (Rev 4)', supplier: 'DTI', supplier_legacy: 'Customer', count: 2, catalog_id: 'dti-hv-850-rev4' });
  assert.deepEqual(linked.fields['2_6'], stored.fields['2_6']);          // the stored limit is untouched
  assert.equal(val(stored, '2_4', 'catalog_id'), undefined);             // the input draft is not mutated
  assert.equal(withCatalogLink(linked, CATALOG), linked);                // idempotent
  const own = draft({ '2_4': filled({ manufacturer_model: 'Own design', supplier: 'Customer' }) });
  assert.equal(withCatalogLink(own, CATALOG), own);
  assert.equal(matchCatalog('HV-8500', CATALOG), undefined);
  assert.equal(linkedController(stored, CATALOG)?.id, 'dti-hv-850-rev4');
});

test('806 V pack on an 800 V controller is a validation error', () => {
  const d = (max) => draft({ '2_1': filled({ voltage_nominal_V: 756, voltage_max_V: max }), '2_4': filled({ manufacturer_model: 'DTI HV-850 (Rev 4)', catalog_id: 'dti-hv-850-rev4' }) });
  assert.deepEqual(batteryVsController(d(806), CATALOG), { max: 806, limit: 800, controller: 'DTI HV-850 (Rev 4)' });
  assert.equal(batteryVsController(d(800), CATALOG), null);
  assert.equal(batteryVsController(draft({ '2_1': filled({ voltage_max_V: 900 }), '2_4': filled({ manufacturer_model: 'Own design' }) }), CATALOG), null);
  assert.equal(batteryMaxV(draft({ '2_1': filled({ series_cells: 192, cell_voltage_max_V: 4.2 }) })), 806.4);
  assert.ok(batteryVsController(draft({ '2_1': filled({ series_cells: 192, cell_voltage_max_V: 4.2 }), '2_4': filled({ manufacturer_model: 'HV-850' }) }), CATALOG));
});

test('a controller whose devices the catalogue does not name gets ONE warning; a named one does not', () => {
  assert.equal(powerDevicesUnknown(applyControllerChoice(draft(), { kind: 'catalog', entry: HV850 }, CATALOG), CATALOG), true);
  assert.equal(powerDevicesUnknown(applyControllerChoice(draft(), { kind: 'catalog', entry: FULL }, CATALOG), CATALOG), false);
  assert.equal(powerDevicesUnknown(draft({ '2_4': filled({ manufacturer_model: 'Own design' }) }), CATALOG), false);   // own design: entered in the controller definition
  assert.equal(statedPowerDevices(draft({ '2_5': filled({ device_type: 'SiC MOSFET', voltage_class_V: 1200 }) })), 'SiC MOSFET, 1200 V');
  assert.equal(statedPowerDevices(draft()), '');
});

test('supplier menu offers the catalogue brands', () => {
  assert.deepEqual(catalogBrands(CATALOG), ['DTI', 'Example Drives']);
});
