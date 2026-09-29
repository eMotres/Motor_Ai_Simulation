/**
 * One Controller answer → the flat block a Compare row carries.
 *
 * Owner 2026-09-22: *«не забудь Compare сделать для анализа разных вариантов,
 * так же как на всех других меню»* — the Controller tab gets the Thermal and
 * Mechanical tabs' stacked comparison, with the same look and the same row
 * contract, so two topologies (or two devices, two parallel counts, two
 * coldplates, two duties) can be read side by side without leaving the tab.
 *
 * The builders are PURE and live here for the reasons `compare/resultRows`
 * states for its own: they are the whole contract of what a stacked row holds,
 * the table's columns read exactly the keys written here, and nothing in this
 * file fetches or knows about staleness.
 *
 * They are in the CONTROLLER's folder rather than appended to
 * `compare/resultRows.ts` on purpose: that module is shared by three tabs and
 * is edited by whoever is working on them, and a new tab's row builder has no
 * business widening it. The TYPES and the store conventions are imported from
 * it, so there is still exactly one definition of what a stacked row is.
 */
import type { LocalRow, ResultBlock } from '../compare/resultRows';
import type { ColumnDef } from '../common/LocalCompareTable';
import type { ControllerResult } from './controllerApi';

export type { LocalRow, ResultBlock };

