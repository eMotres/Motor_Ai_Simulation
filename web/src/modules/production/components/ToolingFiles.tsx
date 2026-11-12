/**
 * The Tooling tab's files (owner 2026-11-12: "all drawings and 3-D models for tooling go into Tooling", with a viewer, not links only).
 * Below the order table, one card per tooling part: its drawing (PDF in the page with Full screen / Download, the PNG as a small
 * picture) and a rotatable 3-D view of its STEP file in the standard viewer (rotate, fit / home, ortho / perspective).  The server
 * lists only the files the caller may download (GET /part_files) and converts the STEP to GLB on first use.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Button, IconButton, Paper, Typography } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useTranslation } from 'react-i18next';
import { UnifiedViewer } from '../../viewer3d';
import { downloadFile, productionApi, stepViewUrl } from '../api';
import { drawingStem, toolingFileRows } from '../helpers';
import type { BomRow, Part, PartFile, PartFiles, ProdCtx } from '../types';
import { DocPreview, FileThumb } from './FilePreview';
import PartName from './PartName';

/** Up to this many parts with files are open from the start; more start closed (each open card loads a PDF and a 3-D view). */
const OPEN_AT_START = 3;

/** The part's STEP in the standard 3-D viewer; the GLB is built by the server on the first request. */
export const StepView: React.FC<{ ctx: ProdCtx; file: PartFile; onError: (e: string) => void }> = ({ ctx, file, onError }) => {
  const { t } = useTranslation('production');
  const btn = { textTransform: 'none', fontSize: 11.5, py: 0, minHeight: 0 } as const;
  return (
    <Paper variant="outlined" sx={{ bgcolor: 'var(--panel-2)', borderRadius: 1.5, overflow: 'hidden' }} data-testid="tooling-step-view">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.25, py: 0.6, borderBottom: '1px solid var(--line-soft)' }}>
        <Typography sx={{ fontSize: 12.5, fontWeight: 700, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {t('toolingFiles.model3d')} · {file.name}
        </Typography>
        <Button size="small" variant="outlined" sx={btn} onClick={() => void downloadFile(ctx.die, ctx.config, file.file_id, file.name).catch((e) => onError(String(e)))}>
          {t('toolingFiles.downloadStep')}
        </Button>
      </Box>
      <Box sx={{ height: { xs: 340, md: 460 }, position: 'relative' }}>
        <UnifiedViewer sourceKey={`production-step:${ctx.die}:${ctx.config}:${file.file_id}`} resetKey={`production-step:${ctx.die}:${ctx.config}:${file.file_id}`}
          glbUrl={stepViewUrl(ctx.die, ctx.config, file.file_id)} toolbarSpec="full" tree={null}
          contextLostText={t('toolingFiles.model3dLost')} noViewText="" />
      </Box>
    </Paper>
  );
};

