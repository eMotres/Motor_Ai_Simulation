// node --test — the pure rules of the per-duty DUTY CYCLE block
// (lib/dutySettings.effectiveDutyCycle, .dutyCycleFromForm and .dutyCycleChip),
// copied verbatim (the repo's convention for node tests: `node --test` cannot
// load the TS modules, so the pure functions under test are re-stated here and
// kept in sync).
//
// What they pin, in the order it matters:
//
//   • the two LAYERS: ▶ writes the yaml's block as the snapshot, the editor's
//     un-saved edit outranks it, and an EMPTY overlay is the user retiring the
//     cycle rather than "nothing said" — the same precedence the operating
//     point has had since 2026-09-01, one level down;
//   • a field the chosen KIND does not use is not written into the block: an
//     `ed_pct` beside an S2 pull is a number the solver never reads and the
//     reader of the yaml has to guess about;
//   • a blank rest duty is an explicit `null` — the machine standing
//     UNPOWERED, which is a real segment of a real cycle — and never an absent
//     key, which would read as "it keeps running";
//   • the catalog CHIP says what the machine does in three words, and says
//     nothing at all for a duty that has no cycle (every duty saved before
//     2026-09-14) rather than claiming an S1 nobody wrote.
import test from 'node:test';
import assert from 'node:assert/strict';

// ── lib/dutySettings.effectiveDutyCycle ──────────────────────────────────────
function effectiveDutyCycle(entry) {
  if (!entry) return null;
  if (entry.block !== undefined && entry.block !== null) {
    return Object.keys(entry.block).length ? entry.block : null;
  }
  if (entry.snap && Object.keys(entry.snap).length) return entry.snap;
  return null;
}

// ── lib/dutySettings.dutyCycleFromForm ───────────────────────────────────────
function dutyCycleFromForm(f) {
  const num = (v) => {
    const t = (v ?? '').trim();
    if (t === '') return null;
    const x = Number(t);
    return Number.isFinite(x) ? x : null;
  };
  const kind = ['S1', 'S2', 'S3', 'segments'].includes(f.kind) ? f.kind : 'S1';
  const out = { kind };
  if (kind !== 'segments' && (f.duty ?? '').trim()) out.duty = f.duty;
  if (kind === 'S2') out.t_on_s = num(f.tOn);
  if (kind === 'S3') {
    out.ed_pct = num(f.edPct);
    out.cycle_s = num(f.cycleS);
    out.rest_duty = (f.restDuty ?? '').trim() ? f.restDuty : null;
  }
  if (kind === 'segments') {
    out.segments = (f.segments ?? [])
      .filter((s) => num(s.t_s) !== null)
      .map((s) => ({ duty: s.duty.trim() ? s.duty : null, t_s: num(s.t_s) }));
  }
  const t0 = num(f.tStartC);
  if (t0 !== null) out.t_start_c = t0;
  const nmax = num(f.nCyclesMax);
  if (nmax !== null) out.n_cycles_max = Math.max(1, Math.round(nmax));
  if ((f.calibrationDuty ?? '').trim()) out.calibration_duty = f.calibrationDuty;
  return out;
}

// ── lib/dutySettings.dutyCycleChip ───────────────────────────────────────────
function dutyCycleChip(block) {
  if (!block || !Object.keys(block).length) return null;
  const kind = String(block.kind ?? 'S1');
  const n = (v) => {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') {
      return null;
    }
    const x = Number(v);
    return Number.isFinite(x) ? x : null;
  };
  const g = (x) => String(Math.round(x * 100) / 100);
  if (kind === 'S2') {
    const t = n(block.t_on_s);
    return t != null ? `S2 ${g(t)} s` : 'S2';
  }
  if (kind === 'S3') {
    const ed = n(block.ed_pct), cyc = n(block.cycle_s);
    if (ed == null) return cyc != null ? `S3 ${g(cyc)} s · ED found` : 'S3';
    const found = block.found === true ? ' found' : '';
    return cyc != null ? `S3 ED ${g(ed)} %${found} · ${g(cyc)} s`
                       : `S3 ED ${g(ed)} %${found}`;
  }
  if (kind === 'segments') {
    const segs = Array.isArray(block.segments) ? block.segments : [];
    const tot = segs.reduce((a, s) => a + (n(s?.t_s) ?? 0), 0);
    return segs.length ? `${segs.length} segments · ${g(tot)} s` : 'segments';
  }
  return 'S1';
}

