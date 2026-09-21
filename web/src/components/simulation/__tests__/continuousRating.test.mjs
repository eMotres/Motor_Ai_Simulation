// node --test — the THIRD `solve_to` option (owner 2026-09-21: *«давай сделаем
// кнопку, или лучше добавим ещё один элемент в меню»*), beside `steady` and
// `limits`: the largest current the machine may hold FOR EVER at this duty's
// own saved cooling (S1).
//
// The repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure functions under test are re-stated here verbatim and
// kept in sync — see couplingLine.test.mjs / timeToLimit.test.mjs, which do
// the same for the same reason.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// ── verbatim from coupledApi.ts ───────────────────────────────────────────
function continuousRatingLine(c) {
  const r = c?.continuous_rating;
  if (!r) return null;
  if (r.trustworthy === false) {
    const why = r.notes?.find(n => n.startsWith('THE 2-D')
                                  || n.startsWith('CONTRADICTS'))
      ?? r.note ?? 'the map could not be iterated';
    return `S1: NOT A RATING — ${why}`;
  }
  if (!r.ok || r.feasible === false || r.I_cont_A_rms == null) {
    return `S1: ${r.refusal?.error ?? r.note ?? 'no continuous rating under '
      + 'this cooling'}`;
  }
  const parts = [`${r.I_cont_A_rms.toFixed(1)} A rms`];
  if (r.limiting_part) {
    const lim = r.limits_c?.[r.limiting_part];
    parts.push(`limited by ${r.limiting_part}`
      + (lim == null ? '' : ` ${Math.round(lim)} °C`));
  }
  return `S1: ${parts.join(' · ')}`;
}

function continuousRatingTip(c) {
  const r = c?.continuous_rating;
  if (!r) return '';
  const rows = (r.parts ?? []).map(p => {
    const q = p.quantity_c != null ? `${p.quantity_c.toFixed(1)} °C` : '—';
    const lim = p.limit_c != null ? ` / ${p.limit_c} °C` : '';
    return `· ${p.quantity ?? p.part}: ${q}${lim}`;
  });
  return [
    r.headline ?? '',
    r.cooling_label ? `Cooling: ${r.cooling_label}.` : '',
    'Largest current the machine holds for ever at this cooling: torque '
    + 'scaled linearly with current, iron and magnet losses held at the '
    + 'solved point.',
    ...rows,
    r.trustworthy === false
      ? (r.notes?.find(n => n.startsWith('THE 2-D')) ?? '') : '',
  ].filter(Boolean).join('\n');
}

// ── fixtures ──────────────────────────────────────────────────────────────
const RATING = {
  ok: true, feasible: true, I_cont_A_rms: 34.36, I_cont_A_peak: 48.59,
  s: 0.54, limiting_part: 'magnet',
  limits_c: { winding: 200, magnet: 149.7 },
  temperatures_c: { winding: 103.0, magnet: 149.7 },
  parts: [
    { part: 'winding', quantity: 'the winding hot spot', quantity_c: 103.0,
      limit_c: 200 },
    { part: 'magnet', quantity: 'the hottest magnet element',
      quantity_c: 149.7, limit_c: 149.7 },
  ],
  power: { T_em_Nm: 1.175, P_shaft_W: 1230, eta_shaft: 0.933 },
  cooling_label: 'forced air 40 m/s + bore air 10 m/s, 30 °C',
  headline: '34.4 A rms continuously, 1230 W at the shaft — the magnet sits '
            + 'on 149.7 °C',
};

test('the line: only the current and the part it is limited by', () => {
  // Owner addendum, 2026-09-21: *«не пиши уже мощность и момент — его и так
  // видно»* — no torque, no power: the tiles already show the machine's
  // numbers, and the S1 torque is a linear estimate anyway.  Both stay on
  // the STORED block (RATING.power below) for the API/CLI and the tooltip.
  assert.equal(
    continuousRatingLine({ continuous_rating: RATING }),
    'S1: 34.4 A rms · limited by magnet 150 °C');
});

test('nothing to say when the answer was not asked for', () => {
  assert.equal(continuousRatingLine(undefined), null);
  assert.equal(continuousRatingLine(null), null);
  assert.equal(continuousRatingLine({}), null);
});

test('a refused search still says something, never goes silent', () => {
  const refused = { continuous_rating: {
    ok: false, feasible: false,
    refusal: { error: 'this cooling cannot hold even the losses that do not '
                      + 'come from the current', error_code: 'infeasible' },
  } };
  assert.equal(continuousRatingLine(refused),
    'S1: this cooling cannot hold even the losses that do not come from the '
    + 'current');
});

