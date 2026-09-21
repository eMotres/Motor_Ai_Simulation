/**
 * The cooling correlations AND the request builder, tested without a browser.
 *
 * `airH` and `liqH` are the only PHYSICS this front end computes for itself
 * (everything else is the solver's), and they moved file on 2026-09-07 —
 * `simulation/CoolingControls` → `thermal/api` — while the Configure tab kept
 * reading them.  A move is exactly when a silent sign error or a lost clamp
 * slips in, so the shapes they promise are pinned here.
 *
 * The functions are re-implemented in this file rather than imported: the module
 * they live in is TypeScript and pulls in `import.meta.env`, which `node --test`
 * cannot load.  The bodies below are copied VERBATIM from `thermal/api.ts`; what
 * is pinned here is therefore the SHAPE the correlations must keep — the floor,
 * the clamps, the 8 L/min anchor, the q^0.8 law, monotonicity — so a rewrite of
 * either function that changes any of those has to change this file too, and
 * changing it is the moment someone has to justify the new number.
 *
 * `getCoolingPayload` is NOT tested: it reads localStorage, which does not exist
 * in node, and wrapping it to inject a store would test the wrapper.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copies of the shipped correlations ─────────────────────────── */

function airH(v, D) {
  const nu = 1.56e-5, ka = 0.0263, Pr = 0.707;
  if (!(v > 0) || !(D > 0)) return 7;
  const Re = v * D / nu;
  const Nu = 0.3 + (0.62 * Math.sqrt(Re) * Math.cbrt(Pr)) / Math.pow(1 + Math.pow(0.4 / Pr, 2 / 3), 0.25)
    * Math.pow(1 + Math.pow(Re / 282000, 5 / 8), 4 / 5);
  return Math.max(Nu * ka / D, 7);
}

const liqH = (q) => Math.min(8000, Math.max(600, 1800 * Math.pow(Math.max(q, 0.1) / 8, 0.8)));

/* ── air ─────────────────────────────────────────────────────────────────── */

test('airH: still air is the natural-convection floor, not zero', () => {
  // A stopped fan must not read as "no cooling at all" — a housing in still air
  // still loses heat, and a 0 would make every temperature above it infinite.
  assert.equal(airH(0, 0.15), 7);
  assert.equal(airH(-3, 0.15), 7);
});

test('airH: a degenerate diameter falls back to the floor', () => {
  assert.equal(airH(10, 0), 7);
  assert.equal(airH(10, -0.15), 7);
});

test('airH rises monotonically with blow speed', () => {
  const D = 0.15;
  const speeds = [0, 1, 2, 5, 10, 20, 30, 60];
  const h = speeds.map((v) => airH(v, D));
  for (let i = 1; i < h.length; i++) {
    assert.ok(h[i] >= h[i - 1],
      `h fell from ${h[i - 1]} to ${h[i]} between ${speeds[i - 1]} and ${speeds[i]} m/s`);
  }
  // …and it is forced convection well above the floor by the time anything is
  // actually blowing.
  assert.ok(h[speeds.indexOf(10)] > 20, `10 m/s gave only ${h[speeds.indexOf(10)]}`);
});

test('airH stays in the physical range for a real housing', () => {
  // 25…250 W/m²K is the band a fan-cooled cylinder lives in; anything outside
  // it means the correlation has been broken, not that the motor got better.
  for (const v of [2, 5, 10, 20, 30]) {
    const h = airH(v, 0.15);
    assert.ok(h > 10 && h < 400, `${v} m/s gave ${h} W/m²K`);
  }
});

test('airH: a bigger cylinder cools worse at the same speed', () => {
  // Nu ~ Re^0.5 against a 1/D, so h falls with diameter — the reason a 200 mm
  // machine is harder to air-cool than an 80 mm one at the same wind.
  assert.ok(airH(10, 0.20) < airH(10, 0.08));
});

/* ── liquid ──────────────────────────────────────────────────────────────── */

test('liqH is monotone in flow', () => {
  const q = [0, 0.5, 1, 2, 4, 8, 16, 40, 200];
  const h = q.map(liqH);
  for (let i = 1; i < h.length; i++) {
    assert.ok(h[i] >= h[i - 1], `h fell from ${h[i - 1]} to ${h[i]} between ${q[i - 1]} and ${q[i]} L/min`);
  }
});

