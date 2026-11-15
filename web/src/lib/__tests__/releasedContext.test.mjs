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
//   • the offline fallback of the suggested die name is the released die's
//     family base (no topology; the real proposal is the server's);
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

// the offline fallback of the prompt's name: the released die's family base (owner 2026-10-26:
// the real proposal is the server's "<family base> <your suffix> <n>", lib/dieNames.ts)
let familyBase = (name) => String(name ?? '').trim().split(/\s+/)[0] ?? '';
try { familyBase = (await import('../dieNameRule.ts')).familyBase; } catch { /* old Node: approximation */ }
function suggestedDieName(ctx) {
  return familyBase(String(ctx.released_from ?? '')) || 'new die';
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
  const by = String(ctx.by ?? '').trim();
  const when = ctx.at ? String(ctx.at).replace('T', ' ').slice(0, 16) : '';

  const kind = changed ? 'differs'
    : liveIsDie ? (ctx.live_matches_config === true ? 'same' : 'edited')
    : 'foreign';
  const tail = `${by ? ` by ${by}` : ''}${when ? ` at ${when}` : ''}`;
  const vars = { die: rel, config: cfg, changed, by: by ? ` by ${by}` : '', when: when ? ` at ${when}` : '' };

  const line = kind === 'same'
    ? `No die attached: ${rel} was detached${tail}. Nothing to save.`
    : kind === 'edited'
      ? `No die attached: the screen differs from ${rel}${cfg ? ` / ${cfg}` : ''}.`
      : kind === 'differs'
        ? `No die attached: the screen is a new lamination (${changed}).`
        : 'No die attached: the loaded machine is not a catalog duty.';
  const tip = `Context released from '${rel}'${cfg ? ` / ${cfg}` : ''}${duty ? ` / ${duty}` : ''}${tail}: `
    + `${ctx.reason ?? 'a whole-machine load outside the catalog'}. `
    + (kind === 'same'
        ? 'The machine on screen is that configuration unchanged, so reloading it loses nothing.'
        : 'Nothing is synced into the catalog until the machine on screen is saved or the stored duty is reloaded.');

  const canReload = dieExists && !!cfg && !!duty;
  const reload = canReload ? {
    label: kind === 'same' ? `Reload ${cfg}` : `Discard changes and reload ${cfg}`,
    die: rel, config: cfg, duty,
  } : null;

  return {
    kind, vars, line, tip,
    saveNewDie: dieExists && (kind === 'differs' || kind === 'foreign') ? {
      label: 'Save as new die',
      initialName: suggestedDieName(ctx),
      primary: true,
      hint: 'A new unlocked die from the geometry on screen, one configuration from its build (stack, wire, winding, materials) '
        + 'and one duty at the Simulation tab\'s point; the last run\'s results are recorded into it. The released die is untouched.',
    } : null,
    saveNewConfig: (kind === 'edited' && dieExists) ? {
      label: `Save as new configuration of ${rel}`,
      die: rel, duty: duty || 'rated',
    } : null,
    reload,
    reattach: ((kind === 'edited' || (kind === 'same' && !reload)) && dieExists && cfg) ? {
      label: `Re-attach to ${rel} / ${cfg}${duty ? ` / ${duty}` : ''}`,
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


const SAME = { ...OWNER, reason: 'CIANO10 12_40: import Fusion v24 geometry for a new die',
               by: 'owner@example.com', at: '2026-11-16T09:30:12',
               die_diffs: [], live_is_die: true, live_matches_config: true };

test('screen identical to the stored configuration: one short line, only "Reload <config>"', () => {
  const o = releasedOffers(SAME);
  assert.equal(o.kind, 'same');
  assert.equal(o.line, 'No die attached: CIANO14 50 edited was detached by owner@example.com at 2026-11-16 09:30. Nothing to save.');
  assert.ok(o.line.length < 120);
  assert.deepEqual(o.reload, { label: 'Reload L15', die: 'CIANO14 50 edited', config: 'L15', duty: 'rated' });
  // nothing that creates a copy, nothing to re-attach while a reload exists
  assert.equal(o.saveNewDie, null);
  assert.equal(o.saveNewConfig, null);
  assert.equal(o.reattach, null);
  assert.match(o.tip, /import Fusion v24 geometry/);
});

test('identical screen without a stored duty: re-attach is the only button', () => {
  const o = releasedOffers({ ...SAME, released_duty: null });
  assert.equal(o.reload, null);
  assert.equal(o.saveNewDie, null);
  assert.deepEqual(o.reattach, { label: 'Re-attach to CIANO14 50 edited / L15', die: 'CIANO14 50 edited',
                                 config: 'L15', duty: null });
});

test('same lamination but real unsaved differences: re-attach, save as new configuration, discard', () => {
  const o = releasedOffers({ ...SAME, live_matches_config: false });
  assert.equal(o.kind, 'edited');
  assert.match(o.line, /differs from CIANO14 50 edited \/ L15/);
  assert.ok(o.saveNewConfig);
  assert.equal(o.saveNewDie, null);        // same lamination: a new die is not the way
  assert.equal(o.reload.label, 'Discard changes and reload L15');
  // keep editing the same configuration: re-attach (loads nothing)
  assert.equal(o.reattach.label, 'Re-attach to CIANO14 50 edited / L15 / rated');
  // an unknown comparison never claims "nothing to save"
  assert.equal(releasedOffers({ ...SAME, live_matches_config: null }).kind, 'edited');
  assert.equal(releasedOffers({ ...SAME, live_matches_config: undefined }).kind, 'edited');
});

test('the 12:47 state (another lamination) offers "save as new die" as the primary button and a discard', () => {
  const o = releasedOffers(OWNER);
  assert.equal(o.kind, 'differs');
  assert.equal(o.line, 'No die attached: the screen is a new lamination (poles/segment 7 → 8).');
  assert.equal(o.saveNewDie.primary, true);
  assert.equal(o.saveNewDie.initialName, 'CIANO14 50');
  assert.equal(o.saveNewConfig, null);
  assert.equal(o.reattach, null);
  assert.deepEqual(o.reload, { label: 'Discard changes and reload L15', die: 'CIANO14 50 edited',
                               config: 'L15', duty: 'rated' });
});

test('the reload offer needs a configuration AND a duty', () => {
  assert.equal(releasedOffers({ ...OWNER, released_config: null, released_duty: null }).reload, null);
  assert.equal(releasedOffers({ ...OWNER, released_duty: null }).reload, null);
  assert.ok(releasedOffers({ ...OWNER, released_config: null }).saveNewDie);
});

test('a released die that no longer exists offers nothing that needs it', () => {
  const o = releasedOffers({ ...OWNER, die_exists: false, die_diffs: [], live_is_die: null });
  assert.equal(o.kind, 'foreign');
  assert.equal(o.saveNewDie, null);
  assert.equal(o.saveNewConfig, null);
  assert.equal(o.reload, null);
  assert.equal(o.reattach, null);
});

test('an ordinary user, an active context and a context with no released die draw nothing', () => {
  assert.equal(releasedOffers({ ...OWNER, can_write: false }), null);
  assert.equal(releasedOffers({ ...OWNER, active: true }), null);
  assert.equal(releasedOffers({ active: false, can_write: true }), null);
  assert.equal(releasedOffers(null), null);
  assert.equal(releasedOffers(undefined), null);
});

test('the suggested die name is the family base - no topology, no free words', () => {
  assert.equal(suggestedDieName(OWNER), 'CIANO14 50');
  assert.equal(suggestedDieName({ ...OWNER, released_from: 'CIANO14 50 12s14p' }), 'CIANO14 50');
  assert.equal(suggestedDieName({ ...OWNER, live_topology: null }), 'CIANO14 50');
  assert.equal(suggestedDieName({ released_from: '' }), 'new die');
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
