// Production module — data model (docs/PRODUCTION_MODULE_DESIGN.md section 4).

export type Lang = 'en' | 'zh';
export type TranslationStatus = 'machine' | 'confirmed' | 'pending';

/** Bilingual text: the author's language is the original, the other side is a machine translation until confirmed. */
export interface Bilingual {
  en: string;
  zh: string;
  src_lang: Lang;
  status: { en: TranslationStatus; zh: TranslationStatus };
}

export type MemberRole = 'owner' | 'engineer' | 'oem';
/** The caller's standing in one production ('admin' = platform admin who is not a member). */
export type CallerRole = MemberRole | 'admin' | null;

export interface Member { email: string; role: MemberRole }

export interface Production {
  die: string;
  config: string;
  title: Bilingual;
  order_quantity: number;
  status: 'active' | 'paused' | 'archived';
  current_revision: string;
  /** the PRODUCT revision of the motor (Rev A, Rev B ...; the level above this production's own revisions); absent when none */
  product_revision?: { rev: string; label?: string; lineage?: string; latest?: boolean; frozen?: boolean } | null;
  share_costs_with_oem?: boolean;
  /** what is produced: a motor (default) or electronics (the 800 V controller) */
  product_type?: 'motor' | 'electronics';
  members: Member[];
  created_at: string;
  updated_at: string;
  /** admin list only: members without a signed NDA (when the server reports it) */
  members_without_nda?: string[];
}

export interface DrawingFile { file_id: string; kind: 'pdf' | 'png'; name: string }

export interface Part {
  id: string;
  name: Bilingual;
  kind: 'made' | 'purchased' | 'consumable' | 'assembly';
  qty_per_motor: number;
  material: string;
  spec: string;
  material_i18n?: Record<Lang, string>;
  spec_i18n?: Record<Lang, string>;
  description: Bilingual;
  drawing_files: DrawingFile[];
  optional_step: { file_id: string } | null;
  revision: string;
  parent_id?: string | null;
}

export interface AssemblyNode {
  id: string;
  name: Bilingual;
  kind: 'made' | 'purchased' | 'consumable' | 'assembly';
  qty_per_motor: number;
  parent_id?: string | null;
  unit_price?: number | null;
  line_total?: number | null;
  children?: AssemblyNode[];
}

export interface AssemblyTree {
  id: string;
  name: Bilingual;
  sub_assemblies: Array<{
    node: AssemblyNode;
    children: AssemblyNode[];
  }>;
}

export interface BomRow {
  part_id: string;
  qty_per_motor: number;
  order_total: number;
  unit_price?: number | null;
  currency?: string;
  note?: string | null;
  line_total?: number | null;
  procurement?: ProcurementLine | null;
}

export type ProcurementStatus =
  | 'received'
  | 'partial'
  | 'late'
  | 'at_risk'
  | 'confirmed'
  | 'ordered'
  | 'to_order'
  | 'not_ordered';

export interface Receipt {
  date: string;
  timestamp?: string | null;
  qty: number;
  accepted: number;
  rejected: number;
  inspector: string;
  note: string;
}

export interface ProcurementLine {
  part_id: string;
  supplier: string;
  po_number: string;
  lead_time_days: number;
  requested_on: string | null;
  confirmed_on: string | null;
  ordered_on: string | null;
  due_on: string | null;
  need_by: string | null;
  order_by?: string | null;
  expected_arrival?: string | null;
  qty_ordered: number | null;
  qty_needed?: number;
  qty_per_motor?: number;
  received_qty?: number;
  rejected_qty?: number;
  received_on?: string | null;
  receipts: Receipt[];
  status?: ProcurementStatus;
  is_late?: boolean;
  is_at_risk?: boolean;
  is_to_order?: boolean;
  has_quality_issue?: boolean;
  note: string | null;
  updated_by?: string;
  updated_at?: string;
  part_name?: Bilingual;
  part_kind?: 'made' | 'purchased' | 'consumable';
  material?: string;
  spec?: string;
  drawing_files?: DrawingFile[];
}

export interface AssemblyWarning {
  part_id: string;
  expected_arrival: string;
  assembly_planned_start: string;
  message: string;
}

export interface ReadinessSummary {
  parts_received_count: number;
  parts_total_count: number;
  received_pct: number;
  qty_received_total: number;
  qty_needed_total: number;
  qty_received_pct: number;
  late_count: number;
  at_risk_count: number;
  to_order_count: number;
  quality_issue_count: number;
  ready_for_assembly: boolean;
  latest_expected_arrival: string | null;
  status_message: string;
  assembly_warnings: AssemblyWarning[];
}

export interface ProcurementData {
  rows: ProcurementLine[];
  summary: ReadinessSummary;
}

export type SchedulePhaseStatus = 'not_started' | 'in_progress' | 'done' | 'blocked';
export type ScheduleOwnerRole = 'us' | 'factory';

export interface SchedulePhase {
  id: string;
  key: string;
  title: { en: string; zh: string };
  planned_start: string;
  planned_end: string;
  actual_start: string;
  actual_end: string;
  status: SchedulePhaseStatus;
  depends_on: string[];
  percent_complete: number;
  owner_role: ScheduleOwnerRole;
  note: string;
  duration_days?: number;
  early_start_day?: number;
  early_finish_day?: number;
  slack_days?: number;
  is_critical?: boolean;
}

