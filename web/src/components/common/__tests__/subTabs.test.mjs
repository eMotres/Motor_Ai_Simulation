// node --test - one sub-menu style (owner 2026-10-30): panels switch their views with the shared SubTabs, which looks
// like the main menu's Tab, and no panel draws a separate segmented-button row for navigation any more.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const src = (p) => readFileSync(join(root, p), 'utf8');

test('SubTabs uses the main menu tab look (size, case) and real tabs semantics (MUI Tabs/Tab)', () => {
  const sub = src('components/common/SubTabs.tsx');
  const app = src('App.tsx');
  assert.match(app, /fontSize: '0\.8rem', textTransform: 'none'/);
  assert.match(sub, /fontSize: '0\.8rem', textTransform: 'none'/);
  assert.match(sub, /<Tabs[\s\S]*variant="scrollable"/);
  assert.match(sub, /<Tab\b/);
});

test('every navigation switch is a SubTabs; the Optimization one sits in the header row after the label', () => {
  const sweep = src('components/sweep/SweepConfigPanel.tsx');
  assert.doesNotMatch(sweep, /ToggleButtonGroup/);
  const header = sweep.indexOf('{/* ── Header ── */}');
  const body = sweep.indexOf('{/* ── Body');
  const tabs = sweep.indexOf('<SubTabs value={algoTab}');
  assert.ok(header > 0 && tabs > header && tabs < body, 'algorithm sub-tabs belong to the header row');
  assert.ok(sweep.indexOf('>Optimize</Typography>') < tabs);
  for (const f of ['components/static3d/Static3DPanel.tsx', 'components/system/SystemPanel.tsx', 'components/materials/MaterialsCatalogPanel.tsx',
    'modules/tests/components/TestsPanel.tsx', 'modules/production/components/ProductionPage.tsx',
    'modules/development/components/DevelopmentPage.tsx', 'components/admin/sections/DirectorySection.tsx']) {
    assert.match(src(f), /<SubTabs\b/, f);
    assert.doesNotMatch(src(f), /<Tabs\b/, `${f} still has its own Tabs`);
  }
});

test('the Simulations group gets a header row with its members as sub-tabs', () => {
  const app = src('App.tsx');
  assert.match(app, /data-testid="simulations-header"/);
  assert.match(app, /<SubTabs value=\{activeTab\} items=\{sim\.members\}/);
});
