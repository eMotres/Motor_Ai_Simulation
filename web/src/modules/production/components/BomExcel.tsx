/**
 * BOM list to and from Excel (owner 2026-11-05).  Upload shows what the file would change; nothing is written until the editor applies it.
 * A part that is not in the file stays in the BOM and a blank cell keeps its value, so an import never deletes.
 */
import React, { useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { downloadBomXlsx, productionApi } from '../api';
import type { BomImportChange, BomImportPlan, ProdCtx } from '../types';

const ACTION_COLOR: Record<string, string> = { new: '#10b981', update: '#38bdf8', same: 'var(--text-4)' };

const BomExcel: React.FC<{ ctx: ProdCtx; hasUnsavedEdits: boolean; onDone: () => void }> = ({ ctx, hasUnsavedEdits, onDone }) => {
  const { t } = useTranslation('production');
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [plan, setPlan] = useState<BomImportPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const fail = (e: unknown) => setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) }));
  const close = () => { setPlan(null); setFile(null); setErr(null); };

  const preview = async (picked: File) => {
    setBusy(true); setErr(null); setDone(null);
    try {
      setPlan(await productionApi.importBom(ctx.die, ctx.config, picked, ctx.list));
      setFile(picked);
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const apply = async () => {
    if (!file || !plan) return;
    setBusy(true); setErr(null);
    try {
      await productionApi.importBom(ctx.die, ctx.config, file, ctx.list, plan.plan_id);
      setDone(t('bomExcel.applied', { added: plan.summary.new, changed: plan.summary.update }));
      close();
      onDone();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  // the stored price is per motor on the motor list ("Unit price" x quantity), per piece on tooling: the label says which
  const fieldLabel = (c: BomImportChange) => (c.field === 'unit_price'
    ? t(ctx.list ? 'bom.unitPriceTooling' : 'bom.unitPrice')
    : t(`bomExcel.field.${c.field}`));
  const value = (c: BomImportChange, v: unknown) => {
    if (v === null || v === undefined || v === '') return '—';
    return c.field === 'kind' ? t(`parts.kindValue.${String(v)}`, String(v)) : String(v);
  };
  const canApply = !!plan && plan.errors.length === 0 && plan.summary.new + plan.summary.update > 0;
  const listed = plan?.rows.filter((r) => r.action !== 'same') ?? [];

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
      <Tooltip title={hasUnsavedEdits ? t('bomExcel.saveFirst') : ''}>
        <span>
          <Button size="small" disabled={busy || hasUnsavedEdits} sx={{ textTransform: 'none' }} onClick={() => input.current?.click()}>
            {t('bomExcel.upload')}
          </Button>
        </span>
      </Tooltip>
      <Button size="small" disabled={busy} sx={{ textTransform: 'none' }}
        onClick={() => { void downloadBomXlsx(ctx.die, ctx.config, ctx.list).catch(fail); }}>
        {t('bomExcel.download')}
      </Button>
      <HelpTip title={t('bomExcel.tip')} />
      <input ref={input} type="file" hidden accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        onChange={(e) => {
          const picked = e.target.files?.[0];
          e.target.value = '';                                // the same file can be picked again after it is corrected
          if (picked) void preview(picked);
        }} />
      {done && <Typography sx={{ fontSize: 12, color: '#10b981' }}>{done}</Typography>}
      {err && !plan && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}

      <Dialog open={!!plan} onClose={busy ? undefined : close} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: 15 }}>{t('bomExcel.title', { name: file?.name ?? '' })}</DialogTitle>
        {plan && (
          <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <Typography sx={{ fontSize: 13 }}>
              {t('bomExcel.summary', { added: plan.summary.new, changed: plan.summary.update, same: plan.summary.same })}
            </Typography>
            {plan.errors.length > 0 && (
              <Alert severity="error" sx={{ fontSize: 12.5 }}>
                <Box sx={{ fontWeight: 600, mb: 0.5 }}>{t('bomExcel.hasErrors')}</Box>
                {plan.errors.map((e) => (
                  <Box key={`${e.row}-${e.code}`}>
                    {e.row > 0 && `${t('bomExcel.row', { row: e.row })}: `}{t(`bomExcel.error.${e.code}`)}{e.value ? ` (${e.value})` : ''}
                  </Box>
                ))}
              </Alert>
            )}
            {listed.length > 0 && (
              <Table size="small" sx={{ '& td, & th': { fontSize: 12.5, borderColor: 'var(--line-soft)', py: 0.5 } }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ width: 48 }}>#</TableCell>
                    <TableCell>{t('col.title')}</TableCell>
                    <TableCell sx={{ width: 90 }}>{t('bomExcel.action')}</TableCell>
                    <TableCell>{t('bomExcel.changes')}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {listed.map((r) => (
                    <TableRow key={r.row}>
                      <TableCell sx={{ color: 'var(--text-4)' }}>{r.row}</TableCell>
                      <TableCell>{r.name}</TableCell>
                      <TableCell sx={{ color: ACTION_COLOR[r.action] }}>{t(`bomExcel.actionValue.${r.action}`)}</TableCell>
                      <TableCell>
                        {r.changes.map((c) => (
                          <Box key={c.field}>
                            {fieldLabel(c)}: {r.action === 'new' ? value(c, c.new) : `${value(c, c.old)} → ${value(c, c.new)}`}
                          </Box>
                        ))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {plan.missing.length > 0 && (
              <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>
                {t('bomExcel.missing', { names: plan.missing.map((m) => m.name).join(', ') })}
              </Typography>
            )}
            {plan.ignored_columns.length > 0 && (
              <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>
                {t('bomExcel.ignored', { names: plan.ignored_columns.join(', ') })}
              </Typography>
            )}
            {err && <Alert severity="error" sx={{ fontSize: 12.5 }}>{err}</Alert>}
          </DialogContent>
        )}
        <DialogActions>
          <Button size="small" disabled={busy} sx={{ textTransform: 'none' }} onClick={close}>{t('cancel')}</Button>
          <Button size="small" variant="contained" disabled={busy || !canApply} sx={{ textTransform: 'none' }} onClick={() => { void apply(); }}>
            {t('bomExcel.apply')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default BomExcel;
