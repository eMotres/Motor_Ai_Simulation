/** Propellers > Mission: the pieces of the page (motor picker, input fields, best-candidate card, ranked table, chart, details, refined propeller). */
import { useState } from 'react';
import { Alert, Box, Button, Checkbox, Chip, FormControlLabel, ListSubheader, MenuItem, Select, Table, TableBody, TableCell, TableHead, TableRow,
  TableSortLabel, TextField, Tooltip, Typography } from '@mui/material';
import { CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Scatter, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import { useTranslation } from 'react-i18next';

import { fmt } from '../../lib/propellerDesign';
import type { Geometry } from '../../lib/propellerDesignApi';
import { chartData, failureMessage, motorGroups, motorKey, msToKmh, stripText, tableRow } from '../../lib/propellerMission';
import type { Alternative, CandidateResult, MotorEntry, Objective, Point, Refined, SortKey } from '../../lib/propellerMission';
import { PlanformChart, ZoneTable } from './DesignerParts';
import { Panel } from './DesignerParts';

const TXT = { fontSize: 10, fill: 'var(--text-3)' };
const SERIES = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf', '#8c564b', '#e377c2'];
const CELL = { fontSize: 11, py: 0.25, whiteSpace: 'nowrap' } as const;

/** A text field with a short label and the explanation in a tooltip. */
export function MField({ label, tip, value, onChange, width = 96, error }: { label: string; tip?: string; value: string; onChange: (v: string) => void; width?: number; error?: string | null }) {
  const { t } = useTranslation('simulation');
  return (
    <Tooltip title={tip ?? ''} disableHoverListener={!tip}>
      <TextField size="small" label={label} value={value} error={!!error} helperText={error ? t(`propMissionFieldErr_${error}`) : undefined} onChange={(e) => onChange(e.target.value)} sx={{ width }}
        inputProps={{ inputMode: 'decimal', 'aria-label': label, 'aria-invalid': !!error }} InputLabelProps={{ sx: { fontSize: 12 } }} FormHelperTextProps={{ sx: { fontSize: 10, mx: 0 } }} />
    </Tooltip>
  );
}

/** Values in labelled cells (label above, value below); a missing value is skipped. */
export function KvGrid({ items, testId }: { items: Array<[string, string | number | null | undefined]>; testId?: string }) {
  return (
    <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mt: 0.5 }} data-testid={testId}>
      {items.filter(([, v]) => v !== null && v !== undefined && v !== '').map(([k, v]) => (
        <Box key={k} sx={{ minWidth: 56 }}>
          <Typography sx={{ fontSize: 10, color: 'var(--text-3)', lineHeight: 1.2 }}>{k}</Typography>
          <Typography sx={{ fontSize: 13, lineHeight: 1.3 }}>{v}</Typography>
        </Box>
      ))}
    </Box>
  );
}

/** What could and could not be verified for a candidate (voltage check, drive bus window, current limits), each with its status. */
export function CheckList({ checks }: { checks: Record<string, string> | undefined }) {
  const { t } = useTranslation('simulation');
  if (!checks || Object.keys(checks).length === 0) return null;
  const bad = (v: string) => v === 'unverified' || v === 'unknown' || v === 'fail';
  return (
    <Box sx={{ mt: 1 }} data-testid="mission-checks">
      <Tooltip title={Object.values(checks).some(bad) ? t('propMissionChecksUnavailable') : ''}>
        <Typography sx={{ fontSize: 12, fontWeight: 600, color: Object.values(checks).some(bad) ? 'var(--warn, #ed6c02)' : undefined }}>{t('propMissionChecks')}</Typography>
      </Tooltip>
      <KvGrid items={Object.entries(checks).map(([k, v]) => [t(`propMissionCheck_${k}`, { defaultValue: k }), t(`propMissionCheckVal_${v}`, { defaultValue: v })] as [string, string])} />
    </Box>
  );
}

export function MCheck({ label, tip, checked, onChange }: { label: string; tip?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <Tooltip title={tip ?? ''} disableHoverListener={!tip}>
      <FormControlLabel control={<Checkbox size="small" checked={checked} onChange={(e) => onChange(e.target.checked)} />}
        label={<Typography sx={{ fontSize: 12 }}>{label}</Typography>} />
    </Tooltip>
  );
}

