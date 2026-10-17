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
import { canEditProcurement, displayText, getStatusColor, makeBilingual } from '../helpers';
import type {
  CostRow,
  DocumentRec,
  DrawingFile,
  Lang,
  Part,
  ProcurementLine,
  ProdCtx,
  Proposal,
  Receipt,
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
  procurementLine?: ProcurementLine;
  documents?: DocumentRec[];
  threads?: Thread[];
  proposals?: Proposal[];
  onCostChange?: (partId: string, unitPrice: number | null, currency: string, note: string | null) => void;
  onProcurementSaved?: () => void;
  onChanged: () => void;
  onClose?: () => void;
}

export const PartDetailPanel: React.FC<PartDetailPanelProps> = ({
  part,
  ctx,
  orderQuantity,
  costRow,
  procurementLine,
  documents = [],
  threads = [],
  proposals = [],
  onCostChange,
  onProcurementSaved,
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

  const canProcure = canEditProcurement(ctx.role);

  const [procSupplier, setProcSupplier] = useState(procurementLine?.supplier || '');
  const [procPo, setProcPo] = useState(procurementLine?.po_number || '');
  const [procLeadDays, setProcLeadDays] = useState(String(procurementLine?.lead_time_days ?? 0));
  const [procOrderedOn, setProcOrderedOn] = useState(procurementLine?.ordered_on || '');
  const [procNeedBy, setProcNeedBy] = useState(procurementLine?.need_by || procurementLine?.due_on || '');
  const [procConfirmedOn, setProcConfirmedOn] = useState(procurementLine?.confirmed_on || '');
  const [procRequestedOn, setProcRequestedOn] = useState(procurementLine?.requested_on || '');
  const [procQtyOrdered, setProcQtyOrdered] = useState(procurementLine?.qty_ordered !== null && procurementLine?.qty_ordered !== undefined ? String(procurementLine.qty_ordered) : '');
  const [procNote, setProcNote] = useState(procurementLine?.note || '');
  const [receipts, setReceipts] = useState<Receipt[]>(procurementLine?.receipts || []);
  const [procBusy, setProcBusy] = useState(false);
  const [procMsg, setProcMsg] = useState<string | null>(null);

  // New receipt form state
  const [showAddReceipt, setShowAddReceipt] = useState(false);
  const [rcptDate, setRcptDate] = useState(new Date().toISOString().slice(0, 10));
  const [rcptQty, setRcptQty] = useState('');
  const [rcptAccepted, setRcptAccepted] = useState('');
  const [rcptRejected, setRcptRejected] = useState('0');
  const [rcptInspector, setRcptInspector] = useState('');
  const [rcptNote, setRcptNote] = useState('');

  useEffect(() => {
    if (procurementLine) {
      setProcSupplier(procurementLine.supplier || '');
      setProcPo(procurementLine.po_number || '');
      setProcLeadDays(String(procurementLine.lead_time_days ?? 0));
      setProcOrderedOn(procurementLine.ordered_on || '');
      setProcNeedBy(procurementLine.need_by || procurementLine.due_on || '');
      setProcConfirmedOn(procurementLine.confirmed_on || '');
      setProcRequestedOn(procurementLine.requested_on || '');
      setProcQtyOrdered(procurementLine.qty_ordered !== null && procurementLine.qty_ordered !== undefined ? String(procurementLine.qty_ordered) : '');
      setProcNote(procurementLine.note || '');
      setReceipts(procurementLine.receipts || []);
    }
  }, [procurementLine]);

  const saveProcurement = async (newRcpts?: Receipt[]) => {
    setProcBusy(true);
    setProcMsg(null);
    setErr(null);
    try {
      const payload: Partial<ProcurementLine> = {
        supplier: procSupplier.trim(),
        po_number: procPo.trim(),
        lead_time_days: Math.max(0, parseInt(procLeadDays) || 0),
        ordered_on: procOrderedOn.trim() || null,
        need_by: procNeedBy.trim() || null,
        confirmed_on: procConfirmedOn.trim() || null,
        requested_on: procRequestedOn.trim() || null,
        qty_ordered: procQtyOrdered.trim() ? Math.max(0, parseFloat(procQtyOrdered)) : null,
        note: procNote.trim() || null,
        receipts: newRcpts ?? receipts,
      };
      await productionApi.saveProcurementLine(ctx.die, ctx.config, part.id, payload);
      setProcMsg(t('saved'));
      setTimeout(() => setProcMsg(null), 3000);
      onProcurementSaved?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setProcBusy(false);
    }
  };

  const handleRecordReceipt = async () => {
    const q = parseFloat(rcptQty);
    const acc = parseFloat(rcptAccepted);
    const rej = parseFloat(rcptRejected || '0');
    if (isNaN(q) || isNaN(acc) || isNaN(rej) || q <= 0 || acc < 0 || rej < 0) {
      setErr(t('procurement.invalidReceiptQty', 'Please enter valid non-negative quantities.'));
      return;
    }
    if (Math.abs(acc + rej - q) > 1e-4) {
      setErr(t('procurement.mismatchReceipt', 'Accepted + Rejected must equal Total Received Qty.'));
      return;
    }
    const newEntry: Receipt = {
      date: rcptDate.trim() || new Date().toISOString().slice(0, 10),
      qty: q,
      accepted: acc,
      rejected: rej,
      inspector: rcptInspector.trim(),
      note: rcptNote.trim(),
    };
    const nextList = [...receipts, newEntry];
    setReceipts(nextList);
    setShowAddReceipt(false);
    setRcptQty('');
    setRcptAccepted('');
    setRcptRejected('0');
    setRcptInspector('');
    setRcptNote('');
    await saveProcurement(nextList);
  };

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

      {/* Procurement & Quality Inspection Block */}
      <Divider sx={{ my: 1.5 }} />
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1, flexWrap: 'wrap', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-1)' }}>
            {t('procurement.blockTitle', 'Procurement & Incoming Inspection')}
          </Typography>
          {procurementLine?.status && (
            <Chip
              size="small"
              label={t(`procurement.statusValue.${procurementLine.status}`, procurementLine.status)}
              sx={{
                height: 20,
                fontSize: 10.5,
                fontWeight: 600,
                bgcolor: getStatusColor(procurementLine.status).bg,
                color: getStatusColor(procurementLine.status).text,
                border: `1px solid ${getStatusColor(procurementLine.status).border}`,
              }}
            />
          )}
          {procurementLine?.has_quality_issue && (
            <Chip
              size="small"
              label={t('procurement.qualityIssue', 'Quality Issue (Rejections)')}
              sx={{ height: 20, fontSize: 10.5, fontWeight: 600, bgcolor: 'rgba(239, 68, 68, 0.15)', color: '#ef4444', border: '1px solid rgba(239, 68, 68, 0.4)' }}
            />
          )}
        </Box>

        {canProcure && (
          <Button
            size="small"
            variant="contained"
            disabled={procBusy}
            onClick={() => void saveProcurement()}
            sx={{ textTransform: 'none', fontSize: 12, height: 26 }}
          >
            {procBusy ? t('saving') : t('procurement.saveLine', 'Save Procurement')}
          </Button>
        )}
      </Box>

      {procMsg && <Alert severity="success" sx={{ mb: 1, py: 0.25, fontSize: 12 }}>{procMsg}</Alert>}

      {/* Procurement PO Fields */}
      <Box sx={{ p: 1.5, bgcolor: 'var(--panel)', borderRadius: 1.5, mb: 1.5, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
          <TextField
            size="small"
            label={t('procurement.supplier', 'Supplier')}
            value={procSupplier}
            disabled={!canProcure}
            onChange={(e) => setProcSupplier(e.target.value)}
            sx={{ flex: 1.5, minWidth: 160, '& .MuiInputBase-input': { fontSize: 12.5 } }}
            placeholder="e.g. Baosteel / Guangdong Magnet"
          />
          <TextField
            size="small"
            label={t('procurement.poNumber', 'PO Number')}
            value={procPo}
            disabled={!canProcure}
            onChange={(e) => setProcPo(e.target.value)}
            sx={{ flex: 1, minWidth: 130, '& .MuiInputBase-input': { fontSize: 12.5 } }}
            placeholder="e.g. PO-2026-088"
          />
          <TextField
            size="small"
            type="number"
            label={t('procurement.leadTimeDays', 'Lead Time (days)')}
            value={procLeadDays}
            disabled={!canProcure}
            onChange={(e) => setProcLeadDays(e.target.value)}
            sx={{ width: 120, '& .MuiInputBase-input': { fontSize: 12.5 } }}
          />
          <TextField
            size="small"
            type="number"
            label={t('procurement.qtyOrdered', 'Qty Ordered (override)')}
            value={procQtyOrdered}
            disabled={!canProcure}
            onChange={(e) => setProcQtyOrdered(e.target.value)}
            placeholder={String((part.qty_per_motor || 1) * orderQuantity)}
            sx={{ width: 150, '& .MuiInputBase-input': { fontSize: 12.5 } }}
          />
        </Box>

        <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
          <TextField
            size="small"
            type="date"
            InputLabelProps={{ shrink: true }}
            label={t('procurement.orderedOn', 'Ordered Date')}
            value={procOrderedOn}
            disabled={!canProcure}
            onChange={(e) => setProcOrderedOn(e.target.value)}
            sx={{ width: 140, '& .MuiInputBase-input': { fontSize: 12 } }}
          />
          <TextField
            size="small"
            type="date"
            InputLabelProps={{ shrink: true }}
            label={t('procurement.confirmedOn', 'Supplier Promised')}
            value={procConfirmedOn}
            disabled={!canProcure}
            onChange={(e) => setProcConfirmedOn(e.target.value)}
            sx={{ width: 150, '& .MuiInputBase-input': { fontSize: 12 } }}
          />
          <TextField
            size="small"
            type="date"
            InputLabelProps={{ shrink: true }}
            label={t('procurement.needBy', 'Need By (Assembly)')}
            value={procNeedBy}
            disabled={!canProcure}
            onChange={(e) => setProcNeedBy(e.target.value)}
            sx={{ width: 160, '& .MuiInputBase-input': { fontSize: 12 } }}
          />
          <TextField
            size="small"
            label={t('procurement.note', 'Procurement Note')}
            value={procNote}
            disabled={!canProcure}
            onChange={(e) => setProcNote(e.target.value)}
            placeholder="e.g. Includes express air freight"
            sx={{ flex: 1, minWidth: 180, '& .MuiInputBase-input': { fontSize: 12 } }}
          />
        </Box>

        {/* Derived Dates info */}
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', pt: 0.5, fontSize: 11.5, color: 'var(--text-3)' }}>
          {procurementLine?.order_by && (
            <span>
              {t('procurement.orderBy', 'MRP Order By')}: <strong style={{ color: procurementLine.is_to_order ? '#f97316' : 'var(--text-1)' }}>{procurementLine.order_by}</strong>
            </span>
          )}
          {procurementLine?.expected_arrival && (
            <span>
              {t('procurement.expectedArrival', 'Expected Arrival')}: <strong style={{ color: procurementLine.is_late ? '#ef4444' : procurementLine.is_at_risk ? '#f59e0b' : 'var(--text-1)' }}>{procurementLine.expected_arrival}</strong>
            </span>
          )}
          <span>
            {t('procurement.receivedSummary', 'Received')}: <strong style={{ color: procurementLine?.status === 'received' ? '#10b981' : 'var(--text-1)' }}>{procurementLine?.received_qty ?? 0} / {procurementLine?.qty_needed ?? ((part.qty_per_motor || 1) * orderQuantity)}</strong>
          </span>
        </Box>
      </Box>

      {/* Incoming Inspection & Receipts Table */}
      <Box sx={{ mb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 0.75 }}>
          <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>
            {t('procurement.receiptsTitle', 'Goods Receipts & Incoming Inspection (Partial Deliveries)')}
          </Typography>
          {canProcure && !showAddReceipt && (
            <Button
              size="small"
              onClick={() => setShowAddReceipt(true)}
              sx={{ textTransform: 'none', fontSize: 11.5, py: 0.2 }}
            >
              + {t('procurement.addReceipt', 'Record Incoming Receipt')}
            </Button>
          )}
        </Box>

        {receipts.length > 0 ? (
          <Box sx={{ border: '1px solid var(--line-soft)', borderRadius: 1, overflow: 'hidden', mb: 1 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
              <thead>
                <tr style={{ background: 'var(--panel)', color: 'var(--text-3)', textAlign: 'left' }}>
                  <th style={{ padding: '6px 10px' }}>{t('procurement.rcptDate', 'Date')}</th>
                  <th style={{ padding: '6px 10px', textAlign: 'right' }}>{t('procurement.rcptQty', 'Delivered')}</th>
                  <th style={{ padding: '6px 10px', textAlign: 'right' }}>{t('procurement.rcptAccepted', 'Accepted')}</th>
                  <th style={{ padding: '6px 10px', textAlign: 'right' }}>{t('procurement.rcptRejected', 'Rejected')}</th>
                  <th style={{ padding: '6px 10px' }}>{t('procurement.rcptInspector', 'Inspector')}</th>
                  <th style={{ padding: '6px 10px' }}>{t('procurement.rcptNote', 'Inspection Note')}</th>
                </tr>
              </thead>
              <tbody>
                {receipts.map((rcpt, idx) => (
                  <tr key={idx} style={{ borderTop: '1px solid var(--line-soft)' }}>
                    <td style={{ padding: '5px 10px' }}>{rcpt.date}</td>
                    <td style={{ padding: '5px 10px', textAlign: 'right' }}>{rcpt.qty}</td>
                    <td style={{ padding: '5px 10px', textAlign: 'right', color: '#10b981', fontWeight: 600 }}>{rcpt.accepted}</td>
                    <td style={{ padding: '5px 10px', textAlign: 'right', color: rcpt.rejected > 0 ? '#ef4444' : 'var(--text-4)' }}>{rcpt.rejected}</td>
                    <td style={{ padding: '5px 10px', color: 'var(--text-3)' }}>{rcpt.inspector || '—'}</td>
                    <td style={{ padding: '5px 10px', color: 'var(--text-3)' }}>{rcpt.note || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Box>
        ) : (
          <Typography sx={{ fontSize: 11.5, color: 'var(--text-4)', mb: 1 }}>
            {t('procurement.noReceipts', 'No receipts recorded yet.')}
          </Typography>
        )}

        {/* Add Receipt Sub-form */}
        {showAddReceipt && canProcure && (
          <Box sx={{ p: 1.5, bgcolor: 'var(--panel)', border: '1px solid var(--line-soft)', borderRadius: 1.5, mb: 1, display: 'flex', flexDirection: 'column', gap: 1 }}>
            <Typography sx={{ fontSize: 11.5, fontWeight: 700, color: '#38bdf8' }}>
              {t('procurement.recordInspection', 'New Incoming Goods Receipt')}
            </Typography>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
              <TextField
                size="small"
                type="date"
                InputLabelProps={{ shrink: true }}
                label={t('procurement.rcptDate', 'Date')}
                value={rcptDate}
                onChange={(e) => setRcptDate(e.target.value)}
                sx={{ width: 135, '& .MuiInputBase-input': { fontSize: 12 } }}
              />
              <TextField
                size="small"
                type="number"
                label={t('procurement.rcptQty', 'Total Qty')}
                value={rcptQty}
                onChange={(e) => {
                  setRcptQty(e.target.value);
                  if (!rcptAccepted) setRcptAccepted(e.target.value);
                }}
                sx={{ width: 100, '& .MuiInputBase-input': { fontSize: 12 } }}
              />
              <TextField
                size="small"
                type="number"
                label={t('procurement.rcptAccepted', 'Accepted')}
                value={rcptAccepted}
                onChange={(e) => setRcptAccepted(e.target.value)}
                sx={{ width: 100, '& .MuiInputBase-input': { fontSize: 12 } }}
              />
              <TextField
                size="small"
                type="number"
                label={t('procurement.rcptRejected', 'Rejected')}
                value={rcptRejected}
                onChange={(e) => setRcptRejected(e.target.value)}
                sx={{ width: 100, '& .MuiInputBase-input': { fontSize: 12 } }}
              />
              <TextField
                size="small"
                label={t('procurement.rcptInspector', 'Inspector')}
                value={rcptInspector}
                onChange={(e) => setRcptInspector(e.target.value)}
                placeholder="QC Inspector"
                sx={{ width: 130, '& .MuiInputBase-input': { fontSize: 12 } }}
              />
              <TextField
                size="small"
                label={t('procurement.rcptNote', 'Inspection Note')}
                value={rcptNote}
                onChange={(e) => setRcptNote(e.target.value)}
                placeholder="Pass / Scratches noted"
                sx={{ flex: 1, minWidth: 160, '& .MuiInputBase-input': { fontSize: 12 } }}
              />
            </Box>
            <Box sx={{ display: 'flex', gap: 1, justifyContent: 'flex-end', mt: 0.5 }}>
              <Button size="small" onClick={() => setShowAddReceipt(false)} sx={{ textTransform: 'none', fontSize: 11.5 }}>
                {t('cancel')}
              </Button>
              <Button size="small" variant="contained" onClick={handleRecordReceipt} sx={{ textTransform: 'none', fontSize: 11.5 }}>
                {t('procurement.confirmReceipt', 'Save Receipt & Inspection')}
              </Button>
            </Box>
          </Box>
        )}
      </Box>

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
