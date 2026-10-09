import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, ButtonBase, Chip, CircularProgress, Link, Paper, Table, TableBody,
  TableCell, TableRow, TextField, Typography, FormControl, Select, MenuItem,
} from '@mui/material';
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Legend } from 'recharts';
import { useTranslation } from 'react-i18next';
import { fetchPropellers } from '../../lib/propellerApi';
import type { PropSummary } from '../../lib/configuratorPropeller';
import { nsT } from '../../i18n/nsT';
import { buildRpmGroups, type RpmMetric } from './propellerRpmChart';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');
const tx = nsT('motors');
const headSx = { fontSize: 10, color: 'var(--text-3)', width: 145, py: 0.65, px: 1 } as const;
const valueSx = { fontSize: 11, color: 'var(--text-1)', py: 0.65, px: 1, overflowWrap: 'anywhere' } as const;

interface PropellerDetail extends PropSummary {
  series?: string;
  status?: string;
  diameter_mm?: number | null;
  disc_diameter_mm?: number | null;
  pitch_in?: number | null;
  pitch_mm?: number | null;
  material?: string | null;
  mass_g?: number | null;
  mass_single_blade_g?: number | null;
  product_url?: string | null;
  published_limits?: Record<string, unknown>;
  geometry?: Record<string, unknown> | null;
  hub?: Record<string, unknown> | null;
  source_urls?: string[] | null;
  accessed?: string | null;
  performance?: {
    data_quality?: string;
    static_hover_only?: boolean;
    test_density_kg_m3?: number;
    power_estimate?: unknown;
    tables?: Array<Record<string, unknown>>;
  };
  fit?: {
    ct?: Record<string, unknown> | null;
    cp?: Record<string, unknown> | null;
    points?: number;
  };
  notes?: unknown[];
}

