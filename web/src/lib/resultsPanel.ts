// The results panel of a stored point (owner 2026-11-01: «the information panel is the Configure one, the same everywhere;
// a tile that cannot be computed is simply not shown»).  Pure: no React, no fetch, no i18n - the caption is a locale key
// the renderer resolves, and no value is invented: the tiles come from lib/emResultTiles.ts, which omits every tile whose
// number the record lacks and every row left empty.
import { emTileRows, type EmMech, type EmTileSpec, type EmTileSummary } from './emResultTiles';
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
  return emTileRows(summary, { k3dBasis, mech: panelMech(panel.summary), cooling: panel.cooling ?? null, cogging: panelCogging(panel) });
}

/** The DC bus the point was computed to need (the DC BUS tile), for the battery block; null when not stored. */
export function panelDcBus(panel: StoredPanel | null | undefined): number | null {
  const tile = panelRows(panel).flat().find((t) => t.id === 'dcBusMin');
  return tile?.value ?? null;
}
