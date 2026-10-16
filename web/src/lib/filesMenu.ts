// The Geometry tab's "Files" menu: WHICH exports / imports it offers and which of them are enabled (owner 2026-10-15).  Pure, so
// node --test pins every permission rule; the server enforces the same rules again (this only keeps the UI honest).
//
//   Active  the electromagnetic parameter set:  Fusion CSV and FreeCAD bundle out, the same two in (import = admin, as always).
//   Full    the full construction: STEP, BOM (CSV / XLSX), the full parameter set (Fusion CSV), the parametric FreeCAD file out;
//           the CAD-only parameters in (Fusion CSV / FreeCAD file) where the round-trip supports it.
//
// Rules kept from before: STEP and every full-construction file only after the NDA (admins and the local dev backend need none);
// the FreeCAD FCStd is never offered to a plain `user` (owner 2026-10-13: "only STEP for now"); imports write the catalog, so they
// follow the catalog-write rule; every download is logged with its NDA on the server.

import type { ConstructionScope } from './constructionScope';

export type FileItemId =
  | 'fusionCsvEm' | 'freecadEm' | 'fusionCsvEmImport' | 'freecadEmImport'
  | 'step' | 'stepSimplified' | 'bomCsv' | 'bomXlsx' | 'paramsCsv' | 'fcstd' | 'paramsImport' | 'fcstdImport';

export type DisabledReason = 'offline' | 'nda' | 'adminOnly' | 'noModel' | 'readOnly' | 'noSimplified';

export interface FilesCtx {
  scope: ConstructionScope;
  connectedToApi: boolean;
  isAdmin: boolean;
  enforced: boolean;
  /** account role: 'user' | 'engineer' | 'admin' | 'anon' */
  role: string;
  /** the server's `can_download` (NDA signed, or admin) */
  canDownload: boolean;
  /** a CAD model is built for the open motor */
  modelBuilt: boolean;
  /** may this session write the open die/configuration (the server's `can_write`) */
  canWrite: boolean;
  /** the SIMPLIFIED envelope-only STEP of the open motor exists (needs a signed-in account only: no NDA, no verification) */
  simplifiedBuilt?: boolean;
  /** the open model is a frameless kit: STEP and BOM are those of the kit (labels say so) */
  kit?: boolean;
}

export interface FileItem {
  id: FileItemId;
  section: 'export' | 'import';
  labelKey: string;
  enabled: boolean;
  reason?: DisabledReason;
}

const KIT_LABELLED: readonly FileItemId[] = ['step', 'bomCsv', 'bomXlsx'];

const item = (id: FileItemId, section: 'export' | 'import', enabled: boolean, reason?: DisabledReason, kit = false): FileItem =>
  ({ id, section, labelKey: kit && KIT_LABELLED.includes(id) ? `files_${id}Kit` : `files_${id}`, enabled, ...(enabled ? {} : { reason }) });

/** A plain `user` account on an enforcing backend never gets the FreeCAD file. */
export const mayHaveFreecadFile = (c: Pick<FilesCtx, 'isAdmin' | 'enforced' | 'role'>): boolean =>
  c.isAdmin || !c.enforced || c.role === 'engineer' || c.role === 'admin';

export function filesMenu(c: FilesCtx): FileItem[] {
  if (c.scope === 'active') {
    const out = (id: FileItemId) => item(id, 'export', c.connectedToApi, 'offline');
    const imp = (id: FileItemId) => (!c.connectedToApi ? item(id, 'import', false, 'offline')
      : (c.isAdmin || !c.enforced) ? item(id, 'import', true) : item(id, 'import', false, 'adminOnly'));
    return [out('fusionCsvEm'), out('freecadEm'), imp('fusionCsvEmImport'), imp('freecadEmImport')];
  }
  const k = !!c.kit;
  const dl = (id: FileItemId): FileItem => (!c.connectedToApi ? item(id, 'export', false, 'offline', k)
    : !c.modelBuilt ? item(id, 'export', false, 'noModel', k)
      : !c.canDownload ? item(id, 'export', false, 'nda', k) : item(id, 'export', true, undefined, k));
  const imp = (id: FileItemId): FileItem => (!c.connectedToApi ? item(id, 'import', false, 'offline')
    : !c.modelBuilt ? item(id, 'import', false, 'noModel')
      : !c.canWrite ? item(id, 'import', false, 'readOnly') : item(id, 'import', true));
  // the simplified STEP is the one export that is never behind the NDA (owner 2026-10-15): it needs the API, a built model and the files
  const simple: FileItem = !c.connectedToApi ? item('stepSimplified', 'export', false, 'offline')
    : !c.modelBuilt ? item('stepSimplified', 'export', false, 'noModel')
      : !c.simplifiedBuilt ? item('stepSimplified', 'export', false, 'noSimplified') : item('stepSimplified', 'export', true);
  const list: FileItem[] = [dl('step'), simple, dl('bomCsv'), dl('bomXlsx'), dl('paramsCsv')];
  if (mayHaveFreecadFile(c)) list.push(dl('fcstd'));
  list.push(imp('paramsImport'));
  if (mayHaveFreecadFile(c)) list.push(imp('fcstdImport'));
  return list;
}

/** Server path (below /api/model3d/{die}/{config}) and the fallback file name of a Full download. */
export const FULL_DOWNLOADS: Readonly<Partial<Record<FileItemId, { path: string; fallback: string }>>> = {
  step: { path: 'model.step', fallback: 'model.step' },
  stepSimplified: { path: 'simplified.step', fallback: 'simplified.step' },
  bomCsv: { path: 'bom.csv', fallback: 'bom.csv' },
  bomXlsx: { path: 'bom.xlsx', fallback: 'bom.xlsx' },
  paramsCsv: { path: 'params.csv', fallback: 'params.csv' },
  fcstd: { path: 'model.fcstd', fallback: 'model.FCStd' },
};