test('liqH is clamped to what a real jacket can produce', () => {
  assert.equal(liqH(0), 600);          // a dead pump is still a wetted wall
  assert.equal(liqH(-5), 600);
  assert.equal(liqH(1e6), 8000);       // no pump buys an infinite film
});

test('liqH is anchored at 1800 W/m²K for 8 L/min', () => {
  // The anchor is the whole calibration: q^0.8 through one measured point.
  assert.ok(Math.abs(liqH(8) - 1800) < 1e-9);
});

test('liqH follows a q^0.8 law between the clamps', () => {
  // Doubling the flow multiplies h by 2^0.8 ≈ 1.741 — Dittus-Boelter's shape.
  const ratio = liqH(16) / liqH(8);
  assert.ok(Math.abs(ratio - Math.pow(2, 0.8)) < 1e-9, `ratio was ${ratio}`);
});

test('liquid beats air by an order of magnitude, which is why it exists', () => {
  assert.ok(liqH(8) > 10 * airH(10, 0.15));
});

/* ═══════════════════════════════════════════════════════════════════════════
 * The request builder — which fields each mode actually sends
 *
 * Added 2026-09-07, when the cooling grew a SECOND surface (the rotor bore) and
 * lost the outlet temperature as an input (it is a result of the flow now).
 * `coolingFields` / `coolingIssue` are exported from `stores/thermalStore` and
 * copied VERBATIM below, for the same reason the correlations above are: the
 * module they live in is TypeScript and reaches `import.meta.env` through the
 * api layer, which `node --test` cannot load.  What is pinned is the CONTRACT —
 * a liquid jacket must not ship an `air_speed_mps` the solver would key its
 * cache on and never use, a `fluid_temp_out_c` must not be shipped at all, and
 * a loop with no pump must be refused here rather than by the backend's 422.
 * ═══════════════════════════════════════════════════════════════════════════ */

const num = (v, def) => {
  const t = (v ?? '').trim();
  const n = Number(t);
  return t !== '' && Number.isFinite(n) ? n : def;
};

function coolingFields(s) {
  const ambient = num(s.ambientT, 40);
  const liquid = s.coolMode === 'liquid';
  const boreLiquid = s.boreMode === 'liquid';
  const shaftMm = Math.max(0, num(s.shaftExtMm, 0));
  const robot = s.coolMode === 'robotics';
  const endFaces = s.endFaces === 'none' ? 'none' : 'still';
  const mountG = robot ? Math.max(0, num(s.mountG, 0)) : 0;
  const mountTyped = (s.mountT ?? '').trim() !== '';
  return {
    cooling_mode: s.coolMode,
    ambient_temp: ambient,
    h_conv: s.coolMode === 'manual' ? num(s.hConv, 50) : undefined,
    air_speed_mps: s.coolMode === 'air' ? num(s.airSpeed, 0) : undefined,
    fluid: liquid ? s.fluid : undefined,
    fluid_temp_in_c: liquid ? num(s.tIn, ambient) : undefined,
    flow_lpm: liquid ? num(s.flowLpm, 8) : undefined,
    bore_mode: s.boreMode,
    bore_air_speed_mps: s.boreMode === 'air' ? num(s.boreAirSpeed, 0) : undefined,
    bore_fluid: boreLiquid ? s.boreFluid : undefined,
    bore_fluid_temp_in_c: boreLiquid ? num(s.boreTIn, ambient) : undefined,
    bore_flow_lpm: boreLiquid ? num(s.boreFlowLpm, 4) : undefined,
    shaft_ext_length_mm: shaftMm > 0 ? shaftMm : undefined,
    shaft_ext_sides: shaftMm > 0
      ? (num(s.shaftExtSides, 2) === 1 ? 1 : 2) : undefined,
    frame: s.frame === 'open' ? 'open' : undefined,
    // The wash speed is ALWAYS 0 now (owner 2026-09-21): the panel no longer
    // has its own "wash m/s" input, so an open frame always tells the backend
    // to use the housing's own outer-surface air speed. `s.openAirSpeed` is
    // deliberately not read here — a value left over from before today must
    // not ride back onto the wire.
    open_air_speed_mps: s.frame === 'open' ? 0 : undefined,
    emissivity: robot ? Math.min(Math.max(num(s.emissivity, 0.9), 0), 1)
                      : undefined,
    end_faces: robot ? endFaces : undefined,
    end_face_sides: (robot && endFaces !== 'none')
      ? (num(s.endFaceSides, 2) === 1 ? 1 : 2) : undefined,
    mount_g_w_per_k: mountG > 0 ? mountG : undefined,
    mount_temp_c: (mountG > 0 && mountTyped) ? num(s.mountT, ambient) : undefined,
  };
}

