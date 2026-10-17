/**
 * Part Detail Panel (rendered below the BOM table when a row is clicked).
 * Requirement F: Detailed view of a part including drawing preview + download,
 * optional STEP, description, material, spec, quantity per motor/order, revision,
 * linked documents, discussion thread / proposals, and (for editors) price + edit controls.
 */
import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  MenuItem,
  Paper,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import PartName from './PartName';
import { downloadFile, downloadModelStep, fileObjectUrl, productionApi } from '../api';
import { displayText, makeBilingual } from '../helpers';
import type {
  CostRow,
  DocumentRec,
  DrawingFile,
  Lang,
  Part,
  ProdCtx,
  Proposal,
  Thread,
} from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const CHIP = { height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-2)' } as const;

export const DrawingPreview: React.FC<{ ctx: ProdCtx; d: DrawingFile }> = ({ ctx, d }) => {
  const { t } = useTranslation('production');
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (d.kind !== 'png') return;
    let live = true;
    let made: string | null = null;
    fileObjectUrl(ctx.die, ctx.config, d.file_id)
      .then((u) => {
        made = u;
        if (live) setUrl(u);
        else URL.revokeObjectURL(u);
      })
      .catch(() => {});
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [ctx.die, ctx.config, d.file_id, d.kind]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, width: 160 }}>
      {d.kind === 'png' && url && (
        <Box
          component="img"
          src={url}
          alt={d.name}
          sx={{ width: 160, height: 110, objectFit: 'contain', bgcolor: '#fff', borderRadius: 1 }}
        />
      )}
      <Button
        size="small"
        sx={{ textTransform: 'none', fontSize: 11, justifyContent: 'flex-start', p: 0 }}
        onClick={() => void downloadFile(ctx.die, ctx.config, d.file_id, d.name)}
      >
        {d.kind.toUpperCase()} · {d.name || t('parts.download')}
      </Button>
    </Box>
  );
};

export interface PartDetailPanelProps {
  part: Part;
  ctx: ProdCtx;
  orderQuantity: number;
  costRow?: CostRow;
  documents?: DocumentRec[];
  threads?: Thread[];
  proposals?: Proposal[];
  onCostChange?: (partId: string, unitPrice: number | null, currency: string, note: string | null) => void;
  onChanged: () => void;
  onClose?: () => void;
}

