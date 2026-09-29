// node --test — the pure rules behind the RELEASED-context strip
// (lib/releasedContext.releasedOffers, suggestedDieName, diffText,
// dieDefiningSelected), copied verbatim (the repo's convention for node tests:
// `node --test` cannot load the TS modules, so the pure functions under test
// are re-stated here and kept in sync).
//
// What they pin, in the order it matters (2026-09-20, the 12:47 dead end):
//
//   • a released context with an identity diff (poles/segment 7 → 8) offers
//     "Save as NEW die" and "Discard and reload", NOT "save as new
//     configuration" — the machine is a different lamination;
//   • a released context whose live machine IS the die again offers
//     "Save as new configuration of X" too;
//   • the reload offer needs a configuration and a duty (the backend fills
//     them in for a single-config/single-duty die — the owner's file);
//   • an ordinary user (can_write false) and an active context draw nothing;
//   • the suggested die name never repeats the released name (409 on the
//     backend) and carries the live topology;
//   • die-defining selection is the intersection with the served key list.
import test from 'node:test';
import assert from 'node:assert/strict';

// ── lib/releasedContext.ts ───────────────────────────────────────────────────
const DIE_KEY_LABELS = {
  stator_diameter: 'stator Ø',
  num_seg: 'segments',
  num_slots_per_segment: 'slots/segment',
  num_poles_per_segment: 'poles/segment',
};
function dieKeyLabel(key) { return DIE_KEY_LABELS[key] ?? key; }

function diffText(diffs) {
  return (diffs ?? [])
    .map(d => `${d.label ?? dieKeyLabel(d.key)} ${d.die} → ${d.live}`)
    .join('; ');
}

function suggestedDieName(ctx) {
  const rel = String(ctx.released_from ?? '').trim() || 'new die';
  const t = ctx.live_topology ?? null;
  const topo = t && t.slots && t.poles ? `${t.slots}s${t.poles}p` : '';
  const base = rel.replace(/\s+\d+s\d+p$/, '');
  return topo ? `${base} ${topo}` : `${rel} new`;
}

function releasedOffers(ctx) {
  if (!ctx || ctx.active === true) return null;
  const rel = String(ctx.released_from ?? '').trim();
  if (!rel || ctx.can_write !== true) return null;
  const diffs = ctx.die_diffs ?? [];
  const changed = diffText(diffs);
  const liveIsDie = ctx.live_is_die === true;
  const dieExists = ctx.die_exists !== false;
  const cfg = String(ctx.released_config ?? '').trim();
  const duty = String(ctx.released_duty ?? '').trim();

  const line = changed
    ? `⚠ no die active — the machine on screen is not '${rel}' any more (${changed}); your work is NOT lost — save it as a new die`
    : liveIsDie
      ? `⚠ no die active — '${rel}' was released (${ctx.reason ?? 'a whole-machine load'}); the machine on screen is that lamination again`
      : `⚠ no die active — the loaded machine is not a catalog duty (released from '${rel}')`;
  const tip = `Context released from '${rel}'${cfg ? ` / ${cfg}` : ''}${duty ? ` / ${duty}` : ''}`
    + `${ctx.at ? ` at ${String(ctx.at).replace('T', ' ')}` : ''}: `
    + `${ctx.reason ?? 'a whole-machine load outside the catalog'}. `
    + (changed
        ? `A die keeps its diameter and slot/pole topology for life, so a machine with ${changed} is a NEW lamination. `
          + 'Nothing is synced into the catalog until you save it as a new die (the live geometry, its build and the operating point go into it), '
          + 'or discard the live changes and reload the released duty.'
        : 'The machine on screen belongs to no die, so nothing is synced into the catalog. Re-attach it to the released die as it is, '
          + 'save it as a new configuration or a new die, or reload the released duty with ▶.');

  return {
    line, tip,
    saveNewDie: dieExists ? {
      label: changed
        ? `＋ Save as NEW die (copy of ${rel} with the new lamination: ${changed})`
        : `＋ Save as NEW die (copy of ${rel})`,
      initialName: suggestedDieName(ctx),
      hint: 'A new unlocked die from the geometry on screen, one configuration from its build (stack, wire, winding, materials) '
        + 'and one duty at the Simulation tab\'s point; the last run\'s results are recorded into it. The released die is untouched.',
    } : null,
    saveNewConfig: (liveIsDie && dieExists) ? {
      label: `＋ Save as new configuration of ${rel}`,
      die: rel, duty: duty || 'rated',
    } : null,
    reload: (dieExists && cfg && duty) ? {
      label: `↺ Discard live changes and reload ▶ ${rel} / ${cfg} / ${duty}`,
      die: rel, config: cfg, duty,
    } : null,
    reattach: (liveIsDie && dieExists && cfg) ? {
      label: `↩ Re-attach to ${rel} / ${cfg}${duty ? ` / ${duty}` : ''} (keep the machine on screen)`,
      die: rel, config: cfg, duty: duty || null,
    } : null,
  };
}

function dieDefiningSelected(names, dieKeys) {
  const keys = new Set(dieKeys ?? []);
  const out = [];
  for (const n of names) if (keys.has(n)) out.push(n);
  return out;
}

// ── the owner's state at 12:47:08, as /api/family/context now serves it ──────
const OWNER = {
  active: false, can_write: true,
  released_from: 'CIANO14 50 edited', released_config: 'L15', released_duty: 'rated',
  reason: 'live machine is not this die: num_poles_per_segment 7 → 8',
  at: '2026-09-20T12:47:08', die_exists: true,
  die_diffs: [{ key: 'num_poles_per_segment', label: 'poles/segment', die: 7, live: 8 }],
  live_is_die: false,
  live_topology: { stator_diameter: 50, slots: 12, poles: 16, motor_length: 15 },
};

