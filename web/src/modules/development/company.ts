/** The project's Company details form (owner 2026-11-22); the server owns the field list. */
export interface CompanyField { id: string; group: string; kind: string }
export interface CompanyDetails {
  values: Record<string, string>;
  confirmed: { by: string; at: string } | null;
  completed: boolean;
  ready_to_confirm: boolean;
  can_confirm: boolean;
  fields: CompanyField[];
  required: string[];
  prefill?: Record<string, string>;
}
export const COMPANY_GROUPS = ['legal', 'signatory', 'delivery'] as const;