function coolingIssue(s) {
  if (s.coolMode === 'liquid' && !(num(s.flowLpm, 0) > 0))
    return 'coolant flow must be greater than 0 L/min';
  if (s.coolMode === 'manual' && !(num(s.hConv, 0) > 0))
    return 'h must be greater than 0 W/m²K';
  if (s.boreMode === 'liquid' && !(num(s.boreFlowLpm, 0) > 0))
    return 'bore coolant flow must be greater than 0 L/min';
  if (s.boreMode === 'still' && s.coolMode !== 'robotics')
    return ('a still (unventilated) bore belongs to the robotics mode — it '
            + 'radiates out of the two ends at the machine’s emissivity, and '
            + 'that input only exists there');
  if (s.coolMode === 'robotics') {
    const eps = num(s.emissivity, 0.9);
    if (!(eps >= 0 && eps <= 1)) return 'emissivity must be between 0 and 1';
  }
  const mountG = s.coolMode === 'robotics' ? num(s.mountG, 0) : 0;
  if (mountG < 0) return 'the mount conductance cannot be negative';
  if (s.coolMode === 'none' && s.boreMode === 'none' && !(mountG > 0))
    return 'no cooled surface and no mount conductance — the heat has nowhere to leave';
  return null;
}

/** The store's defaults, as the panel holds them (all strings). */
const DEFAULTS = {
  coolMode: 'air', ambientT: '40', airSpeed: '10', fluid: 'water',
  tIn: '40', flowLpm: '8', hConv: '50',
  boreMode: 'none', boreAirSpeed: '10', boreFluid: 'water',
  boreTIn: '40', boreFlowLpm: '4',
  shaftExtMm: '0', shaftExtSides: '2',
  frame: 'housed', openAirSpeed: '0',
  emissivity: '0.9', mountG: '2', mountT: '', endFaces: 'still',
  endFaceSides: '2',
};
const inputs = (over = {}) => ({ ...DEFAULTS, ...over });

/** What actually goes on the wire: the fetch layer drops undefined values, so
 *  a key with `undefined` is a key that is NOT sent. */
const sent = (r) => Object.fromEntries(Object.entries(r).filter(([, v]) => v !== undefined));
const keys = (r) => Object.keys(sent(r)).sort();

test('the outlet temperature is never an input, in any mode', () => {
  // The whole point of the 2026-09-07 change: how hot the coolant comes back is
  // what the machine DOES to it, so shipping it as a boundary condition would
  // be solving a jacket nobody can build.
  for (const coolMode of ['air', 'liquid', 'manual', 'none']) {
    for (const boreMode of ['none', 'air', 'liquid']) {
      const r = coolingFields(inputs({ coolMode, boreMode }));
      assert.ok(!('fluid_temp_out_c' in sent(r)),
        `${coolMode}/${boreMode} shipped an outlet temperature`);
      assert.ok(!('bore_fluid_temp_out_c' in sent(r)),
        `${coolMode}/${boreMode} shipped a bore outlet temperature`);
    }
  }
});

test('air on the outer surface sends the speed and nothing liquid', () => {
  assert.deepEqual(keys(coolingFields(inputs({ coolMode: 'air' }))),
    ['air_speed_mps', 'ambient_temp', 'bore_mode', 'cooling_mode'].sort());
});

test('a liquid jacket sends coolant, inlet and flow — and no air speed', () => {
  const r = coolingFields(inputs({ coolMode: 'liquid', tIn: '35', flowLpm: '12' }));
  assert.deepEqual(keys(r),
    ['ambient_temp', 'bore_mode', 'cooling_mode', 'fluid', 'fluid_temp_in_c',
     'flow_lpm'].sort());
  assert.equal(r.fluid_temp_in_c, 35);
  assert.equal(r.flow_lpm, 12);
});

