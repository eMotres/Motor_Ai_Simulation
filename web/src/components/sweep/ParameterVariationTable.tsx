import React, { useEffect, useCallback, useState, useMemo } from 'react';
import {
  Box,
  Typography,
  TextField,
  IconButton,
  Tooltip,
  Divider,
  Chip,
  Button,
} from '@mui/material';
import ShowChartIcon    from '@mui/icons-material/ShowChart';
import CloseIcon        from '@mui/icons-material/Close';
import RefreshIcon      from '@mui/icons-material/Refresh';
import AddIcon          from '@mui/icons-material/Add';
import PlayArrowIcon    from '@mui/icons-material/PlayArrow';
import CircularProgress from '@mui/material/CircularProgress';
import { useMotorStore } from '../../stores/motorStore';
import type { VariationMode } from '../../types/motor';
import AddParameterDialog from '../parameters/AddParameterDialog';

const numFieldSx = {
  width: 64,
  '& .MuiInputBase-input': {
    px: '4px', py: '4px', fontSize: 12, textAlign: 'right' as const,
  },
};

/**
 * Free-typing numeric cell.
 *
 * The old field bound `value={number.toFixed(prec)}` straight to the store and
 * parsed every keystroke, so each re-render rewrote the text — you could not
 * type a decimal ("2.6" → snapped to "2.000"), could not clear the box, and the
 * cursor jumped.  Here the DISPLAYED text is a local draft string: you type
 * whatever you want, valid numbers are committed live to the local edit buffer
 * (so Recalculate/Enter still sees them), and the value is normalised + clamped
 * to [min, max] only on blur / Enter.  Focus selects the text so you can just
 * start typing your number, like a spreadsheet cell.
 */
interface ParamValueFieldProps {
  value: number;
  type: 'float' | 'int';
  step?: number;
  min?: number;
  max?: number;
  dirty: boolean;
  onCommit: (v: number) => void;   // live local commit (no API call)
  onEnter: () => void;             // Recalculate
}

