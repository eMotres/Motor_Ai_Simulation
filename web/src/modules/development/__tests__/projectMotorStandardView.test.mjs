// The project motor page and Configure show ONE standard Simulation output: the results table of the selected point (SummaryTable)
// with the stored field view beside it (owner 2026-11-26).  Wiring is pinned from the sources, as in the neighbouring tests.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');

test('the project motor page renders the standard view for every live variant, not its own table', () => {
  const page = src('../components/DevelopmentPage.tsx');
  assert.doesNotMatch(page, /<SharedComparisonTable/);
  assert.match(page, /<ReadOnlySimulationView key=.*embedded withField projectId=\{selected\.id\} die=\{r\.motor\.die\} config=\{r\.motor\.config\}/);
  assert.match(page, /r\.motor\.available !== false/);                                        // the same availability gate the open buttons used
});

test('Configure of a project motor uses the same component and the same props', () => {
  const cfg = src('../../../components/compare/PointsConfigure.tsx');
  assert.match(cfg, /\{focus && duty && <ReadOnlySimulationView key=.*embedded withField projectId=\{focus\.id\}/);
  assert.match(cfg, /\{!focus && <Box sx=\{\{ display: 'grid'/);                              // the own tiles serve the motors outside a project
});

test('the view stacks table, field and charts in ONE column of the same width, and reuses the standard viewer, kinds and rules', () => {
  const view = src('../../../components/simulation/ReadOnlySimulationView.tsx');
  assert.match(view, /<SummaryTable[\s\S]*\{withField && point && <PointFieldPane[\s\S]*<TransientCharts/);   // table, then the field, then the charts
  assert.doesNotMatch(view, /results-with-field|flex: '1 1 560px'/);                                         // no side-by-side half-width table
  assert.match(src('../../../components/simulation/PointFieldPane.tsx'), /\.\.\.SIM_BLOCK_SX/);
  const pane = src('../../../components/simulation/PointFieldPane.tsx');
  assert.match(pane, /import\('\.\.\/compare\/StoredFieldViewer'\)/);                         // no new viewer
  assert.match(pane, /storedFieldKinds\(current\)/);                                         // a map of another speed is never offered
  assert.match(pane, /developmentApi\.motorField\(/);                                        // the project's own GET route: nothing is solved or written
  assert.doesNotMatch(pane, /method: 'POST'|fetch\(/);
});