/** A real number, or `undefined` — never `NaN`, never `Number(null) === 0`. */
function num(v: unknown): number | undefined {
  if (v === null || v === undefined || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() !== '' ? v : undefined;
}

/** Write a key only when there is a MEASUREMENT behind it (resultRows' rule):
 *  an absent key prints "—", a stored null prints as a hole and colours as a
 *  zero. */
function put(out: ResultBlock, key: string,
             v: number | string | boolean | null | undefined): void {
  if (v === null || v === undefined) return;
  if (typeof v === 'number' && !Number.isFinite(v)) return;
  if (typeof v === 'string' && v.trim() === '') return;
  out[key] = v;
}

/** What this controller was solved WITH — device, map, cooling, the point. */
export function controllerInputsFromResult(res: ControllerResult): ResultBlock {
  const out: ResultBlock = {};
  const t = res.topology ?? ({} as ControllerResult['topology']);
  const s = res.settings ?? {};
  const p = res.point ?? {};
  const cool = (res.thermal?.coldplate ?? {}) as Record<string, unknown>;
  put(out, 'device', str(res.device));
  put(out, 'topology', str(t.preset_label) ?? str(t.preset));
  put(out, 'connection', str(t.star_delta));
  put(out, 'n_parallel', num(s.devices_parallel));
  put(out, 'n_bridges', num(t.n_bridges));
  put(out, 'n_switches', num(t.n_switches));
  put(out, 'n_devices', num(t.n_devices));
  put(out, 'duty', str(res.context?.duty));
  put(out, 'f_carrier_hz', num(p.f_carrier_hz));
  put(out, 'v_dc_V', num(p.v_dc_V));
  put(out, 'dead_time_us', num(s.dead_time_us));
  put(out, 'i_phase_rms_A', num(p.i_phase_rms_A));
  put(out, 'coolant', str(cool.coolant));
  put(out, 'flow_lpm', num(cool.flow_lpm));
  put(out, 't_in_c', num(cool.t_in_c));
  put(out, 'r_tim_k_w', num(res.thermal?.r_tim_k_w));
  return out;
}

/** What came OUT: the loss split, the temperatures, the two efficiencies, the
 *  DC link, the datasheet verdict and the cost proxy. */
export function controllerRowFromResult(res: ControllerResult): ResultBlock {
  const out: ResultBlock = {};
  const L = (res.losses ?? {}) as Record<string, unknown>;
  const T = (res.thermal ?? {}) as Record<string, unknown>;
  const E = res.efficiency ?? ({} as ControllerResult['efficiency']);
  const D = (res.dc_link ?? {}) as Record<string, unknown>;
  put(out, 'p_conduction_W', num(L.conduction_W));
  put(out, 'p_third_quadrant_W', num(L.third_quadrant_W));
  put(out, 'p_switching_W', num(L.switching_W));
  put(out, 'p_total_W', num(L.total_W));
  put(out, 't_j_max_c', num(T.t_j_max_c));
  put(out, 't_j_margin_K', num(T.margin_K));
  put(out, 't_case_c', num(T.t_case_c));
  put(out, 'eta_inverter_pct', E.inverter == null ? undefined : E.inverter * 100);
  put(out, 'eta_wall_to_shaft_pct',
      E.wall_to_shaft == null ? undefined : E.wall_to_shaft * 100);
  put(out, 'i_cap_rms_A', num(D.i_cap_rms_A));
  put(out, 'limits', str(res.limits_verdict));
  const failed = (res.limits ?? []).filter(r => r.verdict === 'fail').length;
  put(out, 'limits_failed', failed);
  // Cost PROXY — devices × the card's own quoted price, and only when the card
  // carries one. It is not a bill of materials: no gate drivers, no busbars,
  // no coldplate, no assembly.
  const price = num(res.device_row?.price?.amount);
  const n = num(res.topology?.n_devices);
  if (price !== undefined && n !== undefined) {
    put(out, 'cost_proxy', price * n);
    put(out, 'currency', str(res.device_row?.price?.currency));
  }
  return out;
}

/** One row of the Controller tab's stacked comparison. */
export function localControllerRow(res: ControllerResult):
    { inputs: ResultBlock; results: ResultBlock } {
  if (!res || !res.device) {
    throw new Error('nothing to compare yet — press Solve first');
  }
  return { inputs: controllerInputsFromResult(res),
           results: controllerRowFromResult(res) };
}

/** A name that says what the variant IS, so a stack of six is readable. */
export function controllerRowName(res: ControllerResult): string {
  const t = res.topology;
  const bits = [res.device, t?.preset_label,
                `x${res.settings?.devices_parallel ?? 1}`];
  if (res.context?.duty) bits.push(res.context.duty);
  return bits.filter(Boolean).join(' · ');
}

/** The columns, in reading order. `better` is only set where the direction is
 *  a fact: lower loss and lower junction temperature are better, higher
 *  efficiency and higher margin are better; a carrier or a bus voltage has no
 *  direction and is never coloured. */
export const CONTROLLER_COMPARE_COLUMNS: ColumnDef[] = [
  { key: 'device', label: 'Device', kind: 'input' },
  { key: 'topology', label: 'Topology', kind: 'input' },
  { key: 'connection', label: 'Connection', kind: 'input' },
  { key: 'duty', label: 'Duty', kind: 'input' },
  { key: 'n_parallel', label: 'Parallel', kind: 'input', d: 0 },
  { key: 'n_switches', label: 'Switches', kind: 'input', d: 0 },
  { key: 'n_devices', label: 'Devices', kind: 'input', d: 0 },
  { key: 'f_carrier_hz', label: 'Carrier', unit: 'Hz', kind: 'input', d: 0 },
  { key: 'v_dc_V', label: 'DC link', unit: 'V', kind: 'input', d: 0 },
  { key: 'dead_time_us', label: 'Dead time', unit: 'µs', kind: 'input', d: 2 },
  { key: 'coolant', label: 'Coolant', kind: 'input' },
  { key: 'flow_lpm', label: 'Flow', unit: 'L/min', kind: 'input', d: 1 },
  { key: 't_in_c', label: 'Inlet', unit: '°C', kind: 'input', d: 0 },

  { key: 'p_conduction_W', label: 'Conduction', unit: 'W', kind: 'result', d: 0, better: 'lo' },
  { key: 'p_third_quadrant_W', label: '3rd quadrant', unit: 'W', kind: 'result', d: 0, better: 'lo' },
  { key: 'p_switching_W', label: 'Switching', unit: 'W', kind: 'result', d: 0, better: 'lo' },
  { key: 'p_total_W', label: 'Total loss', unit: 'W', kind: 'result', d: 0, better: 'lo' },
  { key: 't_j_max_c', label: 'T_j max', unit: '°C', kind: 'result', d: 0, better: 'lo' },
  { key: 't_j_margin_K', label: 'T_j margin', unit: 'K', kind: 'result', d: 0, better: 'hi' },
  { key: 't_case_c', label: 'Case', unit: '°C', kind: 'result', d: 0, better: 'lo' },
  { key: 'eta_inverter_pct', label: 'η inverter', unit: '%', kind: 'result', d: 2, better: 'hi' },
  { key: 'eta_wall_to_shaft_pct', label: 'η wall-to-shaft', unit: '%', kind: 'result', d: 2, better: 'hi' },
  { key: 'i_cap_rms_A', label: 'DC ripple', unit: 'A rms', kind: 'result', d: 0, better: 'lo' },
  { key: 'limits', label: 'Datasheet limits', kind: 'result',
    fmt: (v) => (v === 'pass' ? 'PASS' : v === 'fail' ? 'FAIL'
                 : v === 'warn' ? 'WARNING' : '—') },
  { key: 'limits_failed', label: 'Limits failed', kind: 'result', d: 0, better: 'lo' },
  { key: 'cost_proxy', label: 'Device cost proxy', kind: 'result', d: 0, better: 'lo' },
];
