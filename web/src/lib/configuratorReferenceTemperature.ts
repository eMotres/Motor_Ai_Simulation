/**
 * Source-pinned input temperatures of a reviewed (audited) Configure reference card.
 * This is not an operating-temperature prediction or a 20 °C constant set.
 *
 * The audited facts are NOT in this bundle (review F4, 2026-10-20): the server sends them
 * with the card in `GET /api/catalog/references` as `reference_basis`, filtered by the same
 * visibility rule as the card itself.  This module only validates what arrived.
 */

export interface ReferenceTemperatureBasis {
  windingC: number;
  magnetC: number;
  /** Steel has no independently recorded solve temperature in the audited source. */
  steelC: number | null;
  stackLengthMm: number;
  rpm: number;
  currentA: number;
  sourceRecord: string;
  sourceSha256: string;
  coldConstantsAvailable: boolean;
}

/** What the server says about a card: `required` = fail closed unless `basis` is usable. */
export interface ReferenceBasisAnswer {
  required: boolean;
  basis: ReferenceTemperatureBasis | null;
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Validate the server's basis object; anything malformed is null (fail closed). */
export function parseReferenceTemperatureBasis(value: unknown): ReferenceTemperatureBasis | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const b = value as Record<string, unknown>;
  if (!isNum(b.windingC) || !isNum(b.magnetC) || !isNum(b.stackLengthMm) || !isNum(b.rpm) || !isNum(b.currentA)) return null;
  if (!(b.steelC === null || isNum(b.steelC))) return null;
  if (typeof b.sourceRecord !== 'string' || typeof b.sourceSha256 !== 'string') return null;
  if (typeof b.coldConstantsAvailable !== 'boolean') return null;
  return Object.freeze({
    windingC: b.windingC, magnetC: b.magnetC, steelC: b.steelC as number | null,
    stackLengthMm: b.stackLengthMm, rpm: b.rpm, currentA: b.currentA,
    sourceRecord: b.sourceRecord, sourceSha256: b.sourceSha256,
    coldConstantsAvailable: b.coldConstantsAvailable,
  });
}

/** The catalog entry's `reference_basis` block, or null when the server sent none. */
export function parseReferenceBasisAnswer(value: unknown): ReferenceBasisAnswer | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  return { required: v.required === true, basis: parseReferenceTemperatureBasis(v.basis) };
}

/** The audited basis of a card, or null (not an audited card, or the server's basis is unusable). */
export function configuratorReferenceTemperatureBasis(
  answer: ReferenceBasisAnswer | null | undefined,
): ReferenceTemperatureBasis | null {
  return answer?.basis ?? null;
}

/** Resistance reference temperature. Standard extraction_rev 2+ calls the
 *  transient solver with its explicit 120°C default; an audited reference
 *  carries its own winding temperature (server basis). Older/imported records
 *  without provenance stay explicitly unknown instead of inheriting either value. */
export function configuratorResistanceReferenceTemperature(
  answer: ReferenceBasisAnswer | null | undefined,
  extractionRev: unknown,
  recordedTempC: unknown,
): number | null {
  const pinned = configuratorReferenceTemperatureBasis(answer);
  if (pinned) return pinned.windingC;
  const recorded = Number(recordedTempC);
  if (recordedTempC != null && Number.isFinite(recorded) && recorded >= -50 && recorded <= 400) return recorded;
  const rev = Number(extractionRev);
  return Number.isFinite(rev) && rev >= 2 ? 120 : null;
}