export function KindChip({ kind }: { kind: MotorEntry['kind'] }) {
  const { t } = useTranslation('simulation');
  const color = kind === 's1' ? 'success' : kind === 'estimate' ? 'warning' : 'default';
  return <Chip size="small" color={color} variant="outlined" label={t(`propMissionKind_${kind}`)} sx={{ fontSize: 10, height: 18 }} />;
}

/** The catalogue motors, searchable by name / designation and grouped by die; a motor with no usable data is greyed with the reason. */
export function MotorPicker({ motors, value, onChange }: { motors: MotorEntry[]; value: string; onChange: (key: string) => void }) {
  const { t } = useTranslation('simulation');
  const [query, setQuery] = useState('');
  const groups = motorGroups(motors, query);
  const chosen = motors.find((m) => motorKey(m) === value);
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }} data-testid="mission-motor">
      <TextField size="small" label={t('propMissionMotorSearch')} value={query} onChange={(e) => setQuery(e.target.value)} inputProps={{ 'aria-label': t('propMissionMotorSearch') }}
        InputLabelProps={{ sx: { fontSize: 12 } }} />
      <Select size="small" displayEmpty value={value} onChange={(e) => onChange(String(e.target.value))} sx={{ fontSize: 12 }} inputProps={{ 'aria-label': t('propMissionMotor') }}
        renderValue={(v) => (v ? (chosen?.name ?? String(v)) : t('propMissionMotorPick'))} MenuProps={{ PaperProps: { sx: { maxHeight: 420 } } }}>
        {groups.length === 0 && <MenuItem disabled sx={{ fontSize: 12 }}>{t('propMissionMotorNone')}</MenuItem>}
        {groups.flatMap((g) => [
          <ListSubheader key={`h-${g.die}`} sx={{ fontSize: 11, lineHeight: '24px' }}>{g.die}</ListSubheader>,
          ...g.motors.map((m) => (
            <MenuItem key={motorKey(m)} value={motorKey(m)} disabled={m.kind === 'none'} sx={{ fontSize: 12, gap: 1, opacity: m.kind === 'none' ? 0.6 : 1 }}>
              <Tooltip title={m.kind === 'none' ? (m.reason ?? '') : ''} placement="right">
                <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', width: '100%' }}>
                  <span style={{ flex: 1 }}>{m.name}</span><KindChip kind={m.kind} />
                </Box>
              </Tooltip>
            </MenuItem>
          )),
        ])}
      </Select>
      {chosen && (
        <Box data-testid="mission-motor-info">
          <KvGrid items={[[t('propMissionModel'), t(`propMissionKind_${chosen.kind}`)], ['Kv, rpm/V', chosen.kv_rpm_per_V != null ? fmt(chosen.kv_rpm_per_V, 0) : null],
            ['R, mΩ', chosen.R_phase_ohm != null ? fmt(chosen.R_phase_ohm * 1000, 1) : null], [t('propMissionPoles'), chosen.pole_pairs != null ? fmt(chosen.pole_pairs * 2, 0) : null],
            ['kg', chosen.mass_kg != null ? fmt(chosen.mass_kg, 2) : null], [`${t('propMissionPeak')}, A`, chosen.I_peak_A != null ? fmt(chosen.I_peak_A, 0) : null]]} />
          {chosen.kind === 'estimate' && <Alert severity="warning" sx={{ py: 0, mt: 0.5, fontSize: 11 }}>{t('propMissionEstimateWarn')}</Alert>}
          {chosen.kind === 'none' && <Alert severity="error" sx={{ py: 0, mt: 0.5, fontSize: 11 }}>{chosen.reason}</Alert>}
        </Box>
      )}
    </Box>
  );
}

