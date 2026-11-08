/** Admin · two regions (docs/regions/DESIGN.md): the region column / filter helpers, the Servers -> Regions rows, and the
 *  EN / zh-CN parity of the new strings. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { normRegion, regionLabel, matchesRegion, regionOf, projectRegion, serverRows } from '../regions.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const LOCALES = join(HERE, '..', '..', '..', 'locales');
const admin = (lng) => JSON.parse(readFileSync(join(LOCALES, lng, 'admin.json'), 'utf8'));

test('an account without a region is EU; labels and the filter', () => {
  assert.equal(normRegion(undefined), 'eu');
  assert.equal(normRegion(' US '), 'us');
  assert.equal(normRegion('mars'), 'eu');
  assert.equal(regionLabel('us'), 'US');
  assert.equal(regionLabel(null), 'EU');
  assert.ok(matchesRegion('us', 'all') && matchesRegion(undefined, 'eu') && !matchesRegion('us', 'eu'));
});

test('rows owned by an e-mail take the account region; a project takes its owner\'s', () => {
  const map = { 'a@x.com': 'us', 'b@x.com': 'eu' };
  assert.equal(regionOf('A@x.com ', map), 'us');
  assert.equal(regionOf('ghost@x.com', map), 'eu');
  assert.equal(regionOf(null, null), 'eu');
  assert.equal(projectRegion([{ email: 'b@x.com', role: 'engineer' }, { email: 'a@x.com', role: 'owner' }], map), 'us');
  assert.equal(projectRegion([], map), 'eu');
});

test('single-region mode: one row, this server', () => {
  const rows = serverRows({
    region: 'eu', mode: 'single', peer: null,
    local: { region: 'eu', ok: true, version: '0.9.1', commit: '1c37b56b2f', load: { cores: 16, load1: 4, load_pct: 25 },
      queue: { running: 1, queued: 2 }, disk: { free_gb: 812.4, used_pct: 41.2 } },
  });
  assert.deepEqual(rows, [{ region: 'EU', self: true, state: 'ok', version: '0.9.1 · 1c37b56b', load: '25 %', queue: '1 / 2', disk: '812 GB (41 %)' }]);
  assert.deepEqual(serverRows(null), []);
});

test('an unreachable peer is a row with dashes, never an error', () => {
  const rows = serverRows({ region: 'eu', mode: 'multi', local: { region: 'eu', ok: true }, peer: { region: 'us', ok: false, reachable: false } });
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[1], { region: 'US', self: false, state: 'down', version: '—', load: '—', queue: '—', disk: '—' });
  assert.equal(rows[0].load, '—');                                  // unknown load (e.g. no getloadavg) is a dash
});

test('EN and zh-CN carry the same regions keys', () => {
  const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`])).sort();
  const en = admin('en').regions;
  const zh = admin('zh-CN').regions;
  assert.ok(en && zh);
  assert.deepEqual(keys(zh), keys(en));
  for (const k of ['title', 'region', 'allRegions', 'homeRegion', 'remoteTip']) assert.ok(en[k] && zh[k], k);
  assert.match(zh.remoteTip, /\{\{region\}\}/);
});

test('the Servers section mounts the Regions card; the Users table has the region column and filter', () => {
  const servers = readFileSync(join(HERE, '..', 'sections', 'ServersSection.tsx'), 'utf8');
  assert.match(servers, /<RegionsCard \/>/);
  const users = readFileSync(join(HERE, '..', 'sections', 'UsersSection.tsx'), 'utf8');
  assert.match(users, /data-testid="region-filter"/);
  assert.match(users, /data-testid="region-cell"/);
  assert.match(users, /\/api\/admin\/users\/\$\{encodeURIComponent\(email\)\}\/region/);
  for (const f of ['RegionsCard.tsx', join('sections', 'UsersSection.tsx'), join('sections', 'ProjectsSection.tsx'), 'VerificationPanel.tsx', 'UserDrawer.tsx']) {
    const src = readFileSync(join(HERE, '..', f), 'utf8');
    const added = src.split(/\r?\n/).filter((l) => /regions\./.test(l));
    for (const l of added) assert.doesNotMatch(l, /variant="caption"|helperText=/, f);   // one label + tooltip, no caption lines
  }
});
