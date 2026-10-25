// One display system for motors: the points-limited decision, the unified open, the stored thermal / mechanical tiles,
// the Motors-tab project highlight and the read-only map helpers (synthetic data only).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  configureShow, dutyToOpen, mechTiles, partSafetyFactors, pickPointDuty, pointDuties, provenanceLine, retentionVerdict,
  storedFieldKinds, thermalTiles,
} from '../pointsLimited.ts';
import { highlightIndex, isConfigHighlighted, isDieHighlighted, motorKey, pickFocusProject } from '../projectFocus.ts';
import { outlineLoops, parametersLine, partBoundaryEdges } from '../storedFieldPayload.ts';

const duty = (name, extra = {}) => ({ duty: name, has_result: true, operating_point: {}, comparison: {}, ...extra });
const view = (mode, duties, extra = {}) => ({ die: 'D', config: 'c1', configure_available: mode === 'full', configure_duties: [], mode, duties, ...extra });

// ── the mode decision ────────────────────────────────────────────────────────

test('points-limited only on a clear "points" answer without a passport; everything else keeps the full Configure', () => {
  assert.equal(configureShow(view('points', [duty('a')])), 'points-limited');
  assert.equal(configureShow(view('full', [duty('a')])), 'full');
  assert.equal(configureShow(view('empty', [])), 'full');
  assert.equal(configureShow(null), 'full');
  assert.equal(configureShow(undefined), 'full');
  // a passport that exists wins even if the server said points (never replace the full path on a contradiction)
  assert.equal(configureShow({ ...view('points', [duty('a')]), configure_available: true }), 'full');
});

test('the mode switch stands on the loaded duty when it has a point, keeps the user choice, else the first point', () => {
  const v = view('points', [duty('rated'), duty('peak'), { duty: 'none', has_result: false }]);
  assert.deepEqual(pointDuties(v).map((d) => d.duty), ['rated', 'peak']);
  assert.equal(pickPointDuty(v, 'peak'), 'peak');
  assert.equal(pickPointDuty(v, 'none'), 'rated');            // the loaded duty has no point
  assert.equal(pickPointDuty(v, 'peak', 'rated'), 'rated');   // the user's own switch wins while it is valid
  assert.equal(pickPointDuty(v, 'peak', 'gone'), 'peak');
  assert.equal(pickPointDuty(view('points', []), 'x'), null);
  assert.equal(pickPointDuty(null, 'x'), null);
});

// ── the unified open ─────────────────────────────────────────────────────────

test('a project motor click loads a duty exactly like the Motors tab would: asked, passport duty, first result, default, first duty', () => {
  assert.equal(dutyToOpen({ configure_duty: 'cd', default_duty: 'dd', duties: ['x'], results: [] }, 'asked'), 'asked');
  assert.equal(dutyToOpen({ configure_duty: 'cd', default_duty: 'dd', duties: ['x'], results: [] }), 'cd');
  assert.equal(dutyToOpen({ configure_duty: null, default_duty: 'dd', duties: ['x'], results: [{ duty: 'r1', has_result: false }, { duty: 'r2', has_result: true }] }), 'r2');
  assert.equal(dutyToOpen({ configure_duty: null, default_duty: 'dd', duties: ['x'], results: [] }), 'dd');
  assert.equal(dutyToOpen({ configure_duty: null, default_duty: null, duties: [{ name: 'first' }, 'second'], results: [] }), 'first');
  assert.equal(dutyToOpen({ configure_duty: null, default_duty: null, duties: [], results: [] }), null);
  assert.equal(dutyToOpen({ configure_duty: ' ', default_duty: '', duties: undefined, results: undefined }), null);
});

// ── thermal / mechanical tiles + provenance ──────────────────────────────────