test('the 12:47 state offers "save as NEW die" and "discard & reload", never a dead end', () => {
  const o = releasedOffers(OWNER);
  assert.ok(o);
  assert.match(o.line, /poles\/segment 7 → 8/);
  assert.match(o.line, /NOT lost/);
  assert.ok(o.saveNewDie);
  assert.equal(o.saveNewDie.label,
    '＋ Save as NEW die (copy of CIANO14 50 edited with the new lamination: poles/segment 7 → 8)');
  assert.equal(o.saveNewDie.initialName, 'CIANO14 50 edited 12s16p');
  // a different lamination is NOT a configuration of the released die, and
  // cannot be re-attached to it either
  assert.equal(o.saveNewConfig, null);
  assert.equal(o.reattach, null);
  assert.deepEqual(o.reload, {
    label: '↺ Discard live changes and reload ▶ CIANO14 50 edited / L15 / rated',
    die: 'CIANO14 50 edited', config: 'L15', duty: 'rated',
  });
  assert.match(o.tip, /at 2026-09-20 12:47:08/);
  assert.match(o.tip, /NEW lamination/);
});

test('when the live machine is the die again, "save as new configuration" is offered too', () => {
  const o = releasedOffers({ ...OWNER, die_diffs: [], live_is_die: true });
  assert.ok(o);
  assert.match(o.line, /that lamination again/);
  assert.deepEqual(o.saveNewConfig, { label: '＋ Save as new configuration of CIANO14 50 edited',
                                      die: 'CIANO14 50 edited', duty: 'rated' });
  assert.equal(o.saveNewDie.label, '＋ Save as NEW die (copy of CIANO14 50 edited)');
  assert.ok(o.reload);
  // …and the machine on screen can be RE-ATTACHED without a load
  assert.deepEqual(o.reattach, {
    label: '↩ Re-attach to CIANO14 50 edited / L15 / rated (keep the machine on screen)',
    die: 'CIANO14 50 edited', config: 'L15', duty: 'rated',
  });
  // a configuration alone is enough to re-attach (the duty is optional)
  const o2 = releasedOffers({ ...OWNER, die_diffs: [], live_is_die: true, released_duty: null });
  assert.deepEqual(o2.reattach, {
    label: '↩ Re-attach to CIANO14 50 edited / L15 (keep the machine on screen)',
    die: 'CIANO14 50 edited', config: 'L15', duty: null,
  });
  assert.equal(o2.reload, null);
  // no configuration known → nothing to re-attach to
  assert.equal(releasedOffers({ ...OWNER, die_diffs: [], live_is_die: true,
                                released_config: null }).reattach, null);
});

test('the reload offer needs a configuration AND a duty; the other offers do not', () => {
  const o = releasedOffers({ ...OWNER, released_config: null, released_duty: null });
  assert.ok(o);
  assert.equal(o.reload, null);
  assert.ok(o.saveNewDie);
  const o2 = releasedOffers({ ...OWNER, released_duty: null });
  assert.equal(o2.reload, null);
});

test('a released die that no longer exists offers nothing that needs it', () => {
  const o = releasedOffers({ ...OWNER, die_exists: false, die_diffs: [], live_is_die: null });
  assert.ok(o);
  assert.equal(o.saveNewDie, null);
  assert.equal(o.saveNewConfig, null);
  assert.equal(o.reload, null);
  assert.equal(o.reattach, null);
  assert.match(o.line, /released from 'CIANO14 50 edited'/);
});

test('an ordinary user, an active context and a context with no released die draw nothing', () => {
  assert.equal(releasedOffers({ ...OWNER, can_write: false }), null);
  assert.equal(releasedOffers({ ...OWNER, active: true }), null);
  assert.equal(releasedOffers({ active: false, can_write: true }), null);
  assert.equal(releasedOffers(null), null);
  assert.equal(releasedOffers(undefined), null);
});

test('the suggested die name carries the live topology and never repeats a topology suffix', () => {
  assert.equal(suggestedDieName(OWNER), 'CIANO14 50 edited 12s16p');
  assert.equal(suggestedDieName({ ...OWNER, released_from: 'CIANO14 50 12s14p' }), 'CIANO14 50 12s16p');
  assert.equal(suggestedDieName({ ...OWNER, live_topology: null }), 'CIANO14 50 edited new');
  assert.equal(suggestedDieName({ released_from: '' }), 'new die new');
});

test('diffText names every identity key that moved, in order', () => {
  assert.equal(diffText([{ key: 'stator_diameter', die: 40, live: 50 },
                         { key: 'num_poles_per_segment', label: 'poles/segment', die: 7, live: 8 }]),
               'stator Ø 40 → 50; poles/segment 7 → 8');
  assert.equal(diffText([]), '');
  assert.equal(diffText(null), '');
});

test('die-defining selection is the intersection with the served key list', () => {
  const keys = ['stator_diameter', 'num_seg', 'num_slots_per_segment', 'num_poles_per_segment'];
  assert.deepEqual(dieDefiningSelected(['tooth_width', 'num_poles_per_segment', 'gamma_deg'], keys),
                   ['num_poles_per_segment']);
  assert.deepEqual(dieDefiningSelected(['tooth_width'], keys), []);
  assert.deepEqual(dieDefiningSelected(['stator_diameter'], null), []);
});
