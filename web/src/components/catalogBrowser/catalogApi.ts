/** The reference-catalogue API (`src/motor_ai_sim/routes/catalog_cards.py`). */
import type { CardEnvelope, CardSummary, CatalogKind } from './catalogLogic';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API}/api/catalog/cards`;

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const d = (await r.json())?.detail;
      msg = typeof d === 'string' ? d : (d?.message ?? JSON.stringify(d));
    } catch { /* keep */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const listCards = (kind: CatalogKind) =>
  fetch(`${BASE}/${kind}`, { cache: 'no-store' })
    .then(j<{ kind: CatalogKind; cards: CardSummary[] }>).then((r) => r.cards);

export const getCard = (kind: CatalogKind, id: string) =>
  fetch(`${BASE}/${kind}/card?id=${encodeURIComponent(id)}`, { cache: 'no-store' })
    .then(j<CardEnvelope>);

export interface UsedBy { die: string; config: string; count: number; where: string }

export const getUsedBy = (kind: CatalogKind, id: string) =>
  fetch(`${BASE}/${kind}/used_by?id=${encodeURIComponent(id)}`, { cache: 'no-store' })
    .then(j<{ machines: UsedBy[] }>).then((r) => r.machines);

/** Admin only — the route answers 401/403 to anybody else. */
export const addDeviceCard = (cardYaml: string, overwrite = false) =>
  fetch(`${BASE}/device`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ card_yaml: cardYaml, overwrite }),
  }).then(j<{ ok: boolean; part: string; file: string }>);
