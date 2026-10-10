import type { RpmMetric } from './propellerRpmChart';

/** Returns the `motors` i18n key of the caption (EN source, ZH mirror). */
export function chartCaption(provenance: string, metric: RpmMetric): string {
  if (provenance.includes('estimated')) return 'propellerCaptionEstimated';
  if (provenance.includes('calculated from published shaft torque')) {
    return 'propellerCaptionFromShaftTorque';
  }
  // The group provenance also carries fit-membership metadata such as
  // "included in fit". Published observations take precedence over that
  // metadata when describing what the visible points actually are.
  if (provenance.includes('published thrust')) return 'propellerCaptionThrust';
  if (provenance.includes('published shaft torque')) return 'propellerCaptionShaft';
  if (provenance.includes('fit')) return 'propellerCaptionFit';
  return metric === 'thrust' ? 'propellerCaptionThrust' : 'propellerCaptionShaft';
}
