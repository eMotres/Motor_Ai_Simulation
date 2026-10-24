import test from 'node:test';
import { formatDiffValue } from '../specificationDiff.ts';

test('diff translates choice states without translating customer phase text', () => {
  const t = key => ({'unknown':'未知','mission.source.catalog':'目录'})[key] ?? key;
  const entry = {fieldId:'mission_profile',path:'phase',property:'phase',before:null,after:null,change:'changed'};
  assert.equal(formatDiffValue('missing',entry,undefined,'zh-CN',t),'missing');
  assert.equal(formatDiffValue('not_known_yet',{...entry,path:'status'},undefined,'zh-CN',t),'未知');
  assert.equal(formatDiffValue('catalog',{...entry,path:'source',property:'source'},undefined,'zh-CN',t),'目录');
});
import assert from 'node:assert/strict';
import { formatDiffLabel, semanticSpecificationDiff } from '../specificationDiff.ts';

const schema = { schema_version: 2, sections: [{ id: '1', fields: [
  { id: '1_4', label: { en: 'Continuous duty', 'zh-CN': '连续工况' }, short_label: { en: 'S1', 'zh-CN': 'S1' }, properties: [
    { id: 'torque_Nm', label: { en: 'Torque', 'zh-CN': '转矩' }, unit: 'N·m' },
    { id: 'speed_rpm', label: { en: 'Speed', 'zh-CN': '转速' }, unit: 'rpm' },
  ] },
  { id: 'load_characteristic', label: { en: 'Load curve', 'zh-CN': '负载曲线' }, properties: [
    { id: 'catalog_id', label: { en: 'Catalog item', 'zh-CN': '目录项目' } },
    { id: 'source_snapshot.vendor', label: { en: 'Vendor', 'zh-CN': '供应商' } },
  ] },
  { id: 'mission_profile', label: 'Mission profile' },
  { id: 'delivery_window', label: 'Delivery window' },
] }] };

test('stable mission IDs make row order irrelevant and report leaf edits', () => {
  const a = { schema_version: 1, mission_profile: [
    { row_id: 'a', phase: 'cruise', speed_rpm: 1000 }, { row_id: 'b', phase: 'climb', speed_rpm: 2000 },
  ] };
  const reordered = { schema_version: 2, mission_profile: [
    { row_id: 'b', phase: 'climb', speed_rpm: 2100 }, { row_id: 'a', phase: 'cruise', speed_rpm: 1000 },
  ] };
  const diff = semanticSpecificationDiff(a, reordered);
  assert.equal(diff.length, 1);
  assert.deepEqual({ rowId: diff[0].rowId, path: diff[0].path, before: diff[0].before, after: diff[0].after },
    { rowId: 'b', path: 'speed_rpm', before: 2000, after: 2100 });
});

test('legacy rows get deterministic index identity and added/removed rows emit leaf entries', () => {
  const diff = semanticSpecificationDiff({ mission_profile: [{ phase: 'old', speed_rpm: 100 }] },
    { mission_profile: [{ row_id: 'new', phase: 'new', speed_rpm: 250 }] });
  assert.ok(diff.some((entry) => entry.rowId === 'legacy-0' && entry.change === 'removed' && entry.before === 'old'));
  assert.ok(diff.some((entry) => entry.rowId === 'new' && entry.change === 'added' && entry.after === 250));
  assert.ok(diff.every((entry) => entry.before === null || ['string', 'number', 'boolean'].includes(typeof entry.before)));
});

test('migration row index provenance pairs a legacy row with its assigned stable ID', () => {
  const oldDraft = { mission_profile: [{ phase: 'cruise', speed_rpm: 1000 }] };
  const newDraft = { mission_profile: [{ row_id: 'stable-migrated', phase: 'cruise', speed_rpm: 1200 }],
    provenance: { schema_migrations: [{ mission_row_index_map: [{ legacy_index: 0, row_id: 'stable-migrated' }] }] } };
  const diff = semanticSpecificationDiff(oldDraft, newDraft);
  assert.equal(diff.length, 1);
  assert.equal(diff[0].rowId, 'stable-migrated');
  assert.equal(diff[0].before, 1000);
  assert.equal(diff[0].after, 1200);
  const reverse = semanticSpecificationDiff(newDraft, { mission_profile: [{ phase: 'cruise', speed_rpm: 1000 }] });
  assert.equal(reverse.length, 1);
  assert.equal(reverse[0].rowId, 'stable-migrated');
  assert.equal(reverse[0].before, 1200);
  assert.equal(reverse[0].after, 1000);
});