const ParamValueField: React.FC<ParamValueFieldProps> = ({
  value, type, step, min, max, dirty, onCommit, onEnter,
}) => {
  const prec = type === 'int' ? 0
    : step && step < 0.1 ? 3
    : step && step < 1   ? 2
    : 1;
  const fmt = (v: number) =>
    Number.isFinite(v) ? (type === 'int' ? String(Math.round(v)) : v.toFixed(prec)) : '';

  const [draft, setDraft]     = useState<string>(fmt(value));
  const [focused, setFocused] = useState(false);

  // Follow the store value ONLY while not editing — never clobber typing.
  useEffect(() => {
    if (!focused) setDraft(fmt(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, focused]);

  const parse = (s: string) => {
    const raw = s.trim().replace(',', '.');                 // accept comma decimal
    return type === 'int' ? parseInt(raw, 10) : parseFloat(raw);
  };

  return (
    <TextField
      size="small"
      value={draft}
      onChange={(e) => {
        const s = e.target.value;
        setDraft(s);                                        // show exactly what is typed
        const n = parse(s);
        if (!Number.isNaN(n)) onCommit(n);                  // live-commit valid numbers
      }}
      onFocus={(e) => {
        setFocused(true);
        const el = e.target;
        requestAnimationFrame(() => { try { el.select(); } catch { /* noop */ } });
      }}
      onBlur={() => {
        setFocused(false);
        let n = parse(draft);
        if (Number.isNaN(n)) { setDraft(fmt(value)); return; }  // blank → revert
        if (typeof min === 'number' && n < min) n = min;        // clamp
        if (typeof max === 'number' && n > max) n = max;
        if (type === 'int') n = Math.round(n);
        onCommit(n);
        setDraft(fmt(n));
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { (e.target as HTMLInputElement).blur(); onEnter(); }
        else if (e.key === 'Escape') { setDraft(fmt(value)); (e.target as HTMLInputElement).blur(); }
      }}
      inputProps={{ inputMode: 'decimal', step, min, max }}
      sx={{
        ...numFieldSx,
        '& .MuiOutlinedInput-root': dirty ? {
          '& fieldset':        { borderColor: '#f59e0b55' },
          '&:hover fieldset':  { borderColor: '#f59e0b' },
        } : {},
      }}
    />
  );
};

const ParameterVariationTable: React.FC = () => {
  const [dialogOpen, setDialogOpen] = useState(false);

  const {
    parameterSchema,
    parameterGroups,
    geometry,
    sweepConfig,
    updateVariation,
    updateGeometryViaApi,
    updateGeometry,
    connectedToApi,
    initVariationsFromSchema,
    fetchSchemaFromApi,
    isGeometryUpdating,
  } = useMotorStore();

  // ── local edits (not yet sent to API) ───────────────────────────────────
  // key → locally edited value
  const [localValues, setLocalValues] = useState<Record<string, number>>({});

  // Initialise / sync local values when geometry loads from server
  // Only reset keys that are NOT dirty (i.e. not pending recalc)
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());

  useEffect(() => {
    setLocalValues(prev => {
      const next: Record<string, number> = {};
      Object.entries(geometry).forEach(([k, v]) => {
        // Keep local edit if key is dirty, otherwise follow server
        next[k] = dirtyKeys.has(k) && prev[k] !== undefined ? prev[k] : (v as number);
      });
      return next;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry]);

  useEffect(() => {
    if (parameterSchema.length > 0) initVariationsFromSchema();
  }, [parameterSchema.length]);

  const dirtyCount = dirtyKeys.size;
  const isDirty    = dirtyCount > 0;

  // ── value committed from a field → local edit buffer only (no API) ──────
  const commitValue = useCallback((name: string, v: number) => {
    setLocalValues(prev => ({ ...prev, [name]: v }));
    setDirtyKeys(prev => {
      // Mark dirty only if value actually changed from the server value
      const serverVal = geometry[name] as number;
      const next = new Set(prev);
      if (v !== serverVal) next.add(name);
      else next.delete(name);
      return next;
    });
  }, [geometry]);

  // ── Recalculate → send all dirty changes to API ───────────────────────
  const handleRecalculate = useCallback(async () => {
    if (!isDirty) return;

    const pending: Record<string, number> = {};
    dirtyKeys.forEach(k => { pending[k] = localValues[k]; });

    if (connectedToApi) {
      await updateGeometryViaApi(pending);
    } else {
      updateGeometry(pending);
    }
    setDirtyKeys(new Set());
  }, [isDirty, dirtyKeys, localValues, connectedToApi, updateGeometryViaApi, updateGeometry]);

  // ── sweep toggle ──────────────────────────────────────────────────────
  const toggleSweep = useCallback((name: string) => {
    const variation = sweepConfig.variations[name];
    const isActive  = variation?.mode !== 'fixed' && variation?.mode !== undefined;
    if (isActive) {
      updateVariation(name, { mode: 'fixed' });
    } else {
      // Only whitelisted (optimizable) parameters may be ADDED to the sweep.
      const sch = parameterSchema.find(p => p.name === name);
      if (sch && sch.optimizable === false) return;
      // On first selection, default Min AND Max to the parameter's CURRENT
      // value — the user then widens the range.  (Schema min/max were far too
      // wide, e.g. Slot Height 1..100.)
      const cur    = Number(localValues[name] ?? geometry[name] ?? 0);
      const schema = parameterSchema.find(p => p.name === name);
      updateVariation(name, {
        mode: 'sweep',
        min:  cur,
        max:  cur,
        step: variation?.step ?? schema?.step ?? Math.max(0.01, Math.abs(cur) * 0.1),
      });
    }
  }, [sweepConfig, localValues, geometry, parameterSchema, updateVariation]);

  const groups = useMemo(() =>
    parameterGroups
      .map(g => ({
        ...g,
        params: parameterSchema.filter(p => p.group === g.id && p.type !== 'string' && !p.hidden),
      }))
      .filter(g => g.params.length > 0),
    [parameterGroups, parameterSchema]
  );

  const isRunning = isGeometryUpdating;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

      {/* ── Header ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1, px: 0.5, gap: 1 }}>
        <Chip
          label={`${parameterSchema.length} parameters`}
          size="small"
          variant="outlined"
          sx={{ fontSize: 10, height: 20 }}
        />
        <Box sx={{ flex: 1 }} />
        <Button
          size="small"
          startIcon={<AddIcon sx={{ fontSize: 14 }} />}
          onClick={() => setDialogOpen(true)}
          sx={{ fontSize: 11, py: 0.25, px: 1, minHeight: 24 }}
        >
          Add
        </Button>
        <Tooltip title="Reload schema from API">
          <IconButton
            size="small"
            onClick={fetchSchemaFromApi}
            sx={{ p: 0.4, opacity: 0.6, '&:hover': { opacity: 1 } }}
          >
            <RefreshIcon sx={{ fontSize: 15 }} />
          </IconButton>
        </Tooltip>
      </Box>

      {/* ── RECALCULATE button ── */}
      <Box sx={{ px: 0.5, mb: 1.5 }}>
        <Button
          variant={isDirty ? 'contained' : 'outlined'}
          color={isDirty ? 'primary' : 'inherit'}
          size="small"
          fullWidth
          startIcon={
            isRunning
              ? <CircularProgress size={13} color="inherit" />
              : <PlayArrowIcon sx={{ fontSize: 16 }} />
          }
          onClick={handleRecalculate}
          disabled={!isDirty || isRunning}
          sx={{
            fontWeight: 700,
            fontSize: 11,
            letterSpacing: '0.06em',
            py: 0.6,
            transition: 'all 0.2s',
            ...(isDirty && !isRunning && {
              boxShadow: '0 0 8px rgba(59,130,246,0.4)',
            }),
          }}
        >
          {isRunning
            ? 'Recalculating…'
            : isDirty
              ? `Recalculate  (${dirtyCount} changed)`
              : 'Recalculate'}
        </Button>
      </Box>

      <AddParameterDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />

      {/* ── Parameter rows ── */}
      {groups.map((group, gi) => (
        <Box key={group.id}>
          {gi > 0 && <Divider sx={{ my: 1 }} />}
          <Typography
            variant="caption"
            color="primary"
            sx={{
              fontWeight: 700, display: 'block', px: 0.5, py: 0.5,
              textTransform: 'uppercase', fontSize: 10, letterSpacing: 0.5,
            }}
          >
            {group.label}
          </Typography>

          {group.params.map(param => {
            const variation = sweepConfig.variations[param.name];
            const isActive  = variation?.mode !== 'fixed' && variation?.mode !== undefined;
            const dirty     = dirtyKeys.has(param.name);
            const localVal  = localValues[param.name];
            const displayVal = localVal !== undefined
              ? localVal
              : (geometry[param.name] ?? 0);
            // Whitelist gate: only optimizable params may be ADDED to the sweep.
            // An already-active param always keeps its remove (✕) control.
            const canSweep = isActive || param.optimizable !== false;

            return (
              <Box
                key={param.name}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  px: 0.5,
                  py: 0.4,
                  borderRadius: 1,
                  borderLeft: dirty
                    ? '2px solid #f59e0b'
                    : isActive
                      ? '2px solid #3b82f6'
                      : '2px solid transparent',
                  bgcolor: dirty
                    ? 'rgba(245,158,11,0.04)'
                    : isActive
                      ? 'rgba(59,130,246,0.05)'
                      : 'transparent',
                  '&:hover': {
                    bgcolor: dirty
                      ? 'rgba(245,158,11,0.08)'
                      : isActive
                        ? 'rgba(59,130,246,0.08)'
                        : 'rgba(255,255,255,0.03)',
                  },
                }}
              >
                {/* Parameter name */}
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap sx={{ lineHeight: 1.4 }}>
                    {param.label}
                    {param.unit && (
                      <Typography
                        component="span" variant="caption"
                        color="text.disabled" sx={{ ml: 0.5 }}
                      >
                        ({param.unit})
                      </Typography>
                    )}
                  </Typography>
                </Box>

                {/* Editable value — free-typing local draft */}
                <ParamValueField
                  value={typeof displayVal === 'number' ? displayVal : (Number(displayVal) || 0)}
                  type={param.type as 'float' | 'int'}
                  step={param.step}
                  min={param.min}
                  max={param.max}
                  dirty={dirty}
                  onCommit={(v) => commitValue(param.name, v)}
                  onEnter={handleRecalculate}
                />

                {/* Add / remove from sweep — only for whitelisted params */}
                {canSweep ? (
                  <Tooltip title={isActive ? 'Remove from sweep' : 'Add to sweep'}>
                    <IconButton
                      size="small"
                      color={isActive ? 'primary' : 'default'}
                      onClick={() => toggleSweep(param.name)}
                      sx={{ p: 0.4, opacity: isActive ? 1 : 0.35, '&:hover': { opacity: 1 } }}
                    >
                      {isActive
                        ? <CloseIcon     sx={{ fontSize: 15 }} />
                        : <ShowChartIcon sx={{ fontSize: 15 }} />}
                    </IconButton>
                  </Tooltip>
                ) : (
                  // Not optimizable → no sweep toggle, keep row alignment
                  <Box sx={{ width: 30, height: 30, flexShrink: 0 }} />
                )}
              </Box>
            );
          })}
        </Box>
      ))}
    </Box>
  );
};

export default ParameterVariationTable;
