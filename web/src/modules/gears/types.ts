// Shapes of the gear core's schema-v1 records, only the fields this page reads (docs/gear_module/CORE_API_AND_LIMITATIONS.md).
// Numeric null is "not available", never zero; metric_availability says why.

export type ValidityState = 'valid' | 'estimated' | 'unsupported' | 'failed' | string;
export type Operation = 'point' | 'spectrum' | 'assembly' | 'pose';

export interface FieldError { path: string; code: string; message: string }
export interface Availability { status: string; reason?: string; state?: string; nonfinite?: string }
export interface Rec { schema_type?: string; metric_availability?: Record<string, Availability>; state?: ValidityState; flags?: string[]; missing_inputs?: string[] }

/** The complete canonical GearboxRequest record the template route returns; opaque to the page. */
export type RequestRecord = Record<string, unknown> & { request_id: string; operation: Operation };

export type StageKind = 'planetary' | 'compound_planetary' | 'pair';
export type GearRole = 'sun' | 'planet' | 'planet2' | 'ring' | 'gear1' | 'gear2';

/** Plain gear fields (GearSpec); optional ones are left out of the body when blank. */
export interface PlainGear { z: number; m_n_mm: number; b_mm: number; x?: number; beta_deg?: number; tooth_thickness_allowance_mm?: number; material_name?: string }
export interface PlainStage {
  uid: string;                  // becomes the stable stage_id
  kind: StageKind;
  name: string;
  n_planets?: number;
  fixed_member?: string;
  gears: Partial<Record<GearRole, PlainGear>>;
  bearings: Record<string, string>;
}
export interface PlainBin { torque_out_Nm: number; speed_out_rpm: number; duration_h: number; ambient_temp_C: number; label: string }
export interface PlainBody {
  operation?: Operation; request_id?: string; name?: string; lubricant?: string; preset_name?: string;
  input_speed_rpm?: number; torque_Nm?: number; torque_at?: 'input' | 'output'; power_direction?: 'forward' | 'reverse';
  temp_C?: number; input_angle_deg?: number; outline_stride?: number; outline_once?: boolean;
  stages?: Array<Record<string, unknown>>;
  spectrum?: { bins: PlainBin[]; peak_torque_out_Nm?: number; peak_events?: number; peak_speed_out_rpm?: number };
}

export interface PresetCheck extends Rec { criterion: string; actual_value: number | null; limit_value: number | null; unit: string; passed: boolean }
export interface PartRecord extends Rec { part_name: string; min_safety_factor: number | null; required_safety_factor: number | null; passed: boolean; governing_load_case?: string }
export interface MemberRating extends Rec { member_name: string; S_F: number | null; S_H: number | null; bending_state: ValidityState; pitting_state: ValidityState; is_internal: boolean }
export interface PairRating extends Rec { mesh_name: string; gear1_rating: MemberRating; gear2_rating: MemberRating; min_S_F: number | null; min_S_H: number | null; lubrication?: { eta_mPas?: number | null; lambda_film?: number | null; h_min_um?: number | null; boundary_lubrication_flag?: boolean } & Rec }
export interface BearingLife extends Rec { role: string; bearing_name: string; L10h_h: number | null; L10mh_h: number | null; S0_static: number | null; passed: boolean }
export interface BearingSpectrumLife extends Rec { role: string; bearing_name: string; L10_spectrum_h: number | null; min_S0_static: number | null; passed: boolean }
export interface MinerLife extends Rec { target_name: string; mechanism: string; damage_D: number | null; predicted_life_hours: number | null }
export interface StageResult extends Rec {
  stage_name: string; stage_kind: string; efficiency: number | null; mass_kg: number | null; member_torques_Nm: Record<string, number>;
  pair_ratings: Record<string, PairRating>; bearing_ratings: BearingLife[];
}
export interface EvaluationResult extends Rec {
  gearbox_name: string; overall_ratio: number | null; input_speed_rpm: number | null; input_torque_Nm: number | null;
  output_speed_rpm: number | null; output_torque_Nm: number | null; overall_efficiency: number | null; reverse_efficiency: number | null;
  power_in_W: number | null; power_loss_W: number | null; backlash_out_arcmin: number | null; torsional_stiffness_out_Nm_rad: number | null;
  mass_kg: number | null; envelope_diameter_mm: number | null; envelope_length_mm: number | null;
  passed: boolean; pass_reasons: string[]; preset_name: string; duty_source: string;
  min_gear_life_h: number | null; min_bearing_life_h: number | null;
  gear_miner_lives: MinerLife[]; bearing_spectrum_lives: BearingSpectrumLife[];
  preset_evaluation: ({ all_passed: boolean; checks: PresetCheck[] } & Rec) | null;
  parts_summary: ({ all_passed: boolean; parts: PartRecord[]; min_safety_factor: number | null } & Rec) | null;
  stage_results: StageResult[];
}

export interface Outline extends Rec { z: number; is_internal: boolean; outer_diameter_mm: number | null; outline_mm: Array<[number, number]>; b_mm: number }
export interface Placement { name: string; member_id: string; role: string; stage_index: number; outline_id: string; outline: Outline | null; center_mm: [number, number, number]; face_width_mm: number; reference_angle_deg: number }
export interface AssemblyResult extends Rec { members: Placement[]; outlines: Record<string, Outline>; overall_ratio: number }
export interface PoseMember { name: string; member_id: string; role: string; center_mm: [number, number, number]; rotation_deg: number }
export interface PoseResult extends Rec { input_angle_deg: number; members: Record<string, PoseMember> }

export interface GearboxResponse<R = EvaluationResult | AssemblyResult | PoseResult> {
  request_id: string; operation: Operation; core_version: string; request_hash: string;
  member_instances: Record<string, string[]>; result: R;
}

export interface CataloguesAnswer {
  materials: Array<{ name: string; designation?: string; heat_treatment?: string }>;
  lubricants: Array<{ name: string; kind: string }>;
  bearings: Array<{ name: string; core_supported: boolean; d_mm: number; D_mm: number; B_mm: number }>;
  presets: Array<{ name: string; description?: string }>;
}
