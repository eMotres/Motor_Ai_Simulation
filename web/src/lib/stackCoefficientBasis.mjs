export function stackCoefficientReferenceOnly(cardId, stackMm, basis) {
  if (cardId !== 'candidate_d85_ciano28_85_20sw1200_l13') return false;
  return !basis || !Number.isFinite(stackMm)
    || Math.abs(stackMm - basis.stackLengthMm) > 1e-9;
}
