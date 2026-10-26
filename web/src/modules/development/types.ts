export type ProjectStatus = 'spec' | 'design' | 'simulation' | 'quote' | 'prototype' | 'production' | 'closed';
export interface ProjectMember { email: string; role: 'customer' | 'engineer' | 'owner'; pending?: boolean }
export interface Completeness { filled?: number; required?: number; filled_required?: number; total_required?: number; answered?: number; unknown?: number; missing?: number; invalid?: number; sections?: Record<string, { filled: number; required: number; total?: number; complete?: boolean }> }
export interface ProjectSummary {
  id: string; title: string; customer_org: string; status: ProjectStatus; owner_engineer?: string;
  members: ProjectMember[]; linked_motors: Array<{ die: string; config: string }>;
  grant_removal_candidates?: Array<{ email: string; die: string; config: string; reason: string }>;
  can_edit?: boolean; can_edit_spec?: boolean; completeness?: Completeness; notes?: string;
  created_at?: string; updated_at?: string;
}
export type Localized = string | { en?: string; 'zh-CN'?: string };
export interface SchemaColumn { id: string; label: Localized; type?: string; unit?: string }
export interface SchemaItems { type?: string; properties?: SchemaProperty[] }
/** UI-only menu entries of a free text / number value (the backend ignores them): the EN value is stored. */
export type SchemaSuggestion = number | string | { value: string | number; label?: Localized };
export interface SchemaProperty { id: string; suggestions?: SchemaSuggestion[]; label: Localized; short_label?: Localized; help?: Localized; type?: string; unit?: string; allow_range?: boolean; required?: boolean; required_flag?: string; /** required when the answer's cooling medium is this one (coolant inlet / water temperature) */ required_when_cooling_medium?: string; min?: number; max?: number; read_only?: boolean; /** kept in the data, not asked on the page */ ui_hidden?: boolean; /** pre-filled editable default (counted as filled) */ default?: unknown; properties?: SchemaProperty[]; items?: SchemaItems; options?: Array<string | { value: string; label?: Localized }> }
export interface SchemaField { id: string; suggestions?: SchemaSuggestion[]; label: Localized; short_label?: Localized; type?: string; unit?: string; required?: boolean; required_flag?: string; required_mark?: string; min_filled_properties?: number; /** each group needs at least one filled property (e.g. torque OR power) */ required_any?: string[][]; /** asked and required only for these cooling media (or while none is chosen) */ applies_when_cooling_medium?: string[]; /** asked only while one of these media is chosen (not while none is) */ only_when_cooling_medium?: string[]; multiline?: boolean; allow_not_known_yet?: boolean; handled_by?: string; storage_namespace?: 'fields' | 'production'; presentation_group?: string; source_section_id?: string; help?: Localized; options?: Array<string | { value: string; label?: Localized }>; choices?: Array<string | { value: string; label?: Localized }>; columns?: SchemaColumn[]; properties?: SchemaProperty[]; items?: SchemaItems; min?: number; max?: number; allow_range?: boolean; ui_hidden?: boolean }
export interface SchemaSection { id: string; title: Localized; fields?: SchemaField[]; kind?: string; mission_profile?: boolean; production?: boolean }
export interface DevelopmentSchema { schema_version?: number; sections: SchemaSection[] }
export interface SpecVersion { version: number; author_display_name?: string; created_at?: string }
export interface SpecState { draft: Record<string, unknown>; revision: number; schema_version?: number; schema?: DevelopmentSchema; completeness: Completeness; versions?: SpecVersion[]; version?: number }
export interface SpecSnapshot { version: number; schema_version?: number; schema?: DevelopmentSchema; author_display_name?: string; created_at?: string; spec?: Record<string, unknown>; draft?: Record<string, unknown> }
export interface ProjectThreadMessage { id?: string; author_display_name?: string; created_at: string; text: string; field_ref?: string; row_id?: string; version?: number; revision?: number; resolved?: boolean; resolved_at?: string; resolved_by?: string }
export interface ResultProvenance {
  kind: 'estimate' | 'solve' | 'unknown'; source?: string; run_id?: string;
  revision?: string; input_fingerprint?: string; geometry_fingerprint?: string;
  materials_fingerprint?: string; solver_version?: string;
}
export interface MotorResult { duty?: string; has_result?: boolean; can_open_configure?: boolean; can_open_simulation?: boolean; operating_point?: Record<string, unknown>; electromagnetic?: Record<string, unknown>; stored_results?: Record<string, unknown>; comparison?: Record<string, unknown>; provenance?: ResultProvenance; result_provenance?: string; torque_Nm?: number; power_W?: number; efficiency_pct?: number; losses_W?: number; temperatures_C?: Record<string, number> | number; computed_at?: string }
/** `canonical_die` is used for link management; `die` is the caller's ordinary resolver alias for opening. */
/** A staff mark on a linked motor: who set it and when (`report_commit`: the code the reviewer looked at). */
export interface StaffFlag { by?: string; at?: string; report_commit?: string }
export interface ProjectMotor { /** staff checked this variant's report: members may download it */ report_reviewed?: StaffFlag | null; /** THE recommended variant of the project (one at a time, a staff choice) */ recommended?: StaffFlag | null; die: string; canonical_die?: string; config: string; /** when the variant joined the project (ISO); absent on a legacy row: "Latest" ranks it older than any stamped variant */ linked_at?: string | null; status?: 'current' | 'trial'; available?: boolean; can_open_configure?: boolean; /** any stored duty result: Configure opens as a points-only card */ can_open_points?: boolean; configure_duty?: string | null; can_open_simulation?: boolean; simulation_duty?: string | null; duties?: Array<string | { name?: string | null }>; default_duty?: string | null; results?: MotorResult[]; kind?: 'history'; snapshot_id?: string; created_at?: string; author_display_name?: string; note?: string; source?: string }
/** One stored duty point of the points-only Configure card: stored numbers only. */
export interface MotorPoint { duty: string; has_result?: boolean; operating_point?: Record<string, unknown>; electromagnetic?: Record<string, unknown>; comparison?: Record<string, unknown>; stored_results?: Record<string, unknown> }
/** One design step (linked motor) of a project with its stored points; `note` is the engineers' short design-step note. */
export interface MotorPointVariant { /** staff marks of the variant (recommended / report reviewed) */ report_reviewed?: StaffFlag | null; recommended?: StaffFlag | null; step: number; die: string; canonical_die: string; config: string; status: 'current' | 'trial'; note: string; points: MotorPoint[] }
export interface MotorPointsAnswer { project_id: string; variants: MotorPointVariant[]; selected: { die: string; config: string } | null }
/** Where a stored block came from (every field is optional: an old row has no solver stamp). */
export interface StoredProvenance { computed_at?: string; recorded_at?: string; code_version?: string; app_version?: string; image?: string; geometry_fingerprint?: string; mesh_size_mm?: number; n_triangles?: number; element_order?: number }
export type FieldKind = 'em' | 'thermal' | 'rotor_stress';
export interface StoredThermal {
  /** the DUTY's temperatures: the converged coupled loop at the duty's own point, else the steady temperature map; `kind` says which */
  duty_temps?: { kind: 'coupled' | 'thermal'; winding_c?: number; magnet_c?: number; magnet_max_c?: number; bearing_c?: number; housing_c?: number; converged?: boolean; drive?: string; /** the coupled loop's OWN electromagnetic numbers (its torque / losses at its own temperatures) */ em?: { torque_nm?: number; p_loss_total_w?: number; efficiency_pct?: number }; provenance?: StoredProvenance };
  /** an S1 / limit-search state of the coupled loop: shown on its own line, never as the duty's temperature */
  limit_search?: { kind: string; solve_to?: 'continuous' | 'limits' | string; mode?: string; winding_c?: number; magnet_c?: number; provenance?: StoredProvenance };
  s1?: { ok?: boolean; feasible?: boolean; I_cont_A_rms?: number; limiting_part?: string; limits_c?: Record<string, number>; provenance?: StoredProvenance };
  s2?: { within_limits?: boolean; limiting_part?: string; time_to_limit_s?: number; limits_c?: Record<string, number>; starts?: Record<string, { time_to_limit_s?: number }>; provenance?: StoredProvenance };
  cycle?: Record<string, unknown>;
  provenance?: StoredProvenance;
}
export interface StoredRotorStress { case?: string; sf_min?: number; sf_min_part?: string; sf_min_p05?: number; overspeed_factor?: number; max_displacement_um?: number; rotor_od_growth_um?: number; interference_mm?: number; proof_rpm?: number; limit_speed_rpm?: number;
  /** the load case of the record, from its own fields */
  worst_case?: { rpm?: number; torque_nm?: number; loads?: string; case?: string;
    /** the magnetic pull of the case (2026-10-26): `em` (the duty's field) or `uniform` (an estimate), its peak pressure */
    pull_source?: 'em' | 'uniform'; pull_mpa?: number; pull_per_pole_n?: number; pull_label?: string; pull_estimate?: boolean };
  /** the load case the retention verdict was reached under, when it carried a pull */
  load_case?: string;
  /** the speed the solve was asked at (the proof case may run above it) */
  request_rpm?: number;
  filed_under?: string; records?: number;
  /** superseded = made before the contact repair (#422); unknown = no date */
  solver_status?: 'current' | 'superseded' | 'unknown';
  /** differs = another machine print than the newest EM / thermal record of the motor */
  geometry_status?: 'match' | 'differs' | 'unknown';
  stale?: boolean;
  retention?: { status?: string; verdict?: string; message?: string }; magnet_retention?: Record<string, unknown>; air_gap?: Record<string, number>; parts?: Record<string, { material?: string; safety_factor?: number; von_mises_max_mpa?: number; strength_mpa?: number }>; provenance?: StoredProvenance }
