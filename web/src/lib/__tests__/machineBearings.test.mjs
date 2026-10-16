// node --test — the PURE functions of lib/machineBearings.ts, copied verbatim
// (the repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure functions under test are re-stated here and kept in sync
// — see panelSettings.test.mjs, which does the same).
//
// What is worth testing here is not arithmetic; it is the two rules that decide
// which bearing pair a client is looking at:
//
//   1. "no bearings" is a REAL answer and must never come back as an assigned
//      pair with zero loss.  A blank card is not a bearing.
//   2. the server context belongs to the OWNER, so it only counts when it names
//      the machine THIS client has open — otherwise a client's ▶-copy of the
//      40 mm motor would show the vendor's 200 mm spindle bearings, and its
//      no-load loss with them.
import test from 'node:test';
import assert from 'node:assert/strict';

const num = (v, fallback) =>
  (v != null && Number.isFinite(Number(v))) ? Number(v) : fallback;

function normalizeEnd(e) {
  if (!e || typeof e !== 'object') return null;
  const card = String(e.card ?? '').trim();
  if (!card) return null;
  const out = { card };
  const seals = String(e.seals ?? '').trim();
  const grease = String(e.grease ?? '').trim();
  if (seals) out.seals = seals;
  if (grease) out.grease = grease;
  return out;
}

function normalizeBearings(b) {
  if (!b || typeof b !== 'object') return null;
  let A = normalizeEnd(b.A);
  let B = normalizeEnd(b.B);
  if (!A && !B) return null;
  if (!A) A = B ? { ...B } : null;
  if (!B) B = A ? { ...A } : null;
  const lub = b.lubrication === 'oil_air' ? 'oil_air' : 'grease';
  const ts = b.temp_source === 'thermal' ? 'thermal' : 'manual';
  return {
    A, B,
    lubrication: lub,
    preload_n: Math.max(0, num(b.preload_n, 0)),
    temp_source: ts,
    temp_c: b.temp_c == null ? null : num(b.temp_c, 70),
  };
}

function bearingsPayload(b) {
  const n = normalizeBearings(b);
  if (!n) return null;
  return {
    A: n.A, B: n.B, lubrication: n.lubrication, preload_n: n.preload_n,
    temp_source: n.temp_source, temp_c: n.temp_c ?? null,
  };
}

function machineKey(loc, presetId) {
  if (loc?.die && loc?.config) return `fam:${loc.die}/${loc.config}`;
  if (presetId) return `preset:${presetId}`;
  return null;
}

function bearingsChipLabel(b) {
  const n = normalizeBearings(b);
  if (!n) return 'no bearings';
  const a = n.A?.card ?? '';
  const c = n.B?.card ?? '';
  const parts = [];
  parts.push(a && c && a === c ? `2 × ${a}` : [a, c].filter(Boolean).join(' / '));
  parts.push(n.lubrication === 'oil_air' ? 'oil-air' : 'grease');
  if (n.preload_n > 0) parts.push(`${Math.round(n.preload_n)} N preload`);
  return parts.filter(Boolean).join(' · ');
}

function resolveBearings({ ctx, loc, local, treeEntry }) {
  if (ctx?.active && ctx?.can_write === true) {
    return { bearings: normalizeBearings(ctx.bearings), die: ctx.die,
             config: ctx.config, canWrite: true };
  }
  const base = { die: loc?.die ?? null, config: loc?.config ?? null, canWrite: false };
  const mine = normalizeBearings(local);
  if (mine) return { bearings: mine, ...base };
  if (loc?.die && loc?.config) {
    if (ctx?.active && ctx.die === loc.die && ctx.config === loc.config) {
      return { bearings: normalizeBearings(ctx.bearings), ...base };
    }
    return { bearings: normalizeBearings(treeEntry?.bearings), ...base };
  }
  return { bearings: normalizeBearings(ctx?.bearings), ...base };
}

/* ── normalisation ───────────────────────────────────────────────────────── */

const PAIR = {
  A: { card: '71910 CE/HCP4A' }, B: { card: '71910 CE/HCP4A' },
  lubrication: 'oil_air', preload_n: 200, temp_source: 'manual', temp_c: 70,
};

test('a blank card is not a bearing', () => {
  assert.equal(normalizeBearings({ A: { card: '   ' }, B: null }), null);
  assert.equal(normalizeBearings({}), null);
  assert.equal(normalizeBearings(null), null);
  assert.equal(normalizeBearings('61811-2RS1'), null);
});

test('one card named means two of it', () => {
  const n = normalizeBearings({ A: { card: '61811-2RS1' } });
  assert.equal(n.A.card, '61811-2RS1');
  assert.equal(n.B.card, '61811-2RS1');
  // and the copy is independent — editing B must not rewrite A
  n.B.card = 'other';
  assert.equal(n.A.card, '61811-2RS1');
});

test('unknown lubrication and temp source fall back, they do not pass through', () => {
  const n = normalizeBearings({ A: { card: 'x' }, lubrication: 'petrol',
                                temp_source: 'guess' });
  assert.equal(n.lubrication, 'grease');
  assert.equal(n.temp_source, 'manual');
});

