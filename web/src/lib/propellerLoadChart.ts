// The propeller LOAD chart of Configure (owner 2026-10-18, after the X13-18S datasheet chart): one chart, speed [rpm]
// on x, ONE switchable parameter on y (thrust, torque, currents, efficiency, g/W, powers, temperatures), one line per
// propeller.  The points are the backend's propeller S1 rows (motor_ai_sim.propeller_s1): each one a
// steady continuous operating point of this motor driving that propeller, cooled by it.  The part above the
// continuous limit is drawn dashed.  Pure functions, no runtime imports (node --test).

/** One S1 row as /api/propellers/{id}/s1 sends it (only the fields the chart reads). */
export interface S1ChartRow {
  rpm: number;
  thrust_N: number | null;
  torque_prop_Nm?: number | null;
  shaft_power_W?: number | null;
  electrical_power_W?: number | null;
  battery_power_W?: number | null;
  battery_current_A?: number | null;
  current_A?: number | null;
  T_winding_C?: number | null;
  T_magnet_C?: number | null;
  ok: boolean;
  /** inside the MOTOR's own limits (the controller / pack current judged apart, owner 2026-10-18) */
  ok_motor?: boolean;
}

export type LoadMetric = 'thrust' | 'torque' | 'batteryCurrent' | 'phaseCurrent' | 'efficiency' | 'gPerW'
  | 'shaftPower' | 'inputPower' | 'tWinding' | 'tMagnet';

/** The owner's five first (thrust, power, torque, efficiency, g/W), then currents, battery power and temperatures. */
export const LOAD_METRICS: readonly LoadMetric[] = ['thrust', 'shaftPower', 'torque', 'efficiency', 'gPerW',
  'batteryCurrent', 'phaseCurrent', 'inputPower', 'tWinding', 'tMagnet'];

export const METRIC_UNIT: Readonly<Record<LoadMetric, string>> = {
  thrust: 'kg', torque: 'N·m', batteryCurrent: 'A', phaseCurrent: 'A rms', efficiency: '%', gPerW: 'g/W',
  shaftPower: 'W', inputPower: 'W', tWinding: '°C', tMagnet: '°C',
};

const G0 = 9.80665;
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The power drawn: the battery's when the drive is modelled, else the motor's electrical input. */
export const inputPowerW = (r: S1ChartRow): number | null => num(r.battery_power_W) ?? num(r.electrical_power_W);

export function metricValue(r: S1ChartRow, m: LoadMetric): number | null {
  const pin = inputPowerW(r);
  const ps = num(r.shaft_power_W);
  const tn = num(r.thrust_N);
  switch (m) {
    case 'torque': return num(r.torque_prop_Nm);
    case 'thrust': return tn != null ? tn / G0 : null;
    case 'batteryCurrent': return num(r.battery_current_A);
    case 'phaseCurrent': return num(r.current_A);
    case 'efficiency': return pin && ps != null && pin > 0 ? (ps / pin) * 100 : null;
    case 'gPerW': return pin && tn != null && pin > 0 ? (tn / G0 * 1000) / pin : null;
    case 'shaftPower': return ps;
    case 'inputPower': return pin;
    case 'tWinding': return num(r.T_winding_C);
    case 'tMagnet': return num(r.T_magnet_C);
    default: return null;
  }
}

export interface LoadPoint { x: number; y: number; rpm: number }

/** One propeller's line over speed: the continuous part (rows inside the motor's own limits; the supply's limit is a
 *  separate vertical line) and the part above it (dashed; it
 *  starts at the last continuous point so the two join).  Rows without a value are left out. */
export function loadLine(rows: readonly S1ChartRow[] | null | undefined, m: LoadMetric): { ok: LoadPoint[]; over: LoadPoint[] } {
  const pts = (rows ?? [])
    .map((r) => ({ r, n: num(r.rpm), y: metricValue(r, m) }))
    .filter((p): p is { r: S1ChartRow; n: number; y: number } => p.n != null && p.n > 0 && p.y != null)
    .sort((a, b) => a.n - b.n);
  const firstOver = pts.findIndex((p) => !(p.r.ok_motor ?? p.r.ok));
  const toPt = (p: { r: S1ChartRow; n: number; y: number }): LoadPoint => ({ x: p.n, y: p.y, rpm: p.n });
  if (firstOver < 0) return { ok: pts.map(toPt), over: [] };
  const ok = pts.slice(0, firstOver).map(toPt);
  const over = pts.slice(Math.max(0, firstOver - 1)).map(toPt);
  return { ok, over: ok.length ? over : pts.slice(firstOver).map(toPt) };
}

/** Line colours, one per propeller in the order picked. */
export const LOAD_COLORS = ['#3b82f6', '#f59e0b', '#16a34a', '#db2777', '#8b5cf6', '#0891b2'];
