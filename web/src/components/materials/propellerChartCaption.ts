import type { RpmMetric } from './propellerRpmChart';

export function chartCaption(provenance: string, metric: RpmMetric): string {
  if (provenance.includes('estimated')) return 'Estimated coefficient curve';
  if (provenance.includes('calculated from published shaft torque')) {
    return 'Calculated from published shaft torque';
  }
  // The group provenance also carries fit-membership metadata such as
  // "included in fit". Published observations take precedence over that
  // metadata when describing what the visible points actually are.
  if (provenance.includes('published thrust')) return 'Published thrust measurements';
  if (provenance.includes('published shaft torque')) return 'Published shaft measurements';
  if (provenance.includes('fit')) return 'Calculated from published coefficient fit';
  return metric === 'thrust' ? 'Published thrust measurements' : 'Published shaft measurements';
}
