/**
 * BOM table with cost breakdown, pricing, and integrated part details.
 * Requirement F: The BOM tab is the single place for parts.
 * Clicking any BOM row opens a detail panel BELOW the BOM table with full part information,
 * drawing previews + downloads, optional STEP, linked docs, discussions/proposals, and editor controls.
 * Deep link ?part=<id> is supported and preserved.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import PartName from './PartName';
import { useContentLang } from './BilingualText';
import { PartDetailPanel } from './PartCard';
import { productionApi } from '../api';
import { calcCostSummary, canEditCosts, canViewCosts, emptyBilingual, orderTotal, withQty } from '../helpers';
import type {
  Bilingual,
  BomRow,
  CostRow,
  CostsData,
  DocumentRec,
  ExtraCost,
  Part,
  ProdCtx,
  Proposal,
  Thread,
} from '../types';

const PRESET_EXTRAS: Array<{ en: string; zh: string; defaultAmount: number }> = [
  { en: 'Packaging', zh: '包装', defaultAmount: 15 },
  { en: 'Shipping', zh: '运输', defaultAmount: 20 },
  { en: 'Labour', zh: '装配人工', defaultAmount: 30 },
  { en: 'Facility', zh: '厂房分摊', defaultAmount: 10 },
  { en: 'Scrap', zh: '工序损耗', defaultAmount: 10 },
  { en: 'Management', zh: '管理费', defaultAmount: 15 },
  { en: 'VAT', zh: '增值税', defaultAmount: 25 },
];

export const AddPart: React.FC<{ die: string; config: string; onDone: () => void }> = ({ die, config, onDone }) => {
  const { t } = useTranslation('production');
  const [open, setOpen] = useState(false);
  const [nameEn, setNameEn] = useState('');
  const [nameZh, setNameZh] = useState('');
  const [kind, setKind] = useState<Part['kind']>('made');
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <Button size="small" sx={{ textTransform: 'none', alignSelf: 'flex-start' }} onClick={() => setOpen(true)}>{t('parts.add')}</Button>;
  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', flexWrap: 'wrap', mb: 1.5 }}>
      <TextField size="small" label={`${t('parts.name')} (EN)`} value={nameEn} onChange={(e) => setNameEn(e.target.value)}
        sx={{ minWidth: 200, flex: 1, '& .MuiInputBase-input': { fontSize: 13 } }} placeholder="e.g. Stator core" />
      <TextField size="small" label={`${t('parts.name')} (中文)`} value={nameZh} onChange={(e) => setNameZh(e.target.value)}
        sx={{ minWidth: 160, flex: 1, '& .MuiInputBase-input': { fontSize: 13 } }} placeholder="例：定子铁芯" />
      <TextField select size="small" value={kind} onChange={(e) => setKind(e.target.value as Part['kind'])} sx={{ width: 140 }}>
        {(['made', 'purchased', 'consumable'] as const).map((k) => <MenuItem key={k} value={k}>{t(`parts.kindValue.${k}`)}</MenuItem>)}
      </TextField>
      <Button size="small" variant="contained" disabled={!nameEn.trim()} sx={{ textTransform: 'none' }}
        onClick={async () => {
          try {
            await productionApi.createPart(die, config, {
              name: {
                en: nameEn.trim(),
                zh: nameZh.trim(),
                src_lang: 'en',
                status: { en: 'confirmed', zh: nameZh.trim() ? 'confirmed' : 'pending' },
              },
              description: emptyBilingual('en'),
              kind,
              qty_per_motor: 1,
              material: '',
              spec: '',
              material_i18n: { zh: '', en: '' },
              spec_i18n: { zh: '', en: '' },
            });
            setOpen(false); setNameEn(''); setNameZh(''); onDone();
          } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
        }}>{t('add')}</Button>
      <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setOpen(false)}>{t('cancel')}</Button>
      {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
    </Box>
  );
};

interface BomTableProps {
  rows: BomRow[];
  parts: Part[];
  orderQuantity: number;
  ctx: ProdCtx;
  shareCostsWithOem?: boolean;
  documents?: DocumentRec[];
  threads?: Thread[];
  proposals?: Proposal[];
  onPartChanged?: () => void;
}

const BomTable: React.FC<BomTableProps> = ({
  rows,
  parts,
  orderQuantity,
  ctx,
  shareCostsWithOem = false,
  documents = [],
  threads = [],
  proposals = [],
  onPartChanged,
}) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const byId = useMemo(() => new Map(parts.map((p) => [p.id, p])), [parts]);

  const hasCostAccess = canViewCosts(ctx.role, shareCostsWithOem) || rows.some((r) => r.unit_price !== undefined);
  const canEdit = ctx.canEdit && canEditCosts(ctx.role);

  const [draftRows, setDraftRows] = useState<BomRow[]>(rows);
  const [costRows, setCostRows] = useState<CostRow[]>([]);
  const [extras, setExtras] = useState<ExtraCost[]>([]);
  const [marginPct, setMarginPct] = useState<number>(0);
  const [initialCosts, setInitialCosts] = useState<CostsData | null>(null);

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Selected part for the bottom detail panel + deep linking
  const [selectedPartId, setSelectedPartId] = useState<string | null>(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      return params.get('part');
    } catch {
      return null;
    }
  });

  const handleRowClick = (partId: string) => {
    const next = selectedPartId === partId ? null : partId;
    setSelectedPartId(next);
    try {
      const url = new URL(window.location.href);
      if (next) {
        url.searchParams.set('part', next);
      } else {
        url.searchParams.delete('part');
      }
      window.history.replaceState({}, '', url.toString());
    } catch {}
  };

  // Extra cost add dialog
  const [addExtraOpen, setAddExtraOpen] = useState(false);
  const [extraPreset, setExtraPreset] = useState<string>(PRESET_EXTRAS[0].en);
  const [customExtraEn, setCustomExtraEn] = useState('');
  const [customExtraZh, setCustomExtraZh] = useState('');
  const [extraAmount, setExtraAmount] = useState('15');

  useEffect(() => {
    setDraftRows(rows);
  }, [rows]);

  // Load costs if accessible
  useEffect(() => {
    if (!hasCostAccess) return;
    let live = true;
    productionApi.costs(ctx.die, ctx.config)
      .then((data) => {
        if (!live) return;
        setInitialCosts(data);
        setCostRows(data.rows || []);
        setExtras(data.extras || []);
        setMarginPct(data.margin_pct || 0);
      })
      .catch(() => {
        if (!live) return;
        const initialCostRows: CostRow[] = rows.map((r) => ({
          part_id: r.part_id,
          unit_price: r.unit_price ?? null,
          currency: r.currency || 'CNY',
          note: r.note || null,
        }));
        setCostRows(initialCostRows);
      });
    return () => { live = false; };
  }, [ctx.die, ctx.config, hasCostAccess, rows]);

  // Merge cost info by part_id for fast lookup
  const costMap = useMemo(() => new Map(costRows.map((r) => [r.part_id, r])), [costRows]);

  // Recalculate summary in real-time
  const summary = useMemo(() => {
    return calcCostSummary(draftRows, costRows, extras, marginPct, orderQuantity);
  }, [draftRows, costRows, extras, marginPct, orderQuantity]);

  // Check if anything has changed
  const dirty = useMemo(() => {
    const bomChanged = draftRows.some((r, i) => {
      const orig = rows[i];
      return !orig || orig.qty_per_motor !== r.qty_per_motor;
    });
    if (!hasCostAccess) return bomChanged;

    const initialMap = new Map((initialCosts?.rows || []).map((r) => [r.part_id, r]));
    const costsChanged = costRows.some((r) => {
      const init = initialMap.get(r.part_id);
      if (!init) return r.unit_price !== null || (r.note && r.note.trim() !== '');
      return init.unit_price !== r.unit_price || init.currency !== r.currency || (init.note || '') !== (r.note || '');
    });
    const marginChanged = initialCosts ? initialCosts.margin_pct !== marginPct : marginPct !== 0;
    const extrasChanged = initialCosts ? JSON.stringify(initialCosts.extras) !== JSON.stringify(extras) : extras.length > 0;

    return bomChanged || costsChanged || marginChanged || extrasChanged;
  }, [draftRows, rows, hasCostAccess, initialCosts, costRows, marginPct, extras]);

  const handleUnitPriceChange = (partId: string, val: string) => {
    const num = val.trim() === '' ? null : Number(val);
    setCostRows((prev) => {
      const idx = prev.findIndex((r) => r.part_id === partId);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], unit_price: num !== null && !isNaN(num) ? num : null };
        return next;
      }
      return [...prev, { part_id: partId, unit_price: num !== null && !isNaN(num) ? num : null, currency: 'CNY', note: null }];
    });
  };

  const handleCurrencyChange = (partId: string, cur: string) => {
    setCostRows((prev) => {
      const idx = prev.findIndex((r) => r.part_id === partId);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], currency: cur };
        return next;
      }
      return [...prev, { part_id: partId, unit_price: null, currency: cur, note: null }];
    });
  };

  const handleNoteChange = (partId: string, noteStr: string) => {
    setCostRows((prev) => {
      const idx = prev.findIndex((r) => r.part_id === partId);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], note: noteStr };
        return next;
      }
      return [...prev, { part_id: partId, unit_price: null, currency: 'CNY', note: noteStr }];
    });
  };

  const handleAddExtra = () => {
    let name: Bilingual;
    if (extraPreset === 'custom') {
      name = {
        en: customExtraEn.trim() || 'Extra cost',
        zh: customExtraZh.trim() || customExtraEn.trim() || '额外费用',
        src_lang: 'en',
        status: { en: 'confirmed', zh: 'confirmed' },
      };
    } else {
      const p = PRESET_EXTRAS.find((x) => x.en === extraPreset) || PRESET_EXTRAS[0];
      name = {
        en: p.en,
        zh: p.zh,
        src_lang: 'en',
        status: { en: 'confirmed', zh: 'confirmed' },
      };
    }
    const amount = Math.max(0, Number(extraAmount) || 0);
    const newExtra: ExtraCost = {
      id: `extra_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
      name,
      amount,
    };
    setExtras((prev) => [...prev, newExtra]);
    setAddExtraOpen(false);
    setCustomExtraEn('');
    setCustomExtraZh('');
    setExtraAmount('15');
  };

  const handleDeleteExtra = (id: string) => {
    setExtras((prev) => prev.filter((x) => x.id !== id));
  };

  const saveAll = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const bomChanged = draftRows.some((r, i) => !rows[i] || rows[i].qty_per_motor !== r.qty_per_motor);
      if (bomChanged) {
        await productionApi.saveBom(ctx.die, ctx.config, draftRows);
      }
      if (hasCostAccess) {
        await productionApi.saveCosts(ctx.die, ctx.config, {
          rows: costRows,
          extras,
          margin_pct: marginPct,
        });
        const updatedCosts = await productionApi.costs(ctx.die, ctx.config);
        setInitialCosts(updatedCosts);
        setCostRows(updatedCosts.rows || []);
        setExtras(updatedCosts.extras || []);
        setMarginPct(updatedCosts.margin_pct || 0);
      }
      setMsg(t('saved'));
      setTimeout(() => setMsg(null), 3000);
      ctx.reload();
      onPartChanged?.();
    } catch (e) {
      setMsg(t('failed', { reason: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  };

  const selectedPart = selectedPartId ? byId.get(selectedPartId) : null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* Add Part bar for editors */}
      {canEdit && <AddPart die={ctx.die} config={ctx.config} onDone={() => { onPartChanged?.(); ctx.reload(); }} />}

      {/* Main BOM Table */}
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'hidden' }}>
        <Table size="small" sx={{ '& td, & th': { borderColor: 'var(--line-soft)', fontSize: 12.5, py: 0.6 } }}>
          <TableHead>
            <TableRow sx={{ bgcolor: 'var(--panel)' }}>
              <TableCell sx={{ width: 40 }}>#</TableCell>
              <TableCell>{t('col.title')}</TableCell>
              <TableCell>{t('parts.kind')}</TableCell>
              <TableCell align="right">{t('parts.qty')}</TableCell>
              <TableCell align="right">{t('col.orderQty')}</TableCell>
              {hasCostAccess && (
                <>
                  <TableCell align="right">
                    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                      {t('bom.unitPrice')}
                      <HelpTip title={t('costs.unitPriceTip')} />
                    </Box>
                  </TableCell>
                  <TableCell align="right">{t('bom.lineTotal', { count: orderQuantity })}</TableCell>
                  <TableCell>{t('bom.currency')}</TableCell>
                  <TableCell>{t('bom.note')}</TableCell>
                </>
              )}
              <TableCell>{t('bom.material')}</TableCell>
              <TableCell>{t('bom.spec')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {draftRows.map((r, i) => {
              const p = byId.get(r.part_id);
              const crow = costMap.get(r.part_id);
              const unitPrice = crow?.unit_price !== undefined ? crow.unit_price : (r.unit_price ?? null);
              const currency = crow?.currency || r.currency || 'CNY';
              const note = crow?.note !== undefined ? crow.note : (r.note || '');
              const lineTotal = unitPrice !== null && !isNaN(unitPrice) ? unitPrice * orderQuantity : null;
              const isSelected = selectedPartId === r.part_id;

              const mat = p?.material_i18n ? (p.material_i18n[ui] || p.material_i18n.zh || p.material) : p?.material;
              const spc = p?.spec_i18n ? (p.spec_i18n[ui] || p.spec_i18n.zh || p.spec) : p?.spec;

              return (
                <TableRow
                  key={r.part_id}
                  hover
                  onClick={() => handleRowClick(r.part_id)}
                  sx={{
                    bgcolor: isSelected ? 'rgba(59, 130, 246, 0.08)' : undefined,
                    cursor: 'pointer',
                    '&:hover': { bgcolor: isSelected ? 'rgba(59, 130, 246, 0.14) !important' : undefined },
                  }}
                >
                  <TableCell sx={{ color: 'var(--text-4)' }}>{i + 1}</TableCell>
                  <TableCell>
                    {p ? <PartName name={p.name} /> : <span style={{ color: 'var(--text-4)' }}>{t('bom.unknownPart')}</span>}
                  </TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{p ? t(`parts.kindValue.${p.kind}`) : ''}</TableCell>
                  <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                    {canEdit ? (
                      <TextField
                        size="small"
                        value={r.qty_per_motor}
                        sx={{ width: 65, '& .MuiInputBase-input': { fontSize: 12.5, py: 0.3, textAlign: 'right' } }}
                        onChange={(e) => setDraftRows((d) => withQty(d, r.part_id, Number(e.target.value) || 0, orderQuantity))}
                      />
                    ) : (
                      r.qty_per_motor
                    )}
                  </TableCell>
                  <TableCell align="right">{canEdit ? orderTotal(r.qty_per_motor, orderQuantity) : r.order_total}</TableCell>

                  {/* Cost Columns */}
                  {hasCostAccess && (
                    <>
                      <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                        {canEdit ? (
                          <TextField
                            size="small"
                            type="number"
                            value={unitPrice !== null ? unitPrice : ''}
                            placeholder={t('costs.missing')}
                            onChange={(e) => handleUnitPriceChange(r.part_id, e.target.value)}
                            sx={{
                              width: 85,
                              '& .MuiInputBase-input': {
                                fontSize: 12.5,
                                py: 0.3,
                                textAlign: 'right',
                                color: unitPrice === null ? '#f87171' : undefined,
                              },
                            }}
                          />
                        ) : unitPrice !== null ? (
                          unitPrice.toFixed(2)
                        ) : (
                          <span style={{ color: '#f87171' }}>{t('costs.missing')}</span>
                        )}
                      </TableCell>
                      <TableCell align="right">
                        {lineTotal !== null ? lineTotal.toFixed(2) : '—'}
                      </TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        {canEdit ? (
                          <TextField
                            select
                            size="small"
                            value={currency}
                            onChange={(e) => handleCurrencyChange(r.part_id, e.target.value)}
                            sx={{ width: 85, '& .MuiInputBase-input': { fontSize: 12, py: 0.3 } }}
                          >
                            <MenuItem value="CNY">CNY</MenuItem>
                            <MenuItem value="USD">USD</MenuItem>
                            <MenuItem value="EUR">EUR</MenuItem>
                          </TextField>
                        ) : (
                          currency
                        )}
                      </TableCell>
                      <TableCell sx={{ color: 'var(--text-2)' }} onClick={(e) => e.stopPropagation()}>
                        {canEdit ? (
                          <TextField
                            size="small"
                            value={note || ''}
                            placeholder="Optional note"
                            onChange={(e) => handleNoteChange(r.part_id, e.target.value)}
                            sx={{ width: 140, '& .MuiInputBase-input': { fontSize: 12, py: 0.3 } }}
                          />
                        ) : (
                          note || '—'
                        )}
                      </TableCell>
                    </>
                  )}

                  <TableCell sx={{ color: 'var(--text-2)' }}>{mat || '—'}</TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{spc || '—'}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>

      {/* Editor Save Controls */}
      {canEdit && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Button
            size="small"
            variant="contained"
            disabled={!dirty || busy}
            sx={{ textTransform: 'none', px: 2 }}
            onClick={() => void saveAll()}
          >
            {t('save')}
          </Button>
          {msg && (
            <Typography
              sx={{
                fontSize: 12,
                color: msg === t('saved') ? '#34d399' : '#f87171',
                fontWeight: 600,
              }}
            >
              {msg}
            </Typography>
          )}
        </Box>
      )}

      {/* Cost Summary Card */}
      {hasCostAccess && (
        <Paper
          sx={{
            bgcolor: 'var(--panel-2)',
            border: '1px solid var(--line-soft)',
            borderRadius: 1.5,
            p: 2,
            mt: 1,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-0)' }}>
                {t('costs.summaryTitle')}
              </Typography>
              <HelpTip title={t('costs.summaryTip')} />
            </Box>
            {canEdit && (
              <Button
                size="small"
                startIcon={<AddIcon fontSize="small" />}
                sx={{ textTransform: 'none', fontSize: 12 }}
                onClick={() => setAddExtraOpen(true)}
              >
                {t('costs.addExtra')}
              </Button>
            )}
          </Box>

          {/* Missing Prices Warning */}
          {summary.missing_prices_count > 0 && (
            <Alert severity="warning" sx={{ mb: 2, fontSize: 12.5, py: 0.5 }}>
              {t('costs.missingWarning', { count: summary.missing_prices_count })}
            </Alert>
          )}

          {/* Cost Summary Table */}
          <Table size="small" sx={{ '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5, py: 0.75 } }}>
            <TableHead>
              <TableRow>
                <TableCell>{t('costs.summaryTitle')}</TableCell>
                <TableCell align="right">{t('costs.perMotor')} (CNY)</TableCell>
                <TableCell align="right">{t('costs.orderTotals', { count: orderQuantity })} (CNY)</TableCell>
                {canEdit && <TableCell sx={{ width: 40 }}></TableCell>}
              </TableRow>
            </TableHead>
            <TableBody>
              {/* Parts Subtotal */}
              <TableRow>
                <TableCell sx={{ fontWeight: 600, color: 'var(--text-1)' }}>
                  {t('costs.partsSubtotal')}
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>
                  {summary.parts_subtotal.toFixed(2)}
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>
                  {summary.order_parts_total.toFixed(2)}
                </TableCell>
                {canEdit && <TableCell></TableCell>}
              </TableRow>

              {/* Extra Cost Lines */}
              {extras.map((extra) => {
                const extraName = ui === 'zh' ? (extra.name.zh || extra.name.en) : (extra.name.en || extra.name.zh);
                const orderTot = (extra.amount || 0) * orderQuantity;
                return (
                  <TableRow key={extra.id} sx={{ bgcolor: 'var(--panel)' }}>
                    <TableCell sx={{ pl: 3, color: 'var(--text-2)' }}>
                      • {extraName}
                    </TableCell>
                    <TableCell align="right" sx={{ color: 'var(--text-2)' }}>
                      {canEdit ? (
                        <TextField
                          size="small"
                          type="number"
                          value={extra.amount}
                          onChange={(e) => {
                            const val = Math.max(0, Number(e.target.value) || 0);
                            setExtras((prev) =>
                              prev.map((x) => (x.id === extra.id ? { ...x, amount: val } : x))
                            );
                          }}
                          sx={{ width: 85, '& .MuiInputBase-input': { fontSize: 12, py: 0.25, textAlign: 'right' } }}
                        />
                      ) : (
                        extra.amount.toFixed(2)
                      )}
                    </TableCell>
                    <TableCell align="right" sx={{ color: 'var(--text-2)' }}>
                      {orderTot.toFixed(2)}
                    </TableCell>
                    {canEdit && (
                      <TableCell align="center">
                        <IconButton
                          size="small"
                          sx={{ p: 0.25, color: '#f87171' }}
                          onClick={() => handleDeleteExtra(extra.id)}
                        >
                          <DeleteOutlineIcon fontSize="small" />
                        </IconButton>
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}

              {/* Extra Subtotal */}
              {extras.length > 0 && (
                <TableRow>
                  <TableCell sx={{ fontWeight: 600, color: 'var(--text-1)' }}>
                    {t('costs.extraSubtotal')}
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600 }}>
                    {summary.extra_subtotal.toFixed(2)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600 }}>
                    {summary.order_extra_total.toFixed(2)}
                  </TableCell>
                  {canEdit && <TableCell></TableCell>}
                </TableRow>
              )}

              {/* Total Cost Per Motor */}
              <TableRow sx={{ bgcolor: 'var(--panel)', borderTop: '2px solid var(--line-soft)' }}>
                <TableCell sx={{ fontWeight: 700, color: 'var(--text-0)' }}>
                  {t('costs.costPerMotor')}
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 700 }}>
                  {summary.cost_per_motor.toFixed(2)}
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 700 }}>
                  {summary.order_cost_total.toFixed(2)}
                </TableCell>
                {canEdit && <TableCell></TableCell>}
              </TableRow>

              {/* Margin % */}
              <TableRow>
                <TableCell sx={{ color: 'var(--text-2)' }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    {t('costs.marginPct')}
                    <HelpTip title={t('costs.marginTip')} />
                  </Box>
                </TableCell>
                <TableCell align="right">
                  {canEdit ? (
                    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                      <TextField
                        size="small"
                        type="number"
                        value={marginPct}
                        onChange={(e) => setMarginPct(Math.max(0, Math.min(99.9, Number(e.target.value) || 0)))}
                        sx={{ width: 75, '& .MuiInputBase-input': { fontSize: 12, py: 0.25, textAlign: 'right' } }}
                      />
                      <span>%</span>
                    </Box>
                  ) : (
                    `${marginPct.toFixed(1)} %`
                  )}
                </TableCell>
                <TableCell align="right" sx={{ color: 'var(--text-4)' }}>
                  —
                </TableCell>
                {canEdit && <TableCell></TableCell>}
              </TableRow>

              {/* Price Per Motor */}
              <TableRow sx={{ bgcolor: 'rgba(52, 211, 153, 0.08)' }}>
                <TableCell sx={{ fontWeight: 800, fontSize: 13.5, color: '#34d399' }}>
                  {t('costs.pricePerMotor')}
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 800, fontSize: 13.5, color: '#34d399' }}>
                  {summary.price_per_motor.toFixed(2)}
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 800, fontSize: 13.5, color: '#34d399' }}>
                  {summary.order_price_total.toFixed(2)}
                </TableCell>
                {canEdit && <TableCell></TableCell>}
              </TableRow>
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* Part Detail Panel (rendered below BOM table when a row is clicked) */}
      {selectedPart && (
        <Box sx={{ mt: 1.5 }}>
          <PartDetailPanel
            part={selectedPart}
            ctx={ctx}
            orderQuantity={orderQuantity}
            costRow={costMap.get(selectedPart.id)}
            documents={documents}
            threads={threads}
            proposals={proposals}
            onCostChange={(partId, price, cur, nt) => {
              handleUnitPriceChange(partId, price !== null ? String(price) : '');
              if (cur) handleCurrencyChange(partId, cur);
              if (nt !== undefined) handleNoteChange(partId, nt || '');
            }}
            onChanged={() => {
              onPartChanged?.();
              ctx.reload();
            }}
            onClose={() => handleRowClick(selectedPart.id)}
          />
        </Box>
      )}

      {/* Add Extra Cost Dialog */}
      <Dialog open={addExtraOpen} onClose={() => setAddExtraOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ fontSize: 15, fontWeight: 700 }}>{t('costs.addExtra')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '10px !important' }}>
          <TextField
            select
            size="small"
            label={t('costs.extraName')}
            value={extraPreset}
            onChange={(e) => setExtraPreset(e.target.value)}
          >
            {PRESET_EXTRAS.map((p) => (
              <MenuItem key={p.en} value={p.en}>
                {ui === 'zh' ? `${p.zh} (${p.en})` : `${p.en} (${p.zh})`}
              </MenuItem>
            ))}
            <MenuItem value="custom">Custom…</MenuItem>
          </TextField>

          {extraPreset === 'custom' && (
            <>
              <TextField
                size="small"
                label="Name (EN)"
                value={customExtraEn}
                onChange={(e) => setCustomExtraEn(e.target.value)}
                placeholder="e.g. Export crate"
              />
              <TextField
                size="small"
                label="Name (中文)"
                value={customExtraZh}
                onChange={(e) => setCustomExtraZh(e.target.value)}
                placeholder="例：出口木箱"
              />
            </>
          )}

          <TextField
            size="small"
            type="number"
            label={`${t('costs.amount')} (CNY)`}
            value={extraAmount}
            onChange={(e) => setExtraAmount(e.target.value)}
          />
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button size="small" onClick={() => setAddExtraOpen(false)} sx={{ textTransform: 'none' }}>
            {t('cancel')}
          </Button>
          <Button size="small" variant="contained" onClick={handleAddExtra} sx={{ textTransform: 'none' }}>
            {t('add')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default BomTable;