test('manual h sends the coefficient alone', () => {
  const r = coolingFields(inputs({ coolMode: 'manual', hConv: '250' }));
  assert.deepEqual(keys(r), ['ambient_temp', 'bore_mode', 'cooling_mode', 'h_conv'].sort());
  assert.equal(r.h_conv, 250);
});

test('an uncooled outer surface sends no cooling numbers at all', () => {
  const r = coolingFields(inputs({ coolMode: 'none', boreMode: 'air' }));
  assert.deepEqual(keys(r),
    ['ambient_temp', 'bore_air_speed_mps', 'bore_mode', 'cooling_mode'].sort());
  assert.equal(r.cooling_mode, 'none');
});

test('the bore is off by default, and off means one field', () => {
  const r = coolingFields(inputs());
  assert.equal(r.bore_mode, 'none');
  assert.ok(!('bore_air_speed_mps' in sent(r)));
  assert.ok(!('bore_fluid' in sent(r)));
  assert.ok(!('bore_flow_lpm' in sent(r)));
});

test('a liquid bore sends its own coolant, inlet and pump', () => {
  const r = coolingFields(inputs({
    coolMode: 'liquid', boreMode: 'liquid', boreFluid: 'oil',
    boreTIn: '30', boreFlowLpm: '2.5',
  }));
  assert.equal(r.bore_fluid, 'oil');
  assert.equal(r.bore_fluid_temp_in_c, 30);
  assert.equal(r.bore_flow_lpm, 2.5);
  // …and the two loops stay independent: the outer one is untouched by it.
  assert.equal(r.fluid, 'water');
  assert.equal(r.flow_lpm, 8);
});

/* ── the shaft outside the housing ─────────────────────────────────────────
   User 2026-09-07: "торцы и лобовые части — только для вала, всё остальное
   вращается внутри мотора".  The rotor's end faces and the end windings turn in
   a closed housing and are deliberately not modelled; the shaft stubs are. */

test('the shaft path is OFF by default, and off means neither field is sent', () => {
  // A `shaft_ext_sides` beside a stub of zero length is a parameter the solver
  // keys its cache on and never uses — the same failure an `air_speed_mps`
  // beside a water jacket would be.
  const r = coolingFields(inputs());
  assert.ok(!('shaft_ext_length_mm' in sent(r)));
  assert.ok(!('shaft_ext_sides' in sent(r)));
});

test('an exposed shaft sends its length and its side count', () => {
  const r = coolingFields(inputs({ shaftExtMm: '120', shaftExtSides: '1' }));
  assert.equal(r.shaft_ext_length_mm, 120);
  assert.equal(r.shaft_ext_sides, 1);
});

test('the shaft DIAMETER is never sent — it comes from the geometry', () => {
  // A second place to type a shaft diameter is a second place for it to
  // disagree with the CAD, and the backend derives it from the shaft tube's OD.
  for (const mm of ['0', '50', '250']) {
    assert.ok(!('shaft_ext_diameter_mm' in sent(coolingFields(inputs({ shaftExtMm: mm })))),
      `${mm} mm shipped a diameter`);
  }
});

test('a blank or negative stub is off, not a negative heat path', () => {
  for (const bad of ['', '   ', '-30', 'abc']) {
    const r = sent(coolingFields(inputs({ shaftExtMm: bad })));
    assert.ok(!('shaft_ext_length_mm' in r), `${JSON.stringify(bad)} was sent`);
  }
});

test('the side count is 1 or 2 and nothing else', () => {
  // The backend refuses anything else by name; the panel must not make it ask.
  assert.equal(coolingFields(inputs({ shaftExtMm: '80', shaftExtSides: '1' })).shaft_ext_sides, 1);
  for (const s of ['2', '3', '0', '', 'x']) {
    assert.equal(coolingFields(inputs({ shaftExtMm: '80', shaftExtSides: s })).shaft_ext_sides, 2,
      `sides ${JSON.stringify(s)}`);
  }
});

