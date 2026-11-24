/** Documents: upload (owner/engineer/admin) and traced download (every member). */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Chip, CircularProgress, InputAdornment, MenuItem, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import { useTranslation } from 'react-i18next';
import { PartName } from './PartName';
import HelpTip from '../../../components/common/HelpTip';
import ProcessView from './ProcessView';
import { useAuth } from '../../../contexts/AuthContext';
import { downloadDocumentsZip, downloadFile, openFileInNewWindow, processDocument, ProductionApiError, productionApi } from '../api';
import { canDownloadProductionDocuments, processDocumentErrorKey, productionZipErrorKey } from '../helpers';
import {
  canShareLink, documentsForLink, latestUploadDay, productionLinkUrl, validDay, type ProductionLink,
} from '../../../lib/productionLink';
import { DEFAULT_CLOSED, DOCUMENT_TOPICS, groupDocuments, isDocumentTopic, stepChipLabel, stepsOfDocuments, topicContext, topicOf, validClosedGroups, type TopicGroup } from '../documentTopics';
import { useRememberedView } from '../../../lib/useRememberedView';
import type { AssemblyStep, DocumentRec, Part, ProcessSection, ProdCtx } from '../types';
import { NARROW } from '../../../lib/narrowLayout';

const NEW_BG = 'rgba(37, 99, 235, 0.10)';

/** Copy text; browsers without the clipboard API (or a refused permission) get the text in a prompt to copy by hand. */
async function copyText(text: string, manualLabel: string): Promise<void> {
  try { await navigator.clipboard.writeText(text); } catch { window.prompt(manualLabel, text); }
}

