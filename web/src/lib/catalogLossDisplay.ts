export interface CatalogLossInput {
  loss_w?: number | null;
  loss_mech_w?: number | null;
  loss_mech_derived?: boolean;
}

export interface CatalogLossDisplay {
  available: boolean;
  totalW: number | null;
  electromagneticW: number | null;
  mechanicalW: number | null;
  mechanicalKnown: boolean;
  mechanicalDerived: boolean;
}

/** Preserve an absent result as unknown while retaining a computed physical zero. */
export function catalogLossDisplay(r: CatalogLossInput | null | undefined): CatalogLossDisplay {
  const em = r?.loss_w;
  if (typeof em !== 'number' || !Number.isFinite(em)) {
    return { available: false, totalW: null, electromagneticW: null, mechanicalW: null,
      mechanicalKnown: false, mechanicalDerived: false };
  }
  const mech = r?.loss_mech_w;
  const mechanicalKnown = typeof mech === 'number' && Number.isFinite(mech);
  return {
    available: true,
    totalW: em + (mechanicalKnown ? mech : 0),
    electromagneticW: em,
    mechanicalW: mechanicalKnown ? mech : null,
    mechanicalKnown,
    mechanicalDerived: mechanicalKnown && r?.loss_mech_derived === true,
  };
}
