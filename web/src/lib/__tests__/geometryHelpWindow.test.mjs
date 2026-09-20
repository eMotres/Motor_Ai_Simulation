// node --test — the pure URL-building rule of lib/geometryHelpWindow.ts,
// restated here verbatim (node --test cannot load the TS modules — see
// geometryApplyOutcome.test.mjs for the same repo convention).
//
// The Geometry tab's Help button opens a STATIC picture
// (web/public/help/geometry_parameters.{svg,png}, built once by
// scripts/geometry_help_sheet.py — one picture for every model, not a
// per-machine backend render), so the only logic worth a unit test is "what
// URL does the button open", independent of `window`/DOM (which node does
// not have) — the actual `window.open` + wrapper-HTML call is exercised by
// hand in the browser, not here.
import test from 'node:test';
import assert from 'node:assert/strict';

function geometryHelpUrls(origin) {
  const base = origin.replace(/\/$/, '');
  return {
    svg: `${base}/help/geometry_parameters.svg`,
    png: `${base}/help/geometry_parameters.png`,
  };
}

test('builds both asset URLs under the page origin', () => {
  const urls = geometryHelpUrls('http://localhost:5173');
  assert.equal(urls.svg, 'http://localhost:5173/help/geometry_parameters.svg');
  assert.equal(urls.png, 'http://localhost:5173/help/geometry_parameters.png');
});

test('a trailing slash on the origin is not doubled', () => {
  const urls = geometryHelpUrls('https://emotres.com/');
  assert.equal(urls.svg, 'https://emotres.com/help/geometry_parameters.svg');
  assert.equal(urls.png, 'https://emotres.com/help/geometry_parameters.png');
});

test('works for any origin — a static asset, no API base involved', () => {
  const urls = geometryHelpUrls('http://127.0.0.1:8099');
  assert.equal(urls.svg, 'http://127.0.0.1:8099/help/geometry_parameters.svg');
});