const PartFilesCard: React.FC<{ ctx: ProdCtx; part: Part | undefined; partId: string; files: PartFile[]; open: boolean; onToggle: () => void; onError: (e: string) => void }> =
  ({ ctx, part, partId, files, open, onToggle, onError }) => {
    const { t } = useTranslation('production');
    const pdfs = files.filter((f) => f.kind === 'pdf');
    const steps = files.filter((f) => f.kind === 'step');
    // a picture that is the render of a PDF of the same name is only a thumbnail; one without a PDF is shown as a drawing
    const stems = new Set(pdfs.map((f) => drawingStem(f.name)));
    const images = files.filter((f) => f.kind === 'image');
    const loose = images.filter((f) => !stems.has(drawingStem(f.name)));
    const thumbs = images.filter((f) => stems.has(drawingStem(f.name)));
    const name = part?.name?.en || part?.name?.zh || partId;
    return (
      <Paper variant="outlined" sx={{ bgcolor: 'var(--panel)', borderRadius: 1.5 }} data-testid="tooling-files-card">
        <Box onClick={onToggle} role="button" tabIndex={0} aria-expanded={open} aria-label={t('toolingFiles.toggle', { name })}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
          sx={{ display: 'flex', alignItems: 'center', gap: 1.25, px: 1.25, py: 0.9, cursor: 'pointer', flexWrap: 'wrap' }}>
          <IconButton size="small" tabIndex={-1} sx={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}><ExpandMoreIcon fontSize="small" /></IconButton>
          <Box sx={{ minWidth: 0, flex: '1 1 200px' }}>
            <PartName name={part?.name} bold />
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mt: 0.2 }}>
              {[pdfs.length ? `PDF ${pdfs.length}` : '', images.length ? `PNG ${images.length}` : '', steps.length ? `STEP ${steps.length}` : ''].filter(Boolean).join(' · ') || t('toolingFiles.none')}
            </Typography>
          </Box>
          {!open && thumbs.slice(0, 1).map((f) => {
            const pdf = pdfs.find((p) => drawingStem(p.name) === drawingStem(f.name));
            return <Box key={f.file_id} onClick={(e) => e.stopPropagation()}><FileThumb ctx={ctx} file={f} openId={pdf?.file_id} openKind={pdf ? 'pdf' : undefined} /></Box>;
          })}
        </Box>
        {open && (
          files.length === 0
            ? <Typography sx={{ fontSize: 12, color: 'var(--text-4)', px: 1.5, pb: 1.25 }}>{t('toolingFiles.none')}</Typography>
            : (
              <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'flex-start', px: 1.25, pb: 1.25 }}>
                {(pdfs.length > 0 || loose.length > 0) && (
                  <Box sx={{ flex: '1 1 460px', minWidth: { xs: 0, sm: 320 }, display: 'flex', flexDirection: 'column', gap: 1 }}>
                    {pdfs.map((f) => <DocPreview key={f.file_id} ctx={ctx} doc={f} onError={onError} />)}
                    {loose.map((f) => <DocPreview key={f.file_id} ctx={ctx} doc={f} onError={onError} />)}
                  </Box>
                )}
                {steps.length > 0 && (
                  <Box sx={{ flex: '1 1 420px', minWidth: { xs: 0, sm: 320 }, display: 'flex', flexDirection: 'column', gap: 1 }}>
                    {steps.map((f) => <StepView key={f.file_id} ctx={ctx} file={f} onError={onError} />)}
                  </Box>
                )}
              </Box>
            )
        )}
      </Paper>
    );
  };

export const ToolingFiles: React.FC<{ ctx: ProdCtx; rows: BomRow[]; parts: Part[] }> = ({ ctx, rows, parts }) => {
  const { t } = useTranslation('production');
  const [data, setData] = useState<PartFiles[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [opened, setOpened] = useState<Record<string, boolean> | null>(null);
  useEffect(() => {
    let live = true;
    productionApi.partFiles(ctx.die, ctx.config, 'tooling')
      .then((d) => { if (live) { setData(d); setErr(null); } })
      .catch((e) => { if (live) setErr(t('errors.load', { reason: e instanceof Error ? e.message : String(e) })); });
    return () => { live = false; };
  }, [ctx.die, ctx.config, rows, t]);
  const list = useMemo(() => toolingFileRows(rows, data ?? []), [rows, data]);
  const withFiles = list.filter((x) => x.files.length > 0);
  const isOpen = useCallback((id: string) => (opened ? !!opened[id] : withFiles.length <= OPEN_AT_START), [opened, withFiles.length]);
  const toggle = (id: string) => setOpened((cur) => {
    const base = cur ?? Object.fromEntries(list.map((x) => [x.part_id, withFiles.length <= OPEN_AT_START]));
    return { ...base, [id]: !isOpen(id) };
  });
  const setAll = (v: boolean) => setOpened(Object.fromEntries(list.map((x) => [x.part_id, v])));
  const byId = useMemo(() => new Map(parts.map((p) => [p.id, p])), [parts]);
  if (rows.length === 0) return null;
  return (
    <Box sx={{ mt: 2 }} data-testid="tooling-files">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 13, fontWeight: 800, color: 'var(--text-0)' }}>{t('toolingFiles.title')}</Typography>
        <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)', flex: 1, minWidth: 200 }}>{t('toolingFiles.tip')}</Typography>
        {withFiles.length > 1 && (
          <>
            <Button size="small" sx={{ textTransform: 'none', fontSize: 11.5 }} onClick={() => setAll(true)}>{t('toolingFiles.expandAll')}</Button>
            <Button size="small" sx={{ textTransform: 'none', fontSize: 11.5 }} onClick={() => setAll(false)}>{t('toolingFiles.collapseAll')}</Button>
          </>
        )}
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 0.75 }}>{err}</Typography>}
      {data && withFiles.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>{t('toolingFiles.noneAny')}</Typography>}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {withFiles.map((x) => (
          <PartFilesCard key={x.part_id} ctx={ctx} part={byId.get(x.part_id)} partId={x.part_id} files={x.files}
            open={isOpen(x.part_id)} onToggle={() => toggle(x.part_id)} onError={setErr} />
        ))}
      </Box>
    </Box>
  );
};

export default ToolingFiles;
