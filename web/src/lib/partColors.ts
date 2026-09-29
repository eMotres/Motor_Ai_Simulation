/** Single source of truth for motor-part colours.
 *
 * Used by EVERY surface that shows a part: the 3D/2D viewers (ApiMotorMesh),
 * the CadQuery STL layer (STLMesh), the ComponentTree swatches and the
 * MaterialBar chips — so the tree legend always matches the geometry.
 * Style reference: the catalog MotorThumbnail (dark slate iron, copper
 * windings, red/blue magnets).
 */
export const PART_COLORS = {
  statorIron: '#42526b',   // dark slate steel (thumbnail style)
  rotorIron: '#394860',
  shaft: '#2b3648',
  sleeve: '#1f2937',   // carbon-fibre retaining ring — near-black, like the real thing
  magnetN: '#e02718',
  magnetS: '#2e86ff',   // bright azure — must not blend into the slate iron
  copper: '#e0821a',
  copperPhases: ['#e0821a', '#d2491a', '#b8860b'],
  slotLiner: '#16a34a',
  enamel: '#d97706',
  inBand: '#22c55e',
  outBand: '#a855f7',
  // The three domains the THERMAL map added on 2026-09-07, when the air the
  // conduction solve actually uses stopped being white space and became parts
  // of their own (user: "и воздух тоже показывать — он же входит в расчёт, и в
  // дереве отображать их тоже нужно").  Deliberately pale: they are the things
  // BETWEEN the metals, and a saturated fill for them would read as a component
  // rather than as what fills the space around one.
  slotFill: '#efe4c4',    // impregnation / air in the slot — cream
  gapAir: '#a5e8ef',      // the mechanical clearance — pale cyan
  pocketAir: '#cbd5e1',   // air inside the rotor — light grey
} as const;
