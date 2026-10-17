/** BOM table (order totals follow the per-motor quantity); the per-motor quantity is editable by owner/engineer/admin. */
import React, { useEffect, useState } from 'react';
import { Box, Button, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText from './BilingualText';
import { productionApi } from '../api';
import { orderTotal, withQty } from '../helpers';
import type { BomRow, Part, ProdCtx } from '../types';

const BomTable: React.FC<{ rows: BomRow[]; parts: Part[]; orderQuantity: number; ctx: ProdCtx }> = ({ rows, parts, orderQuantity, ctx }) => {
  const { t } = useTranslation('production');
  const [draft, setDraft] = useState<BomRow[]>(rows);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => setDraft(rows), [rows]);
  const byId = new Map(parts.map((p) => [p.id, p]));
  const dirty = JSON.stringify(draft) !== JSON.stringify(rows);

  const save = async () => {
    try { await productionApi.saveBom(ctx.die, ctx.config, draft); setMsg(`✓ ${t('saved')}`); ctx.reload(); }
    catch (e) { setMsg(`✗ ${t('failed', { reason: e instanceof Error ? e.message : String(e) })}`); }
  };

  if (draft.length === 0) return <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('bom.none')}</Typography>;
  return (
    <Box>
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto' }}>
        <Table size="small" sx={{
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
          '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>#</TableCell>
              <TableCell>{t('bom.part')}</TableCell>
              <TableCell>{t('bom.kind')}</TableCell>
              <TableCell align="right">{t('bom.perMotor')}</TableCell>
              <TableCell align="right">{t('bom.orderTotal', { count: orderQuantity })}</TableCell>
              <TableCell>{t('bom.material')}</TableCell>
              <TableCell>{t('bom.spec')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {draft.map((r, i) => {
              const p = byId.get(r.part_id);
              return (
                <TableRow key={r.part_id}>
                  <TableCell sx={{ color: 'var(--text-4)' }}>{i + 1}</TableCell>
                  <TableCell>{p ? <BilingualText value={p.name} size={12.5} /> : <span style={{ color: 'var(--text-4)' }}>{t('bom.unknownPart')}</span>}</TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{p ? t(`parts.kindValue.${p.kind}`) : ''}</TableCell>
                  <TableCell align="right">
                    {ctx.canEdit ? (
                      <TextField size="small" value={r.qty_per_motor} sx={{ width: 70, '& .MuiInputBase-input': { fontSize: 12.5, py: 0.4, textAlign: 'right' } }}
                        onChange={(e) => setDraft((d) => withQty(d, r.part_id, Number(e.target.value) || 0, orderQuantity))} />
                    ) : r.qty_per_motor}
                  </TableCell>
                  <TableCell align="right">{ctx.canEdit ? orderTotal(r.qty_per_motor, orderQuantity) : r.order_total}</TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{p?.material}</TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{p?.spec}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>
      {ctx.canEdit && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 1 }}>
          <Button size="small" variant="contained" disabled={!dirty} sx={{ textTransform: 'none' }} onClick={() => void save()}>{t('save')}</Button>
          {msg && <Typography sx={{ fontSize: 11, color: msg.startsWith('✓') ? '#34d399' : '#f87171' }}>{msg}</Typography>}
        </Box>
      )}
    </Box>
  );
};

export default BomTable;