/* ── the two layers ───────────────────────────────────────────────────────── */

const SNAP = { kind: 'S1' };
const EDIT = { kind: 'S3', ed_pct: 25, cycle_s: 60, rest_duty: null };

test('a duty nobody has touched has no cycle at all', () => {
  // NOT an S1: every duty saved before 2026-09-14 carries no block, and reading
  // one as "continuous" would put a claim in the yaml's mouth.
  assert.equal(effectiveDutyCycle(null), null);
  assert.equal(effectiveDutyCycle(undefined), null);
  assert.equal(effectiveDutyCycle({}), null);
  assert.equal(effectiveDutyCycle({ snap: null }), null);
  assert.equal(effectiveDutyCycle({ snap: {} }), null);
});

test('▶ alone shows the block the yaml carries', () => {
  assert.deepEqual(effectiveDutyCycle({ snap: SNAP }), SNAP);
});

test('the un-saved edit outranks what ▶ restored', () => {
  assert.deepEqual(effectiveDutyCycle({ snap: SNAP, block: EDIT }), EDIT);
});

test('an EMPTY overlay is the user retiring the cycle, not silence', () => {
  // The only way to say "this duty has no cycle any more" — the catalog reads
  // an empty block the same way (routes/family: entry.pop("duty_cycle")).
  assert.equal(effectiveDutyCycle({ snap: SNAP, block: {} }), null);
  // …while a null overlay is genuine silence and falls through to the snapshot.
  assert.deepEqual(effectiveDutyCycle({ snap: SNAP, block: null }), SNAP);
});

/* ── the editor's fields → the stored block ───────────────────────────────── */

test('S1 writes the kind and nothing else', () => {
  assert.deepEqual(dutyCycleFromForm({ kind: 'S1' }), { kind: 'S1' });
});

test('an unknown kind falls back to the continuous point', () => {
  assert.equal(dutyCycleFromForm({ kind: 'S9' }).kind, 'S1');
  assert.equal(dutyCycleFromForm({ kind: '' }).kind, 'S1');
});

test('S2 writes the pull and carries no ED', () => {
  const b = dutyCycleFromForm({ kind: 'S2', tOn: '25', edPct: '25',
                                cycleS: '60', restDuty: 'rated' });
  assert.deepEqual(b, { kind: 'S2', t_on_s: 25 });
  assert.ok(!('ed_pct' in b));
  assert.ok(!('cycle_s' in b));
  assert.ok(!('rest_duty' in b));
});

test('S3 writes ED, the cycle time and an explicit rest duty', () => {
  assert.deepEqual(
    dutyCycleFromForm({ kind: 'S3', edPct: '25', cycleS: '60',
                        restDuty: 'rated 120С wire 80C NdFeB', tOn: '9' }),
    { kind: 'S3', ed_pct: 25, cycle_s: 60,
      rest_duty: 'rated 120С wire 80C NdFeB' });
});

test('a blank rest duty is null — the machine standing unpowered', () => {
  const b = dutyCycleFromForm({ kind: 'S3', edPct: '25', cycleS: '60' });
  assert.ok('rest_duty' in b, 'an absent key would read as "it keeps running"');
  assert.equal(b.rest_duty, null);
});

test('a blank number is null and not zero — the backend refuses it by name', () => {
  // `Number('') === 0` would turn "not filled in yet" into a cycle of nothing,
  // and an ED of 0 % is a machine that never runs.
  assert.equal(dutyCycleFromForm({ kind: 'S2', tOn: '' }).t_on_s, null);
  assert.equal(dutyCycleFromForm({ kind: 'S3', edPct: '  ', cycleS: 'abc' }).ed_pct, null);
  assert.equal(dutyCycleFromForm({ kind: 'S3', edPct: '25', cycleS: 'abc' }).cycle_s, null);
});

test('segments drop the rows with no duration and keep the unpowered ones', () => {
  const b = dutyCycleFromForm({ kind: 'segments', segments: [
    { duty: 'peak', t_s: '2' }, { duty: '', t_s: '8' },
    { duty: 'rated', t_s: '' },
  ] });
  assert.deepEqual(b.segments, [
    { duty: 'peak', t_s: 2 }, { duty: null, t_s: 8 },
  ]);
  // a segment list names its duties per row, so the top-level one is not written
  assert.ok(!('duty' in b));
});

