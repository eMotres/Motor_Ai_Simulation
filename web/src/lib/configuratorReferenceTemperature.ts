/**
 * Source-pinned input temperatures for the one reviewed D85 Configure reference.
 * This is not an operating-temperature prediction or a 20 °C constant set.
 */
export const D85_REFERENCE_CANDIDATE_SHA256 =
  '7d333cee34b8783a4b581d19f1ca7733659e0fb79e758ca0f2d53be10cf056df';
export const D85_REFERENCE_LEDGER_SHA256 =
  '03f600ce90b9462d53cc09bf88f9e55c375c1e63c6d37deb14d2c1b84e87b1b4';

export interface ReferenceTemperatureBasis {
  windingC: number;
  magnetC: number;
  /** Steel has no independently recorded solve temperature in this source. */
  steelC: null;
  stackLengthMm: 13;
  rpm: 1000;
  currentA: 25.88;
  sourceRecord: 'lchk_rated_72steps';
  sourceSha256: string;
  coldConstantsAvailable: false;
}

const D85_TEMPERATURE_BASIS: ReferenceTemperatureBasis = Object.freeze({
  windingC: 180,
  magnetC: 150,
  steelC: null,
  stackLengthMm: 13,
  rpm: 1000,
  currentA: 25.88,
  sourceRecord: 'lchk_rated_72steps',
  sourceSha256: D85_REFERENCE_LEDGER_SHA256,
  coldConstantsAvailable: false,
});

/** Resolve only the exact candidate whose source record and temperatures were audited. */
export function configuratorReferenceTemperatureBasis(
  candidateSha256: unknown,
): ReferenceTemperatureBasis | null {
  return typeof candidateSha256 === 'string'
    && candidateSha256.toLowerCase() === D85_REFERENCE_CANDIDATE_SHA256
    ? D85_TEMPERATURE_BASIS
    : null;
}

/** Resistance reference temperature. Standard extraction_rev 2+ calls the
 *  transient solver with its explicit 120°C default; the reviewed D85 source
 *  is the exception (180°C). Older/imported records without provenance stay
 *  explicitly unknown instead of inheriting either value. */
export function configuratorResistanceReferenceTemperature(
  candidateSha256: unknown,
  extractionRev: unknown,
  recordedTempC: unknown,
): number | null {
  const pinned = configuratorReferenceTemperatureBasis(candidateSha256);
  if (pinned) return pinned.windingC;
  const recorded = Number(recordedTempC);
  if (recordedTempC != null && Number.isFinite(recorded) && recorded >= -50 && recorded <= 400) return recorded;
  const rev = Number(extractionRev);
  return Number.isFinite(rev) && rev >= 2 ? 120 : null;
}