export interface StoredMechanical { rotor_stress?: StoredRotorStress; modes?: { frequencies_hz?: number[]; provenance?: StoredProvenance }; critical_speeds?: { verdict?: string; rated_rpm?: number; critical_speeds?: Array<{ rpm?: number }>; provenance?: StoredProvenance } }
export interface MotorViewDuty extends MotorPoint { /** where the EM tiles come from: the EM run (fixed winding temperature) or, with no EM torque stored, the coupled loop */ sources?: { em?: { kind?: 'em_run' | 'coupled'; coil_temp_c?: number; magnet_temp_c?: number } }; em_run?: { numbers?: Record<string, number>; provenance?: StoredProvenance } | null; thermal?: StoredThermal | null; mechanical?: StoredMechanical | null; fields?: Partial<Record<FieldKind, { provenance?: StoredProvenance; point?: Record<string, number> }>>; kinds?: string[]; /** records filed under this duty that were left out (another point, superseded, impossible numbers) - diagnostics, never drawn */ hidden_records?: Array<{ kind: string; reason: string; value?: string }> }
/** `mode`: full = a passport exists (ordinary Configure); points = stored points only (points-limited Configure); empty = nothing stored. */
export interface MotorView { die: string; config: string; configure_available: boolean; configure_duties: string[]; mode: 'full' | 'points' | 'empty'; duties: MotorViewDuty[]; solver_now?: { code_version?: string; version?: string } }
/** One stored field map: the payload has the shape the tab adapter of its kind draws. */
export interface StoredField { kind: FieldKind; duty: string; payload: Record<string, unknown> & { views: string[] }; provenance: StoredProvenance; point?: Record<string, number> }
export interface ProjectMotorPickerItem { die: string; config: string; label?: string; diameter_mm?: number; slots?: number; poles?: number; stack_mm?: number; linked_at?: string; unlinked_at?: string; unlinked_by?: string; disabled?: boolean; reason?: string }
export interface ProjectSnapshot { id: string; kind: 'history'; die?: string; config?: string; label?: string; diameter_mm?: number; slots?: number; poles?: number; stack_mm?: number; created_at?: string; author_display_name?: string; note?: string; source?: string; results?: MotorResult[] }
