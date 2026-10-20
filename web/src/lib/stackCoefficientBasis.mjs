/** Stack coefficients of an audited reference card hold only at its audited stack length;
 *  `required` is the server's flag (no card id lives in the bundle). */
export function stackCoefficientReferenceOnly(required, stackMm, basis) {
  if (required !== true) return false;
  return !basis || !Number.isFinite(stackMm)
    || Math.abs(stackMm - basis.stackLengthMm) > 1e-9;
}
