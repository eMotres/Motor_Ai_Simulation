/** Order requests of a project (owner 2026-11-11): a request to our team, never a price. */
export interface OrderRequest { id: string; die: string; config: string; quantity: number; note: string; by: string; at: string; status: 'open' | 'acknowledged' | 'closed' }

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/** The request still being handled for this variant (a closed one lets the customer ask again). */
export function activeOrderRequest(requests: OrderRequest[], die: string, config: string): OrderRequest | null {
  return requests.find((r) => r.status !== 'closed' && same(r.die, die) && same(r.config, config)) ?? null;
}

/** The variant has a computed result to order from (the same facts the Motors & results table shows). */
export function motorHasResults(motor: { can_open_points?: boolean; results?: Array<{ has_result?: boolean }> }): boolean {
  return !!motor.can_open_points || (motor.results ?? []).some((r) => !!r.has_result);
}

/** A whole number of at least 1, else null. */
export function orderQuantity(text: string): number | null {
  const n = Number(text);
  return text.trim() !== '' && Number.isInteger(n) && n >= 1 && n <= 1_000_000 ? n : null;
}