export interface Milestone {
  id: string;
  title: { en: string; zh: string };
  date: string;
  done: boolean;
  rule?: string | null;
}

export interface ScheduleBaseline {
  phases?: Array<{
    id: string;
    key?: string;
    planned_start: string;
    planned_end: string;
  }>;
  milestones?: Array<{
    id: string;
    date: string;
  }>;
  frozen_at?: string;
  frozen_by?: string;
}

export interface ScheduleData {
  phases: SchedulePhase[];
  tasks: SchedulePhase[];
  milestones: Milestone[];
  baseline: ScheduleBaseline;
  procurement_summary?: ReadinessSummary;
}

export interface TimelineProduction {
  config: string;
  title: Bilingual;
  order_quantity: number;
  status: 'active' | 'paused' | 'archived';
  procurement: ProcurementData;
  schedule: ScheduleData;
}

export interface TimelineData {
  die: string;
  productions: TimelineProduction[];
}

export interface CostRow {
  part_id: string;
  unit_price: number | null;
  currency: string;
  note: string | null;
  line_total?: number | null;
}

export interface ExtraCost {
  id: string;
  name: Bilingual;
  amount: number;
  /** same columns as a BOM line (owner 2026-10-18) */
  currency?: string;
  note?: string | null;
}

/** Totals of one currency; amounts in different currencies are never added together. */
export interface CurrencyTotals {
  currency: string;
  parts_subtotal: number;
  extra_subtotal: number;
  cost_per_motor: number;
  price_per_motor: number;
  order_parts_total: number;
  order_extra_total: number;
  order_cost_total: number;
  order_price_total: number;
}

export interface CostSummary {
  margin_pct: number;
  order_quantity: number;
  /** one entry per currency in use (the default currency when nothing is priced), in display order */
  by_currency: CurrencyTotals[];
  missing_prices_count: number;
  warnings: string[];
}

export interface CostsData {
  rows: CostRow[];
  extras: ExtraCost[];
  margin_pct: number;
  summary: CostSummary;
}

export type ProcessKind = 'fits' | 'assembly' | 'winding' | 'tests';
export interface ProcessSection {
  id: string;
  kind: ProcessKind;
  title: Bilingual;
  body: Bilingual;
  tables: string[][][];
  tables_i18n?: {
    zh?: string[][][];
    en?: string[][][];
  };
}

export interface DocumentRec {
  id: string;
  uploader_email: string;
  uploaded_at: string;
  part_id: string | null;
  file_id: string;
  name: string;
  /** our own working document: hidden from the factory (OEM members) */
  internal?: boolean;
  /** a picture posted to one assembly step */
  step_id?: string | null;
}

export interface Attachment { file_id: string; name: string }

export interface Message {
  id: string;
  author_email: string;
  src_lang: Lang;
  text: Bilingual;
  attachments: Attachment[];
  created_at: string;
}

export interface Thread {
  id: string;
  part_id: string | null;
  created_at: string;
  messages: Message[];
}

export type ProposalStatus = 'open' | 'accepted' | 'rejected' | 'implemented';
export interface Proposal {
  id: string;
  part_id: string | null;
  author_email: string;
  text: Bilingual;
  status: ProposalStatus;
  decision_note: Bilingual | null;
  linked_revision: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface Acknowledgement { email: string; time: string }
export interface Revision {
  number: string;
  date: string;
  author_email: string;
  summary: Bilingual;
  changes: Bilingual[];
  acknowledgements: Acknowledgement[];
}

/** Which bilingual field a confirm/retry call addresses. */
export interface TranslationRef {
  entity: 'production' | 'part' | 'process' | 'thread' | 'proposal' | 'revision';
  entity_id: string;
  /** dotted path inside the entity, e.g. "name", "text", "changes.2" */
  field: string;
  /** message id inside a thread, when entity is "thread" */
  message_id?: string;
}

/** Everything a panel needs to know about who is looking at which production. */
export interface ProdCtx {
  die: string;
  config: string;
  role: CallerRole;
  email: string;
  uiLang: Lang;
  canEdit: boolean;
  /** 'electronics' productions have no motor CAD model: no per-part 3-D view */
  productType?: 'motor' | 'electronics';
  /** 'tooling' = the separate tooling / fixtures order list next to the motor BOM (owner 2026-10-19); absent = the motor BOM */
  list?: 'tooling';
  reload: () => void;
}


/** One step of the assembly PROCESS (owner 2026-10-17). */
export interface AssemblyStep {
  id: string;
  n: number;
  title: Bilingual;
  kind: 'operation' | 'check';
  parts: string[];
  after: string[];
  /** the process sections of this step by id (empty = matched by title keywords) */
  sections?: string[];
  /** the step's own documents by id (a test drawing for a check step), shown with its parts' drawings */
  documents?: string[];
  note: Bilingual | null;
  parts_resolved?: Array<{ id: string; name: Bilingual }>;
}
export interface AssemblyProcessView { default: boolean; steps: AssemblyStep[] }

/** GET /api/production/{die}/{config}/access - the answer behind a shared documents link. */
export type ProductionAccess =
  | { access: 'member'; role: MemberRole | 'admin'; can_share: boolean; factory: boolean }
  | { access: 'none'; requested: boolean };