test('a negative or unreadable preload becomes zero, never NaN', () => {
  assert.equal(normalizeBearings({ A: { card: 'x' }, preload_n: -50 }).preload_n, 0);
  assert.equal(normalizeBearings({ A: { card: 'x' }, preload_n: 'lots' }).preload_n, 0);
  assert.equal(normalizeBearings({ A: { card: 'x' }, preload_n: 200 }).preload_n, 200);
});

test('empty seal / grease overrides are dropped, real ones survive', () => {
  const n = normalizeBearings({ A: { card: 'x', seals: '', grease: '  ' },
                                B: { card: 'x', grease: 'LGLT_2' } });
  assert.equal('seals' in n.A, false);
  assert.equal('grease' in n.A, false);
  assert.equal(n.B.grease, 'LGLT_2');
});

test('the payload is null when there is nothing to save', () => {
  assert.equal(bearingsPayload({ lubrication: 'grease' }), null);
  assert.deepEqual(bearingsPayload(PAIR), {
    A: { card: '71910 CE/HCP4A' }, B: { card: '71910 CE/HCP4A' },
    lubrication: 'oil_air', preload_n: 200, temp_source: 'manual', temp_c: 70,
  });
});

/* ── the chip line ───────────────────────────────────────────────────────── */

test('the chip says what the machine is built with', () => {
  assert.equal(bearingsChipLabel(PAIR), '2 × 71910 CE/HCP4A · oil-air · 200 N preload');
  assert.equal(bearingsChipLabel({ A: { card: '61811-2RS1' } }),
               '2 × 61811-2RS1 · grease');
  assert.equal(bearingsChipLabel({ A: { card: 'a' }, B: { card: 'b' } }),
               'a / b · grease');
});

test('no bearings says so, and never reads as an assigned pair', () => {
  assert.equal(bearingsChipLabel(null), 'no bearings');
  assert.equal(bearingsChipLabel({ lubrication: 'oil_air', preload_n: 200 }),
               'no bearings');
});

/* ── identity ────────────────────────────────────────────────────────────── */

test('a machine key needs a machine', () => {
  assert.equal(machineKey({ die: 'D', config: 'L40' }, 'p1'), 'fam:D/L40');
  assert.equal(machineKey({ die: 'D' }, 'p1'), 'preset:p1');
  assert.equal(machineKey(null, null), null);
});

/* ── the resolver — the rule that keeps one machine's bearings off another ── */

const OWNED = { active: true, can_write: true, die: 'D', config: 'L40',
                bearings: { A: { card: '618/8-2Z' } } };

test('an owner reads the yaml the context carries', () => {
  const r = resolveBearings({ ctx: OWNED });
  assert.equal(r.canWrite, true);
  assert.equal(r.bearings.A.card, '618/8-2Z');
  assert.equal(r.die, 'D');
});

test("a client's own remembered pair beats everything below it", () => {
  const r = resolveBearings({
    ctx: { active: true, can_write: false, die: 'OTHER', config: 'X',
           bearings: { A: { card: '71912 CE/HCP4A' } } },
    loc: { die: 'D', config: 'L40' },
    local: { A: { card: '61811-2RS1' }, lubrication: 'grease' },
  });
  assert.equal(r.bearings.A.card, '61811-2RS1');
  assert.equal(r.canWrite, false);
  assert.equal(r.die, 'D');
});

test("the OWNER's context is ignored when it names a different machine", () => {
  // This is the whole point: without the die/config comparison a client's
  // ▶-copy of the 40 mm motor would show the vendor's 200 mm spindle bearings.
  const r = resolveBearings({
    ctx: { active: true, can_write: false, die: 'BIG', config: 'L180',
           bearings: { A: { card: '71910 CE/HCP4A' } } },
    loc: { die: 'SMALL', config: 'L12' },
    treeEntry: { bearings: { A: { card: '618/8-2Z' } } },
  });
  assert.equal(r.bearings.A.card, '618/8-2Z');
});

test('the context IS used when it names the same machine', () => {
  const r = resolveBearings({
    ctx: { active: true, can_write: false, die: 'D', config: 'L40',
           bearings: { A: { card: '61814-2RS1' } } },
    loc: { die: 'D', config: 'L40' },
    treeEntry: { bearings: { A: { card: 'WRONG' } } },
  });
  assert.equal(r.bearings.A.card, '61814-2RS1');
});

test('a machine nobody has given bearings resolves to null, not a default pair', () => {
  assert.equal(resolveBearings({ ctx: { active: true, can_write: true, bearings: null } })
    .bearings, null);
  assert.equal(resolveBearings({ ctx: null, loc: null }).bearings, null);
  assert.equal(resolveBearings({
    ctx: { active: true, can_write: false, die: 'D', config: 'L40' },
    loc: { die: 'D', config: 'L40' }, treeEntry: null,
  }).bearings, null);
});
