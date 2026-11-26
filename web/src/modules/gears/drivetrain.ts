// "With motor": one gearbox behind one motor configuration's SAVED operating points, at output load rows (POST /api/gears/drivetrain,
// schema gear_drivetrain-1, PR #907).  Pure logic and types; nothing here starts a solver.  Web tests use real route answers as fixtures.
import type { RequestRecord } from './types';

export const DRIVETRAIN_SCHEMA = 'gear_drivetrain-1';

export interface LoadRow { name: string; torque_out_Nm: number; speed_out_rpm: number; duty: string }
export interface MotorChoice { die: string; configuration: string }
export type GearboxSource = { kind: 'inline'; request: RequestRecord } | { kind: 'record'; record_id: string; project: string };

export interface DrivetrainRequest {
  gearbox: { record_id: string; project?: string } | { request: RequestRecord };
  motor: { die: string; configuration: string; duties?: Record<string, string> };
  load: Array<{ name: string; mode?: string; torque_out_Nm: number; speed_out_rpm: number }>;
}

export interface StageLoss {
  stage: string; stage_id: string; mesh_W: number | null; bearings_W: number | null; churning_W: number | null; total_W: number;
  total_partial: boolean; loss_inputs_missing: string[]; efficiency: number | null; efficiency_partial: boolean; state: string;
}
export interface RowGearbox {
  torque_in_Nm: number; speed_in_rpm: number; power_in_W: number; power_out_W: number; loss_W: number; efficiency: number | null;
  loss_by_stage: StageLoss[]; loss_partial: boolean; loss_inputs_missing: string[]; efficiency_partial: boolean; mass_kg: number | null;
  state: string; flags: string[]; missing_inputs: string[];
}
export type TorqueBasis = 'shaft' | 'field_legacy';
export interface DrivetrainRow {
  name: string; mode: string; torque_out_Nm: number; speed_out_rpm: number;
  status: 'ok' | 'missing' | 'gearbox_failed' | string;
  reason?: string; motor_duty?: string | null;
  covered_by_saved_point: boolean | null; limited_by: string | null; limits_exceeded?: string[];
  gearbox?: RowGearbox;
  motor_required?: { torque_Nm: number; speed_rpm: number; shaft_power_W: number };
  motor_saved_point?: { torque_Nm: number; speed_rpm: number; efficiency: number | null; torque_basis: TorqueBasis; shaft_convention?: string | null };
  margin?: { torque_Nm: number; torque_pct: number; speed_rpm: number; speed_pct: number };
  efficiency_parts?: {
    gearbox: { efficiency: number | null; partial_losses: boolean; at: { torque_in_Nm: number; speed_in_rpm: number } };
    motor: { efficiency: number | null; duty: string; at: { torque_Nm: number; speed_rpm: number } } | null;
  };
  chain_efficiency: number | null; chain_efficiency_reason?: string; chain_efficiency_partial_losses?: boolean; motor_point_matches?: boolean;
  mass?: { motor_kg: number | null; gearbox_kg: number | null; total_kg: number | null; sources: { motor: string | null; gearbox: string }; motor_mass_partial: boolean };
  output_torque_per_total_mass_Nm_per_kg?: number | null;
  // a failed gearbox row carries what the core said, as the route sends it
  gearbox_state?: string; gearbox_flags?: string[]; missing_inputs?: string[];
}
export interface DrivetrainResponse {
  schema: string; read_only: boolean; motoring_only: boolean; reverse_not_computed: boolean;
  gearbox: { kind: string; name: string; preset_name: string; ratio_abs: number; record_id: string | null; project: string | null; temp_C: number; request_id: string; reverses_direction: boolean;
    record_version?: number; record_state?: string; record_flags?: string[]; record_name?: string };
  motor: { die: string; configuration: string; mass_kg: number | null; mass_source: string | null; mass_partial: boolean };
  rows: DrivetrainRow[]; core_limitations: string[]; assumptions: string[];
}

export const MAX_ROWS = 20;
export const defaultRows = (): LoadRow[] => [
  { name: 'continuous', torque_out_Nm: 9.8, speed_out_rpm: 247, duty: '' },
  { name: 'peak', torque_out_Nm: 20, speed_out_rpm: 120, duty: '' },
];

/** The request body.  The row name is the mode the route maps to a saved duty; a duty picked per row goes into motor.duties. */
export function buildDrivetrainRequest(motor: MotorChoice, rows: readonly LoadRow[], source: GearboxSource): DrivetrainRequest {
  const duties: Record<string, string> = {};
  for (const r of rows) if (r.duty) duties[r.name] = r.duty;
  const mt: DrivetrainRequest['motor'] = { die: motor.die, configuration: motor.configuration };
  if (Object.keys(duties).length) mt.duties = duties;
  return {
    gearbox: source.kind === 'record' ? { record_id: source.record_id, ...(source.project ? { project: source.project } : {}) } : { request: source.request },
    motor: mt,
    load: rows.map((r) => ({ name: r.name, torque_out_Nm: r.torque_out_Nm, speed_out_rpm: r.speed_out_rpm })),
  };
}

