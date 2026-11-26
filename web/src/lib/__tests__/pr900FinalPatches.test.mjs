// Execute the components' actual projection/formatter bodies without a browser or JSX runtime.
// Extraction must succeed; numerical and bilingual outputs are asserted, not just source patterns.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes, registerHooks } from 'node:module';
import { anyMarked, basesMixed, basisTag, isMarked, SHAFT_CONVENTION } from '../shaftBasisLabel.ts';
import { pointApply3dK } from '../sweepApply3d.ts';
import { coincidentGroups } from '../coincidentPoints.ts';
registerHooks({
  resolve(specifier, context, nextResolve) {
    try { return nextResolve(specifier, context); }
    catch (err) {
      if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier))
        return nextResolve(`${specifier}.ts`, context);
      throw err;
    }
  },
});
const { coggingOf } = await import('../coggingPp.ts');

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const descent = read('../../components/sweep/DescentPanel.tsx');
const sweep = read('../../components/sweep/SweepStudyPanel.tsx');
const translations = Object.fromEntries(['en', 'zh-CN'].map(lang =>
  [lang, JSON.parse(read(`../../locales/${lang}/simulation.json`))]));
const txFor = lang => key => {
  assert.equal(typeof translations[lang][key], 'string', `${lang}/${key}`);
  return translations[lang][key];
};
function between(source, start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `missing component body: ${start}`);
  return source.slice(a, b);
}
function run(body, env = {}) {
  const js = stripTypeScriptTypes(`function exercise() {\n${body}\n}`, { mode: 'strip' });
  return new Function(...Object.keys(env), js + '\nreturn exercise();')(...Object.values(env));
}
function capture(source, re) {
  const m = source.match(re);
  assert.ok(m, `missing component expression: ${re}`);
  return m[1];
}
const loadLineBasis = run(between(descent, 'const loadLineBasis', 'const RIPPLE_SLIDER_MAX') + 'return loadLineBasis;');
const chartTag = (p, chartMarked) => run(between(descent, '  const chartTag', '  const loadLineDesigns') + 'return chartTag(p);',
  { p, chartMarked, basisTag, loadLineBasis });
const projections = {
  load: capture(descent, /data=\{(arr\.map\(\(p: any\) => \(\{[^\n]+\}\)\))\}/),
  picked: capture(descent, /data=\{(\[\{ td: selectedPt\.td[^\n]+\}\])\}/),
  rated: capture(descent, /data=\{(\[\{ td: rp\.td[^\n]+\}\])\}/),
};

for (const lang of ['en', 'zh-CN']) {
  const tx = txFor(lang);
  test(`Descent actual load-line, picked, rated projections and basis labels (${lang})`, () => {
    for (const marker of [{}, { shaft_conv: 1 }, { shaft_convention: SHAFT_CONVENTION }]) {
      const marked = isMarked(marker);
      const a = { td: 4.35, eff: 89.69, I: 10, T: 8, ...marker };
      const b = { td: 4.55, eff: 87.69, I: 20, T: 10, ...marker };
      const state = { loadLines: { design: [a, b] }, chartMarked: marked,
        loadLineBasis, basisTag, shaftTx: tx };
      const designs = run(between(descent, '  const loadLineDesigns', '  const [maxIters') + 'return loadLineDesigns;', state);
      assert.equal(designs.length, 1);
      assert.equal(designs[0][0], marked ? `design (${tx('shaftBasisCurrent')})` : 'design');
      const rated = run(between(descent, '  const ratedMarkers', '  const st:') + 'return ratedMarkers;', {
        sweepConfig: { ratedTorqueNm: 9 }, ratedTorque: 9, loadLineDesigns: designs,
        LL_PALETTE: ['orange'], loadLineBasis,
      });
      assert.equal(rated.length, 1);
      assert.ok(Math.abs(rated[0].td - 4.45) < 1e-12);
      assert.equal(rated[0].eff, 88.69);
      assert.equal(rated[0].I, 15);
      const outputs = [
        run('return ' + projections.load, { arr: [a], loadLineBasis })[0],
        run('return ' + projections.picked, { selectedPt: a, loadLineBasis })[0],
        run('return ' + projections.rated, { rp: rated[0] })[0],
      ];
      for (const row of outputs) {
        assert.equal(isMarked(row), marked);
        assert.equal(chartTag(row, marked), marked ? 'current' : null);
        assert.equal(chartTag(row, true), marked ? 'current' : 'earlier');
      }
      assert.deepEqual(outputs[0], { td: 4.35, eff: 89.69, I: 10, z: 1, shaft_conv: marked ? 1 : undefined });
      assert.deepEqual(outputs[1], { td: 4.35, eff: 89.69, z: 9, shaft_conv: marked ? 1 : undefined });
    }
    // Mixed source is split before interpolation; never create a rated point across conventions.
    const loadLines = { mixed: [{ T: 8, shaft_conv: 1 }, { T: 10 }] };
    const designs = run(between(descent, '  const loadLineDesigns', '  const [maxIters') + 'return loadLineDesigns;',
      { loadLines, chartMarked: true, loadLineBasis, shaftTx: tx });
    assert.equal(designs.length, 2);
    const rated = run(between(descent, '  const ratedMarkers', '  const st:') + 'return ratedMarkers;', {
      sweepConfig: { ratedTorqueNm: 9 }, ratedTorque: 9, loadLineDesigns: designs, LL_PALETTE: ['orange'], loadLineBasis,
    });
    assert.deepEqual(rated, []);
  });

  test(`Descent history tooltip is scoped to displayed history and preserves old props (${lang})`, () => {
    const old = { iter: 1, efficiency: .9, torque_per_mass: 5 };
    const marked = { iter: 2, efficiency: 870 / 970, torque_per_mass: 4.35, shaft_convention: SHAFT_CONVENTION };
    const body = between(descent, '  const chartData =', '  // Descent direction') + 'return {chartData, historyTooltipProps};';
    for (const history of [[old, old], [marked, marked], [old, marked], []]) {
      const { chartData, historyTooltipProps: props } = run(body, { history, loadLineBasis, anyMarked, basisTag, shaftTx: tx });
      if (!history.some(isMarked)) assert.deepEqual(props, {});
      else for (const row of chartData) {
        assert.equal(props.labelFormatter(row.iter, [{ payload: row }]),
          `${row.iter} — ${tx(isMarked(row) ? 'shaftBasisCurrent' : 'shaftBasisEarlier')}`);
        assert.equal(row.eff_pct, row.efficiency * 100);
      }
    }
    assert.ok(between(descent, '{/* Metric trajectories:', '{/*').includes('{...historyTooltipProps}'));
  });
}