test('a non-monotone map is flagged NOT A RATING, never quoted as a current', () => {
  const bad = { continuous_rating: { ok: true, feasible: true,
    I_cont_A_rms: 72.1, trustworthy: false,
    notes: ['THE 2-D THERMAL SOLVE IS NOT MONOTONE under this cooling'] } };
  assert.equal(continuousRatingLine(bad),
    'S1: NOT A RATING — THE 2-D THERMAL SOLVE IS NOT MONOTONE under this '
    + 'cooling');
});

// ── the production defect, 2026-09-21 ───────────────────────────────────────
// The consistency guard (backend `_cr_consistency_guard`) flags a rating that
// contradicts the loop's own time_to_limit verdict; this line must surface
// THAT reason, not a hard-coded one.
test('a contradiction with the loop is flagged and named', () => {
  const contra = { continuous_rating: { ok: true, feasible: true,
    I_cont_A_rms: 64.9, trustworthy: false, s: 1.0195,
    notes: ['CONTRADICTS THE LOOP\'S OWN VERDICT: time_to_limit says this '
           + 'point is OVER a limit (winding), yet the continuous rating '
           + 'came out at or above the duty\'s own current (s=1.020) — a '
           + 'point that reaches its limit cannot hold MORE current for '
           + 'ever'] } };
  const line = continuousRatingLine(contra);
  assert.ok(line.startsWith('S1: NOT A RATING — CONTRADICTS'));
  assert.ok(line.includes('cannot hold MORE current for ever'));
});

test('a re-solve failure falls back to the block\'s own note', () => {
  const failed = { continuous_rating: { ok: true, feasible: true,
    I_cont_A_rms: 64.9, trustworthy: false,
    note: 'the fixed-point re-solve did not converge within the map-pass '
         + 'budget, so this number is not a settled rating', notes: [] } };
  assert.equal(continuousRatingLine(failed),
    'S1: NOT A RATING — the fixed-point re-solve did not converge within '
    + 'the map-pass budget, so this number is not a settled rating');
});

test('a feasible rating with no power on the block still shows the current', () => {
  const bare = { continuous_rating: { ok: true, feasible: true,
    I_cont_A_rms: 13.37, limiting_part: 'magnet',
    limits_c: { magnet: 149.9 } } };
  assert.equal(continuousRatingLine(bare),
    'S1: 13.4 A rms · limited by magnet 150 °C');
});

test('the tooltip carries the headline, the cooling and every judged part', () => {
  const tip = continuousRatingTip({ continuous_rating: RATING });
  assert.ok(tip.includes(RATING.headline));
  assert.ok(tip.includes('Cooling: forced air 40 m/s + bore air 10 m/s, 30 °C.'));
  assert.ok(tip.includes('· the winding hot spot: 103.0 °C / 200 °C'));
  assert.ok(tip.includes('· the hottest magnet element: 149.7 °C / 149.7 °C'));
  assert.ok(tip.includes('torque'));
});

test('the tooltip is empty when there is no rating', () => {
  assert.equal(continuousRatingTip(undefined), '');
  assert.equal(continuousRatingTip({}), '');
});

// ── the selector: three options, the third one names the model ─────────────
test('the panel selector offers "continuous rating (S1)" as a third option', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(
    join(here, '..', '..', 'simulation', 'SimulationPanel.tsx'), 'utf8');
  assert.ok(src.includes('<MenuItem value="steady">steady state</MenuItem>'));
  assert.ok(src.includes('<MenuItem value="limits">time to the limits</MenuItem>'));
  assert.ok(src.includes(
    '<MenuItem value="continuous">continuous rating (S1)</MenuItem>'));
  // The HelpTip states the model in words a reader may act on, not just a name.
  const tipStart = src.indexOf('Continuous rating (S1): does the same');
  assert.ok(tipStart > -1, 'the HelpTip has no continuous-rating paragraph');
  const tip = src.slice(tipStart, tipStart + 400);
  assert.ok(tip.includes('largest current'));
  assert.ok(tip.includes('linearly with'));
});

// ── the result line rides the same card as the limit line ──────────────────
test('the summary card reads continuous_rating off the coupling block, not a new prop', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(
    join(here, '..', '..', 'simulation', 'SummaryTable.tsx'), 'utf8');
  assert.ok(src.includes('continuousRatingLine(s.coupling)'));
  assert.ok(src.includes('continuousRatingTip(s.coupling)'));
  assert.ok(src.includes("label=\"Continuous rating\""));
});
