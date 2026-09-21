/**
 * THE COOLING MENU, IN WORDS — one module, so the panel, the 3-D view and the
 * tests cannot say three different things about the same parameter.
 *
 * Owner, 2026-09-17: *«надо более подробно расписать это меню, оно совершенно
 * не очевидно — надо написать, что это параметр для радиации, и какие для
 * чего»*.  The rows of the Thermal tab's cooling block were terse to the point
 * of being internal names — `ε`, `mount W/K`, `mount °C`, `End faces exposed`,
 * `2 ends open`, `shaft out, mm/side` — and nothing on screen said which of
 * them is the radiation input, what the mount conductance conducts into, or
 * what happens when a field is left blank.
 *
 * Three layers, per the project's "one short line + tooltip" rule:
 *
 *   1. LABEL   — a human name with its unit, on the control itself;
 *   2. TIP     — 2…4 sentences on the ⓘ beside it: what the parameter IS, the
 *                physics it enters, typical values, and what moves in the
 *                result when it is raised or lowered;
 *   3. NOTE    — `HOW_IT_WORKS`, one collapsible list of the heat paths in the
 *                order of the 3-D view, each with the parameter that governs it.
 *
 * NO PHYSICS AND NO DEFAULT IS DEFINED HERE.  Every number quoted below is read
 * off something that already exists:
 *
 *   • the defaults are `stores/thermalStore`'s (ε 0.9, mount 2 W/K, mount °C
 *     blank, room 40 °C, end faces open on both ends, shaft out 0 mm);
 *   • the physics is `routes/thermal.solve_field`'s robotics mode — Churchill–Chu
 *     natural convection plus linearised radiation at ε on the housing
 *     (`cooling_models.outer_still`), the same pair down an unventilated bore
 *     (`bore_still`), four lumped end-face conductances G = h_total(ΔT)·A·n_faces
 *     (`end_face_still`), and the mount as a volume sink G·(T_stator − T_mount);
 *   • the SHARES are the real Ø85 L13 robot joint's last heat-path record
 *     (`GET /api/thermal/heat_paths/last`, robotics mode, 40 °C room, 195.8 W
 *     removed): mount 163.2 W = 83 %, end-winding faces 18.4 W = 9.4 %, housing
 *     5.0 W = 2.5 % of which radiation 2.91 W against convection 2.05 W, stator
 *     end annulus 3.9 W = 2.0 %, bore 2.0 W = 1.0 %, rotor and magnet end
 *     annuli 1.7 W = 0.9 % each.  `L13_SHARES` below keeps them in one place so
 *     a text and a test cannot drift apart, and so nobody invents a share.
 */

/** The Ø85 L13 robot joint's measured split, quoted by the tips above.  Watts
 *  and percents exactly as the heat-path record reports them — these are an
 *  ORDER OF MAGNITUDE for the reader, not a specification of his machine. */
export const L13_SHARES = {
  removed_W: 195.8,
  ambient_c: 40,
  mount_W: 163.2, mount_pct: 83,
  housing_W: 5.0, housing_pct: 2.5,
  housing_convection_W: 2.05, housing_radiation_W: 2.91,
  end_turn_faces_W: 18.4, end_turn_faces_pct: 9.4,
  end_faces_total_W: 25.7, end_faces_total_pct: 13,
  bore_W: 2.0, bore_pct: 1,
} as const;

/** Every control of the cooling block this module names.  The keys are the
 *  `thermalStore` field names wherever one exists, so a reader of the panel can
 *  jump straight to the state. */
export type RoboticsControlKey =
  | 'coolMode' | 'ambientT' | 'emissivity' | 'mountG' | 'mountT'
  | 'endFaces' | 'endFaceSides'
  | 'boreMode' | 'shaftExtMm' | 'shaftExtSides'
  | 'frame' | 'openAirSpeed';

