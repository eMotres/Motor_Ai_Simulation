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
  share_costs_with_oem?: boolean;
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
  kind: 'made' | 'purchased' | 'consumable';
  qty_per_motor: number;
  material: string;
  spec: string;
  material_i18n?: Record<Lang, string>;
  spec_i18n?: Record<Lang, string>;
  description: Bilingual;
  drawing_files: DrawingFile[];
  optional_step: { file_id: string } | null;
  revision: string;
}

export interface BomRow {
  part_id: string;
  qty_per_motor: number;
  order_total: number;
  unit_price?: number | null;
  currency?: string;
  note?: string | null;
  line_total?: number | null;
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
}

export interface CostSummary {
  parts_subtotal: number;
  extra_subtotal: number;
  cost_per_motor: number;
  margin_pct: number;
  price_per_motor: number;
  order_quantity: number;
  order_parts_total: number;
  order_extra_total: number;
  order_cost_total: number;
  order_price_total: number;
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
  reload: () => void;
}