test('thermal tiles: the coupled settled values first, the map components otherwise, S1 / S2 only when stored', () => {
  const t = {
    housing_c: 64,
    components_c: { winding: { max: 120 }, magnet: { max: 75 } },
    coupled: { coil_temp_c: 118, magnet_temp_c: 72, bearing_temp_c: 55 },
    s1: { I_cont_A_rms: 41.5, limiting_part: 'winding' },
    s2: { time_to_limit_s: 160, limiting_part: 'winding' },
  };
  const byId = Object.fromEntries(thermalTiles(t).map((x) => [x.id, x.value]));
  assert.deepEqual(byId, { winding: 118, magnet: 72, housing: 64, bearing: 55, s1: 41.5, s2: 160 });
  const noLoop = Object.fromEntries(thermalTiles({ components_c: { winding: { max: 120 } } }).map((x) => [x.id, x.value]));
  assert.deepEqual(noLoop, { winding: 120, magnet: null, housing: null });         // missing stays null, never 0
  assert.deepEqual(thermalTiles(null), []);
});

test('mechanical tiles, retention verdict and the safety factors per part (smallest first)', () => {
  const m = { rotor_stress: {
    sf_min: 3.4, max_displacement_um: 12.5, proof_rpm: 23000, rotor_od_growth_um: 9.1,
    retention: { status: 'pass' },
    parts: { rotor_core: { safety_factor: 5.2, material: 'steel' }, sleeve: { safety_factor: 3.4, material: 'GFRP' }, magnet: {} },
  } };
  const byId = Object.fromEntries(mechTiles(m).map((x) => [x.id, x.value]));
  assert.deepEqual(byId, { sfMin: 3.4, displacement: 12.5, proofRpm: 23000, odGrowth: 9.1 });
  assert.equal(retentionVerdict(m), 'pass');
  assert.equal(retentionVerdict({ rotor_stress: { retention: { status: 'fail' } } }), 'fail');
  assert.equal(retentionVerdict({ rotor_stress: { retention: { message: 'x' } } }), 'unknown');
  assert.equal(retentionVerdict(null), 'unknown');
  assert.deepEqual(partSafetyFactors(m).map((p) => p.part), ['sleeve', 'rotor_core']);   // the part with no SF is not listed
  assert.deepEqual(mechTiles(null), []);
  assert.deepEqual(mechTiles({ modes: {} }), []);
});

test('provenance line names the date, the solver and the mesh; missing parts are left out', () => {
  assert.equal(provenanceLine({ computed_at: '2026-10-26T09:15:30', code_version: 'abcdef0123456789', app_version: '0.1.9', mesh_size_mm: 1.5, n_triangles: 12000 }),
    '2026-10-26 09:15 · abcdef01 v0.1.9 · 1.5 mm, 12,000 tri');
  assert.equal(provenanceLine({ recorded_at: '2026-10-26T09:15:30' }), '2026-10-26 09:15');
  assert.equal(provenanceLine({}), '');
  assert.equal(provenanceLine(null), '');
});

test('stored field kinds follow the viewer order and only the stored ones', () => {
  assert.deepEqual(storedFieldKinds({ fields: { rotor_stress: {}, em: {} } }), ['em', 'rotor_stress']);
  assert.deepEqual(storedFieldKinds({ fields: {} }), []);
  assert.deepEqual(storedFieldKinds(null), []);
});

// ── the Motors tab highlight ─────────────────────────────────────────────────

test('project highlight: configurations and dies of the selected project, alias or canonical name, case-insensitive', () => {
  const idx = highlightIndex([{ die: 'Alias', canonical_die: 'Canon by x', config: 'opt1' }, { die: 'Other', config: 'A' }]);
  assert.equal(isConfigHighlighted(idx, 'alias', 'OPT1'), true);
  assert.equal(isConfigHighlighted(idx, 'Canon by x', 'opt1'), true);
  assert.equal(isConfigHighlighted(idx, 'Alias', 'opt2'), false);             // same die, another configuration
  assert.equal(isDieHighlighted(idx, 'ALIAS'), true);
  assert.equal(isDieHighlighted(idx, 'Third'), false);
  assert.equal(motorKey(' D ', 'C'), motorKey('d', 'c'));
  const empty = highlightIndex(null);
  assert.equal(isDieHighlighted(empty, 'Alias'), false);
});

