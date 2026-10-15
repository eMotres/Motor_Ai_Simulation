// The top toolbar of the model viewers (owner 2026-10-15: "the left side panel would be better on TOP, and the geometry FULL SCREEN").
//
// The 3-D model tab, Geometry > Full and Materials > Full put their controls in ONE compact row above the viewer (it wraps on a narrow
// screen); the viewer takes the full width and height below it; the assembly tree is a collapsible overlay and Geometry > Full keeps
// its CAD parameter table in a right-hand drawer.  This module holds the pure rules: which items the toolbar has for whom, and what is
// open by default at which width.  Permissions are NOT decided here: `canDownload` is the server flag (NDA signed / admin) the 3-D
// tab already used, and the tools exist only with it (lib/model3d.ts panelParts), exactly as before.

/** Below this width the tree starts collapsed and the toolbar wraps onto more rows. */
export const NARROW_PX = 900;

export const isNarrow = (width: number): boolean => width < NARROW_PX;

/** The tree overlay starts collapsed on a narrow screen. */
export const treeStartsCollapsed = (width: number): boolean => isNarrow(width);

/** The CAD parameter drawer of Geometry > Full: closed by default (the model is full screen), remembered per browser. */
export const DRAWER_STORAGE_KEY = 'geometry.full.paramsDrawer';
export function drawerStartsOpen(stored: string | null | undefined): boolean {
  return stored === '1';
}
/** Drawer width in px: 420 on a wide screen, the whole width (minus a gutter) on a phone. */
export const drawerWidth = (viewport: number): number => Math.max(260, Math.min(420, viewport - 16));

export type Model3DItem = 'motor' | 'fit' | 'explode' | 'section' | 'measure' | 'download' | 'simplified' | 'yourNda' | 'info' | 'ndaNote' | 'sign' | 'verify';

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
  /** the SIMPLIFIED envelope-only model (owner 2026-10-15): whether the open model has its files; undefined = the item is not offered.
   *  It needs only a signed-in account (no NDA, no verification), so it is in BOTH toolbars. */
  simplified?: boolean;
}

export interface ToolbarEntry { id: Model3DItem; enabled: boolean }

/** The items of the 3-D model toolbar, in order.  Without the NDA: the motor picker (only when there is a choice), Fit, and the
 *  "Download after NDA" note with the Sign button (or, with the NDA signed, the verification status) - no tool.  With it: the picker, Fit, exploded view, section, measure, the STEP
 *  download (disabled when the model has no STEP), the signed-NDA link when a PDF exists, and the info icon. */
export function model3dToolbar(c: Model3DToolbarCtx): ToolbarEntry[] {
  const e = (id: Model3DItem, enabled = true): ToolbarEntry => ({ id, enabled });
  const simple = c.simplified === undefined ? [] : [e('simplified', c.simplified)];
  if (!c.canDownload) {
    const pick = c.models > 1 || c.preparing ? [e('motor')] : [];
    // signing the NDA does not open the tools by itself (applicant verification): once signed, the status line replaces the Sign button
    return c.ndaSigned ? [...pick, e('fit'), ...simple, e('verify')] : [...pick, e('fit'), ...simple, e('ndaNote'), e('sign')];
  }
  return [e('motor', c.models > 0), e('fit'), e('explode'), e('section'), e('measure'), e('download', c.stepAvailable), ...simple,
    ...(c.hasPdf ? [e('yourNda')] : []), e('info')];
}

export interface FullToolbarCtx {
  /** a frameless kit (cad_variant: frameless) */
  kit: boolean;
  /** a model is built */
  hasModel: boolean;
  /** the model badge (lib/cadParams modelBadge): none | rebuilding | queued | stale | failed */
  badge: string;
  /** may this session rebuild (catalog write) */
  canWrite: boolean;
}

export type FullItem = 'params' | 'kit' | 'badge' | 'rebuildNow' | 'fit';

/** Items of the Geometry > Full toolbar: the CAD-parameter drawer toggle (always), the kit chip (frameless), the rebuild badge (a model
 *  that is not current), "Rebuild now" (a stale or failed model that the account may rebuild), Fit (with a model). */
export function fullToolbar(c: FullToolbarCtx): FullItem[] {
  const out: FullItem[] = ['params'];
  if (c.kit) out.push('kit');
  if (c.hasModel && c.badge !== 'none') out.push('badge');
  if (c.hasModel && c.canWrite && (c.badge === 'stale' || c.badge === 'failed')) out.push('rebuildNow');
  if (c.hasModel) out.push('fit');
  return out;
}