test('the shaft path is independent of how the bore is cooled', () => {
  // They are two different decisions about the same rotor — a pump through the
  // shaft, and how much of the shaft is in the room's air — and neither may
  // switch the other off.
  for (const boreMode of ['none', 'air', 'liquid']) {
    const r = coolingFields(inputs({ boreMode, shaftExtMm: '100' }));
    assert.equal(r.shaft_ext_length_mm, 100, boreMode);
    assert.equal(r.bore_mode, boreMode);
  }
});

/* ── how the machine is BUILT ──────────────────────────────────────────────
   User 2026-09-09, on the 40 mm CIANO14: "нет корпуса" — no housing, the tooth
   blocks with their coils between two end plates on standoff pins, the end
   turns and the axial channels between neighbouring coils in the propeller
   wash.  `housed` is every other machine and must stay byte-identical on the
   wire, because the solver keys its cache on what it is sent. */

test('a housed machine sends no frame at all', () => {
  // A `frame: "housed"` on the wire is the same failure as an `air_speed_mps`
  // beside a water jacket: a value the solver keys its cache on and never
  // reads, so one machine ends up with two cache entries.
  const r = coolingFields(inputs());
  assert.ok(!('frame' in sent(r)));
  assert.ok(!('open_air_speed_mps' in sent(r)));
});

test('an openAirSpeed left over from an open machine is not sent once it is housed', () => {
  const r = coolingFields(inputs({ frame: 'housed', openAirSpeed: '12' }));
  assert.ok(!('frame' in sent(r)));
  assert.ok(!('open_air_speed_mps' in sent(r)));
});

test('an open frame always sends 0 m/s — the panel has no wash input of its own', () => {
  // Removed 2026-09-21: the panel used to let the wash ride separately from
  // the housing's own outer-surface air speed; now it always sends 0, which
  // the backend reads as "use the housing air speed, because it is the same
  // wash" — so the payload must carry 0 even when a stale `openAirSpeed` is
  // still sitting in a saved panel setting from before today.
  const r = coolingFields(inputs({ frame: 'open' }));
  assert.equal(r.frame, 'open');
  assert.equal(r.open_air_speed_mps, 0);
  assert.ok('open_air_speed_mps' in sent(r));
});

test('a leftover openAirSpeed from before the wash input was removed is ignored', () => {
  for (const stale of ['11', '30', '-8', '', 'abc']) {
    const r = coolingFields(inputs({ frame: 'open', openAirSpeed: stale }));
    assert.equal(r.open_air_speed_mps, 0, JSON.stringify(stale));
  }
});

test('the frame is independent of every cooling mode', () => {
  // How the machine is BUILT and how its outer surface is cooled are two
  // different facts: an open machine can still have a jacket on the tooth
  // backs, and neither may switch the other off.
  for (const coolMode of ['air', 'liquid', 'manual', 'none']) {
    const r = coolingFields(inputs({ coolMode, boreMode: 'air', frame: 'open' }));
    assert.equal(r.frame, 'open', coolMode);
    assert.equal(r.open_air_speed_mps, 0, coolMode);
    assert.equal(r.cooling_mode, coolMode);
  }
});

test('the ambient is always sent — it is the air of BOTH air modes', () => {
  for (const coolMode of ['air', 'liquid', 'manual', 'none']) {
    assert.equal(coolingFields(inputs({ coolMode, ambientT: '25' })).ambient_temp, 25);
  }
  // A blank ambient is 40 °C, not Number('') === 0: a 0 °C bay nobody typed
  // would quietly make every temperature below look 40 K better.
  assert.equal(coolingFields(inputs({ ambientT: '' })).ambient_temp, 40);
});

test('an unset coolant inlet falls back to the ambient, not to a number of its own', () => {
  const r = coolingFields(inputs({ coolMode: 'liquid', tIn: '', ambientT: '22' }));
  assert.equal(r.fluid_temp_in_c, 22);
  const b = coolingFields(inputs({ boreMode: 'liquid', boreTIn: '', ambientT: '22' }));
  assert.equal(b.bore_fluid_temp_in_c, 22);
});

/* ── what must be refused before it reaches the solver ────────────────────── */

test('a liquid loop with no pump is refused, on either surface', () => {
  assert.match(coolingIssue(inputs({ coolMode: 'liquid', flowLpm: '' })), /flow/);
  assert.match(coolingIssue(inputs({ coolMode: 'liquid', flowLpm: '0' })), /flow/);
  assert.match(coolingIssue(inputs({ coolMode: 'liquid', flowLpm: '-3' })), /flow/);
  assert.match(coolingIssue(inputs({ boreMode: 'liquid', boreFlowLpm: '0' })), /bore/);
});

