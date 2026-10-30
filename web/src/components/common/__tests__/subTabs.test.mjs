// node --test - one menu pattern on every top tab (owner 2026-10-30): main menu, the context strip, then ONE header row
// (the panel label + its sub-tabs + its actions).  The sub-tabs are the shared SubTabs, which looks like the main menu's
// Tab; no panel draws its own title row, segmented-button row or inner tab bar for navigation.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const src = (p) => readFileSync(join(root, p), 'utf8');

test('SubTabs uses the main menu tab look (size, case) and real tabs semantics (MUI Tabs/Tab)', () => {
  const sub = src('components/common/SubTabs.tsx');
  assert.match(src('App.tsx'), /fontSize: '0\.8rem', textTransform: 'none'/);
  assert.match(sub, /fontSize: '0\.8rem', textTransform: 'none'/);
  assert.match(sub, /<Tabs[\s\S]*variant="scrollable"/);
  assert.match(sub, /<Tab\b/);
});

test('the app draws ONE header row for every tab, between the strip and the content, and the top bar has no dropdown', () => {
  const app = src('App.tsx');
  const header = app.search(/<PanelHeader\s/);
  assert.equal((app.match(/<PanelHeader\s/g) || []).length, 1);
  assert.ok(app.indexOf('<ActiveFamilyStrip />') < header);
  assert.ok(header < app.indexOf("<Box sx={{ flex: 1, overflow: 'hidden' }}>"));
  assert.match(app, /<SubTabs value=\{activeTab\} items=\{simMembers\}/);                  // Simulations members
  assert.doesNotMatch(app, /aria-haspopup|<Menu\b|<MenuItem\b|▾/);
  assert.match(app, /useRememberedView<string>\('simulations\.lastTab'/);
});

test('panel sections are sub-tabs in the shared header row, never a separate bar', () => {
  const sections = {
    'components/sweep/SweepConfigPanel.tsx': 'sweep',
    'components/static3d/Static3DPanel.tsx': 'static3d',
    'components/materials/MaterialsCatalogPanel.tsx': 'materials',
    'components/geometry/GeometryTab.tsx': 'geometry',
    'modules/production/components/ProductionPage.tsx': 'production',
    'modules/development/components/DevelopmentPage.tsx': 'development',
    'components/admin/AdminNav.tsx': 'admin',
  };
  for (const [f, tab] of Object.entries(sections)) {
    assert.match(src(f), new RegExp(`<HeaderTabs tab="${tab}">`), f);
    assert.doesNotMatch(src(f), /<Tabs\b/, `${f} still has its own Tabs`);
  }
  const sweep = src('components/sweep/SweepConfigPanel.tsx');
  assert.doesNotMatch(sweep, /ToggleButtonGroup/);
  assert.match(sweep, /<HeaderRight tab="sweep">/);
  for (const f of ['components/system/SystemPanel.tsx', 'modules/tests/components/TestsPanel.tsx', 'components/admin/sections/DirectorySection.tsx']) {
    assert.match(src(f), /<SubTabs\b/, f);
    assert.doesNotMatch(src(f), /<Tabs\b/, `${f} still has its own Tabs`);
  }
});

test('panels no longer print their own tab name as a title row; their actions ride in the header row', () => {
  assert.match(src('components/cost/CostPanel.tsx'), /<HeaderRight tab="cost">/);
  assert.doesNotMatch(src('components/cost/CostPanel.tsx'), />Material Cost</);
  assert.match(src('components/catalog/MotorsCatalog.tsx'), /<HeaderRight tab="motors">/);
  assert.match(src('modules/production/components/ProductionListPage.tsx'), /<HeaderRight tab="production">/);
  assert.match(src('modules/development/components/DevelopmentPage.tsx'), /<HeaderRight tab="development">/);
  assert.doesNotMatch(src('components/admin/AdminPanel.tsx'), /tx\('admin'\)/);
});
