// The results panel of a stored point (owner 2026-11-01: «the information panel is the Configure one, the same everywhere;
// a tile that cannot be computed is simply not shown»).  Pure: no React, no fetch, no i18n - the caption is a locale key
// the renderer resolves, and no value is invented: the tiles come from lib/emResultTiles.ts, which omits every tile whose
// number the record lacks and every row left empty.
import { configureLook, emTileRows, type EmMech, type EmTileSpec, type EmTileSummary } from './emResultTiles';
import { apply3dFactors, apply3dToSummary, type Apply3dFields } from './apply3d';
import { coggingFromFlat, type CoggingView } from './coggingTile';
import type { StoredPanel } from '../modules/development/types';

/** The one-line caption above the tiles: which record the numbers are, and the drive (locale key under `emTiles.`). */
export interface PanelCaption {
  key: 'sourceCoupledPwm' | 'sourceCoupledPwmNoFreq' | 'sourceCoupled' | 'sourceEmThermal' | 'sourceEm' | 'sourceEmPlain';
  vars: Record<string, string | number>;
  /** the tooltip key: what "final result" means for this source */
  tipKey: 'sourceCoupledTip' | 'sourceEmTip';
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export function panelCaption(source: StoredPanel['source']): PanelCaption {
  if (source.em === 'coupled') {
    const f = num(source.f_carrier_hz);
    if (source.drive === 'inverter') {
      return f != null && f > 0
        ? { key: 'sourceCoupledPwm', vars: { freq: String(Number((f / 1000).toFixed(1))) }, tipKey: 'sourceCoupledTip' }
        : { key: 'sourceCoupledPwmNoFreq', vars: {}, tipKey: 'sourceCoupledTip' };
    }
    return { key: 'sourceCoupled', vars: {}, tipKey: 'sourceCoupledTip' };
  }
  const w = num(source.em_winding_c);
  if (w == null) return { key: 'sourceEmPlain', vars: {}, tipKey: 'sourceEmTip' };
  return { key: source.thermal === 'thermal' ? 'sourceEmThermal' : 'sourceEm', vars: { winding: Math.round(w) }, tipKey: 'sourceEmTip' };
}

/** The bearings + windage block of the saved run (analytic SKF, not FEM); `extra_W: null` = the machine has no bearing model. */
export function panelMech(s: Record<string, unknown>): EmMech | null {
  const extra = num(s.P_mech_extra_W);
  if (extra == null && num(s.P_bearings_W) == null) return null;
  const ml = (s.mech_losses ?? {}) as { cards?: unknown; windage?: { P_gap_W?: number; P_faces_W?: number } };
  const cards = Array.isArray(ml.cards) ? ml.cards.map(String) : [];
  return {
    bearings_W: num(s.P_bearings_W), windage_W: num(s.P_windage_W), extra_W: extra,
    cards: cards.length === 2 && cards[0] === cards[1] ? `2 × ${cards[0]}` : cards.join(' / ') || null,
    temp_C: num(s.bearing_temp_c), gap_W: num(ml.windage?.P_gap_W), faces_W: num(ml.windage?.P_faces_W),
  };
}

/** The summary the tiles are drawn from: the saved run with the machine's 3-D end-effect applied, as the Electromagnetic card
 *  and the ordinary Configure show it by default (lib/apply3d.ts), and the basis its torque numbers (Kt, Km) carry. */
export function panelSummary(panel: StoredPanel): { summary: EmTileSummary; k3dBasis: '3-D' | '3-D flux' | null } {
  const raw = panel.summary as EmTileSummary & Apply3dFields;
  const f = apply3dFactors(raw);
  return f.k != null ? { summary: apply3dToSummary(raw) as EmTileSummary, k3dBasis: f.basis } : { summary: raw, k3dBasis: null };
}

/** The configuration's saved cogging torque, from the flat `cogging_*` keys the server hands over. */
export const panelCogging = (panel: StoredPanel): CoggingView | null => coggingFromFlat(panel.cogging ?? {});

/** All tile rows of a stored point's panel; `[]` when the point stores nothing the tiles can show. */
export function panelRows(panel: StoredPanel | null | undefined): EmTileSpec[][] {
  if (!panel || !panel.summary) return [];
  const { summary, k3dBasis } = panelSummary(panel);
  // the tiles are Configure's, in Configure's look (set, rows, decimals, colours): lib/emResultTiles.configureLook
  return configureLook(emTileRows(summary, { k3dBasis, mech: panelMech(panel.summary), cooling: panel.cooling ?? null, cogging: panelCogging(panel) }), summary);
}

/** panel tile -> the key of the steps table's `comparison` row (and the factor to its unit): the SAME numbers the panel shows. */
const COMPARISON_OF_TILE: Record<string, [string, number]> = {
  torque: ['torque_nm', 1], power: ['power_w', 1e3], efficiency: ['efficiency_pct', 1], tRipple: ['ripple_pct', 1],
  totalLoss: ['p_loss_total_w', 1], copperLoss: ['p_cu_w', 1], ironLoss: ['p_fe_w', 1], magnetLoss: ['p_mag_w', 1],
  vLinePeak: ['v_line_peak_v', 1], vPhasePeak: ['v_phase_peak_v', 1], currDensity: ['current_density_a_mm2', 1],
  mass: ['mass_kg', 1], tPerMass: ['torque_to_mass_nm_kg', 1], tWinding: ['coil_temp_c', 1], tMagnet: ['magnet_temp_c', 1],
};

/** The comparison row of a stored point carrying the panel's FINAL numbers (the coupled loop's first, 3-D applied, shaft power):
 *  merged over the stored row so the steps table and chart cannot read another torque than the panel (owner 2026-11-01).
 *  A tile with no number (hidden, or a temperature drawn as "> limit") leaves the stored value alone. */
export function finalComparison(panel: StoredPanel | null | undefined, stored: Record<string, unknown> | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(stored ?? {}) };
  for (const tile of panelRows(panel).flat()) {
    const map = COMPARISON_OF_TILE[tile.id];
    if (map && tile.value != null) out[map[0]] = tile.value * map[1];
  }
  const mass = one(panelRows(panel), 'mass');
  if (mass && mass.value != null) out.mass_partial = mass.labelKey !== 'configure.mass';
  return out;
}

const one = (rows: EmTileSpec[][], id: string) => rows.flat().find((t) => t.id === id);

/** The DC bus the point was computed to need (the DC BUS tile), for the battery block; null when not stored. */
export function panelDcBus(panel: StoredPanel | null | undefined): number | null {
  const tile = panelRows(panel).flat().find((t) => t.id === 'dcBusMin');
  return tile?.value ?? null;
}