export function BestCard({ best, objective, seed }: { best: CandidateResult | null; objective: Objective; seed?: boolean }) {
  const { t } = useTranslation('simulation');
  if (!best) return <Alert severity="warning" data-testid="mission-nobest">{t('propMissionNoBest')}</Alert>;
  const v = best.objective_value;
  const shown = v === null ? '-' : objective === 'top_speed_ms' ? fmt(msToKmh(v), 1) : fmt(v, objective === 'hover_g_per_w' ? 2 : 1);
  const s = best.strip;
  return (
    <Panel title={seed ? t('propMissionBestSeed') : t('propMissionBest')}>
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'baseline', flexWrap: 'wrap' }} data-testid="mission-best">
        <Typography sx={{ fontSize: 30, fontWeight: 700, lineHeight: 1 }}>{shown}</Typography>
        <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>{t(`propMissionObjUnit_${objective}`)}</Typography>
        <Typography sx={{ fontSize: 13 }}>{best.name}</Typography>
      </Box>
      <KvGrid testId="mission-best-cells" items={[['D, in', fmt(best.D_in, 1)], ['P/D', best.pitch_ratio != null ? fmt(best.pitch_ratio, 2) : null], [t('propMissionTurns'), best.turns ?? null],
        [t('propMissionStrip'), stripText(s)], ['Kv', best.kv_rpm_per_V != null ? fmt(best.kv_rpm_per_V, 0) : null],
        [t('propMissionColTwC'), best.tw_cont != null ? fmt(best.tw_cont, 2) : null], [t('propMissionColTwP'), best.tw_peak != null ? fmt(best.tw_peak, 2) : null]]} />
      <CheckList checks={best.checks} />
    </Panel>
  );
}

const COLS: Array<{ key: SortKey; label: string; tip: string }> = [
  { key: 'name', label: 'propMissionColName', tip: 'propMissionColNameTip' }, { key: 'D_in', label: 'propMissionColD', tip: 'propMissionColDTip' },
  { key: 'pitch_ratio', label: 'propMissionColPD', tip: 'propMissionColPDTip' }, { key: 'turns', label: 'propMissionColTurns', tip: 'propMissionColTurnsTip' },
  { key: 'kv', label: 'propMissionColKv', tip: 'propMissionColKvTip' }, { key: 'range_km', label: 'propMissionColRange', tip: 'propMissionColRangeTip' },
  { key: 'endurance_min', label: 'propMissionColEndurance', tip: 'propMissionColEnduranceTip' }, { key: 'top_speed_ms', label: 'propMissionColTop', tip: 'propMissionColTopTip' },
  { key: 'hover_g_per_w', label: 'propMissionColHover', tip: 'propMissionColHoverTip' }, { key: 'tw_cont', label: 'propMissionColTwC', tip: 'propMissionColTwCTip' },
  { key: 'tw_peak', label: 'propMissionColTwP', tip: 'propMissionColTwPTip' },
];

