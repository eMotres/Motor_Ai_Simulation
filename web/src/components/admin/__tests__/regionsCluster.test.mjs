/** Admin · the other region's load graphs (docs/regions/DESIGN.md): the Overview tile data, the Servers / Overview wiring
 *  and the EN / zh-CN strings. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { remoteTile } from '../regions.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const src = (...p) => readFileSync(join(HERE, '..', ...p), 'utf8');
const admin = (lng) => JSON.parse(readFileSync(join(HERE, '..', '..', '..', 'locales', lng, 'admin.json'), 'utf8'));

const REMOTE = {
  configured: true, reachable: true, region: 'us',
  nodes: { nodes: [], cluster: { nodes: 1, online: 1, cores: 24, cpu_used_cores: 6, mem_used: 32, mem_total: 128 } },
  app: { jobs: { running: 3, queued: 2 } }, live: {}, history: {},
};

test('the tile renders the remote data: CPU %, RAM %, cores, jobs', () => {
  assert.deepEqual(remoteTile(REMOTE, 'server'),
    { region: 'US', label: 'US server', reachable: true, cpu: 25, ram: 25, cores: 24, jobs: '3 / 2' });
});

test('an unreachable peer is a tile without numbers, never zeros', () => {
  const t = remoteTile({ ...REMOTE, reachable: false, nodes: { nodes: [], cluster: null }, app: null });
  assert.equal(t.reachable, false);
  assert.deepEqual([t.cpu, t.ram, t.cores, t.jobs], [null, null, null, '—']);
});

test('single-region mode: no tile (also while nothing is loaded yet)', () => {
  assert.equal(remoteTile({ ...REMOTE, configured: false, reachable: false, region: null }), null);
  assert.equal(remoteTile(null), null);
  assert.equal(remoteTile(undefined), null);
});

test('Overview: the tile goes to Servers, the remote live-load panel follows the local one, both only with a tile', () => {
  const s = src('sections', 'OverviewSection.tsx');
  assert.match(s, /const tile = remoteTile\(remote\)/);
  assert.match(s, /goto: 'servers' as const/);
  assert.match(s, /\.\.\.\(tile \? \[\{/);
  assert.match(s, /\{tile && <LiveLoadPanel remote=\{tile\.region\.toLowerCase\(\)\} \/>\}/);
  assert.match(s, /t\('regions\.down'\)/);                          // "unreachable", not zeros
});

test('Servers: the peer panel reuses ServersPanel, has a heading per server and shows only in multi-region mode', () => {
  const s = src('sections', 'ServersSection.tsx');
  assert.match(s, /<ServersPanel \/>/);
  assert.match(s, /\{peer && <ServersPanel remote=\{peer\} \/>\}/);
  assert.match(s, /testId="server-heading-local"/);
  assert.match(s, /testId="server-heading-remote"/);
  assert.match(s, /remote\?\.configured/);
  const panel = src('ServersPanel.tsx');
  assert.match(panel, /\/api\/admin\/regions\/remote\/cluster/);
  assert.match(panel, /!remote && /);                                // read-only: no add / revoke on the other server
  assert.equal(panel.split('<LineChart').length - 1, 1);             // one chart definition, reused for both servers
  const live = src('LiveLoadPanel.tsx');
  assert.match(live, /\/api\/admin\/regions\/remote\/cluster\?range=/);
  assert.match(live, /!remote && entry\.row\.isJob/);                // no Stop on the other server's jobs
});

test('EN and zh-CN carry the new strings, one label plus a tooltip each, no caption lines', () => {
  const keys = ['serverName', 'remoteTileTip', 'remoteServersTip', 'remoteLive', 'remoteLiveTip'];
  const en = admin('en').regions;
  const zh = admin('zh-CN').regions;
  for (const k of keys) { assert.ok(en[k], k); assert.ok(zh[k], k); }
  assert.match(en.serverName, /\{region\}/);                         // ICU placeholder (i18next-icu), not {{ }}
  assert.match(zh.serverName, /\{region\}/);
  for (const f of ['useRemoteCluster.ts', join('sections', 'ServersSection.tsx'), join('sections', 'OverviewSection.tsx')]) {
    for (const l of src(f).split(/\r?\n/).filter((x) => /regions\./.test(x))) assert.doesNotMatch(l, /variant="caption"|helperText=/, f);
  }
});