test('another project highlights its own motors; a project the caller no longer sees is not trusted', () => {
  const projects = [{ id: 'p1', title: 'One' }, { id: 'p2', title: 'Two' }];
  assert.deepEqual(pickFocusProject(projects, { id: 'p2', title: null }), { id: 'p2', title: 'Two' });
  assert.equal(pickFocusProject(projects, { id: 'gone', title: 'Old' }), null);
  assert.equal(pickFocusProject(projects, null), null);
  const a = highlightIndex([{ die: 'D1', config: 'c' }]);
  const b = highlightIndex([{ die: 'D2', config: 'c' }]);
  assert.equal(isDieHighlighted(a, 'D1'), true);  assert.equal(isDieHighlighted(a, 'D2'), false);
  assert.equal(isDieHighlighted(b, 'D2'), true);  assert.equal(isDieHighlighted(b, 'D1'), false);
});

test('wiring: the Motors tab has the selector and FamilyCatalog marks rows from the highlight store (no list filtering)', () => {
  const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
  assert.match(src('../../../components/catalog/MotorsCatalog.tsx'), /<ProjectHighlightSelect \/>/);
  const fam = src('../../../components/catalog/FamilyCatalog.tsx');
  assert.match(fam, /isConfigHighlighted\(projectIndex, die\.name, b\.config\)/);
  assert.match(fam, /isDieHighlighted\(projectIndex, die\.name\)/);
  const sel = src('../../../components/catalog/ProjectHighlightSelect.tsx');
  assert.match(sel, /developmentApi\.motors\(chosen\.id, true\)/);                // through the project route's own gates
  assert.match(src('../../../lib/openProjectMotor.ts'), /useProjectFocusStore\.getState\(\)\.set/);   // opening a motor sets the focus
});

// ── the read-only maps ───────────────────────────────────────────────────────

test('part boundary edges: the mesh border and every edge between two parts, each once', () => {
  // two triangles sharing the diagonal; same part -> only the 4 border edges, different parts -> the diagonal too
  const tris = [[0, 1, 2], [0, 2, 3]];
  assert.equal(partBoundaryEdges(tris, [1, 1], 4).length, 4);
  const split = partBoundaryEdges(tris, [1, 2], 4);
  assert.equal(split.length, 5);
  assert.ok(split.some(([a, b]) => (a === 0 && b === 2) || (a === 2 && b === 0)));
  const loops = outlineLoops([[0, 0], [10, 0], [10, 10], [0, 10]], tris, [1, 2], 2);
  assert.equal(loops.length, 5);
  assert.ok(loops.every((l) => l.length === 2));
  assert.ok(loops.flat().some(([x, y]) => x === 20 && y === 20));               // scaled
});

test('parameters line: operating point, mesh, solver, date - only what is known', () => {
  assert.equal(parametersLine({ rpm: 3000.4, currentA: 41.55, gammaDeg: 12, provenance: 'p' }), '3000 rpm · 41.5 A · 12.0° · p');
  assert.equal(parametersLine({ rpm: null, provenance: 'p' }), 'p');
  assert.equal(parametersLine({}), '');
});

test('wiring: a click on a Development motor row opens it like the Motors tab; the action cell keeps its own buttons', () => {
  const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
  const page = src('../components/DevelopmentPage.tsx');
  assert.match(page, /onRowClick=\{\(r\) => \{ if \(!r\.history\) void openProjectMotor\(\{ id: selected\.id, title: selected\.title \}, r\.motor, r\.result\.duty\)/);
  assert.match(page, /renderRowEnd=\{\(r\) => <Box component="td" onClick=\{\(e\) => e\.stopPropagation\(\)\}/);   // buttons do not also trigger the row
  const table = src('../../../components/compare/SharedComparisonTable.tsx');
  assert.match(table, /onClick=\{onRowClick \? \(\) => onRowClick\(row\) : undefined\}/);
});
