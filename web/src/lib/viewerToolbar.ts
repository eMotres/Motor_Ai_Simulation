// The top toolbar of the model viewers (owner 2026-10-15: "the left side panel would be better on TOP, and the geometry FULL SCREEN").
//
// The 3-D model tab, Geometry > Full and Materials > Full put their controls in ONE compact row above the viewer (it wraps on a narrow
// screen); the assembly tree is a collapsible overlay. Geometry Active and Full share an EM parameter column; Full adds CAD-only
// editable parameters below it. Shared viewer controls live in modules/viewer3d.

/** Below this width the tree starts collapsed and the toolbar wraps onto more rows. */
export const NARROW_PX = 900;

export const isNarrow = (width: number): boolean => width < NARROW_PX;

export type Model3DItem = 'motor' | 'download' | 'simplified' | 'yourNda' | 'info' | 'ndaNote' | 'sign' | 'verify';

export interface Model3DToolbarCtx {
  /** the server flag: NDA signed (or admin) - the only permission the 3-D tab has */
  canDownload: boolean;
  /** number of models the account may open */
  models: number;
  /** the motor open in Configure has no built model yet */
  preparing: boolean;
  /** the open model has a STEP file */
  stepAvailable: boolean;
  /** a signed NDA PDF is on file ("Your signed NDA") */
  hasPdf: boolean;
  /** the NDA is signed but the applicant verification is not complete (server `access`): the toolbar shows its status, not "Sign" */
  ndaSigned?: boolean;
  /** the SIMPLIFIED public model (owner 2026-10-15): whether the open model has its files; undefined = the item is not offered.
   *  It needs only a signed-in account (no NDA, no verification), so it is in BOTH toolbars. */
  simplified?: boolean;
}

export interface ToolbarEntry { id: Model3DItem; enabled: boolean }

/** Host-only items for the 3-D model tab. */
export function model3dToolbar(c: Model3DToolbarCtx): ToolbarEntry[] {
  const e = (id: Model3DItem, enabled = true): ToolbarEntry => ({ id, enabled });
  const simple = c.simplified === undefined ? [] : [e('simplified', c.simplified)];
  if (!c.canDownload) {
    const pick = c.models > 1 || c.preparing ? [e('motor')] : [];
    // signing the NDA does not open the tools by itself (applicant verification): once signed, the status line replaces the Sign button
    return c.ndaSigned ? [...pick, ...simple, e('verify')] : [...pick, ...simple, e('ndaNote'), e('sign')];
  }
  return [e('motor', c.models > 0), e('download', c.stepAvailable), ...simple,
    ...(c.hasPdf ? [e('yourNda')] : []), e('info')];
}

export interface FullToolbarCtx {
  /** a model is built */
  hasModel: boolean;
  /** the model badge (lib/cadParams modelBadge): none | rebuilding | queued | stale | failed */
  badge: string;
  /** may this session rebuild (catalog write) */
  canWrite: boolean;
}

export type FullItem = 'badge' | 'rebuildNow';

/** Host-only model status and permitted rebuild action for Geometry > Full. */
export function fullToolbar(c: FullToolbarCtx): FullItem[] {
  const out: FullItem[] = [];
  if (c.hasModel && c.badge !== 'none') out.push('badge');
  if (c.hasModel && c.canWrite && (c.badge === 'stale' || c.badge === 'failed')) out.push('rebuildNow');
  return out;
}