test('field status transitions and retained values are compared separately', () => {
  const diff = semanticSpecificationDiff({ fields: { '1_4': { status: 'not_known_yet', value: { torque_Nm: 4, speed_rpm: 1000 } } } },
    { fields: { '1_4': { status: 'filled', value: { torque_Nm: 4, speed_rpm: 1100 } } } });
  assert.ok(diff.some((entry) => entry.fieldId === '1_4' && entry.path === 'status' && entry.before === 'not_known_yet' && entry.after === 'filled'));
  assert.ok(diff.some((entry) => entry.path === 'speed_rpm' && entry.before === 1000 && entry.after === 1100));
});

test('mission unknown status and public source flags are visible', () => {
  const diff = semanticSpecificationDiff({ mission_profile_status: 'not_known_yet', mission_profile: [], fields: {
    load_characteristic: { status: 'filled', value: { source: 'catalog', override: false, stale: false } },
  } }, { mission_profile_status: 'filled', mission_profile: [], fields: {
    load_characteristic: { status: 'filled', value: { source: 'manual', override: true, stale: true } },
  } });
  assert.ok(diff.some((entry) => entry.fieldId === 'mission_profile' && entry.path === 'status' && entry.before === 'not_known_yet'));
  assert.deepEqual(diff.filter((entry) => ['source', 'override', 'stale'].includes(entry.path)).map((entry) => entry.path).sort(), ['override', 'source', 'stale']);
});

test('version and private migration provenance are omitted while public source metadata is flattened', () => {
  const before = { schema_version: 1, fields: { load_characteristic: { status: 'filled', value: {
    catalog_id: 'prop-A', content_digest: 'old-public-fingerprint', source_snapshot: { vendor: 'Old vendor', private_note: 'private' },
  } } }, provenance: { schema_migrations: [{ legacy_draft_snapshot: { password: 'hidden' } }] },
    production: { delivery_window: { status: 'filled', value: 'Q2' } } };
  const after = { schema_version: 2, fields: { load_characteristic: { status: 'filled', value: {
    catalog_id: 'prop-A', content_digest: 'new-public-fingerprint', source_snapshot: { vendor: 'New vendor', private_note: 'private' },
  } } }, provenance: { source_sha256: 'hidden' }, production: { delivery_window: { status: 'filled', value: 'Q3' } } };
  const diff = semanticSpecificationDiff(before, after, { before: schema, after: schema });
  assert.ok(diff.some((entry) => entry.path === 'source_snapshot.vendor' && entry.before === 'Old vendor' && entry.after === 'New vendor'));
  assert.ok(diff.some((entry) => entry.path === 'content_digest' && entry.before === 'old-public-fingerprint' && entry.after === 'new-public-fingerprint'));
  assert.ok(diff.some((entry) => entry.namespace === 'production' && entry.fieldId === 'delivery_window' && entry.after === 'Q3'));
  assert.ok(diff.every((entry) => !JSON.stringify(entry).includes('source_sha256') && !JSON.stringify(entry).includes('private')));
  assert.ok(diff.every((entry) => entry.before === null || typeof entry.before !== 'object'));
  assert.equal(formatDiffLabel(diff.find((entry) => entry.fieldId === '1_4') ?? {
    fieldId: '1_4', path: 'speed_rpm', property: 'speed_rpm', before: 1, after: 2, change: 'changed',
  }, schema, 'en'), 'S1 · Speed (rpm)');
  assert.equal(formatDiffLabel({ fieldId: '1_4', path: 'torque_Nm', property: 'torque_Nm', before: 1, after: 2, change: 'changed' }, schema, 'zh-CN'), 'S1 · 转矩 (N·m)');
  assert.equal(formatDiffLabel({ fieldId: 'mission_profile', path: 'torque_Nm', property: 'torque_Nm', before: 1, after: 2, change: 'changed' }, schema, 'zh-CN', (key) => key === 'versionDiff.property.torque_Nm' ? '转矩' : key), 'Mission profile · 转矩');
});
