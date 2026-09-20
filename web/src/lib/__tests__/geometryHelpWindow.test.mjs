// node --test — the pure URL-building rule of lib/geometryHelpWindow.ts,
// restated here verbatim (node --test cannot load the TS modules — see
// geometryApplyOutcome.test.mjs for the same repo convention).
//
// The Geometry tab's Help button opens STATIC pictures
// (web/public/help/geometry_parameters{,_radii}.{svg,png}, built once by
// scripts/geometry_help_sheet.py — one pair of pictures for every model, not
// a per-machine backend render), so the only logic worth a unit test is
// "what URLs does the button open, for which tabs", independent of
// `window`/DOM (which node does not have) — the actual `window.open` +
// wrapper-HTML + tab-switching is exercised by hand in the browser, not here.
import test from 'node:test';
import assert from 'node:assert/strict';

function geometryHelpTabs(origin) {
  const base = origin.replace(/\/$/, '');
  return [
    { id: 'sector', label: 'Sector',
      svg: `${base}/help/geometry_parameters.svg`,
      png: `${base}/help/geometry_parameters.png` },
    { id: 'radii', label: 'Radii',
      svg: `${base}/help/geometry_parameters_radii.svg`,
      png: `${base}/help/geometry_parameters_radii.png` },
  ];
}

test('builds both tabs, each with both asset URLs, under the page origin', () => {
  const tabs = geometryHelpTabs('http://localhost:5173');
  assert.equal(tabs.length, 2);
  assert.deepEqual(tabs.map((t) => t.id), ['sector', 'radii']);
  assert.equal(tabs[0].svg, 'http://localhost:5173/help/geometry_parameters.svg');
  assert.equal(tabs[0].png, 'http://localhost:5173/help/geometry_parameters.png');
  assert.equal(tabs[1].svg, 'http://localhost:5173/help/geometry_parameters_radii.svg');
  assert.equal(tabs[1].png, 'http://localhost:5173/help/geometry_parameters_radii.png');
});

test('a trailing slash on the origin is not doubled', () => {
  const tabs = geometryHelpTabs('https://emotres.com/');
  assert.equal(tabs[0].svg, 'https://emotres.com/help/geometry_parameters.svg');
  assert.equal(tabs[1].png, 'https://emotres.com/help/geometry_parameters_radii.png');
});

test('works for any origin — a static asset, no API base involved', () => {
  const tabs = geometryHelpTabs('http://127.0.0.1:8099');
  assert.equal(tabs[0].svg, 'http://127.0.0.1:8099/help/geometry_parameters.svg');
  assert.equal(tabs[1].svg, 'http://127.0.0.1:8099/help/geometry_parameters_radii.svg');
});

test('the two tabs never collide on a URL', () => {
  const tabs = geometryHelpTabs('http://localhost:5173');
  const urls = tabs.flatMap((t) => [t.svg, t.png]);
  assert.equal(new Set(urls).size, urls.length);
});