/** Editor errors of a refused request, keyed so the load rows and the pickers can show them next to the field. */
export function drivetrainErrorKey(path: string): string {
  return path.replace(/^\/gearbox\/(request|record_id|project)(\/.*)?$/, '/gearbox');
}

// -------------------------------------------------------------------------------------------------------- display

export interface Cell { text: string; missing: boolean; reason: string | null }
const fmt = (v: number, digits: number): string => {
  const a = Math.abs(v);
  return a !== 0 && (a >= 1e6 || a < 1e-3) ? v.toExponential(2) : String(Number(v.toFixed(digits)));
};
/** A number or a dash: null is "not computed", never 0. */
export function cell(value: number | null | undefined, digits = 3, reason: string | null = null): Cell {
  if (value === null || value === undefined || !Number.isFinite(value)) return { text: '-', missing: true, reason };
  return { text: fmt(value, digits), missing: false, reason: null };
}

export interface LossCell { stage: string; mesh: Cell; bearings: Cell; churning: Cell; total: Cell; partial: boolean; missingInputs: string[] }
/** Loss by stage.  A loss the core did not compute is a dash that names the missing inputs; the total is flagged partial then. */
export function lossCells(g: RowGearbox | undefined): LossCell[] {
  return (g?.loss_by_stage ?? []).map((s) => {
    const why = s.loss_inputs_missing.length ? s.loss_inputs_missing.join(', ') : null;
    return {
      stage: s.stage, mesh: cell(s.mesh_W, 3), bearings: cell(s.bearings_W, 3, why), churning: cell(s.churning_W, 3, why),
      total: cell(s.total_W, 3), partial: s.total_partial, missingInputs: s.loss_inputs_missing,
    };
  });
}

/** Chain efficiency: a number only when the route gave one; else a dash with the route's reason. The two parts are separate always. */
export function chainCell(row: DrivetrainRow): Cell {
  return cell(row.chain_efficiency, 4, row.chain_efficiency_reason ?? null);
}
export interface PartCell { efficiency: Cell; at: string }
export function partCells(row: DrivetrainRow): { gearbox: PartCell | null; motor: PartCell | null } {
  const p = row.efficiency_parts;
  if (!p) return { gearbox: null, motor: null };
  return {
    gearbox: { efficiency: cell(p.gearbox.efficiency, 4), at: `${fmt(p.gearbox.at.torque_in_Nm, 3)} N*m, ${fmt(p.gearbox.at.speed_in_rpm, 0)} rpm` },
    motor: p.motor ? { efficiency: cell(p.motor.efficiency, 4), at: `${fmt(p.motor.at.torque_Nm, 3)} N*m, ${fmt(p.motor.at.speed_rpm, 0)} rpm` } : null,
  };
}

export type RowTone = 'ok' | 'warn' | 'missing' | 'failed';
export function rowTone(row: DrivetrainRow): RowTone {
  if (row.status === 'gearbox_failed') return 'failed';
  if (row.status === 'missing') return 'missing';
  return row.covered_by_saved_point === false ? 'warn' : 'ok';
}

/** The motors a page may offer: the family tree's dies and configurations (the server already filters what the account may see). */
export interface MotorOptions { dies: Array<{ name: string; configs: Array<{ name: string; duties: Array<{ name: string; kind: string | null }> }> }> }
export function motorOptions(tree: unknown): MotorOptions {
  const dies = (tree as { dies?: unknown[] } | null)?.dies;
  const out: MotorOptions = { dies: [] };
  for (const d of Array.isArray(dies) ? dies : []) {
    const die = d as { name?: unknown; configs?: unknown[] };
    if (typeof die.name !== 'string') continue;
    const configs = (Array.isArray(die.configs) ? die.configs : []).flatMap((c) => {
      const cfg = c as { name?: unknown; duties?: unknown[] };
      if (typeof cfg.name !== 'string') return [];
      const duties = (Array.isArray(cfg.duties) ? cfg.duties : []).flatMap((x) => {
        const duty = x as { name?: unknown; kind?: unknown };
        return typeof duty.name === 'string' ? [{ name: duty.name, kind: typeof duty.kind === 'string' ? duty.kind : null }] : [];
      });
      return [{ name: cfg.name, duties }];
    });
    out.dies.push({ name: die.name, configs });
  }
  return out;
}

export interface MotorMemory { die: string; configuration: string; rows: LoadRow[]; source: 'inline' | 'record' }
export function validateMotorMemory(raw: unknown): MotorMemory | null {
  const r = raw as MotorMemory | null;
  if (!r || typeof r !== 'object' || typeof r.die !== 'string' || typeof r.configuration !== 'string') return null;
  if (r.source !== 'inline' && r.source !== 'record') return null;
  if (!Array.isArray(r.rows) || r.rows.length === 0 || r.rows.length > MAX_ROWS) return null;
  const names = new Set<string>();
  for (const x of r.rows) {
    if (!x || typeof x.name !== 'string' || typeof x.duty !== 'string' || typeof x.torque_out_Nm !== 'number' || typeof x.speed_out_rpm !== 'number' || names.has(x.name)) return null;
    names.add(x.name);
  }
  return r;
}
