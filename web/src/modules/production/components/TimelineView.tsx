/**
 * One-Picture Project Timeline & Gantt View.
 * Pure SVG Gantt chart:
 * - Tree on left (Configs -> Tasks / Milestones / Part lanes)
 * - Time axis with Month / Week / Day zoom
 * - Baseline ghost bars, planned bars, actual bars with % fill
 * - Finish-to-start dependency arrows and critical path highlight with slack days
 * - Milestone diamonds with automatic completion
 * - Part lanes with order_by tick, ordered -> arrival bar, need_by tick, receipt markers
 * - Today line and legend
 * - L12 + L20 cross-config toggle and late/at-risk filter
 * - Client-side SVG -> Canvas -> PNG download
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  FormControlLabel,
  MenuItem,
  Paper,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import LockResetIcon from '@mui/icons-material/LockReset';
import { useTranslation } from 'react-i18next';
import { productionApi } from '../api';
import {
  canEditSchedule,
  dateSpanToPx,
  dateToPx,
  daysBetween,
  formatReceiptTime,
  formatShortDate,
  getStatusColor,
} from '../helpers';
import type {
  Milestone,
  ProcurementLine,
  ProdCtx,
  Production,
  ScheduleData,
  SchedulePhase,
  TimelineData,
} from '../types';

interface TimelineViewProps {
  ctx: ProdCtx;
  prod: Production;
}

type ZoomLevel = 'month' | 'week' | 'day';

const ROW_HEIGHT = 28;
const TREE_WIDTH = 260;
const HEADER_HEIGHT = 44;

export const TimelineView: React.FC<TimelineViewProps> = ({ ctx, prod }) => {
  const { t, i18n } = useTranslation('production');
  const isZh = i18n.language.startsWith('zh');

  const [loading, setLoading] = useState(true);
  const [scheduleData, setScheduleData] = useState<ScheduleData | null>(null);
  const [procurementRows, setProcurementRows] = useState<ProcurementLine[]>([]);
  const [crossConfigData, setCrossConfigData] = useState<TimelineData | null>(null);

  const [showAllConfigs, setShowAllConfigs] = useState(false);
  const [onlyAtRisk, setOnlyAtRisk] = useState(false);
  const [zoom, setZoom] = useState<ZoomLevel>('week');
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);

  const pxPerDay = useMemo(() => {
    switch (zoom) {
      case 'month':
        return 8;
      case 'day':
        return 32;
      case 'week':
      default:
        return 16;
    }
  }, [zoom]);

  const loadData = async () => {
    setLoading(true);
    setErr(null);
    try {
      const [sched, proc, tl] = await Promise.all([
        productionApi.schedule(ctx.die, ctx.config),
        productionApi.procurement(ctx.die, ctx.config),
        productionApi.timeline(ctx.die),
      ]);
      setScheduleData(sched);
      setProcurementRows(proc.rows || []);
      setCrossConfigData(tl);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [ctx.die, ctx.config]);

  const handleFreezeBaseline = async () => {
    if (!canEditSchedule(ctx.role)) return;
    try {
      const res = await productionApi.freezeBaseline(ctx.die, ctx.config);
      if (scheduleData) {
        setScheduleData({ ...scheduleData, baseline: res.baseline });
      }
      setMsg(t('timeline.baselineFrozen', 'Schedule baseline frozen successfully.'));
      setTimeout(() => setMsg(null), 3500);
      void loadData();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const handleExportPng = () => {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    try {
      const serializer = new XMLSerializer();
      let source = serializer.serializeToString(svgEl);
      if (!source.match(/^<svg[^>]+xmlns="http:\/\/www\.w3\.org\/2000\/svg"/)) {
        source = source.replace(/^<svg/, '<svg xmlns="http://www.w3.org/2000/svg"');
      }
      const svgBlob = new Blob([source], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);
      const img = new Image();
      img.onload = () => {
        const bbox = svgEl.getBoundingClientRect();
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1200, bbox.width || 1200);
        canvas.height = Math.max(600, bbox.height || 600);
        const g2d = canvas.getContext('2d');
        if (g2d) {
          g2d.fillStyle = '#0f172a';
          g2d.fillRect(0, 0, canvas.width, canvas.height);
          g2d.drawImage(img, 0, 0);
          const a = document.createElement('a');
          a.download = `timeline_${ctx.die}_${showAllConfigs ? 'all' : ctx.config}.png`;
          a.href = canvas.toDataURL('image/png');
          a.click();
        }
        URL.revokeObjectURL(url);
      };
      img.src = url;
    } catch (e) {
      setErr(t('timeline.exportFailed', 'PNG export failed'));
    }
  };

  // Compile active production units to display
  const activeProductions: Array<{
    config: string;
    title: string;
    schedule: ScheduleData | null;
    procurement: ProcurementLine[];
  }> = useMemo(() => {
    if (showAllConfigs && crossConfigData && crossConfigData.productions.length > 0) {
      return crossConfigData.productions.map((p) => ({
        config: p.config,
        title: (isZh ? p.title?.zh : p.title?.en) || p.config,
        schedule: p.schedule,
        procurement: p.procurement?.rows || [],
      }));
    }
    return [
      {
        config: ctx.config,
        title: (isZh ? prod.title?.zh : prod.title?.en) || ctx.config,
        schedule: scheduleData,
        procurement: procurementRows,
      },
    ];
  }, [showAllConfigs, crossConfigData, ctx.config, prod.title, scheduleData, procurementRows, isZh]);

  // Compute date timeline range [minDate, maxDate]
  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), []);

  const { minDate, maxDate, totalDays } = useMemo(() => {
    const dates: string[] = [todayIso];

    activeProductions.forEach((ap) => {
      ap.schedule?.phases?.forEach((ph) => {
        if (ph.planned_start) dates.push(ph.planned_start);
        if (ph.planned_end) dates.push(ph.planned_end);
        if (ph.actual_start) dates.push(ph.actual_start);
        if (ph.actual_end) dates.push(ph.actual_end);
      });
      ap.schedule?.milestones?.forEach((m) => {
        if (m.date) dates.push(m.date);
      });
      ap.schedule?.baseline?.phases?.forEach((bp) => {
        if (bp.planned_start) dates.push(bp.planned_start);
        if (bp.planned_end) dates.push(bp.planned_end);
      });
      ap.procurement?.forEach((pr) => {
        if (pr.ordered_on) dates.push(pr.ordered_on);
        if (pr.expected_arrival) dates.push(pr.expected_arrival);
        if (pr.need_by) dates.push(pr.need_by);
        if (pr.order_by) dates.push(pr.order_by);
        pr.receipts?.forEach((r) => {
          if (r.date) dates.push(r.date);
        });
      });
    });

    const validDates = dates.filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d));
    let earliest = validDates.sort()[0] || todayIso;
    let latest = validDates.sort()[validDates.length - 1] || todayIso;

    // Pad 7 days before and 14 days after
    const dEarly = new Date(earliest);
    dEarly.setDate(dEarly.getDate() - 7);
    earliest = dEarly.toISOString().slice(0, 10);

    const dLate = new Date(latest);
    dLate.setDate(dLate.getDate() + 14);
    latest = dLate.toISOString().slice(0, 10);

    const span = Math.max(30, daysBetween(earliest, latest));
    return { minDate: earliest, maxDate: latest, totalDays: span };
  }, [activeProductions, todayIso]);

  // Build rows for the Gantt
  interface GanttRowItem {
    type: 'config_header' | 'phase' | 'milestones_group' | 'part';
    id: string;
    label: string;
    subLabel?: string;
    config: string;
    data?: any;
    rowIndex: number;
  }

  const { ganttRows, totalRowCount, phaseCoordMap } = useMemo(() => {
    const rowsOut: GanttRowItem[] = [];
    let currentRow = 0;
    const pCoordMap = new Map<string, { xStart: number; xEnd: number; y: number }>();

    activeProductions.forEach((ap) => {
      if (showAllConfigs) {
        rowsOut.push({
          type: 'config_header',
          id: `hdr_${ap.config}`,
          label: `${ctx.die} · ${ap.config} (${ap.title})`,
          config: ap.config,
          rowIndex: currentRow++,
        });
      }

      // Phases
      const phases = ap.schedule?.phases || [];
      phases.forEach((ph) => {
        const phLabel = (isZh ? ph.title?.zh : ph.title?.en) || ph.key || ph.id;
        const rIndex = currentRow++;
        rowsOut.push({
          type: 'phase',
          id: `${ap.config}_${ph.id}`,
          label: phLabel,
          subLabel: ph.is_critical ? (isZh ? '关键路径' : 'Critical') : `${ph.slack_days ?? 0}d slack`,
          config: ap.config,
          data: ph,
          rowIndex: rIndex,
        });

        const start = ph.planned_start || ph.actual_start;
        const end = ph.planned_end || ph.actual_end || start;
        if (start && end) {
          const x1 = dateToPx(start, minDate, pxPerDay);
          const x2 = x1 + dateSpanToPx(start, end, pxPerDay);
          const y = rIndex * ROW_HEIGHT + HEADER_HEIGHT + ROW_HEIGHT / 2;
          pCoordMap.set(`${ap.config}_${ph.id}`, { xStart: x1, xEnd: x2, y });
        }
      });

      // Milestones on a combined row
      const milestones = ap.schedule?.milestones || [];
      if (milestones.length > 0) {
        rowsOut.push({
          type: 'milestones_group',
          id: `${ap.config}_milestones`,
          label: isZh ? '关键里程碑' : 'Milestones',
          config: ap.config,
          data: milestones,
          rowIndex: currentRow++,
        });
      }

      // Procurement Part Lanes
      let procParts = ap.procurement || [];
      if (onlyAtRisk) {
        procParts = procParts.filter((p) => p.status === 'late' || p.status === 'at_risk' || p.is_late || p.is_at_risk);
      }
      procParts.forEach((part) => {
        const pName = (isZh ? part.part_name?.zh : part.part_name?.en) || part.part_id;
        rowsOut.push({
          type: 'part',
          id: `${ap.config}_part_${part.part_id}`,
          label: pName,
          subLabel: `${part.supplier || '—'} · PO ${part.po_number || '—'}`,
          config: ap.config,
          data: part,
          rowIndex: currentRow++,
        });
      });
    });

    return { ganttRows: rowsOut, totalRowCount: currentRow, phaseCoordMap: pCoordMap };
  }, [activeProductions, showAllConfigs, isZh, ctx.die, onlyAtRisk, minDate, pxPerDay]);

  const chartWidth = Math.max(900, totalDays * pxPerDay);
  const chartHeight = totalRowCount * ROW_HEIGHT + HEADER_HEIGHT + 30;

  // Generate calendar axis intervals (Weeks / Months)
  const timeAxisMarkers = useMemo(() => {
    const markers: Array<{ x: number; label: string; isMonthStart: boolean }> = [];
    const curr = new Date(minDate);
    const end = new Date(maxDate);

    while (curr <= end) {
      const iso = curr.toISOString().slice(0, 10);
      const isFirstDay = curr.getDate() === 1;
      const isMonday = curr.getDay() === 1;

      if (zoom === 'month' && isFirstDay) {
        const x = dateToPx(iso, minDate, pxPerDay);
        const label = curr.toLocaleDateString(isZh ? 'zh-CN' : 'en-US', { month: 'short', year: 'numeric' });
        markers.push({ x, label, isMonthStart: true });
      } else if (zoom === 'week' && (isMonday || isFirstDay)) {
        const x = dateToPx(iso, minDate, pxPerDay);
        const label = formatShortDate(iso);
        markers.push({ x, label, isMonthStart: isFirstDay });
      } else if (zoom === 'day') {
        const x = dateToPx(iso, minDate, pxPerDay);
        const label = formatShortDate(iso);
        markers.push({ x, label, isMonthStart: isFirstDay });
      }
      curr.setDate(curr.getDate() + 1);
    }
    return markers;
  }, [minDate, maxDate, pxPerDay, zoom, isZh]);

  const todayX = useMemo(() => dateToPx(todayIso, minDate, pxPerDay), [todayIso, minDate, pxPerDay]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {/* Action Controls Bar */}
      <Paper
        sx={{
          p: 1.5,
          bgcolor: 'var(--panel-2)',
          border: '1px solid var(--line-soft)',
          borderRadius: 1.5,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 1.5,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
          {/* L12 + L20 Toggle */}
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={showAllConfigs}
                onChange={(e) => setShowAllConfigs(e.target.checked)}
              />
            }
            label={
              <Typography sx={{ fontSize: 12.5, fontWeight: 600 }}>
                {t('timeline.showAllConfigs', 'Show L12 & L20 together')}
              </Typography>
            }
          />

          {/* Filter late / at risk */}
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={onlyAtRisk}
                onChange={(e) => setOnlyAtRisk(e.target.checked)}
              />
            }
            label={
              <Typography sx={{ fontSize: 12.5, color: onlyAtRisk ? '#f87171' : 'var(--text-2)' }}>
                {t('timeline.onlyAtRisk', 'Only Late / At Risk parts')}
              </Typography>
            }
          />

          {/* Zoom Level Select */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('timeline.zoom', 'Zoom')}:</Typography>
            <TextField
              select
              size="small"
              value={zoom}
              onChange={(e) => setZoom(e.target.value as ZoomLevel)}
              sx={{ width: 100, '& .MuiInputBase-input': { fontSize: 12, py: 0.3 } }}
            >
              <MenuItem value="month">{t('timeline.zoomMonth', 'Month')}</MenuItem>
              <MenuItem value="week">{t('timeline.zoomWeek', 'Week')}</MenuItem>
              <MenuItem value="day">{t('timeline.zoomDay', 'Day')}</MenuItem>
            </TextField>
          </Box>
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {canEditSchedule(ctx.role) && (
            <Button
              size="small"
              variant="outlined"
              startIcon={<LockResetIcon />}
              onClick={handleFreezeBaseline}
              sx={{ textTransform: 'none', fontSize: 12 }}
            >
              {t('timeline.freezeBaseline', 'Freeze Baseline')}
            </Button>
          )}

          <Button
            size="small"
            variant="contained"
            startIcon={<CameraAltIcon />}
            onClick={handleExportPng}
            sx={{ textTransform: 'none', fontSize: 12 }}
          >
            {t('timeline.downloadPng', 'Download PNG')}
          </Button>
        </Box>
      </Paper>

      {/* Messages / Alerts */}
      {err && <Alert severity="error" sx={{ fontSize: 12.5 }}>{err}</Alert>}
      {msg && <Alert severity="success" sx={{ fontSize: 12.5 }}>{msg}</Alert>}

      {/* Legend Bar */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, px: 1, py: 0.5, flexWrap: 'wrap', fontSize: 11.5, color: 'var(--text-3)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 14, height: 8, bgcolor: 'rgba(59, 130, 246, 0.4)', borderRadius: 0.5 }} />
          <span>{t('timeline.legendPlanned', 'Planned')}</span>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 14, height: 8, bgcolor: '#3b82f6', borderRadius: 0.5 }} />
          <span>{t('timeline.legendActual', 'Actual')}</span>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 14, height: 8, border: '1px dashed rgba(255,255,255,0.6)', borderRadius: 0.5 }} />
          <span>{t('timeline.legendBaseline', 'Baseline ghost')}</span>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 14, height: 8, bgcolor: '#f59e0b', borderRadius: 0.5 }} />
          <span>{t('timeline.legendCritical', 'Critical Path')}</span>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 8, height: 8, bgcolor: '#10b981', transform: 'rotate(45deg)' }} />
          <span>{t('timeline.legendMilestone', 'Milestone')}</span>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 14, height: 6, bgcolor: '#ef4444', borderRadius: 0.5 }} />
          <span>{t('timeline.legendLate', 'Late / At Risk')}</span>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 2, height: 12, bgcolor: '#f43f5e' }} />
          <span>{t('timeline.legendToday', 'Today')}</span>
        </Box>
      </Box>

      {/* Main SVG Gantt Container */}
      <Paper
        sx={{
          position: 'relative',
          bgcolor: '#090d16',
          border: '1px solid var(--line-soft)',
          borderRadius: 1.5,
          overflow: 'auto',
          maxHeight: 'calc(100vh - 250px)',
        }}
      >
        {loading ? (
          <Box sx={{ p: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1 }}>
            <CircularProgress size={20} />
            <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('loading')}</Typography>
          </Box>
        ) : (
          <Box sx={{ position: 'relative', width: TREE_WIDTH + chartWidth, height: chartHeight }}>
            <svg
              ref={svgRef}
              width={TREE_WIDTH + chartWidth}
              height={chartHeight}
              style={{ display: 'block' }}
            >
              <defs>
                <marker
                  id="arrow"
                  viewBox="0 0 10 10"
                  refX="6"
                  refY="5"
                  markerWidth="5"
                  markerHeight="5"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#64748b" />
                </marker>
                <marker
                  id="arrow-critical"
                  viewBox="0 0 10 10"
                  refX="6"
                  refY="5"
                  markerWidth="5"
                  markerHeight="5"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#f59e0b" />
                </marker>
              </defs>

              {/* Tree column background */}
              <rect x="0" y="0" width={TREE_WIDTH} height={chartHeight} fill="#0d1322" />
              <line x1={TREE_WIDTH} y1="0" x2={TREE_WIDTH} y2={chartHeight} stroke="#1e293b" strokeWidth="1" />

              {/* Header Time Axis Background */}
              <rect x="0" y="0" width={TREE_WIDTH + chartWidth} height={HEADER_HEIGHT} fill="#0f172a" />
              <line x1="0" y1={HEADER_HEIGHT} x2={TREE_WIDTH + chartWidth} y2={HEADER_HEIGHT} stroke="#334155" strokeWidth="1" />

              {/* Tree Header label */}
              <text x="16" y="27" fill="#94a3b8" fontSize="12" fontWeight="700">
                {isZh ? '项目结构 / 物料清单' : 'Project Breakdown / Parts'}
              </text>

              {/* Time axis vertical grid lines & text labels */}
              {timeAxisMarkers.map((m, idx) => (
                <g key={`marker_${idx}`}>
                  <line
                    x1={TREE_WIDTH + m.x}
                    y1={HEADER_HEIGHT}
                    x2={TREE_WIDTH + m.x}
                    y2={chartHeight}
                    stroke={m.isMonthStart ? '#334155' : '#1e293b'}
                    strokeDasharray={m.isMonthStart ? undefined : '2 3'}
                  />
                  <text
                    x={TREE_WIDTH + m.x + 4}
                    y={27}
                    fill={m.isMonthStart ? '#f1f5f9' : '#94a3b8'}
                    fontSize={m.isMonthStart ? '11.5' : '10.5'}
                    fontWeight={m.isMonthStart ? '700' : '400'}
                  >
                    {m.label}
                  </text>
                </g>
              ))}

              {/* Row Horizontal Striping */}
              {ganttRows.map((r, idx) => {
                const y = idx * ROW_HEIGHT + HEADER_HEIGHT;
                const isEven = idx % 2 === 0;
                return (
                  <g key={`row_bg_${idx}`}>
                    <rect
                      x="0"
                      y={y}
                      width={TREE_WIDTH + chartWidth}
                      height={ROW_HEIGHT}
                      fill={r.type === 'config_header' ? 'rgba(30, 41, 59, 0.7)' : isEven ? 'rgba(15, 23, 42, 0.4)' : 'transparent'}
                    />
                    <line x1="0" y1={y + ROW_HEIGHT} x2={TREE_WIDTH + chartWidth} y2={y + ROW_HEIGHT} stroke="#1e293b" strokeWidth="0.5" />

                    {/* Tree item label */}
                    <text
                      x={r.type === 'config_header' ? 12 : r.type === 'part' ? 24 : 16}
                      y={y + 18}
                      fill={r.type === 'config_header' ? '#38bdf8' : r.type === 'part' ? '#cbd5e1' : '#f1f5f9'}
                      fontSize={r.type === 'config_header' ? '12.5' : '11.5'}
                      fontWeight={r.type === 'config_header' ? '700' : r.data?.is_critical ? '700' : '500'}
                    >
                      {r.label.length > 22 ? `${r.label.slice(0, 22)}…` : r.label}
                    </text>

                    {/* Sub label on tree (slack days or PO info) */}
                    {r.subLabel && (
                      <text
                        x={TREE_WIDTH - 12}
                        y={y + 18}
                        textAnchor="end"
                        fill={r.data?.is_critical ? '#f59e0b' : '#64748b'}
                        fontSize="10"
                      >
                        {r.subLabel}
                      </text>
                    )}
                  </g>
                );
              })}

              {/* Dependency Arrows between tasks */}
              {ganttRows.map((r) => {
                if (r.type !== 'phase') return null;
                const ph = r.data as SchedulePhase;
                if (!ph.depends_on || ph.depends_on.length === 0) return null;

                const targetCoord = phaseCoordMap.get(`${r.config}_${ph.id}`);
                if (!targetCoord) return null;

                return ph.depends_on.map((depId) => {
                  const sourceCoord = phaseCoordMap.get(`${r.config}_${depId}`);
                  if (!sourceCoord) return null;

                  const x1 = TREE_WIDTH + sourceCoord.xEnd;
                  const y1 = sourceCoord.y;
                  const x2 = TREE_WIDTH + targetCoord.xStart;
                  const y2 = targetCoord.y;
                  const isCrit = Boolean(ph.is_critical);

                  const pathD = `M ${x1} ${y1} C ${x1 + 14} ${y1}, ${x2 - 14} ${y2}, ${x2} ${y2}`;
                  return (
                    <path
                      key={`dep_${r.config}_${depId}_${ph.id}`}
                      d={pathD}
                      fill="none"
                      stroke={isCrit ? '#f59e0b' : '#64748b'}
                      strokeWidth={isCrit ? 1.5 : 1}
                      strokeDasharray={isCrit ? undefined : '3 2'}
                      markerEnd={isCrit ? 'url(#arrow-critical)' : 'url(#arrow)'}
                    />
                  );
                });
              })}

              {/* Gantt Row Bars and Items */}
              {ganttRows.map((r) => {
                const y = r.rowIndex * ROW_HEIGHT + HEADER_HEIGHT;

                if (r.type === 'phase') {
                  const ph = r.data as SchedulePhase;
                  const isCrit = Boolean(ph.is_critical);
                  const isDone = ph.status === 'done';

                  // Baseline Ghost Bar
                  const baselineList = scheduleData?.baseline?.phases || [];
                  const basePh = baselineList.find((bp) => bp.id === ph.id || bp.key === ph.key);
                  const bStart = basePh?.planned_start;
                  const bEnd = basePh?.planned_end || bStart;

                  let baselineBar = null;
                  if (bStart && bEnd) {
                    const bx = dateToPx(bStart, minDate, pxPerDay);
                    const bw = dateSpanToPx(bStart, bEnd, pxPerDay);
                    baselineBar = (
                      <rect
                        x={TREE_WIDTH + bx}
                        y={y + 5}
                        width={Math.max(4, bw)}
                        height={ROW_HEIGHT - 10}
                        rx="3"
                        fill="none"
                        stroke="rgba(255, 255, 255, 0.4)"
                        strokeDasharray="2 2"
                        strokeWidth="1"
                      />
                    );
                  }

                  // Planned Bar
                  let plannedBar = null;
                  if (ph.planned_start && ph.planned_end) {
                    const px = dateToPx(ph.planned_start, minDate, pxPerDay);
                    const pw = dateSpanToPx(ph.planned_start, ph.planned_end, pxPerDay);
                    plannedBar = (
                      <rect
                        x={TREE_WIDTH + px}
                        y={y + 7}
                        width={Math.max(4, pw)}
                        height={ROW_HEIGHT - 14}
                        rx="3"
                        fill={isCrit ? 'rgba(245, 158, 11, 0.25)' : 'rgba(59, 130, 246, 0.2)'}
                        stroke={isCrit ? '#f59e0b' : 'rgba(59, 130, 246, 0.6)'}
                        strokeWidth="1"
                      />
                    );
                  }

                  // Actual Bar with percent fill
                  let actualBar = null;
                  const aStart = ph.actual_start;
                  const aEnd = ph.actual_end || (ph.status === 'in_progress' ? todayIso : aStart);
                  if (aStart && aEnd) {
                    const ax = dateToPx(aStart, minDate, pxPerDay);
                    const aw = dateSpanToPx(aStart, aEnd, pxPerDay);
                    const pct = Math.max(0, Math.min(100, ph.percent_complete || (isDone ? 100 : 30)));
                    const fillW = Math.max(2, (aw * pct) / 100);

                    actualBar = (
                      <g>
                        <rect
                          x={TREE_WIDTH + ax}
                          y={y + 8}
                          width={Math.max(4, aw)}
                          height={ROW_HEIGHT - 16}
                          rx="3"
                          fill="rgba(15, 23, 42, 0.8)"
                          stroke={isDone ? '#10b981' : isCrit ? '#f59e0b' : '#3b82f6'}
                          strokeWidth="1"
                        />
                        <rect
                          x={TREE_WIDTH + ax}
                          y={y + 8}
                          width={fillW}
                          height={ROW_HEIGHT - 16}
                          rx="3"
                          fill={isDone ? '#10b981' : isCrit ? '#f59e0b' : '#3b82f6'}
                        />
                        <text
                          x={TREE_WIDTH + ax + aw + 5}
                          y={y + 19}
                          fill={isDone ? '#10b981' : isCrit ? '#f59e0b' : '#94a3b8'}
                          fontSize="10"
                        >
                          {pct}%
                        </text>
                      </g>
                    );
                  }

                  return (
                    <g key={`phase_bar_${r.id}`}>
                      {baselineBar}
                      {plannedBar}
                      {actualBar}
                    </g>
                  );
                }

                if (r.type === 'milestones_group') {
                  const milestones = r.data as Milestone[];
                  return (
                    <g key={`ms_group_${r.id}`}>
                      {milestones.map((m) => {
                        if (!m.date) return null;
                        const mx = dateToPx(m.date, minDate, pxPerDay);
                        const my = y + ROW_HEIGHT / 2;
                        const mTitle = (isZh ? m.title?.zh : m.title?.en) || m.id;
                        return (
                          <g key={`ms_${m.id}`}>
                            {/* Diamond */}
                            <polygon
                              points={`${TREE_WIDTH + mx},${my - 6} ${TREE_WIDTH + mx + 6},${my} ${TREE_WIDTH + mx},${my + 6} ${TREE_WIDTH + mx - 6},${my}`}
                              fill={m.done ? '#10b981' : '#3b82f6'}
                              stroke="#ffffff"
                              strokeWidth="1"
                            />
                            <text
                              x={TREE_WIDTH + mx + 8}
                              y={my + 3}
                              fill={m.done ? '#10b981' : '#94a3b8'}
                              fontSize="9.5"
                            >
                              {mTitle}
                            </text>
                          </g>
                        );
                      })}
                    </g>
                  );
                }

                if (r.type === 'part') {
                  const part = r.data as ProcurementLine;
                  const cInfo = getStatusColor(part.status);

                  // 1. Order By tick (orange/amber inverted triangle)
                  let orderTick = null;
                  if (part.order_by) {
                    const obx = dateToPx(part.order_by, minDate, pxPerDay);
                    orderTick = (
                      <g>
                        <line
                          x1={TREE_WIDTH + obx}
                          y1={y + 4}
                          x2={TREE_WIDTH + obx}
                          y2={y + ROW_HEIGHT - 4}
                          stroke="#f97316"
                          strokeWidth="1.5"
                          strokeDasharray="2 1"
                        />
                        <polygon
                          points={`${TREE_WIDTH + obx - 3},${y + 4} ${TREE_WIDTH + obx + 3},${y + 4} ${TREE_WIDTH + obx},${y + 8}`}
                          fill="#f97316"
                        />
                      </g>
                    );
                  }

                  // 2. Bar: ordered_on -> expected_arrival
                  let leadBar = null;
                  if (part.ordered_on) {
                    const oDate = part.ordered_on;
                    const aDate = part.expected_arrival || part.need_by || oDate;
                    const ox = dateToPx(oDate, minDate, pxPerDay);
                    const ow = Math.max(6, dateSpanToPx(oDate, aDate, pxPerDay));

                    leadBar = (
                      <rect
                        x={TREE_WIDTH + ox}
                        y={y + 8}
                        width={ow}
                        height={ROW_HEIGHT - 16}
                        rx="2"
                        fill={cInfo.bg}
                        stroke={cInfo.border}
                        strokeWidth="1"
                      />
                    );
                  }

                  // 3. Need By tick (red/rose triangle)
                  let needTick = null;
                  if (part.need_by) {
                    const nbx = dateToPx(part.need_by, minDate, pxPerDay);
                    needTick = (
                      <g>
                        <line
                          x1={TREE_WIDTH + nbx}
                          y1={y + 4}
                          x2={TREE_WIDTH + nbx}
                          y2={y + ROW_HEIGHT - 4}
                          stroke="#f43f5e"
                          strokeWidth="1.5"
                        />
                        <polygon
                          points={`${TREE_WIDTH + nbx - 3},${y + ROW_HEIGHT - 4} ${TREE_WIDTH + nbx + 3},${y + ROW_HEIGHT - 4} ${TREE_WIDTH + nbx},${y + ROW_HEIGHT - 8}`}
                          fill="#f43f5e"
                        />
                      </g>
                    );
                  }

                  // 4. Receipt Markers
                  const receiptMarkers = (part.receipts || []).map((rcpt, rcptIdx) => {
                    const dStr = (rcpt.timestamp ? rcpt.timestamp.slice(0, 10) : rcpt.date) || '';
                    if (!dStr) return null;
                    const rx = dateToPx(dStr, minDate, pxPerDay);
                    const ry = y + ROW_HEIGHT / 2;
                    const isRejected = (rcpt.rejected || 0) > 0;
                    const timeLabel = rcpt.timestamp ? formatReceiptTime(rcpt.timestamp) : rcpt.date;
                    return (
                      <circle
                        key={`rcpt_${rcptIdx}`}
                        cx={TREE_WIDTH + rx}
                        cy={ry}
                        r="4"
                        fill={isRejected ? '#ef4444' : '#10b981'}
                        stroke="#fff"
                        strokeWidth="1"
                      >
                        <title>{`${timeLabel}: +${rcpt.accepted || rcpt.qty}`}</title>
                      </circle>
                    );
                  });

                  return (
                    <g key={`part_lane_${r.id}`}>
                      {orderTick}
                      {leadBar}
                      {needTick}
                      {receiptMarkers}
                    </g>
                  );
                }

                return null;
              })}

              {/* Vertical Today Line */}
              <g>
                <line
                  x1={TREE_WIDTH + todayX}
                  y1={HEADER_HEIGHT}
                  x2={TREE_WIDTH + todayX}
                  y2={chartHeight}
                  stroke="#f43f5e"
                  strokeWidth="2"
                />
                <rect
                  x={TREE_WIDTH + todayX - 20}
                  y={HEADER_HEIGHT - 16}
                  width="40"
                  height="14"
                  rx="3"
                  fill="#f43f5e"
                />
                <text
                  x={TREE_WIDTH + todayX}
                  y={HEADER_HEIGHT - 5}
                  textAnchor="middle"
                  fill="#ffffff"
                  fontSize="9.5"
                  fontWeight="700"
                >
                  {isZh ? '今天' : 'Today'}
                </text>
              </g>
            </svg>
          </Box>
        )}
      </Paper>
    </Box>
  );
};

export default TimelineView;