export function ResultTable({ rows, sortKey, dir, onSort, selected, onSelect }: { rows: CandidateResult[]; sortKey: SortKey; dir: 'asc' | 'desc'; onSort: (k: SortKey) => void;
  selected: string | null; onSelect: (id: string) => void }) {
  const { t } = useTranslation('simulation');
  return (
    <Box sx={{ maxHeight: 360, overflow: 'auto' }}>
      <Table size="small" stickyHeader data-testid="mission-table">
        <TableHead>
          <TableRow>
            {COLS.map((c) => (
              <TableCell key={c.key} sx={CELL} sortDirection={sortKey === c.key ? dir : false}>
                <Tooltip title={t(c.tip)}>
                  <TableSortLabel active={sortKey === c.key} direction={sortKey === c.key ? dir : 'desc'} onClick={() => onSort(c.key)}>{t(c.label)}</TableSortLabel>
                </Tooltip>
              </TableCell>
            ))}
            <TableCell sx={CELL}>{t('propMissionColStatus')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => {
            const x = tableRow(r);
            return (
              <TableRow key={x.id} hover selected={selected === x.id} onClick={() => onSelect(x.id)} sx={{ cursor: 'pointer', opacity: x.passed ? 1 : 0.55 }} data-testid={x.passed ? 'mission-row' : 'mission-row-failed'}>
                <TableCell sx={CELL}>{x.name}{x.source === 'catalogue' ? ` (${t('propMissionCatalogueRef')})` : ''}</TableCell>
                <TableCell sx={CELL}>{fmt(x.D_in, 1)}</TableCell>
                <TableCell sx={CELL}>{x.pitch != null ? fmt(x.pitch, 2) : '-'}</TableCell>
                <TableCell sx={CELL}>{x.turns ?? '-'}{x.stripFlags.length ? ' ⚠' : ''}</TableCell>
                <TableCell sx={CELL}>{x.kv != null ? fmt(x.kv, 0) : '-'}</TableCell>
                <TableCell sx={CELL}>{x.range_km != null ? fmt(x.range_km, 1) : '-'}</TableCell>
                <TableCell sx={CELL}>{x.endurance_min != null ? fmt(x.endurance_min, 1) : '-'}</TableCell>
                <TableCell sx={CELL}>{x.top_speed_kmh != null ? fmt(x.top_speed_kmh, 0) : '-'}</TableCell>
                <TableCell sx={CELL}>{x.hover_g_per_w != null ? fmt(x.hover_g_per_w, 2) : '-'}</TableCell>
                <TableCell sx={CELL}>{x.tw_cont != null ? fmt(x.tw_cont, 2) : '-'}</TableCell>
                <TableCell sx={CELL}>{x.tw_peak != null ? fmt(x.tw_peak, 2) : '-'}</TableCell>
                <TableCell sx={{ ...CELL, color: x.passed ? 'var(--ok, #2e7d32)' : 'var(--danger, #c62828)' }}>
                  {x.passed ? t('propMissionPass') : x.reasons.map((k) => t(`propMissionRule_${k}`, { defaultValue: k })).join(', ')}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Box>
  );
}

export function ObjectiveChart({ rows, objective }: { rows: CandidateResult[]; objective: Objective }) {
  const { t } = useTranslation('simulation');
  const d = chartData(rows, objective);
  const conv = (v: number) => (objective === 'top_speed_ms' ? msToKmh(v) : v);
  if (d.lines.length === 0 && d.dots.length === 0) return null;
  return (
    <Panel title={t('propMissionChart')} tip={t('propMissionChartTip')}>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart margin={{ top: 6, right: 12, bottom: 16, left: 0 }}>
          <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
          <XAxis dataKey="D" type="number" domain={['dataMin - 0.5', 'dataMax + 0.5']} tick={TXT} allowDuplicatedCategory={false}
            label={{ value: t('propMissionColD'), position: 'insideBottom', offset: -6, fontSize: 10, fill: 'var(--text-3)' }} />
          <YAxis dataKey="value" type="number" tick={TXT} width={48} domain={['auto', 'auto']} tickFormatter={(v) => fmt(Number(v), 1)} />
          <RcTooltip contentStyle={{ background: 'var(--panel)', fontSize: 12 }} formatter={(v) => fmt(Number(v), 2)} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {d.lines.map((l, k) => (
            <Line key={l.key} data={l.points.map((p) => ({ D: p.D, value: conv(p.value) }))} dataKey="value" name={`P/D ${l.pitch}`} stroke={SERIES[k % SERIES.length]} isAnimationActive={false} dot />
          ))}
          {d.dots.length > 0 && <Scatter name={t('propMissionCatalogueRef')} data={d.dots.map((p) => ({ D: p.D, value: conv(p.value) }))} fill="var(--text-1)" isAnimationActive={false} />}
        </ComposedChart>
      </ResponsiveContainer>
    </Panel>
  );
}

function PointRow({ label, p }: { label: string; p: Point | null | undefined }) {
  const { t } = useTranslation('simulation');
  if (!p) return null;
  return (
    <TableRow>
      <TableCell sx={CELL}>{label}</TableCell>
      <TableCell sx={CELL}>{fmt(msToKmh(p.V_ms), 0)}</TableCell>
      <TableCell sx={CELL}>{fmt(p.rpm, 0)}</TableCell>
      <TableCell sx={CELL}>{fmt(p.torque_Nm, 3)}</TableCell>
      <TableCell sx={CELL}>{p.current_A != null ? fmt(p.current_A, 1) : '-'}</TableCell>
      <TableCell sx={CELL}>{p.pack_power_W != null ? fmt(p.pack_power_W, 0) : '-'}</TableCell>
      <TableCell sx={CELL}>{p.motor_eta != null ? fmt(100 * p.motor_eta, 1) : '-'}</TableCell>
      <TableCell sx={CELL}>{p.T_winding_C != null ? fmt(p.T_winding_C, 0) : '-'}</TableCell>
      <TableCell sx={CELL}>{fmt(p.tip_mach, 2)}</TableCell>
      <TableCell sx={CELL}>{p.verification ? t(`propMissionCheckVal_${p.verification}`, { defaultValue: p.verification }) : '-'}</TableCell>
    </TableRow>
  );
}

/** The selected row: hover and cruise points, the failures with their detail, the N-1 / N / N+1 comparison and the strip notes. */
export function RowDetails({ row, alternatives }: { row: CandidateResult | null; alternatives: Alternative[] | undefined }) {
  const { t } = useTranslation('simulation');
  if (!row) return null;
  const cols = ['propMissionPtV', 'propMissionPtRpm', 'propMissionPtTorque', 'propMissionPtCurrent', 'propMissionPtPower', 'propMissionPtEta', 'propMissionPtTemp', 'propMissionPtTip', 'propMissionPtVerif'];
  return (
    <Panel title={`${t('propMissionDetails')}: ${row.name}`}>
      <Box data-testid="mission-details">
        {row.failures.map((f, i) => (
          <Tooltip key={i} title={(() => { const m = failureMessage(f); return m ? t(m.key, { ...m.params, defaultValue: t('propMissionPointReason_other') }) : ''; })()}>
            <Alert severity="error" sx={{ py: 0, mb: 0.5, fontSize: 12 }}>{t(`propMissionRule_${f.rule}`, { defaultValue: f.rule })}</Alert>
          </Tooltip>
        ))}
        {(row.flags ?? []).includes('extrapolated_below_test') && (
          <Alert severity="warning" sx={{ py: 0, mb: 0.5, fontSize: 12 }} data-testid="mission-extrapolated">
            {t('propMissionFlag_extrapolated_below_test', { rpm: Math.round(row.tested_rpm?.[0] ?? 0) })}
          </Alert>
        )}
        {row.strip && (row.strip.low_stock || row.strip.check) && (
          <Alert severity="warning" sx={{ py: 0, mb: 0.5, fontSize: 11 }}>
            {[row.strip.low_stock ? t('propMissionStripLow', { kg: row.strip.stock_kg }) : '', row.strip.check ? t('propMissionStripCheck') : ''].filter(Boolean).join(' ')}
          </Alert>
        )}
        <CheckList checks={row.checks} />
        <Table size="small">
          <TableHead><TableRow><TableCell sx={CELL} />{cols.map((c) => <TableCell key={c} sx={CELL}>{t(c)}</TableCell>)}</TableRow></TableHead>
          <TableBody>
            <PointRow label={t('propMissionHover')} p={row.points?.hover} />
            <PointRow label={t('propMissionCruise')} p={row.points?.cruise} />
            <PointRow label={t('propMissionHoverEnd')} p={row.points?.hover_end} />
            <PointRow label={t('propMissionCruiseEnd')} p={row.points?.cruise_end} />
          </TableBody>
        </Table>
        {alternatives && alternatives.length > 0 && (
          <Box sx={{ mt: 1 }}>
            <Typography sx={{ fontSize: 12, fontWeight: 600 }}>{t('propMissionAlt')}</Typography>
            <Table size="small">
              <TableHead><TableRow>{['propMissionColTurns', 'propMissionColKv', 'propMissionColRange', 'propMissionColEndurance', 'propMissionColTop', 'propMissionColTwC', 'propMissionColTwP', 'propMissionColStatus']
                .map((c) => <TableCell key={c} sx={CELL}>{t(c)}</TableCell>)}</TableRow></TableHead>
              <TableBody>
                {[...alternatives, { turns: row.turns ?? 0, kv_rpm_per_V: row.kv_rpm_per_V ?? null, objectives: row.objectives, tw_cont: row.tw_cont, tw_peak: row.tw_peak, passed: row.passed }]
                  .sort((a, b) => a.turns - b.turns).map((a) => (
                    <TableRow key={a.turns} selected={a.turns === row.turns}>
                      <TableCell sx={CELL}>{a.turns}</TableCell>
                      <TableCell sx={CELL}>{a.kv_rpm_per_V != null ? fmt(a.kv_rpm_per_V, 0) : '-'}</TableCell>
                      <TableCell sx={CELL}>{a.objectives.range_km != null ? fmt(a.objectives.range_km, 1) : '-'}</TableCell>
                      <TableCell sx={CELL}>{a.objectives.endurance_min != null ? fmt(a.objectives.endurance_min, 1) : '-'}</TableCell>
                      <TableCell sx={CELL}>{a.objectives.top_speed_ms != null ? fmt(msToKmh(a.objectives.top_speed_ms), 0) : '-'}</TableCell>
                      <TableCell sx={CELL}>{a.tw_cont != null ? fmt(a.tw_cont, 2) : '-'}</TableCell>
                      <TableCell sx={CELL}>{a.tw_peak != null ? fmt(a.tw_peak, 2) : '-'}</TableCell>
                      <TableCell sx={CELL}>{a.passed ? t('propMissionPass') : t('propMissionFail')}</TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Box>
    </Panel>
  );
}

/** Stage 2: the optimised own propeller next to the stage-1 best, the winding it needs, its blade geometry (the Designer's own plots) and
 *  the button that saves it as a Designer draft. */
export function RefinedCard({ refined, objective, geo, motorRadiusMm, onSave, saveMsg, busy }: { refined: Refined; objective: Objective; geo: Geometry | null; motorRadiusMm: number;
  onSave: () => void; saveMsg: string | null; busy: boolean }) {
  const { t } = useTranslation('simulation');
  const fv = (v: number | null) => (v === null ? '-' : objective === 'top_speed_ms' ? fmt(msToKmh(v), 1) : fmt(v, objective === 'hover_g_per_w' ? 2 : 1));
  const r = refined.result;
  const s = r?.strip;
  return (
    <Panel title={t('propMissionRefined')} tip={t('propMissionRefinedTip')}>
      <Box data-testid="mission-refined">
        {!refined.best_vector ? <Alert severity="warning">{t('propMissionRefinedNone')}</Alert> : (
          <>
            <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'baseline' }}>
              <Typography sx={{ fontSize: 30, fontWeight: 700, lineHeight: 1 }}>{fv(refined.best_value)}</Typography>
              <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>{t(`propMissionObjUnit_${objective}`)}</Typography>
              <Chip size="small" color={(refined.gain_pct ?? 0) > 0 ? 'success' : 'default'} label={refined.gain_pct === null ? '-' : `${refined.gain_pct >= 0 ? '+' : ''}${fmt(refined.gain_pct, 1)} %`} data-testid="mission-gain" />
            </Box>
            {r && (
              <KvGrid testId="mission-refined-cells" items={[[t('propMissionTurns'), r.turns ?? null], [t('propMissionStrip'), stripText(s)],
                ['Kv', r.kv_rpm_per_V != null ? fmt(r.kv_rpm_per_V, 0) : null], [t('propMissionColTwC'), r.tw_cont != null ? fmt(r.tw_cont, 2) : null],
                [t('propMissionColTwP'), r.tw_peak != null ? fmt(r.tw_peak, 2) : null], [t('propMissionStage1'), fv(refined.seed_value)],
                [t('propMissionEvaluated'), refined.n_evaluations], [t('propMissionFeasible'), refined.n_feasible], [t('propMissionPop'), refined.settings.pop], [t('propMissionGen'), refined.settings.generations]]} />
            )}
            {r && <CheckList checks={r.checks} />}
            <Box sx={{ mt: 1 }}><PlanformChart geo={geo} motorRadiusMm={motorRadiusMm} /></Box>
            <Box sx={{ mt: 1 }}><ZoneTable geo={geo} /></Box>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 1 }}>
              <Button size="small" variant="contained" onClick={onSave} disabled={busy} data-testid="mission-open-designer">{t('propMissionSaveDraft')}</Button>
              {saveMsg && <Typography sx={{ fontSize: 12 }}>{saveMsg}</Typography>}
            </Box>
          </>
        )}
      </Box>
    </Panel>
  );
}
