import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

let curves = null;
try { curves = await import('../configuratorPropellerCurves.ts'); } catch { /* old Node */ }
const t = curves ? test : test.skip;
const HERE = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(join(HERE, 'fixtures', 'propeller_series_25c_d40.json'), 'utf8')).tmotor_p12x4;

t('series curves use real rpm bounds and mark modeled points beyond tested data', () => {
  const got = curves.propellerCurveData(fixture, 3250, 22207);
  assert.deepEqual(got.domain, [3250, 22207]);
  assert.ok(got.points.some((p) => p.thrustTested != null));
  assert.ok(got.points.some((p) => p.thrustBeyondTested != null));
  assert.ok(got.points.every((p) => !(p.thrustTested != null && p.thrustBeyondTested != null)));
  assert.ok(got.points.every((p) => !(p.powerTested != null && p.powerBeyondTested != null)));
});

t('unavailable samples remain gaps and disjoint allowed/series ranges produce no chart domain', () => {
  const sparse = { ...fixture, thrust_N: [...fixture.thrust_N], shaft_power_W: [...fixture.shaft_power_W] };
  const index = sparse.rpm.findIndex((r) => r >= 5000);
  sparse.thrust_N[index] = Number.NaN;
  sparse.shaft_power_W[index] = Number.NaN;
  const got = curves.propellerCurveData(sparse, 4000, 6000);
  const gap = got.points.find((p) => p.rpm === sparse.rpm[index]);
  assert.equal(gap.thrustTested, null);
  assert.equal(gap.thrustBeyondTested, null);
  assert.equal(gap.powerTested, null);
  assert.equal(gap.powerBeyondTested, null);
  assert.deepEqual(curves.propellerCurveData(fixture, 41000, 45000), { domain: null, points: [] });
});

t('drag mapping is bounded to the visible allowed RPM range shared by both charts', () => {
  const domain = [3250, 22207];
  assert.equal(curves.rpmAtPlotPointer(0, 300, domain), 3250);
  assert.equal(curves.rpmAtPlotPointer(150, 300, domain), (3250 + 22207) / 2);
  assert.equal(curves.rpmAtPlotPointer(400, 300, domain), 22207);
  assert.equal(curves.rpmAtPlotPointer(120, 0, domain), 3250);
});
