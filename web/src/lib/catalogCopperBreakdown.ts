export interface CatalogCopperBreakdown {
  dcW: number;
  acProximityW: number;
}

/** Mirrors the catalog model's displayed three-phase DC I²R and residual AC share. */
export function catalogCopperBreakdown(
  phaseCurrentRmsA: number,
  phaseResistanceOhm: number,
  totalCopperLossW: number,
): CatalogCopperBreakdown | null {
  if (![phaseCurrentRmsA, phaseResistanceOhm, totalCopperLossW].every(Number.isFinite)
      || phaseCurrentRmsA < 0 || phaseResistanceOhm < 0) return null;
  const dcW = 3 * phaseCurrentRmsA * phaseCurrentRmsA * phaseResistanceOhm;
  return { dcW, acProximityW: totalCopperLossW - dcW };
}