export interface RoboticsControl {
  /** what the control is CALLED on screen, with its unit */
  label: string;
  /** the caption the 3-D popover uses — the same control, a narrower card */
  short: string;
  /** 2…4 sentences on the ⓘ: what it is, the physics, typical values, effect */
  tip: string;
}

/** The one line under the mode select, when the robotics mode is chosen. */
export const ROBOTICS_SUBTITLE =
  'still air + radiation + heat into the mount; no fan, no liquid';

export const ROBOTICS_HELP: Record<RoboticsControlKey, RoboticsControl> = {
  coolMode: {
    label: 'Outer surface',
    short: 'mode',
    tip: 'How heat leaves the OUTER surface of the stator — the housing, or whatever the machine is wrapped in. Robotics is a joint standing in a room: natural convection plus radiation off the housing, and conduction into the arm it is bolted to — no fan, no jacket, no slipstream. Air, Liquid and Manual h are the blown, jacketed and imposed-film machines; No cooling is adiabatic and only solves when the bore or the mount takes the heat instead.',
  },
  ambientT: {
    label: 'room air °C',
    short: 'room °C',
    tip: 'Temperature of the still air the machine stands in, °C — the room, and the default is 40 °C. Every robotics path works against it: the housing film, the T∞ of the radiation term, the end faces, the open bore, and the mount whenever its own temperature is blank. Raising it moves every temperature in the result up almost one for one; it barely changes any coefficient.',
  },
  emissivity: {
    label: 'ε (radiation)',
    short: 'ε (radiation)',
    tip: 'THIS IS THE RADIATION PARAMETER: the total hemispherical emissivity of the housing surface, 0…1, default 0.9. Heat leaves as light at ε·σ·A·(T⁴ − T_room⁴), and on a small machine in still air that beats the air film — on the Ø85 L13 joint the housing loses 2.91 W by radiation against 2.05 W by convection. 0.9 is anodised, painted or oxidised metal and most real housings; 0.2 is bare machined aluminium and 0.05 polished. Lower it and the housing arrow in the 3-D view shrinks by exactly that much — ε = 0 removes the radiation half and nothing else.',
  },
  mountG: {
    label: 'mount to arm, W/K',
    short: 'mount, W/K',
    tip: 'Contact conductance of the bolted flange into the robot\'s own structure, W/K — heat CONDUCTED out of the stator as G·(T_housing − T_mount), not a film on a surface. On a joint in still air this is the path: 163 W of the Ø85 L13\'s 196 W, 83 % of everything that leaves, against 5 W off the whole housing skin. 0 means bolted to nothing, and the answer then says so; the shipped 2 W/K is an ASSUMPTION — nobody has measured this flange (its material, bolt pattern, contact area, pad or grease) — so any mount line is provisional. Raise it and every temperature in the machine falls: it is the cheapest cooling a joint has.',
  },
  mountT: {
    label: 'mount °C (blank = room air)',
    short: 'mount °C',
    tip: 'The temperature the mount is HELD at, °C — it enters as an infinite sink at exactly this value. BLANK means the room air temperature above, and blank is the honest default: the arm is not a heat source of its own, and pre-filling the field would make a default look like a number somebody measured. Type one when the arm is known to run warm — a neighbouring joint, a hot enclosure — and the mount then carries G·(T_housing − this) instead.',
  },
  endFaces: {
    label: 'End faces',
    short: 'end faces',
    tip: 'The two AXIAL faces of the machine: the end turns standing proud of the core, plus the stator, rotor and magnet end annuli. Open to air puts all four in the room at their own natural-convection + radiation film (G = h·A·n_faces per face) — on the Ø85 L13 they carry 25.7 W together, 13 % of everything, the end turns alone 9.4 %. Closed removes those four paths: it is a machine buried between a gearbox and the arm, and its heat then has to leave through the housing and the mount.',
  },
  endFaceSides: {
    label: 'Ends open',
    short: 'ends open',
    tip: 'How many of the two ends are actually in the room\'s air — every axial conductance scales with the count. Both ends is the default; 1 end is the usual joint, bolted flat against the arm on the flange side so only the far end sees the room. Halving the count roughly halves the end-face watts, about 13 W on the Ø85 L13.',
  },
  boreMode: {
    label: 'Rotor bore',
    short: 'bore',
    tip: 'What is inside the hollow shaft — the only path that reaches the rotor and the magnets WITHOUT crossing the air gap, which is why it moves the magnet temperature more than a bigger fan on the housing does. Open bore, still air is a joint with a cable down it: natural convection in the hole plus radiation out of its two ends at the same ε (2.0 W, 1 % on the Ø85 L13). Forced air and Liquid are a blown or pumped bore with their own speed or flow; Closed is adiabatic, and everything the rotor and the magnets make then crosses the gap into the stator.',
  },
  shaftExtMm: {
    label: 'shaft out of housing, mm/side',
    short: 'mm/side',
    tip: 'How much shaft sticks out of the housing on EACH side, in mm — 0 turns this path off, and off is the default. It is modelled as a FIN, not as a wetted area: the stub has to conduct the heat along the steel before the air can take it, so past about 2.5 decay lengths another millimetre removes nothing. The film on it is a cylinder spinning in still room air, so it improves with rpm. The rotor\'s end faces and the end windings get nothing from this field — inside a housed machine they turn in their own air.',
  },
  shaftExtSides: {
    label: 'Shaft ends out',
    short: 'sides',
    tip: 'How many shaft ends come out of the housing — the same fin, once or twice. 2 is a through shaft; 1 is a machine capped on one end.',
  },
  frame: {
    label: 'Frame',
    short: 'frame',
    tip: 'How the machine is BUILT, which decides whether its end windings and slot air are cooled at all. Housed: they sit inside a closed case, turning in their own air, so whatever they hand it comes straight back through the housing and there is no extra path — this is every normally-built motor. Open: no housing, the tooth blocks with their coils held between two end plates on standoff pins, with the end turns and the axial channels between neighbouring coils in the airflow. On a 40 mm tooth-coil machine the end turns are three quarters of the copper LENGTH, so which of the two it is moves the winding temperature by more than any film coefficient above. On Open, the end turns and the slot channels see the same air as the housing (the Outer surface speed above) — there is no separate wash speed to set.',
  },
  openAirSpeed: {
    label: 'wash m/s',
    short: 'wash m/s',
    tip: 'Air over the end turns and through the slot channels on an open frame, m/s — the propeller wash (10–12 m/s on the 40 mm). 0 means the same air that is blowing on the housing when the outer surface is in air, and still air when it is not: it is one airstream, and typing the number twice is how the two end up disagreeing. Still air is not zero cooling — it is the natural-convection floor, about 7 W/m²K.',
  },
};