test('the start temperature and the cycle cap ride only when they are stated', () => {
  const bare = dutyCycleFromForm({ kind: 'S3', edPct: '25', cycleS: '60' });
  assert.ok(!('t_start_c' in bare));
  assert.ok(!('n_cycles_max' in bare));
  assert.ok(!('calibration_duty' in bare));
  const full = dutyCycleFromForm({ kind: 'S3', edPct: '25', cycleS: '60',
                                   tStartC: '25', nCyclesMax: '80.4',
                                   calibrationDuty: 'rated' });
  assert.equal(full.t_start_c, 25);
  assert.equal(full.n_cycles_max, 80);
  assert.equal(full.calibration_duty, 'rated');
  // a STATED 0 °C start is a cold room somebody has, not "not stated"
  assert.equal(dutyCycleFromForm({ kind: 'S1', tStartC: '0' }).t_start_c, 0);
});

/* ── the catalog chip ─────────────────────────────────────────────────────── */

test('the chip says what the machine does in three words', () => {
  assert.equal(dutyCycleChip({ kind: 'S1' }), 'S1');
  assert.equal(dutyCycleChip({ kind: 'S2', t_on_s: 25 }), 'S2 25 s');
  assert.equal(dutyCycleChip({ kind: 'S3', ed_pct: 25, cycle_s: 60 }),
               'S3 ED 25 % · 60 s');
  assert.equal(dutyCycleChip({ kind: 'segments',
                               segments: [{ t_s: 2 }, { t_s: 8 }] }),
               '2 segments · 10 s');
});

test('a duty with no cycle gets no chip', () => {
  assert.equal(dutyCycleChip(null), null);
  assert.equal(dutyCycleChip(undefined), null);
  assert.equal(dutyCycleChip({}), null);
});

test('the chip survives a half-written block instead of printing NaN', () => {
  assert.equal(dutyCycleChip({ kind: 'S2' }), 'S2');
  assert.equal(dutyCycleChip({ kind: 'S3' }), 'S3');
  assert.equal(dutyCycleChip({ kind: 'S3', ed_pct: 25 }), 'S3 ED 25 %');
  assert.equal(dutyCycleChip({ kind: 'segments' }), 'segments');
});

test('the chip does not print a wall of decimals', () => {
  assert.equal(dutyCycleChip({ kind: 'S2', t_on_s: 2.5 }), 'S2 2.5 s');
  assert.equal(dutyCycleChip({ kind: 'S3', ed_pct: 33.333333, cycle_s: 60 }),
               'S3 ED 33.33 % · 60 s');
});

test('the editor → block → chip round trip reads back what was typed', () => {
  const b = dutyCycleFromForm({ kind: 'S3', edPct: '25', cycleS: '60',
                                restDuty: 'rated' });
  assert.equal(dutyCycleChip(b), 'S3 ED 25 % · 60 s');
  assert.deepEqual(effectiveDutyCycle({ snap: { kind: 'S1' }, block: b }), b);
});

/* ── an ED nobody wrote (the 2026-09-15 reframe) ───────────────────────────── */

test('a blank ED is the tool being asked to FIND one, never a 0 %', () => {
  // `Number(null)` is 0 and `Number('')` is 0: the chip has to say what the
  // block MEANS, and an S3 that states no ratio means "find the allowable one"
  const blank = dutyCycleFromForm({ kind: 'S3', edPct: '', cycleS: '60' });
  assert.equal(blank.ed_pct, null);
  assert.equal(dutyCycleChip(blank), 'S3 60 s · ED found');
  assert.equal(dutyCycleChip({ kind: 'S3', ed_pct: null, cycle_s: 60 }),
               'S3 60 s · ED found');
  assert.equal(dutyCycleChip({ kind: 'S3', ed_pct: '' }), 'S3');
  // …and a ratio the TOOL found is flagged as found, not as a hand-picked one
  assert.equal(dutyCycleChip({ kind: 'S3', ed_pct: 21.61, cycle_s: 60,
                               found: true }),
               'S3 ED 21.61 % found · 60 s');
  // a typed ratio is still a typed ratio
  assert.equal(dutyCycleChip({ kind: 'S3', ed_pct: 25, cycle_s: 60 }),
               'S3 ED 25 % · 60 s');
});
