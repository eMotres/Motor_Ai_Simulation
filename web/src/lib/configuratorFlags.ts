// Configure feature flags.
//
// SHOW_CONFIGURE_CHARTS — the speed / efficiency charts of the results area: "Performance
// across speed" (power & efficiency and loss curves, its three summary tiles, the efficiency
// map) and the generator's charge-map curve.  Hidden by the owner on 2026-10-05 («графики пока
// убери») until they are driven by the v1 passport instead of the analytical scaling.  Nothing
// was removed: set this to true to bring them back.  The geometry pictures and every result
// tile are not behind it.
export const SHOW_CONFIGURE_CHARTS = false;