/* ── the option texts: a menu entry must say what it DOES ─────────────────── */

export const COOL_MODE_LABEL: Record<string, string> = {
  air: 'Air — forced over the housing',
  liquid: 'Liquid — jacket on the housing',
  manual: 'Manual h — imposed film',
  none: 'No cooling — adiabatic outside',
  robotics: 'Robotics — still air + radiation + mount',
};

export const BORE_MODE_LABEL: Record<string, string> = {
  none: 'Closed bore — adiabatic',
  air: 'Forced air through the bore',
  liquid: 'Liquid through the bore',
  still: 'Open bore — still air + radiation',
};

export const FRAME_LABEL: Record<string, string> = {
  housed: 'Housed — closed case',
  open: 'Open — no housing',
};

export const END_FACE_LABEL: Record<string, string> = {
  still: 'End faces: open to the air',
  none: 'End faces: closed off',
};

export const END_FACE_SIDES_LABEL: Record<string, string> = {
  '2': 'Both ends open',
  '1': '1 end open (mount side shut)',
};

export const SHAFT_SIDES_LABEL: Record<string, string> = {
  '2': '2 shaft ends out',
  '1': '1 shaft end out',
};

/* ── the collapsible note: the heat paths, in the order of the 3-D view ───── */

export interface HowLine {
  /** the path itself — "housing → still air" */
  path: string;
  /** the control that governs it, EXACTLY as it is labelled above; '' when the
   *  step is pure conduction and has no input at all */
  param: string;
  /** one clause of physics, and what it is worth */
  text: string;
}

