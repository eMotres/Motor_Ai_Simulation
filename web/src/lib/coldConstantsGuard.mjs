export function coldConstantsUnavailableForCard(cardId, basis) {
  const exactD85 = cardId === 'candidate_d85_ciano28_85_20sw1200_l13';
  return exactD85
    ? basis?.coldConstantsAvailable !== true
    : basis?.coldConstantsAvailable === false;
}