test('a manual h of zero is refused — it is not "no cooling", it is a divide by nothing', () => {
  assert.match(coolingIssue(inputs({ coolMode: 'manual', hConv: '0' })), /h must/);
  assert.equal(coolingIssue(inputs({ coolMode: 'manual', hConv: '50' })), null);
});

test('a machine with no cooled surface anywhere is refused', () => {
  // Pure Neumann: no boundary takes heat away, so there is no steady state to
  // solve for — the answer is not "very hot", it is "no answer".
  assert.match(coolingIssue(inputs({ coolMode: 'none', boreMode: 'none' })), /nowhere/);
  assert.equal(coolingIssue(inputs({ coolMode: 'none', boreMode: 'air' })), null);
});

test('the shipped defaults are solvable', () => {
  assert.equal(coolingIssue(inputs()), null);
  assert.equal(coolingIssue(inputs({ coolMode: 'liquid' })), null);
  assert.equal(coolingIssue(inputs({ boreMode: 'liquid' })), null);
});

/* ═══════════════════════════════════════════════════════════════════════════
 * THE ROBOTICS MODE (2026-09-14)
 *
 * ONE mode and not four switches, by the user's decision: a joint bolted to an
 * arm and standing in a room — still-air housing with an emissivity, an OPEN
 * bore in still air, the exposed AXIAL end faces, and a bolted mount
 * conductance.  The key set below is the CONTRACT with
 * `src/motor_ai_sim/thermal_settings.cooling_fields`, which is the same mapping
 * written twice (the orchestrator runs the thermal solve from the
 * Electromagnetic tab and must send byte-identical fields).  A key that appears
 * on one side and not the other is a cache split, i.e. two answers for one
 * machine.
 * ═══════════════════════════════════════════════════════════════════════════ */

test('robotics sends exactly its five fields beside the two constants', () => {
  const r = coolingFields(inputs({ coolMode: 'robotics', boreMode: 'still' }));
  assert.deepEqual(keys(r), [
    'ambient_temp', 'bore_mode', 'cooling_mode',
    'emissivity', 'end_faces', 'end_face_sides', 'mount_g_w_per_k',
  ].sort());
  assert.equal(r.cooling_mode, 'robotics');
  assert.equal(r.bore_mode, 'still');
  assert.equal(r.emissivity, 0.9);
  assert.equal(r.end_faces, 'still');
  assert.equal(r.end_face_sides, 2);
  assert.equal(r.mount_g_w_per_k, 2);
  // BLANK mount temperature = the ambient, and the KEY stays off the wire:
  // sending the ambient in its place would make a default look like a choice.
  assert.ok(!('mount_temp_c' in sent(r)));
});

test('a typed mount temperature rides with the conductance, and only then', () => {
  const r = coolingFields(inputs({ coolMode: 'robotics', mountT: '22' }));
  assert.equal(r.mount_temp_c, 22);
  // 0 °C is a cold store, not "not typed" — it must survive.
  assert.equal(coolingFields(inputs({ coolMode: 'robotics', mountT: '0' })).mount_temp_c, 0);
  // …but with no mount there is no temperature to state.
  const none = coolingFields(inputs({ coolMode: 'robotics', mountG: '0', mountT: '22' }));
  assert.ok(!('mount_g_w_per_k' in sent(none)));
  assert.ok(!('mount_temp_c' in sent(none)));
});

test('a zero or negative mount is the machine bolted to nothing', () => {
  for (const g of ['0', '', '   ', '-4', 'abc']) {
    const r = sent(coolingFields(inputs({ coolMode: 'robotics', mountG: g })));
    assert.ok(!('mount_g_w_per_k' in r), `mountG ${JSON.stringify(g)} was sent`);
  }
});