export const HOW_IT_WORKS_TITLE = 'How this cooling model works';

/** Six to ten short lines, read top to bottom the way the 3-D view is drawn:
 *  the stator side first (where 97 % of the watts leave on a joint), then the
 *  rotor side.  Every line names the parameter that decides it. */
export const HOW_IT_WORKS: HowLine[] = [
  { path: 'winding → stator iron → housing', param: '',
    text: 'pure conduction through the solids and the slot insulation — no input, it is the machine you drew.' },
  { path: 'housing → still air', param: 'room air °C',
    text: 'Churchill–Chu natural convection, roughly 6 W/m²K at ΔT 60 K. No fan is involved in this mode.' },
  { path: 'housing → radiation', param: 'ε (radiation)',
    text: 'ε·σ·A·(T⁴ − T_room⁴). Bigger than the air film here: 2.91 W against 2.05 W on the Ø85 L13.' },
  { path: 'housing → the arm', param: 'mount to arm, W/K',
    text: 'G·(T_housing − T_mount) into the structure, sinking to mount °C (blank = room air). 83 % of everything on the Ø85 L13 — the bolts, not the air.' },
  { path: 'end turns + core and magnet end annuli → still air', param: 'End faces',
    text: 'four lumped conductances G = h·A·n_faces, scaled by Ends open. 13 % together on the Ø85 L13.' },
  { path: 'end turns + slot ducts → wash', param: 'Frame',
    text: 'only on an open frame; a housed machine has no such path and gets nothing here.' },
  { path: 'rotor + magnets → air gap → stator', param: '',
    text: 'the rotor has no cooled surface of its own, so this is where its heat goes unless the bore or the shaft takes it.' },
  { path: 'rotor → bore', param: 'Rotor bore',
    text: 'still air in the hole plus radiation at the same ε, or forced air, or liquid, or closed = adiabatic. 1 % on the Ø85 L13.' },
  { path: 'rotor → shaft stubs', param: 'shaft out of housing, mm/side',
    text: 'the exposed shaft as a fin in room air; 0 mm switches it off.' },
  { path: 'every film, every pass', param: '',
    text: 'h depends on its own wall temperature (h ∝ ΔT^¼, and the radiation re-linearises about the wall), so the solve iterates until every wall moves by less than half a kelvin.' },
];

/* ── the same words on the 3-D view ───────────────────────────────────────── */

/** Which control sets a heat-path sink — so the arrow tooltip and the surface
 *  popover can name the field the reader has to go and change.  `null` is a
 *  path that is a CONSEQUENCE of the others and is set nowhere. */
export const PARAM_BY_SINK: Record<string, RoboticsControlKey | null> = {
  housing: 'coolMode',
  mount: 'mountG',
  end_face_winding: 'endFaces',
  end_face_stator: 'endFaces',
  end_face_rotor: 'endFaces',
  end_face_magnet: 'endFaces',
  bore: 'boreMode',
  shaft_ends: 'shaftExtMm',
  end_windings: 'frame',
  slot_channels: 'frame',
};

/** "Set by: ε (radiation)" — the third line of an arrow's hover, and the tail
 *  of a legend row's title.  Empty string for a path nothing sets, so the
 *  caller can concatenate it without a guard. */
export function setByLine(sinkId: string): string {
  const key = PARAM_BY_SINK[sinkId];
  if (!key) return '';
  return `Set by: ${ROBOTICS_HELP[key].label}.`;
}