export const PartDetailPanel: React.FC<PartDetailPanelProps> = ({
  part,
  ctx,
  orderQuantity,
  costRow,
  documents = [],
  threads = [],
  proposals = [],
  onCostChange,
  onChanged,
  onClose,
}) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [material, setMaterial] = useState('');
  const [spec, setSpec] = useState('');
  const [qty, setQty] = useState('1');
  const [kind, setKind] = useState<Part['kind']>('made');
  const [err, setErr] = useState<string | null>(null);

  const startEdit = () => {
    setName(displayText(part.name, part.name.src_lang, true).text);
    setDesc(displayText(part.description, part.description.src_lang, true).text);
    setLang(part.name.src_lang);
    setMaterial(part.material_i18n ? (part.material_i18n[ui] || part.material_i18n.zh || part.material) : part.material);
    setSpec(part.spec_i18n ? (part.spec_i18n[ui] || part.spec_i18n.zh || part.spec) : part.spec);
    setQty(String(part.qty_per_motor));
    setKind(part.kind);
    setErr(null);
    setEditing(true);
  };

  const save = async () => {
    try {
      await productionApi.updatePart(ctx.die, ctx.config, part.id, {
        name: makeBilingual(name, lang),
        description: makeBilingual(desc, lang),
        material,
        spec,
        kind,
        qty_per_motor: Number(qty) || 0,
      });
      setEditing(false);
      onChanged();
    } catch (e) {
      setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) }));
    }
  };

  const remove = async () => {
    if (!window.confirm(t('parts.deleteConfirm'))) return;
    try {
      await productionApi.deletePart(ctx.die, ctx.config, part.id);
      onChanged();
      onClose?.();
    } catch (e) {
      setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) }));
    }
  };

  // Material & specification bilingual resolution
  const mat = part.material_i18n ? (part.material_i18n[ui] || part.material_i18n.zh) : part.material;
  const spc = part.spec_i18n ? (part.spec_i18n[ui] || part.spec_i18n.zh) : part.spec;

  // Filter linked documents, thread, and proposals
  const linkedDocs = documents.filter((d) => d.part_id === part.id);
  const partThread = threads.find((th) => th.part_id === part.id);
  const partProposals = proposals.filter((p) => p.part_id === part.id);

  const unitPrice = costRow?.unit_price ?? null;
  const currency = costRow?.currency || 'CNY';
  const note = costRow?.note || '';
  const orderTotalParts = part.qty_per_motor * orderQuantity;

  return (
    <Paper sx={{ ...PANEL, p: 2, position: 'relative' }}>
      {/* Header bar */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <PartName name={part.name} bold size={15} />
          <Chip size="small" label={t(`parts.kindValue.${part.kind}`)} sx={CHIP} />
          <Chip size="small" label={`× ${part.qty_per_motor} (${t('parts.qty')})`} sx={CHIP} />
          <Chip size="small" label={`${t('col.orderQty')}: ${orderTotalParts}`} sx={CHIP} />
          <Chip size="small" label={t('parts.revision', { rev: part.revision })} sx={CHIP} />
          {ctx.canEdit && !editing && (
            <>
              <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }} onClick={startEdit}>
                {t('edit')}
              </Button>
              <Button size="small" sx={{ textTransform: 'none', fontSize: 11, color: '#f87171' }} onClick={() => void remove()}>
                {t('remove')}
              </Button>
            </>
          )}
        </Box>
        {onClose && (
          <IconButton size="small" onClick={onClose} sx={{ color: 'var(--text-3)' }}>
            <CloseIcon fontSize="small" />
          </IconButton>
        )}
      </Box>

      {editing ? (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mb: 2 }}>
          <BilingualInput label={t('parts.name')} value={name} lang={lang} onChange={(v, l) => { setName(v); setLang(l); }} />
          <BilingualInput label={t('parts.description')} value={desc} lang={lang} multiline onChange={(v, l) => { setDesc(v); setLang(l); }} />
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <TextField select size="small" label={t('parts.kind')} value={kind} onChange={(e) => setKind(e.target.value as Part['kind'])} sx={{ width: 140 }}>
              {(['made', 'purchased', 'consumable'] as const).map((k) => (
                <MenuItem key={k} value={k}>{t(`parts.kindValue.${k}`)}</MenuItem>
              ))}
            </TextField>
            <TextField size="small" label={t('parts.qty')} value={qty} onChange={(e) => setQty(e.target.value)} sx={{ width: 100 }} />
            <TextField size="small" label={t('parts.material')} value={material} onChange={(e) => setMaterial(e.target.value)} sx={{ flex: 1, minWidth: 160 }} />
            <TextField size="small" label={t('parts.spec')} value={spec} onChange={(e) => setSpec(e.target.value)} sx={{ flex: 1, minWidth: 160 }} />
          </Box>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" variant="contained" disabled={!name.trim()} sx={{ textTransform: 'none' }} onClick={() => void save()}>
              {t('save')}
            </Button>
            <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setEditing(false)}>
              {t('cancel')}
            </Button>
          </Box>
        </Box>
      ) : (
        <>
          {/* Material & Spec */}
          {(mat || spc) && (
            <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)', mb: 1 }}>
              {[mat && `${t('bom.material')}: ${mat}`, spc && `${t('bom.spec')}: ${spc}`].filter(Boolean).join(' · ')}
            </Typography>
          )}

          {/* Description */}
          {part.description && (part.description.en || part.description.zh) && (
            <Box sx={{ mb: 1.5, p: 1, bgcolor: 'var(--panel)', borderRadius: 1 }}>
              <BilingualText value={part.description} size={12.5} />
            </Box>
          )}
        </>
      )}

      {/* Editor Price & Cost Controls */}
      {ctx.canEdit && onCostChange && (
        <Box sx={{ my: 1.5, p: 1.25, bgcolor: 'var(--panel)', borderRadius: 1 }}>
          <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-1)', mb: 1 }}>
            {t('costs.summaryTitle')}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
            <TextField
              size="small"
              type="number"
              label={`${t('bom.unitPrice')} (${currency})`}
              value={unitPrice !== null ? unitPrice : ''}
              placeholder={t('costs.missing')}
              onChange={(e) => {
                const val = e.target.value.trim();
                const num = val === '' ? null : Number(val);
                onCostChange(part.id, num !== null && !isNaN(num) ? num : null, currency, note);
              }}
              sx={{ width: 140, '& .MuiInputBase-input': { fontSize: 12.5 } }}
            />
            <TextField
              select
              size="small"
              label={t('bom.currency')}
              value={currency}
              onChange={(e) => onCostChange(part.id, unitPrice, e.target.value, note)}
              sx={{ width: 90, '& .MuiInputBase-input': { fontSize: 12 } }}
            >
              <MenuItem value="CNY">CNY</MenuItem>
              <MenuItem value="USD">USD</MenuItem>
              <MenuItem value="EUR">EUR</MenuItem>
            </TextField>
            <TextField
              size="small"
              label={t('bom.note')}
              value={note}
              placeholder="e.g. includes fasteners"
              onChange={(e) => onCostChange(part.id, unitPrice, currency, e.target.value)}
              sx={{ flex: 1, minWidth: 160, '& .MuiInputBase-input': { fontSize: 12 } }}
            />
            {unitPrice !== null && (
              <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                {t('bom.lineTotal', { count: orderQuantity })}: {(unitPrice * orderQuantity).toFixed(2)} {currency}
              </Typography>
            )}
          </Box>
        </Box>
      )}

      {/* Drawings & 3D Downloads */}
      <Divider sx={{ my: 1.5 }} />
      <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 0.75 }}>
        {t('tabs.documents')} & {t('parts.drawings')}
      </Typography>
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {part.drawing_files.length === 0 && (
          <Typography sx={{ fontSize: 11.5, color: 'var(--text-4)' }}>{t('parts.noDrawings')}</Typography>
        )}
        {part.drawing_files.map((d) => (
          <DrawingPreview key={d.file_id} ctx={ctx} d={d} />
        ))}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          {part.optional_step && (
            <Button
              size="small"
              sx={{ textTransform: 'none', fontSize: 11, justifyContent: 'flex-start', p: 0 }}
              onClick={() => void downloadFile(ctx.die, ctx.config, part.optional_step!.file_id, `${part.id}.step`)}
            >
              {t('parts.partStep')}
            </Button>
          )}
          <Tooltip title={t('parts.model3dTip')}>
            <Button
              size="small"
              sx={{ textTransform: 'none', fontSize: 11, justifyContent: 'flex-start', p: 0 }}
              onClick={() =>
                void downloadModelStep(ctx.die, ctx.config).catch((e) =>
                  setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) }))
                )
              }
            >
              {t('parts.model3d')}
            </Button>
          </Tooltip>
        </Box>
      </Box>

      {/* Linked Documents */}
      {linkedDocs.length > 0 && (
        <>
          <Divider sx={{ my: 1.5 }} />
          <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 0.5 }}>
            {t('parts.linkedDocs', 'Linked documents')}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {linkedDocs.map((doc) => (
              <Button
                key={doc.id}
                size="small"
                variant="outlined"
                sx={{ textTransform: 'none', fontSize: 11, py: 0.25 }}
                onClick={() => void downloadFile(ctx.die, ctx.config, doc.file_id, doc.name)}
              >
                {doc.name}
              </Button>
            ))}
          </Box>
        </>
      )}

      {/* Part Discussion & Proposals */}
      <Divider sx={{ my: 1.5 }} />
      <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 0.5 }}>
        {t('tabs.discussion')} & {t('tabs.proposals')}
      </Typography>

      {/* Thread messages if any */}
      {partThread && partThread.messages.length > 0 ? (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: 1 }}>
          {partThread.messages.map((m) => (
            <Box key={m.id} sx={{ p: 1, bgcolor: 'var(--panel)', borderRadius: 1 }}>
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 0.25 }}>
                {m.author_email} · {m.created_at.slice(0, 16).replace('T', ' ')}
              </Typography>
              <BilingualText value={m.text} isMessage size={12} />
            </Box>
          ))}
        </Box>
      ) : (
        <Typography sx={{ fontSize: 11.5, color: 'var(--text-4)', mb: 1 }}>
          {t('parts.noThreads', 'No discussions for this part.')}
        </Typography>
      )}

      {/* Proposals for this part if any */}
      {partProposals.length > 0 ? (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          {partProposals.map((p) => (
            <Box key={p.id} sx={{ p: 1, bgcolor: 'var(--panel)', borderRadius: 1 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.25 }}>
                <Chip size="small" label={t(`proposals.status.${p.status}`)} sx={CHIP} />
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  {p.author_email} · {p.created_at.slice(0, 10)}
                </Typography>
              </Box>
              <BilingualText value={p.text} size={12} />
            </Box>
          ))}
        </Box>
      ) : (
        <Typography sx={{ fontSize: 11.5, color: 'var(--text-4)' }}>
          {t('parts.noProposals', 'No proposals for this part.')}
        </Typography>
      )}

      {err && <Alert severity="error" sx={{ mt: 1.5, fontSize: 12 }}>{err}</Alert>}
    </Paper>
  );
};

export default PartDetailPanel;