test('end_face_sides never rides alone beside end_faces: none', () => {
  // The same failure an `air_speed_mps` beside a water jacket would be: a value
  // the solver keys its cache on and never reads.
  const r = coolingFields(inputs({ coolMode: 'robotics', endFaces: 'none',
                                   endFaceSides: '1' }));
  assert.equal(r.end_faces, 'none');
  assert.ok(!('end_face_sides' in sent(r)));
  const one = coolingFields(inputs({ coolMode: 'robotics', endFaceSides: '1' }));
  assert.equal(one.end_face_sides, 1);
  for (const s of ['2', '3', '0', '', 'x']) {
    assert.equal(coolingFields(inputs({ coolMode: 'robotics', endFaceSides: s }))
                 .end_face_sides, 2, `sides ${JSON.stringify(s)}`);
  }
});

test('the emissivity is clamped to a physical one, and never negative', () => {
  assert.equal(coolingFields(inputs({ coolMode: 'robotics', emissivity: '1.4' })).emissivity, 1);
  assert.equal(coolingFields(inputs({ coolMode: 'robotics', emissivity: '-0.2' })).emissivity, 0);
  // A blank field is the 0.9 default, not Number('') === 0 — a black body read
  // as a mirror would remove most of what a small housing loses.
  assert.equal(coolingFields(inputs({ coolMode: 'robotics', emissivity: '' })).emissivity, 0.9);
  // ε = 0 is a REAL answer (a polished housing) and must survive as one.
  assert.equal(coolingFields(inputs({ coolMode: 'robotics', emissivity: '0' })).emissivity, 0);
});

test('every other mode ships none of the robotics fields', () => {
  // A liquid jacket must key the cache on exactly the tuple it always did, so
  // an emissivity left over from a robotics session may not travel with it.
  for (const coolMode of ['air', 'liquid', 'manual', 'none']) {
    const r = sent(coolingFields(inputs({ coolMode, boreMode: 'air',
                                          emissivity: '0.4', mountG: '9',
                                          mountT: '18', endFaces: 'none' })));
    for (const k of ['emissivity', 'end_faces', 'end_face_sides',
                     'mount_g_w_per_k', 'mount_temp_c']) {
      assert.ok(!(k in r), `${coolMode} shipped ${k}`);
    }
  }
});

test('a still bore outside the robotics mode is refused, by name', () => {
  // It is evaluated with that mode's emissivity, an input no other mode sends —
  // so the backend refuses it and the panel must not make it ask.
  for (const coolMode of ['air', 'liquid', 'manual', 'none']) {
    assert.match(coolingIssue(inputs({ coolMode, boreMode: 'still' })), /robotics/);
  }
  assert.equal(coolingIssue(inputs({ coolMode: 'robotics', boreMode: 'still' })), null);
});

test('an unphysical emissivity is refused before the solver sees it', () => {
  assert.match(coolingIssue(inputs({ coolMode: 'robotics', emissivity: '-1' })),
               /emissivity/);
  assert.match(coolingIssue(inputs({ coolMode: 'robotics', emissivity: '2' })),
               /emissivity/);
  assert.equal(coolingIssue(inputs({ coolMode: 'robotics', emissivity: '0' })), null);
});

test('a negative mount conductance is refused, not silently clamped', () => {
  assert.match(coolingIssue(inputs({ coolMode: 'robotics', mountG: '-2' })),
               /mount conductance/);
});

test('a machine bolted to a cold arm IS cooled, films or no films', () => {
  // WIDENED 2026-09-14: the mount is a conductance to a HELD temperature, so
  // the steady problem has a solution even with every surface adiabatic.  What
  // has nowhere to send its heat is the machine with no door at all.
  assert.equal(coolingIssue(inputs({ coolMode: 'robotics', boreMode: 'none',
                                     mountG: '2' })), null);
  assert.match(coolingIssue(inputs({ coolMode: 'none', boreMode: 'none' })),
               /no mount conductance/);
});

test('the robotics defaults are solvable and describe a whole joint', () => {
  const s = inputs({ coolMode: 'robotics', boreMode: 'still' });
  assert.equal(coolingIssue(s), null);
  const r = coolingFields(s);
  // All four decisions of the mode are stated: the film's emissivity, the open
  // bore, the exposed ends and the flange.  A half-configured still-air machine
  // would otherwise look exactly like a converged answer.
  assert.equal(r.emissivity, 0.9);
  assert.equal(r.bore_mode, 'still');
  assert.equal(r.end_faces, 'still');
  assert.ok(r.mount_g_w_per_k > 0);
});
