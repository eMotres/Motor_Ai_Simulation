export interface ColdBasis { coldConstantsAvailable?: boolean | null }
export function coldConstantsUnavailableForCard(cardId: string | null | undefined, basis: ColdBasis | null | undefined): boolean;
