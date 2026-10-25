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
export interface SchemaProperty { id: string; suggestions?: SchemaSuggestion[]; label: Localized; short_label?: Localized; help?: Localized; type?: string; unit?: string; allow_range?: boolean; required?: boolean; required_flag?: string; min?: number; max?: number; read_only?: boolean; properties?: SchemaProperty[]; items?: SchemaItems; options?: Array<string | { value: string; label?: Localized }> }
export interface SchemaField { id: string; suggestions?: SchemaSuggestion[]; label: Localized; short_label?: Localized; type?: string; unit?: string; required?: boolean; required_flag?: string; required_mark?: string; min_filled_properties?: number; /** each group needs at least one filled property (e.g. torque OR power) */ required_any?: string[][]; /** asked and required only for these cooling media (or while none is chosen) */ applies_when_cooling_medium?: string[]; multiline?: boolean; allow_not_known_yet?: boolean; handled_by?: string; storage_namespace?: 'fields' | 'production'; presentation_group?: string; source_section_id?: string; help?: Localized; options?: Array<string | { value: string; label?: Localized }>; choices?: Array<string | { value: string; label?: Localized }>; columns?: SchemaColumn[]; properties?: SchemaProperty[]; items?: SchemaItems; min?: number; max?: number; allow_range?: boolean }
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
export interface ProjectMotor { die: string; canonical_die?: string; config: string; status?: 'current' | 'trial'; available?: boolean; can_open_configure?: boolean; /** any stored duty result: Configure opens as a points-only card */ can_open_points?: boolean; configure_duty?: string | null; can_open_simulation?: boolean; simulation_duty?: string | null; duties?: Array<string | { name?: string | null }>; default_duty?: string | null; results?: MotorResult[]; kind?: 'history'; snapshot_id?: string; created_at?: string; author_display_name?: string; note?: string; source?: string }
/** One stored duty point of the points-only Configure card: stored numbers only. */
export interface MotorPoint { duty: string; has_result?: boolean; operating_point?: Record<string, unknown>; electromagnetic?: Record<string, unknown>; comparison?: Record<string, unknown>; stored_results?: Record<string, unknown> }
/** One design step (linked motor) of a project with its stored points; `note` is the engineers' short design-step note. */
export interface MotorPointVariant { step: number; die: string; canonical_die: string; config: string; status: 'current' | 'trial'; note: string; points: MotorPoint[] }
export interface MotorPointsAnswer { project_id: string; variants: MotorPointVariant[]; selected: { die: string; config: string } | null }
export interface ProjectMotorPickerItem { die: string; config: string; label?: string; diameter_mm?: number; slots?: number; poles?: number; stack_mm?: number; linked_at?: string; unlinked_at?: string; unlinked_by?: string; disabled?: boolean; reason?: string }
export interface ProjectSnapshot { id: string; kind: 'history'; die?: string; config?: string; label?: string; diameter_mm?: number; slots?: number; poles?: number; stack_mm?: number; created_at?: string; author_display_name?: string; note?: string; source?: string; results?: MotorResult[] }
