// The ONE compact "Files" menu of the Geometry tab (owner 2026-10-15) - replaces the row of FreeCAD / Fusion buttons.
// An export section and an import section; WHICH items there are, and which are enabled, depends on the scope (lib/filesMenu.ts):
//   Active  the electromagnetic parameter set (Fusion CSV, FreeCAD bundle) - exactly the round-trips as before.
//   Full    the full construction: STEP, BOM (CSV / XLSX), the full parameter set, the FreeCAD file; CAD-only parameters in.
// Permission rules live in lib/filesMenu.ts and the server; every Full download is stamped with the NDA and logged there.
import React, { useRef, useState } from 'react';
import { Box, Button, Divider, ListItemIcon, ListItemText, ListSubheader, Menu, MenuItem, Tooltip, Typography } from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import FolderOpenIcon from '@mui/icons-material/FolderOpenOutlined';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { useAuth } from '../../contexts/AuthContext';
import { useFreeCADRoundTrip } from '../common/FreeCADRoundTrip';
import { useFusion360RoundTrip } from '../common/Fusion360RoundTrip';
import { filenameFromDisposition, type ModelInfo } from '../../lib/model3d';
import { FULL_DOWNLOADS, filesMenu, type FileItem, type FileItemId } from '../../lib/filesMenu';
import type { ConstructionScope } from '../../lib/constructionScope';

const tx = nsT('geometry');
const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

const ACCEPT: Partial<Record<FileItemId, string>> = {
  fusionCsvEmImport: '.csv,text/csv', freecadEmImport: '.FCStd,.fcstd,.zip', paramsImport: '.csv,text/csv', fcstdImport: '.FCStd,.fcstd',
};

interface Props {
  scope: ConstructionScope;
  /** the open motor's built model (Full), null when none is built */
  model: ModelInfo | null;
  canDownload: boolean;
  canWrite: boolean;
  /** Full import: validates (dry) or saves a Fusion CSV / FreeCAD file; the panel owns the CAD table */
  importFull: (file: File, dry: boolean) => Promise<{ ok: boolean; lines: string[]; changed: string[] }>;
  /** open the NDA dialog (an NDA-locked item was clicked) */
  onNeedNda: () => void;
}