const scale3d = run(between(sweep, 'const scale3d', 'const GCOL') + 'return scale3d;');
const seriesBody = between(sweep, '  const series = useMemo', '  // Which k_end') + 'return series;';
const chartMixedBody = between(sweep, '  const chartMixed =', '  const nFeasible') + 'return chartMixed;';
const buildSeries = points => run(seriesBody, {
  result: { points }, connectBy: 'current_a', sentOps: [], k3dChart: null,
  useMemo: fn => fn(), readLS: () => 100 * 60 / (2 * Math.PI),
  kOfPoint: p => pointApply3dK(p, null, true), scale3d, coggingOf, coincidentGroups,
  _fmtVar: String,
});
const point = { feasible: true, current_a: 10, gamma_deg: 0, op_index: 0, geom_id: 0,
  T_em_Nm: 10, torque_per_mass_Nm_kg: 4.35, mass_total_kg: 2,
  P_mech_W: 870, efficiency: 870 / 970, P_loss_total_W: 100, T_ripple_pct: 1 };

for (const lang of ['en', 'zh-CN']) {
  const tx = txFor(lang);
  test(`Sweep actual chart rows and tooltip word, including filtered-out markers (${lang})`, () => {
    const marked = { ...point, op_index: 1, shaft_convention: SHAFT_CONVENTION };
    for (const points of [[point], [marked], [point, marked], [point, { ...marked, feasible: false }],
      [point, { ...marked, torque_per_mass_Nm_kg: null }]]) {
      const series = buildSeries(points), rows = series.flatMap(s => s.rows);
      const mixed = run(chartMixedBody, { series, basesMixed, isMarked });
      assert.equal(mixed, rows.some(isMarked) && rows.some(r => !isMarked(r)));
      for (const d of rows) {
        const tag = run(between(sweep, '  const tag = basisTag', '  const ov =') + 'return tag;', { d, mixed, basisTag, isMarked });
        const wordExpr = capture(sweep, /\{tag && <div>\{([^\n]+)\}<\/div>\}/);
        const word = tag ? run('return ' + wordExpr, { tag, tx }) : null;
        assert.equal(word, isMarked(d) ? tx('shaftBasisCurrent') : mixed ? tx('shaftBasisEarlier') : null);
        assert.equal(d.P_mech_W, 870);
        assert.equal(d.x, 4.35);
        assert.equal(d.y, 870 / 970 * 100);
      }
    }
    assert.ok(sweep.includes('<SweepTooltip mixed={chartMixed} />'));
  });

  test(`Sweep actual table columns label every row with numeric export and old columns intact (${lang})`, () => {
    const body = between(sweep, '  const rows = useMemo', '  const [sortId') + 'return {rows, cols};';
    const buildTable = points => run(body, { points, omega: 100, k3d: null, vdcF: 1 / Math.sqrt(3),
      useMemo: fn => fn(), kOfPoint: p => pointApply3dK(p, null, true), scale3d, coggingOf, tx });
    const oldIds = ['T', 'P', 'eff', 'Vdc', 'ripple', 'mass', 'td', 'pd', 'ploss', 'core', 'cuDc', 'cuAc', 'solid'];
    for (const points of [[point], [{ ...point, shaft_convention: SHAFT_CONVENTION }],
      [point, { ...point, shaft_convention: SHAFT_CONVENTION }]]) {
      const { rows, cols } = buildTable(points);
      const marked = points.some(isMarked), basis = cols.find(c => c.id === 'shaftConv');
      assert.deepEqual(cols.map(c => c.id), marked ? ['shaftConv', ...oldIds] : oldIds);
      for (const row of rows) {
        if (marked) {
          assert.equal(basis.fmt(basis.get(row)), tx(row.sc === 1 ? 'shaftBasisCurrent' : 'shaftBasisEarlier'));
          assert.equal(basis.get(row), row.sc); // exportRows uses get, not fmt
        }
        assert.equal(cols.find(c => c.id === 'P').fmt(row.P), '0.87');
        assert.equal(cols.find(c => c.id === 'eff').fmt(row.eff), '89.69');
        assert.equal(cols.find(c => c.id === 'T').fmt(row.T), '10.00');
      }
    }
  });
}
