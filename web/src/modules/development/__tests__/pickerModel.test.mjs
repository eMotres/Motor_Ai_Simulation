import test from 'node:test';
import assert from 'node:assert/strict';
import { filterPickerCatalog, groupPickerCatalog, pickerDisplayLabel, pickerReasonKey, snapshotSourceKey } from '../pickerModel.ts';

test('picker search matches die, configuration, and numeric labels while preserving die groups', () => {
  const rows = [
    { die: 'Die A', config: 'Config 1', label: '30 mm, 12 slots / 14 poles, 10 mm stack' },
    { die: 'Die A', config: 'Config 2', label: '42 mm, 12 slots / 10 poles, 12 mm stack' },
    { die: 'Die B', config: 'Config 3', label: '30 mm, 9 slots / 8 poles, 8 mm stack', disabled: true },
  ];
  assert.deepEqual(filterPickerCatalog(rows, '14 poles').map((row) => row.config), ['Config 1']);
  assert.deepEqual(filterPickerCatalog(rows, 'die b').map((row) => row.die), ['Die B']);
  assert.deepEqual(filterPickerCatalog([{ die: 'Die C', config: 'Config 4', slots: 12, poles: 14, diameter_mm: 30, stack_mm: 10 }], '14').map((row) => row.config), ['Config 4']);
  assert.deepEqual(groupPickerCatalog(filterPickerCatalog(rows, '30 mm')).map(([die, items]) => [die, items.map((item) => item.config)]), [
    ['Die A', ['Config 1']], ['Die B', ['Config 3']],
  ]);
  assert.equal(pickerDisplayLabel({ die: 'Die A', config: 'Config 1', diameter_mm: 30, slots: 12, poles: 14, stack_mm: 10 }, (key, args) => `${key}:${args.diameter}/${args.slots}/${args.poles}/${args.stack}`), 'picker.dimensions:30/12/14/10');
  assert.equal(pickerReasonKey('Already linked to another project'), 'picker.reason.linkedElsewhere');
  assert.equal(pickerReasonKey('Motor is no longer available'), 'picker.reason.motorUnavailable');
  assert.equal(pickerReasonKey('linked_elsewhere'), 'picker.reason.linkedElsewhere');
  assert.equal(pickerReasonKey('not_shareable'), 'picker.reason.shareRequired');
  assert.equal(snapshotSourceKey('saved from Simulation'), 'snapshots.sourceSimulation');
  assert.equal(snapshotSourceKey('unlinked motor'), 'snapshots.sourceUnlinkedMotor');
});
