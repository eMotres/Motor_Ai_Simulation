// Tests section (owner 2026-10-26): measured data of one motor configuration, each value compared with the simulation of
// the same point.  Contract: src/motor_ai_sim/motor_tests (routes.py, comparison.py).

export type TestKind = 'parameters' | 'free_run' | 'load';
export const TEST_KINDS: readonly TestKind[] = ['parameters', 'free_run', 'load'];

export interface TestConditions {
  ambient_c?: number; humidity_pct?: number; rotor?: 'in' | 'out'; state?: string; temp_point?: string;
  magnet_temp_c?: number; winding_temp_c?: number;
}
export interface TestLeads { count?: number; length_mm?: number; awg?: number; area_mm2?: number }
export interface PairValue { pair: string; value_mohm?: number; value_uh?: number; rotor_angle_deg?: number }

export interface TestRecord {
  id: string;
  kind: TestKind;
  date: string;
  title?: string; operator?: string; instrument?: string; profile?: string; note?: string;
  conditions?: TestConditions;
  leads?: TestLeads;
  documents?: string[];
  resistance?: { temp_c?: number; pairs: PairValue[] };
  inductance?: { freq_hz?: number; level_v?: number; pairs: PairValue[] };
  lcr?: Array<{ pair: string; mode: 'series' | 'parallel'; freq_hz?: number; level_v?: number; quantity: string; value: number; unit: string }>;
  insulation?: Array<{ voltage_v: number; resistance_mohm: number; more_than?: boolean }>;
  hipot?: Array<{ voltage_v: number; leakage_ma?: number; time_s?: number; supply?: 'ac' | 'dc' }>;
  surge?: Array<{ voltage_v: number; result: 'ok' | 'fail'; note?: string }>;
  weight?: Array<{ what: string; mass_g: number }>;
  dimensions?: Array<{ what: string; value_mm: number; nominal_mm?: number }>;
  rows?: Array<Record<string, number>>;
  created_by?: string; created_at?: string; updated_by?: string; updated_at?: string;
}

export interface CreateDutyAction {
  kind: 'create_duty';
  body: { die: string; config: string; duty: { name: string; mode: string; rpm: number; current_arms: number } };
}

export interface CompareRow {
  key: string; label: string; params: Record<string, unknown>; unit: string;
  measured: number | null; model: number | null; delta_pct: number | null;
  tip?: string | null; detail?: string; status: 'ok' | 'no_model' | 'info';
  extra: Record<string, unknown>; action?: CreateDutyAction | null;
}
export interface CompareGroup {
  key: string; label: string; params: Record<string, unknown>; tip?: string | null; rows: CompareRow[];
  /** one line saying what the model values are (EN) + the key / params the web translates (tests.basis.<key>) */
  basis?: string | null; basis_key?: string | null; basis_params?: Record<string, unknown>;
}
export interface TestCompare { test_id: string; kind: TestKind; model_available: boolean; groups: CompareGroup[] }

export interface TestsPayload {
  die: string; config: string;
  settings: { tests_visible_to_customer?: boolean };
  can: { add: boolean; edit: boolean; settings: boolean };
  tests: TestRecord[];
  compare: Record<string, TestCompare>;
  model: { available: boolean; duties: number; noload: string[]; magnet?: string | null; has_bearings: boolean;
    end3d?: { k_flux: number | null; k_torque: number | null; source: string | null } | null };
  tips: Record<string, string>;
  documents?: Array<{ id: string; file_id: string; name: string }>;
}