const DocumentsPanel: React.FC<{
  documents: DocumentRec[]; parts: Part[]; ctx: ProdCtx; sections?: ProcessSection[];
  /** the shared link this page was opened with (`since` marks new documents, `ids` narrows the list); null = plain view */
  link?: ProductionLink | null;
  onLinkChange?: (l: ProductionLink | null) => void;
  /** file id -> number of the revision that brings or retires it and that this person has not acknowledged */
  unackedFiles?: Record<string, string>;
  /** part ids of the tooling order list (their documents are filed under Tooling) */
  toolingPartIds?: readonly string[];
  /** a click on a step chip: the page opens the Assembly tab with that step selected */
  onOpenStep?: (stepId: string) => void;
}> = ({ documents, parts, ctx, sections = [], link = null, onLinkChange, unackedFiles = {}, toolingPartIds = [], onOpenStep }) => {
  const { t } = useTranslation('production');
  const { isAdmin } = useAuth();
  const canShare = canShareLink(ctx.role, isAdmin);
  const [sinceDay, setSinceDay] = useState<string>(() => link?.since || latestUploadDay(documents) || new Date().toISOString().slice(0, 10));
  const [copied, setCopied] = useState<'all' | 'new' | null>(null);
  const [zipBusy, setZipBusy] = useState(false);
  const [zipError, setZipError] = useState<string | null>(null);
  const [processError, setProcessError] = useState<string | null>(null);
  const view = documentsForLink(documents, link);
  const copyLink = (kind: 'all' | 'new') => {
    const url = productionLinkUrl(window.location.origin, { die: ctx.die, config: ctx.config, ...(kind === 'new' ? { since: sinceDay } : {}) });
    if (!url) return;
    void copyText(url, t('docs.copyManual')).then(() => { setCopied(kind); window.setTimeout(() => setCopied(null), 2000); });
  };
  const fileRef = useRef<HTMLInputElement>(null);
  const [partId, setPartId] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const byId = new Map(parts.map((p) => [p.id, p]));
  // the assembly steps (not for a project viewer, who has no Assembly tab): the chips of a row and the "named by a step" topic rule
  const [steps, setSteps] = useState<AssemblyStep[]>([]);
  useEffect(() => {
    if (ctx.role === 'viewer') return undefined;
    let dead = false;
    productionApi.assemblyProcess(ctx.die, ctx.config).then((v) => { if (!dead) setSteps(v.steps); }).catch(() => undefined);
    return () => { dead = true; };
  }, [ctx.die, ctx.config, ctx.role]);
  const tctx = useMemo(() => topicContext(parts, toolingPartIds, steps), [parts, toolingPartIds, steps]);
  const stepsOf = useMemo(() => stepsOfDocuments(steps, documents), [steps, documents]);
  const [query, setQuery] = useState('');
  // collapsed groups are remembered per production (view memory); the superseded one starts closed
  const [closed, setClosed] = useRememberedView<TopicGroup[]>(`production.docs.closed.${ctx.die}|${ctx.config}`, [...DEFAULT_CLOSED], validClosedGroups);
  const groups = useMemo(() => groupDocuments(view.shown, tctx, query), [view.shown, tctx, query]);
  // a search or a shared selection shows what it found: every group open
  const forceOpen = view.narrowed || query.trim() !== '';
  const toggle = (g: TopicGroup) => setClosed(closed.includes(g) ? closed.filter((x) => x !== g) : [...closed, g]);
  const [showSections, setShowSections] = useState(false);
  const procDoc = (lang: 'en' | 'zh', mode: 'view' | 'download') => {
    setProcessError(null);
    void processDocument(ctx.die, ctx.config, lang, mode).catch((e) => {
      const status = e instanceof ProductionApiError ? e.status : null;
      const key = processDocumentErrorKey(mode, status);
      setProcessError(key ? t(`process.${key}`) : t('failed', { reason: String(e) }));
    });
  };

  const downloadZip = async () => {
    setZipBusy(true); setZipError(null);
    try { await downloadDocumentsZip(ctx.die, ctx.config, ctx.uiLang); }
    catch (e) {
      const status = e instanceof ProductionApiError ? e.status : null;
      setZipError(t(`docs.zipErrors.${productionZipErrorKey(status)}`));
    }
    finally { setZipBusy(false); }
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setErr(null);
    try {
      for (const f of Array.from(files)) await productionApi.uploadDocument(ctx.die, ctx.config, f, partId || null);
      ctx.reload();
    } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ''; }
  };

  return (
    <Box>
      {/* the process document, generated from the assembly and test steps (owner 2026-10-18); its sections are edited here */}
      {/* a project viewer (owner 2026-11-02) reads the visible documents only: no process document, no ZIP of everything */}
      {ctx.role !== 'viewer' && <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.25, mb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          {canDownloadProductionDocuments(ctx.role) && <Button size="small" variant="contained" disabled={zipBusy} sx={{ textTransform: 'none' }}
            startIcon={zipBusy ? <CircularProgress size={14} color="inherit" /> : undefined} onClick={() => void downloadZip()}>
            {zipBusy ? t('docs.zipPreparing') : t('docs.downloadAllZip')}
          </Button>}
          {zipError && <Typography role="alert" sx={{ fontSize: 11, color: '#f87171' }}>{zipError}</Typography>}
          <Typography sx={{ fontSize: 13.5, fontWeight: 800 }}>{t('process.docTitle', 'Process document')}</Typography>
          <HelpTip title={t('process.docTip')} />
          <Box sx={{ flex: 1 }} />
          {(['en', 'zh'] as const).map((lg) => (
            <React.Fragment key={lg}>
              <Button size="small" sx={{ textTransform: 'none' }} onClick={() => procDoc(lg, 'view')}>{t('process.view', 'View')} {lg === 'en' ? 'EN' : '中文'}</Button>
              <Button size="small" sx={{ textTransform: 'none' }} onClick={() => procDoc(lg, 'download')}>{t('process.download', 'Download')} {lg === 'en' ? 'EN' : '中文'}</Button>
            </React.Fragment>
          ))}
          {processError && <Typography role="alert" sx={{ fontSize: 11, color: '#f87171' }}>{processError}</Typography>}
          {ctx.canEdit && <Button size="small" variant="outlined" sx={{ textTransform: 'none' }} onClick={() => setShowSections((v) => !v)}>
            {showSections ? t('process.hideSections', 'Hide sections') : t('process.editSections', 'Edit process sections')}</Button>}
        </Box>
        {showSections && <Box sx={{ mt: 1.25 }}><ProcessView sections={sections} ctx={ctx} /></Box>}
      </Paper>}
      {ctx.canEdit && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
          <TextField select size="small" label={t('docs.forPart')} value={partId} onChange={(e) => setPartId(e.target.value)} sx={{ minWidth: 200 }}>
            <MenuItem value="">{t('docs.general')}</MenuItem>
            {parts.map((p) => <MenuItem key={p.id} value={p.id}>{p.name.en || p.name.zh}</MenuItem>)}
          </TextField>
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
          <Button size="small" variant="contained" disabled={busy} sx={{ textTransform: 'none' }} onClick={() => fileRef.current?.click()}>{t('docs.upload')}</Button>
          <HelpTip title={t('docs.uploadTip')} />
          {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
        </Box>
      )}
      {canShare && (
        // admins and the production's owners hand the page to engineers: the link opens sign-in first and grants nothing by itself
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
          <Button size="small" variant="outlined" sx={{ textTransform: 'none' }} onClick={() => copyLink('all')}>
            {copied === 'all' ? t('docs.copied') : t('docs.copyLink')}</Button>
          <TextField size="small" type="date" label={t('docs.sinceLabel')} value={sinceDay}
            onChange={(e) => setSinceDay(e.target.value)} InputLabelProps={{ shrink: true }} sx={{ width: 160 }} />
          <Button size="small" variant="outlined" disabled={!validDay(sinceDay)} sx={{ textTransform: 'none' }} onClick={() => copyLink('new')}>
            {copied === 'new' ? t('docs.copied') : t('docs.copyNew')}</Button>
          <HelpTip title={t('docs.copyTip')} />
        </Box>
      )}
      {link?.invalidIds && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25, flexWrap: 'wrap' }}>
          <Typography role="alert" data-testid="docs-link-invalid" sx={{ fontSize: 12.5, color: '#f87171' }}>{t('docs.linkInvalid')}</Typography>
          <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }} onClick={() => onLinkChange?.(null)}>{t('docs.showAll')}</Button>
        </Box>
      )}
      {link && !link.invalidIds && (link.since || link.ids?.length) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25, flexWrap: 'wrap' }}>
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>
            {view.narrowed ? t('docs.selected', { n: view.shown.length }) : t('docs.newSince', { date: link.since, n: view.newIds.size })}
          </Typography>
          <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }} onClick={() => onLinkChange?.(null)}>
            {view.narrowed ? t('docs.showAll') : t('docs.clear')}</Button>
        </Box>
      )}
      {link?.invalidIds ? null : documents.length === 0 ? <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('docs.none')}</Typography> : (
        <>
          <TextField size="small" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('docs.search')}
            inputProps={{ 'aria-label': t('docs.search'), 'data-testid': 'docs-search' }} sx={{ mb: 1.25, width: { xs: '100%', sm: 320 } }}
            InputProps={{ endAdornment: query ? (
              <InputAdornment position="end"><Button size="small" sx={{ textTransform: 'none', minWidth: 0, fontSize: 11 }} onClick={() => setQuery('')}>{t('docs.clear')}</Button></InputAdornment>
            ) : undefined }} />
          {groups.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('docs.noMatch')}</Typography>}
          {groups.length > 0 && (
          <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto' }}>
          <Table size="small" sx={{
            [NARROW]: { minWidth: 680 },  // phone: readable columns, the Paper scrolls sideways
            '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
            '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
          }}>
            <TableHead>
              <TableRow>
                <TableCell>{t('docs.name')}</TableCell><TableCell>{t('docs.forPart')}</TableCell>
                <TableCell>{t('docs.uploader')}</TableCell><TableCell>{t('docs.date')}</TableCell><TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {groups.map((g) => {
                const open = forceOpen || !closed.includes(g.group);
                return (
                  <React.Fragment key={g.group}>
                    <TableRow data-testid={`docs-group-${g.group}`} hover onClick={() => { if (!forceOpen) toggle(g.group); }}
                      sx={{ cursor: forceOpen ? 'default' : 'pointer', bgcolor: 'var(--panel)' }}>
                      <TableCell colSpan={5} sx={{ py: 0.5 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                          {open ? <ExpandMoreIcon sx={{ fontSize: 18 }} /> : <ChevronRightIcon sx={{ fontSize: 18 }} />}
                          <Typography sx={{ fontSize: 13, fontWeight: 800, color: g.group === 'superseded' ? 'var(--text-3)' : 'var(--text-0)' }}>
                            {t(`docs.topic.${g.group}`)}
                          </Typography>
                          <Chip size="small" label={g.docs.length} sx={{ height: 18, fontSize: 10.5, ml: 0.5 }} />
                          <HelpTip title={t(`docs.topicTip.${g.group}`)} />
                        </Box>
                      </TableCell>
                    </TableRow>
                    {open && g.docs.map((d) => {
                const p = d.part_id ? byId.get(d.part_id) : undefined;
                const isNew = view.newIds.has(d.id);
                const docSteps = stepsOf.get(d.id) ?? [];
                return (
                  <TableRow key={d.id} hover sx={isNew ? { bgcolor: NEW_BG } : undefined} data-new={isNew ? '1' : undefined}>
                    <TableCell>
                      {/* a click opens the document in a new window (pdf / pictures); other types download (owner 2026-10-17) */}
                      <Box component="span" title={t('parts.openFull', 'Open full screen')}
                        onClick={() => {
                          const ext = (d.name.split('.').pop() || '').toLowerCase();
                          const viewable = ['pdf', 'png', 'jpg', 'jpeg'].includes(ext);
                          const run = viewable ? openFileInNewWindow(ctx.die, ctx.config, d.file_id, ext === 'jpeg' ? 'jpg' : ext)
                                               : downloadFile(ctx.die, ctx.config, d.file_id, d.name);
                          void run.catch((e) => setErr(t('failed', { reason: String(e) })));
                        }}
                        sx={{ cursor: 'pointer', color: '#2563eb', '&:hover': { textDecoration: 'underline' } }}>
                        {d.name}
                      </Box>
                      {isNew && <Chip size="small" label={t('docs.newBadge')} color="primary" sx={{ ml: 1, height: 18, fontSize: 10 }} />}
                      {unackedFiles[d.file_id] && (
                        <Chip size="small" color="warning" variant="outlined" data-testid="doc-unacked"
                          label={t('docs.changedUnacked', { rev: unackedFiles[d.file_id] })} sx={{ ml: 1, height: 18, fontSize: 10 }} />
                      )}
                      {d.status === 'superseded' && (
                        <Chip size="small" color="warning" data-testid="doc-superseded"
                          label={d.superseded_by ? t('docs.supersededBy', { name: d.superseded_by.name, rev: d.superseded_by.revision }) : t('docs.superseded')}
                          title={t('docs.supersededTip')} sx={{ ml: 1, height: 18, fontSize: 10 }} />
                      )}
                      {/* the assembly steps that show this file: a click opens that step */}
                      {docSteps.length > 0 && (
                        <Box data-testid="doc-steps" sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
                          {docSteps.map((st) => (
                            <Chip key={st.id} size="small" variant="outlined" clickable data-testid="doc-step-chip" label={stepChipLabel(st, ctx.uiLang)}
                              title={t('docs.stepChipTip')} onClick={() => onOpenStep?.(st.id)} sx={{ height: 20, fontSize: 10.5, maxWidth: 260 }} />
                          ))}
                        </Box>
                      )}
                    </TableCell>
                    <TableCell>
                      {p ? <PartName name={p.name} size={12.5} /> : <span style={{ color: 'var(--text-4)' }}>{t('docs.general')}</span>}
                      {/* editors file a document under a topic; "auto" = derived from what is known (documentTopics.topicOf) */}
                      {ctx.canEdit && (
                        <TextField select size="small" variant="standard" value={isDocumentTopic(d.topic) ? d.topic : ''} data-testid="doc-topic"
                          title={t('docs.topicTip.select')} SelectProps={{ displayEmpty: true }} sx={{ display: 'block', mt: 0.25, '& .MuiSelect-select': { fontSize: 11.5, py: 0 } }}
                          inputProps={{ 'aria-label': t('docs.topicLabel') }}
                          onChange={(e) => void productionApi.updateDocument(ctx.die, ctx.config, d.id, { topic: (e.target.value || null) as DocumentRec['topic'] | null })
                            .then(() => ctx.reload()).catch((err) => setErr(t('failed', { reason: String(err) })))}>
                          <MenuItem value="">{t('docs.topicAuto', { topic: t(`docs.topic.${topicOf({ ...d, topic: undefined }, tctx)}`) })}</MenuItem>
                          {DOCUMENT_TOPICS.map((k) => <MenuItem key={k} value={k}>{t(`docs.topic.${k}`)}</MenuItem>)}
                        </TextField>
                      )}
                    </TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>
                      {d.uploader_email}
                      {/* internal = hidden from the factory; editors switch it (owner 2026-10-18) */}
                      {(d.internal || ctx.canEdit) && (
                        <Chip size="small" label={t(d.internal ? 'docs.internal' : 'docs.shared')}
                          title={t('docs.internalTip')}
                          onClick={ctx.canEdit ? () => void productionApi.setDocumentInternal(ctx.die, ctx.config, d.id, !d.internal)
                            .then(() => ctx.reload()).catch((e) => setErr(t('failed', { reason: String(e) }))) : undefined}
                          sx={{ ml: 1, height: 18, fontSize: 10, cursor: ctx.canEdit ? 'pointer' : 'default',
                            bgcolor: d.internal ? '#7f1d1d' : 'transparent', color: d.internal ? '#fff' : 'var(--text-3)',
                            border: d.internal ? 'none' : '1px solid var(--line-soft)' }} />
                      )}
                    </TableCell>
                    <TableCell sx={{ color: 'var(--text-3)' }}>{d.uploaded_at.slice(0, 10)}</TableCell>
                    <TableCell align="right">
                      <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }}
                        onClick={() => void downloadFile(ctx.die, ctx.config, d.file_id, d.name).catch((e) => setErr(t('failed', { reason: String(e) })))}>
                        {t('docs.download')}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
                    })}
                  </React.Fragment>
                );
              })}
            </TableBody>
          </Table>
          </Paper>
          )}
        </>
      )}
    </Box>
  );
};

export default DocumentsPanel;
