/** `required` comes from the server's `reference_basis` block of the card (review F4:
 *  no card id lives in the bundle).  An audited card fails closed unless its audited cold
 *  provenance is present and says cold constants exist; any other card only hides them when
 *  its provenance says they are unavailable. */
export function coldConstantsUnavailableForCard(required, basis) {
  return required === true
    ? basis?.coldConstantsAvailable !== true
    : basis?.coldConstantsAvailable === false;
}
