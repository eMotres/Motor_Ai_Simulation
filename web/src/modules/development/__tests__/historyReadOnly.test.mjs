import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const source = (path) => readFileSync(join(here, path), 'utf8');

test('project snapshots remain list-only metadata and are shown in the shared comparison table', () => {
  const api = source('../api.ts');
  const page = source('../components/DevelopmentPage.tsx');
  assert.match(api, /snapshots:\s*async\s*\(id: string\).*?'GET'/s);
  assert.doesNotMatch(api, /createSnapshot|loadSnapshot|saveSnapshotAsMotor|POST.*snapshots|snapshots.*POST/);
  assert.match(page, /visibleProjectVersions\(motors, snapshots, showAllVersions\)/);
  assert.match(page, /<SharedComparisonTable rows=\{rows\}/);
  assert.match(page, /setHistoryDetail\(r\.motor\)/);
  assert.match(page, /<Dialog open=\{!!historyDetail\}/);
  assert.doesNotMatch(page, /restoreSnapshotToWorkspace|saveSnapshotAsMotor|openSaveAsDialog|snapshots\.loadWorkspace|snapshots\.saveAsMotor/);
  assert.doesNotMatch(page, /catalogTarget|catalogEpoch|setCatalogLoading|setCatalogTarget/);
  assert.match(page, /setUnlinkTarget\(null\); setHistoryDetail\(null\); motorLoadEpochRef/);
});

test('history comparison rows retain unique identity and estimate provenance', async () => {
  const { developmentComparisonRows } = await import('../comparisonRows.ts');
  const rows = developmentComparisonRows([
    { die: 'Synthetic', config: 'cfg', results: [{ duty: 'rated', has_result: true, comparison: { torque_nm: 3 } }] },
    { die: 'Synthetic', config: 'cfg', kind: 'history', snapshot_id: 'point-1', note: 'read only', results: [{ duty: 'rated', has_result: true, result_provenance: 'configure_interpolation', comparison: { torque_nm: 4 } }] },
  ]);
  assert.equal(rows.length, 2);
  assert.notEqual(rows[0].id, rows[1].id);
  assert.equal(rows[1].history, true);
  assert.equal(rows[1].estimate, true);
  assert.equal(rows[1].motor.note, 'read only');
});