function display(value: unknown): string {
  if (value == null || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map((item) => display(item)).join(' – ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function rowsOf(record: Record<string, unknown> | null | undefined, prefix = ''): Array<[string, unknown]> {
  if (!record) return [];
  return Object.entries(record).flatMap(([key, value]) => {
    const label = prefix ? `${prefix} · ${key}` : key;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return rowsOf(value as Record<string, unknown>, label);
    }
    return [[label, value] as [string, unknown]];
  });
}

const PropellersCatalogPanel: React.FC = () => {
  useTranslation('motors');
  const [props, setProps] = useState<PropSummary[] | null>(null);
  const [listError, setListError] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState<PropellerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [metric, setMetric] = useState<RpmMetric>('thrust');

  useEffect(() => {
    let live = true;
    void fetchPropellers().then((list) => {
      if (!live) return;
      setProps(list);
      setListError(!list);
      if (list?.length) setSelectedId((current) => current && list.some((item) => item.id === current)
        ? current : list[0].id);
    });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    let live = true;
    setDetailLoading(true);
    setDetailError(false);
    setDetail(null);
    void fetch(`${API}/api/propellers/${encodeURIComponent(selectedId)}`, { cache: 'no-store' })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<PropellerDetail>;
      })
      .then((value) => {
        if (live && value?.id === selectedId) setDetail(value);
        else if (live) setDetailError(true);
      })
      .catch(() => { if (live) setDetailError(true); })
      .finally(() => { if (live) setDetailLoading(false); });
    return () => { live = false; };
  }, [selectedId]);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (props ?? []).filter((item) => !needle ||
      `${item.id} ${item.vendor} ${item.model}`.toLowerCase().includes(needle));
  }, [props, query]);

  return (
    <Box sx={{ height: '100%', minHeight: 0, display: 'flex', gap: 1.5, p: 1.5,
      flexDirection: { xs: 'column', md: 'row' } }}>
      <Paper sx={{ width: { xs: '100%', md: 330 }, maxHeight: { xs: 300, md: 'none' },
        minHeight: 160, flexShrink: 0, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
        borderRadius: 1.5, p: 1, display: 'flex', flexDirection: 'column' }}>
        <TextField size="small" value={query} onChange={(event) => setQuery(event.target.value)}
          placeholder={tx('propellerCatalogSearch')} inputProps={{ 'aria-label': tx('propellerCatalogSearch') }}
          sx={{ mb: 1, '& .MuiInputBase-input': { fontSize: 12, py: 0.75 } }} />
        <Box sx={{ overflowY: 'auto', minHeight: 0 }}>
          {!props && !listError && <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}><CircularProgress size={18} /></Box>}
          {listError && <Typography sx={{ p: 1, fontSize: 12, color: '#f87171' }}>{tx('propellerCatalogUnavailable')}</Typography>}
          {shown.map((item) => (
            <ButtonBase key={item.id} onClick={() => setSelectedId(item.id)}
              aria-pressed={selectedId === item.id}
              sx={{ width: '100%', display: 'block', textAlign: 'left', p: 1, mb: 0.5,
                borderRadius: 1, border: '1px solid',
                borderColor: selectedId === item.id ? 'var(--accent)' : 'var(--line-soft)',
                bgcolor: selectedId === item.id ? 'var(--panel)' : 'transparent' }}>
              <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-1)' }}>
                {item.vendor} · {item.model}
              </Typography>
              <Typography sx={{ fontSize: 10, color: 'var(--text-3)', mt: 0.25 }}>
                {item.id} · {item.diameter_in ?? '—'} in · {item.blades ?? '—'} blades
              </Typography>
              <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>
                {item.rpm_range_tested ? `${item.rpm_range_tested[0]}–${item.rpm_range_tested[1]} rpm ${tx('propellerCatalogTested')}` : tx('propellerCatalogNoTestRange')}
              </Typography>
            </ButtonBase>
          ))}
          {props && shown.length === 0 && <Typography sx={{ p: 1, fontSize: 12, color: 'var(--text-3)' }}>{tx('propellerCatalogNoMatches')}</Typography>}
        </Box>
      </Paper>

      <Paper sx={{ flex: 1, minWidth: 0, overflow: 'auto', bgcolor: 'var(--panel-2)',
        border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5 }}>
        {detailLoading && <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={20} /></Box>}
        {detailError && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{tx('propellerCatalogDetailUnavailable')}</Typography>}
        {detail && !detailLoading && (
          <>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1 }}>
              <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-0)' }}>
                {detail.vendor} · {detail.model}
              </Typography>
              <Chip size="small" label={detail.selectable ? tx('propellerCatalogTestData') : tx('propellerCatalogGeometryOnly')}
                sx={{ height: 20, fontSize: 10 }} />
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>Performance vs RPM</Typography>
              <FormControl size="small" sx={{ minWidth: 130 }}>
                <Select value={metric} onChange={(e) => setMetric(e.target.value as RpmMetric)} aria-label="Chart metric" sx={{ fontSize: 11, height: 30 }}>
                  <MenuItem value="thrust">Thrust (N)</MenuItem>
                  <MenuItem value="power">Power (W)</MenuItem>
                  <MenuItem value="torque">Shaft torque (N·m)</MenuItem>
                </Select>
              </FormControl>
            </Box>
            {(() => { const groups = buildRpmGroups(detail, metric); const unit = metric === 'thrust' ? ' N' : metric === 'torque' ? ' N·m' : ' W'; return groups.length ? <Box sx={{ mb: 1 }}>
              <Box sx={{ height: 230 }}><ResponsiveContainer width="100%" height="100%"><LineChart margin={{ top: 5, right: 12, bottom: 18, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" /><XAxis type="number" dataKey="rpm" name="RPM" unit=" rpm" domain={['dataMin', 'dataMax']} />
                <YAxis dataKey="value" name={metric} unit={unit} /><Tooltip labelFormatter={(v) => `${v} rpm`} formatter={(value, name) => [typeof value === 'number' ? `${value.toFixed(2)}${unit}` : `—${unit}`, name]} /><Legend />{groups.map((g, i) => <Line key={g.id} data={g.points} name={g.label} type="monotone" dataKey="value" dot={{ r: 2 }} stroke={['var(--accent)', '#60a5fa', '#f59e0b', '#34d399'][i % 4]} strokeDasharray={g.seriesKind === 'electrical_input' ? '5 3' : undefined} />)}
              </LineChart></ResponsiveContainer></Box>
              <Box sx={{ maxHeight: 84, overflowY: 'auto' }}>
                {groups.map((g) => <Typography key={g.id} sx={{ fontSize: 10, color: 'var(--text-3)', overflowWrap: 'anywhere' }}>
                  {g.label}: {g.points.length} points · {g.provenance}{g.sourceUrl ? <> · <Link href={g.sourceUrl} target="_blank" rel="noreferrer" sx={{ fontSize: 10 }}>source</Link></> : null}
                </Typography>)}
              </Box>
            </Box> : <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 1 }}>No finite {metric} data available for the tested RPM range.</Typography>; })()}
            <Table size="small" aria-label="Propeller properties">
              <TableBody>
                {[
                  [tx('propellerCatalogId'), detail.id], [tx('propellerCatalogSeries'), detail.series], [tx('propellerCatalogStatus'), detail.status],
                  [tx('propellerCatalogDiameter'), detail.diameter_mm != null ? `${detail.diameter_mm} mm (${detail.diameter_in ?? '—'} in)` : detail.diameter_in != null ? `${detail.diameter_in} in` : null],
                  [tx('propellerCatalogDiscDiameter'), detail.disc_diameter_mm != null ? `${detail.disc_diameter_mm} mm` : null],
                  [tx('propellerCatalogPitch'), detail.pitch_mm != null ? `${detail.pitch_mm} mm (${detail.pitch_in ?? '—'} in)` : detail.pitch_in != null ? `${detail.pitch_in} in` : null],
                  [tx('propellerCatalogBlades'), detail.blades], [tx('propellerCatalogMaterial'), detail.material], [tx('propellerCatalogMass'), detail.mass_g != null ? `${detail.mass_g} g` : null],
                  [tx('propellerCatalogPowerData'), detail.power_data], [tx('propellerCatalogDataQuality'), detail.data_quality],
                  [tx('propellerCatalogRpmRange'), detail.rpm_range_tested], [tx('propellerCatalogTestDensity'), detail.performance?.test_density_kg_m3 != null ? `${detail.performance.test_density_kg_m3} kg/m³` : null],
                  [tx('propellerCatalogTestBasis'), detail.performance?.static_hover_only ? tx('propellerCatalogStaticTests') : null],
                  [tx('propellerCatalogFitPoints'), Array.isArray(detail.fit?.points) ? `${detail.fit.points.length} points` : detail.fit?.points != null ? `${detail.fit.points} points` : null], [tx('propellerCatalogAccessed'), detail.accessed],
                ].map(([label, value]) => (
                  <TableRow key={String(label)}>
                    <TableCell sx={headSx}>{label}</TableCell>
                    <TableCell sx={valueSx}>{display(value)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {!!detail.geometry && <PropertyBlock title={tx('propellerCatalogGeometry')} values={detail.geometry} />}
            {!!detail.hub && <PropertyBlock title={tx('propellerCatalogHub')} values={detail.hub} />}
            {Array.isArray(detail.source_urls) && detail.source_urls.length > 0 && (
              <Box sx={{ mt: 1 }}>
                <Typography sx={{ fontSize: 10, color: 'var(--text-3)', mb: 0.35 }}>{tx('propellerCatalogSources')}</Typography>
                {detail.source_urls.map((url) => <Box key={url}><Link href={url} target="_blank" rel="noreferrer" sx={{ fontSize: 11, overflowWrap: 'anywhere' }}>{url}</Link></Box>)}
              </Box>
            )}
          </>
        )}
        {!selectedId && !listError && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tx('propellerCatalogSelect')}</Typography>}
      </Paper>
    </Box>
  );
};

const PropertyBlock: React.FC<{ title: string; values: Record<string, unknown> }> = ({ title, values }) => {
  const rows = rowsOf(values);
  if (!rows.length) return null;
  return (
    <Box sx={{ mt: 1 }}>
      <Typography sx={{ fontSize: 10, color: 'var(--text-3)', mb: 0.35 }}>{title}</Typography>
      <Table size="small" aria-label={`${title} properties`}><TableBody>
        {rows.map(([key, value]) => <TableRow key={key}>
          <TableCell sx={headSx}>{key.replaceAll('_', ' ')}</TableCell>
          <TableCell sx={valueSx}>{display(value)}</TableCell>
        </TableRow>)}
      </TableBody></Table>
    </Box>
  );
};

export default PropellersCatalogPanel;
