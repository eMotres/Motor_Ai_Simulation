// Tests section (owner 2026-10-26): measured data of one motor configuration, each value compared with the simulation of
// the same point.  Contract: src/motor_ai_sim/motor_tests (routes.py, comparison.py).

export type TestKind = 'parameters' | 'free_run' | 'load';
export const TEST_KINDS: readonly TestKind[] = ['parameters', 'free_run', 'load'];

/** The sub-menus of the Tests section (owner 2026-11-04).  A tab is a VIEW of the comparison groups, not a record kind:
 *  the stored records and the data model are unchanged (see `tabOfGroup` in tableModel.ts). */
export type TestTab = 'power' | 'resistance' | 'inductance' | 'bearings' | 'free_run' | 'loads' | 'parameters';
export const TEST_TABS: readonly TestTab[] = ['power', 'resistance', 'inductance', 'bearings', 'free_run', 'loads', 'parameters'];

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
  /** our engineers only (owner 2026-11-12): absent for the factory, project members and every outsider */
  note_internal?: string;
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
  cogging?: { pkpk_nm?: number | null; magnet_temp_c?: number | null; pending?: boolean };
  dimensions?: Array<{ what: string; value_mm: number; nominal_mm?: number }>;
  rows?: Array<Record<string, number>>;
  /** load tests: kept by the server on an edit, not shown in the form */
  simulation_curves?: SimulationCurve[]; simulation_note?: string;
  created_by?: string; created_at?: string; updated_by?: string; updated_at?: string;
}

export interface CreateDutyAction {
  kind: 'create_duty';
  /** where the current angle came from (EN), e.g. "duty 'rated' (100 A)" */
  gamma_source?: string;
  body: {
    die: string; config: string;
    duty: { name: string; mode: string; rpm: number; current_arms: number; gamma_deg?: number; kind?: string };
  };
}

/** One row of `POST /api/motor-tests/{die}/{config}/compute`'s answer: the point and its queued solve. */
export interface ComputeJob {
  duty: string; run_id?: string; state: 'queued' | 'refused' | 'done'; position?: number; error?: string;
  gamma_deg?: number; gamma_source?: string;
}
export interface ComputeResult { jobs: ComputeJob[]; skipped: Array<{ name: string; reason: string }> }

/** A row of `GET /api/jobs` (only what the panel reads). */
export interface JobRow { run_id: string; state: string; kind?: string; error?: string }

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
/** A load sweep as one series over rpm (backend `comparison._load_series`): one entry per plottable quantity. */
export interface LoadSeriesPoint { rpm: number; current?: number | null; measured: number | null; model: number | null; delta_pct?: number | null }
export interface LoadSeriesQuantity { key: string; label: string; unit: string; tip?: string | null; points: LoadSeriesPoint[] }
/** One bench row as logged (backend `comparison._load_series`, key `points`): the numbers the per-speed plots are formed from;
 *  `model` holds the model's own values only where a stored duty matched the row. */
export interface LoadPlotRow {
  rpm: number; current?: number | null; torque?: number | null; v_dc?: number | null; i_dc?: number | null;
  t_winding?: number | null; t_magnet?: number | null; t_housing?: number | null; time_min?: number | null;
  model?: { torque?: number | null; eff_system?: number | null; t_winding?: number | null; t_magnet?: number | null };
}
/** One simulation curve of a load record (backend `simulation_curves`): the owner's own points at one speed, not duties. */
export interface SimulationCurve {
  rpm: number;
  points: Array<{ current_a?: number | null; torque_nm?: number | null; loss_w?: number | null;
    eff_system_pct?: number | null; i_dc_a?: number | null }>;
}
export interface LoadSeries {
  x: 'rpm'; quantities: LoadSeriesQuantity[]; points?: LoadPlotRow[];
  /** additive: the record's simulation curves and the one line saying what they include */
  simulation_curves?: SimulationCurve[]; simulation_note?: string;
}
export interface TestCompare {
  test_id: string; kind: TestKind; model_available: boolean; groups: CompareGroup[];
  /** load records only: every bench row at once, for the chart beside the per-point tables */
  series?: LoadSeries | null;
}

/** The 3-D end-effect factor a comparison applied and where it came from (nothing is recomputed in the Tests section):
 *  `inherited` = a passport of an earlier geometry, `current` false = the one stored with a duty, `stale` = either. */
export interface End3dProvenance {
  k_flux: number | null; k_torque: number | null; source: string | null;
  inherited?: boolean; inherited_note?: string | null; date?: string | null; current?: boolean; stale?: boolean;
}

export interface TestsPayload {
  die: string; config: string;
  settings: { tests_visible_to_customer?: boolean };
  can: { add: boolean; edit: boolean; settings: boolean };
  tests: TestRecord[];
  /** absent (with `model` and `tips`) in the project view for anyone but our engineers: the measured data only */
  compare?: Record<string, TestCompare>;
  /** the mass of the per-mass columns: weighed whole motor, whole motor from CAD, or active parts only */
  motor_mass?: { kg: number; basis: 'weighed' | 'stated' | 'cad' | 'active' } | null;
  model?: { available: boolean; duties: number; noload: string[]; magnet?: string | null; has_bearings: boolean;
    end3d?: End3dProvenance | null };
  tips?: Record<string, string>;
  documents?: Array<{ id: string; file_id: string; name: string }>;
}
