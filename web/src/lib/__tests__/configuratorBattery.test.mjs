// node --test — the Battery block opens on the machine's own pack (owner 2026-10-05).
// Imports lib/configuratorBattery.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let B = null;
try { B = await import('../configuratorBattery.ts'); } catch { /* old Node */ }
const t = B ? test : test.skip;

const L12 = { chemistry: 'NMC', cells: 6, v_cell_min: 3.0, v_cell_nom: 3.7, v_cell_max: 4.2,
              v_min: 18.0, v_nom: 22.2, v_max: 25.2 };
const L20 = { chemistry: 'NMC', cells: 12, v_min: 36.0, v_nom: 44.4, v_max: 50.4 };   // totals only

t('L12 opens on 6S 18 / 22.2 / 25.2 V, never the 100-cell default', () => {
  const b = B.batteryFromPack(L12);
  assert.deepEqual(b, { type: 'NMC', cells: 6, nom: 3.7, max: 4.2, min: 3.0 });
  assert.ok(Math.abs(b.cells * b.nom - 22.2) < 1e-9 && Math.abs(b.cells * b.max - 25.2) < 1e-9);
});

t('L20 (pack totals only) is 12S 36 / 44.4 / 50.4 V', () => {
  const b = B.batteryFromPack(L20);
  assert.equal(b.cells, 12);
  assert.ok(Math.abs(b.cells * b.nom - 44.4) < 1e-9);
  assert.ok(Math.abs(b.cells * b.min - 36) < 1e-9 && Math.abs(b.cells * b.max - 50.4) < 1e-9);
});

t('LFP is recognised; a pack without cells or nominal names nothing', () => {
  assert.equal(B.batteryFromPack({ chemistry: 'LiFePO4', cells: 4, v_nom: 12.8 }).type, 'LFP');
  assert.equal(B.batteryFromPack({ cells: 6 }), null);
  assert.equal(B.batteryFromPack({ v_nom: 22.2 }), null);
  assert.equal(B.batteryFromPack(null), null);
});

t('a user edit is remembered for that machine only', () => {
  let raw = null;
  const mine = { type: 'NMC', cells: 7, nom: 3.7, max: 4.2, min: 3.0 };
  raw = B.writeBatteryEdit(raw, 'cat:l12', mine);
  assert.deepEqual(B.readBatteryEdit(raw, 'cat:l12'), mine);
  assert.equal(B.readBatteryEdit(raw, 'cat:l20'), null);        // another machine: untouched
  assert.equal(B.readBatteryEdit('nonsense', 'cat:l12'), null);
  assert.equal(B.readBatteryEdit(null, 'cat:l12'), null);
  assert.equal(B.readBatteryEdit(JSON.stringify({ 'cat:l12': { type: 'NMC', cells: 0 } }), 'cat:l12'), null);
});

t('configured battery edits do not bleed between presets; legacy machine edit is original-config only', () => {
  const legacy = { type: 'NMC', cells: 6, nom: 3.7, max: 4.2, min: 3.0 };
  const newer = { type: 'NMC', cells: 12, nom: 3.7, max: 4.2, min: 3.0 };
  const rawLegacy = B.writeBatteryEdit(null, 'cat:machine', legacy);
  assert.deepEqual(B.readBatteryEditForConfig(rawLegacy, 'cat:machine', 'L12', 'L12'), legacy);
  assert.equal(B.readBatteryEditForConfig(rawLegacy, 'cat:machine', 'L20', 'L12'), null);
  const rawL20 = B.writeBatteryEditForConfig(rawLegacy, 'cat:machine', 'L20', newer);
  assert.deepEqual(B.readBatteryEditForConfig(rawL20, 'cat:machine', 'L20', 'L12'), newer);
  assert.deepEqual(B.readBatteryEditForConfig(rawL20, 'cat:machine', 'L12', 'L12'), legacy);
  assert.notEqual(B.batteryEditKey('cat:machine', 'L12'), B.batteryEditKey('cat:machine', 'L20'));
  const cleared = B.clearBatteryEditForConfig(rawL20, 'cat:machine', 'L12', 'L12');
  assert.equal(B.readBatteryEditForConfig(cleared, 'cat:machine', 'L12', 'L12'), null);
  assert.deepEqual(B.readBatteryEditForConfig(cleared, 'cat:machine', 'L20', 'L12'), newer);
});

t('resetting to the machine pack removes only this machine\'s edit', () => {
  const mine = { type: 'NMC', cells: 7, nom: 3.7, max: 4.2, min: 3.0 };
  let raw = B.writeBatteryEdit(null, 'cat:l12', mine);
  raw = B.writeBatteryEdit(raw, 'cat:l20', { ...mine, cells: 14 });
  raw = B.clearBatteryEdit(raw, 'cat:l12');
  assert.equal(B.readBatteryEdit(raw, 'cat:l12'), null);
  assert.equal(B.readBatteryEdit(raw, 'cat:l20').cells, 14);
  assert.equal(B.clearBatteryEdit('garbage', 'x'), '{}');
});

t('a configured machine with no saved pack never inherits its card passport pack', () => {
  assert.equal(B.batteryForMachineConfig(true, null, L12, L12), null);
  assert.equal(B.batteryForDisplay(null, B.batteryForMachineConfig(true, null, L12, L12)).cells, 100);
  assert.deepEqual(B.batteryForMachineConfig(false, null, L12, null), B.batteryFromPack(L12));
  assert.deepEqual(B.batteryForMachineConfig(false, null, null, L12), B.batteryFromPack(L12));
  assert.deepEqual(B.batteryForMachineConfig(true, L12, null, null), B.batteryFromPack(L12));
});

t('which battery a machine opens on: the edit, else the pack, else stock display', () => {
  const pack = B.batteryFromPack(L12);
  const edit = { type: 'NMC', cells: 7, nom: 3.7, max: 4.2, min: 3.0 };
  assert.equal(B.wantedBattery(edit, pack), edit);
  assert.equal(B.wantedBattery(null, pack), pack);
  assert.equal(B.wantedBattery(null, null), null);
  assert.deepEqual(B.batteryForDisplay(null, null), { type: 'NMC', cells: 100, nom: 3.7, max: 4.2, min: 3.0 });
  assert.ok(B.sameBattery(pack, { ...pack }) && !B.sameBattery(pack, edit));
});
