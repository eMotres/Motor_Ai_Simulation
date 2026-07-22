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
  magnetN: '#e02718',
  magnetS: '#2e86ff',   // bright azure — must not blend into the slate iron
  copper: '#e0821a',
  copperPhases: ['#e0821a', '#d2491a', '#b8860b'],
  slotLiner: '#16a34a',
  enamel: '#d97706',
  inBand: '#22c55e',
  outBand: '#a855f7',
} as const;