const FilesMenu: React.FC<Props> = ({ scope, model, canDownload, canWrite, importFull, onNeedNda }) => {
  useTranslation('geometry');
  const { isAdmin, enforced, role } = useAuth();
  const fc = useFreeCADRoundTrip();
  const fu = useFusion360RoundTrip();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [fullMsg, setFullMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const picking = useRef<FileItemId | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const items = filesMenu({
    scope, connectedToApi: fc.connectedToApi, isAdmin, enforced, role, canDownload, modelBuilt: !!model, canWrite,
    kit: model?.assembly === 'frameless',
  });
  const exports = items.filter((i) => i.section === 'export');
  const imports = items.filter((i) => i.section === 'import');
  const msg = scope === 'full' ? fullMsg : (fc.msg ?? fu.msg);
  const working = busy || fc.busy || fu.busy;

  const download = async (id: FileItemId) => {
    const d = FULL_DOWNLOADS[id];
    if (!d || !model) return;
    setBusy(true); setFullMsg(tx('filesWorking'));
    try {
      const r = await fetch(`${API}/api/model3d/${encodeURIComponent(model.die)}/${encodeURIComponent(model.config)}/${d.path}`);
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(typeof j?.detail === 'string' ? j.detail : String(r.status));
      }
      const name = filenameFromDisposition(r.headers.get('Content-Disposition'), `${model.motor_id}_${d.fallback}`);
      const href = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = href; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
      setFullMsg(tx('filesDownloaded', { name }));
    } catch (e: any) { setFullMsg(tx('filesFailed', { error: String(e?.message ?? e) })); }
    finally { setBusy(false); }
  };

  const importFullFile = async (id: FileItemId, f: File) => {
    setBusy(true); setFullMsg(tx('filesWorking'));
    try {
      const dry = await importFull(f, true);
      if (!dry.ok) { setFullMsg(tx('filesRefused', { lines: dry.lines.join(' | ') })); return; }
      if (!dry.changed.length) { setFullMsg(tx('filesNothingToApply')); return; }
      if (!window.confirm(tx('filesConfirmApply', { n: dry.changed.length, names: dry.changed.slice(0, 20).join(', ') }))) {
        setFullMsg(tx('filesCancelled')); return;
      }
      const res = await importFull(f, false);
      setFullMsg(res.ok ? tx('filesApplied', { n: res.changed.length }) : tx('filesRefused', { lines: res.lines.join(' | ') }));
    } finally { setBusy(false); void id; }
  };

  const onPick = (it: FileItem) => {
    setAnchor(null);
    if (!it.enabled) { if (it.reason === 'nda') onNeedNda(); return; }
    if (it.section === 'import') {
      picking.current = it.id;
      if (fileInput.current) fileInput.current.accept = ACCEPT[it.id] ?? '';
      fileInput.current?.click();
      return;
    }
    if (scope === 'full') { void download(it.id); return; }
    if (it.id === 'fusionCsvEm') void fu.doExport();
    else if (it.id === 'freecadEm') void fc.doExport();
  };

  const onFile = (f: File) => {
    const id = picking.current;
    if (!id) return;
    if (id === 'fusionCsvEmImport') void fu.doImport(f);
    else if (id === 'freecadEmImport') void fc.doImport(f);
    else void importFullFile(id, f);
  };

  const row = (it: FileItem) => {
    const locked = !it.enabled && it.reason === 'nda';
    const tip = it.enabled ? tx(`${it.labelKey}Tip`, { defaultValue: '' }) : tx(`filesReason_${it.reason}`);
    return (
      <Tooltip key={it.id} title={tip} placement="left" enterDelay={300}>
        <span>
          <MenuItem dense disabled={!it.enabled && !locked} onClick={() => onPick(it)} data-testid={`files-item-${it.id}`}
            aria-disabled={!it.enabled} sx={{ fontSize: 12.5, opacity: it.enabled ? 1 : 0.65 }}>
            <ListItemText primaryTypographyProps={{ fontSize: 12.5 }}>{tx(it.labelKey)}</ListItemText>
            {locked && <ListItemIcon sx={{ minWidth: 0, ml: 1 }}><LockOutlinedIcon sx={{ fontSize: 14 }} /></ListItemIcon>}
          </MenuItem>
        </span>
      </Tooltip>
    );
  };

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
      <Button size="small" variant="outlined" data-testid="files-menu-button" disabled={working}
        startIcon={<FolderOpenIcon sx={{ fontSize: 16 }} />} endIcon={<ArrowDropDownIcon />}
        onClick={(e) => setAnchor(e.currentTarget)} aria-haspopup="menu" aria-expanded={!!anchor}
        sx={{ textTransform: 'none', fontSize: 12, py: 0.25 }}>
        {tx('filesMenu')}
      </Button>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)} slotProps={{ list: { dense: true, sx: { minWidth: 230 } } }}>
        <ListSubheader sx={{ lineHeight: '28px', fontSize: 10.5, letterSpacing: 0.5, textTransform: 'uppercase', bgcolor: 'transparent' }}>
          {tx('filesExport')}
        </ListSubheader>
        {exports.map(row)}
        <Divider />
        <ListSubheader sx={{ lineHeight: '28px', fontSize: 10.5, letterSpacing: 0.5, textTransform: 'uppercase', bgcolor: 'transparent' }}>
          {tx('filesImport')}
        </ListSubheader>
        {imports.map(row)}
        {scope === 'full' && (
          <Typography sx={{ px: 2, py: 0.75, fontSize: 10.5, color: 'var(--text-4)', maxWidth: 270 }}>{tx('filesFullNote')}</Typography>
        )}
      </Menu>
      <input ref={fileInput} type="file" hidden data-testid="files-import-input"
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) onFile(f); }} />
      {msg && <Typography variant="caption" color="text.secondary" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }} title={msg}>{msg}</Typography>}
    </Box>
  );
};

export default FilesMenu;
