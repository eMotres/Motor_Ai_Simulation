import React, { useEffect, useCallback, useState, useMemo, useRef } from 'react';
import {
  Box,
  Typography,
  TextField,
  IconButton,
  Tooltip,
  Divider,
  Chip,
  Button,
  Snackbar,
} from '@mui/material';
import ShowChartIcon    from '@mui/icons-material/ShowChart';
import CloseIcon        from '@mui/icons-material/Close';
import RefreshIcon      from '@mui/icons-material/Refresh';
import AddIcon          from '@mui/icons-material/Add';
import PlayArrowIcon    from '@mui/icons-material/PlayArrow';
import HelpOutlineIcon  from '@mui/icons-material/HelpOutline';
import CircularProgress from '@mui/material/CircularProgress';
import { useMotorStore } from '../../stores/motorStore';
import { pageVisible } from '../../lib/pageVisible';
import AddParameterDialog from '../parameters/AddParameterDialog';
import FreeCADRoundTrip from '../common/FreeCADRoundTrip';
import Fusion360RoundTrip from '../common/Fusion360RoundTrip';
import SectionLabel from '../common/SectionLabel';
import HelpTip from '../common/HelpTip';
import { ConfirmDialog, type ConfirmState } from '../common/PromptDialogs';
import { useDieContext } from '../common/useDieContext';
import { dieKeyLabel } from '../../lib/releasedContext';
import { openGeometryHelpWindow } from '../../lib/geometryHelpWindow';
import { useWireStock } from '../materials/useWireStock';
import { stockHint } from '../../lib/wireStock';

const numFieldSx = {
  width: '100%',   // fill the fixed-width value column → values line up vertically
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
  disabled?: boolean;              // locked by the active die/configuration
  onCommit: (v: number) => void;   // live local commit (no API call)
  onEnter: () => void;             // Recalculate
}

const ParamValueField: React.FC<ParamValueFieldProps> = ({
  value, type, step, min, max, dirty, disabled, onCommit, onEnter,
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
      disabled={!!disabled}
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

const FAMILY_API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

const ParameterVariationTable: React.FC = () => {
  const [dialogOpen, setDialogOpen] = useState(false);

  // Locks of the ACTIVE die/configuration: with the die locked only the
  // free keys (stack length, wire height, turns) stay editable; with the
  // configuration locked too the whole geometry is read-only.  The backend
  // enforces this on PUT (423) — greying the fields makes it VISIBLE.
  const [famLocks, setFamLocks] = useState<{
    dieLocked: boolean; cfgLocked: boolean; freeKeys: Set<string>;
    die?: string; config?: string;
  } | null>(null);
  useEffect(() => {
    const load = () => fetch(`${FAMILY_API}/api/family/context`)
      .then(r => r.json())
      .then(c => setFamLocks(c?.active ? {
        dieLocked: c.die_locked === true, cfgLocked: c.config_locked === true,
        freeKeys: new Set<string>(c.free_keys || []),
        die: c.die, config: c.config,
      } : null))
      .catch(() => setFamLocks(null));
    load();
    window.addEventListener('family-changed', load);
    window.addEventListener('sim-design-applied', load);
    // Locks can be toggled from ANOTHER tab (the Motors catalog) or another
    // browser window entirely — a light poll keeps the greying honest even
    // when no event reaches this window.
    const id = setInterval(() => { if (pageVisible()) load(); }, 10_000);
    return () => {
      window.removeEventListener('family-changed', load);
      window.removeEventListener('sim-design-applied', load);
      clearInterval(id);
    };
  }, []);
  const paramLocked = (name: string): string | null => {
    if (!famLocks) return null;
    const free = famLocks.freeKeys.has(name);
    if (free && famLocks.cfgLocked) return `configuration '${famLocks.config}'`;
    if (!free && famLocks.dieLocked) return `die '${famLocks.die}'`;
    return null;
  };
  // DIE-DEFINING keys (stator Ø, segments, slots/poles per segment): on an
  // UNLOCKED active die they are editable, but changing one makes the live
  // machine a DIFFERENT lamination — the backend then releases the die
  // context.  Warn BEFORE the change (flag + confirm), not after: the
  // after-the-fact release is what left the owner's optimised machine
  // unsaveable on 2026-09-20 12:47 (poles/segment 7 → 8 typed here).
  const dieCtx = useDieContext();
  const dieDefining = (name: string): boolean => dieCtx.active && dieCtx.dieKeys.has(name);
  const [askNewDie, setAskNewDie] = useState<ConfirmState | null>(null);

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
  const [showSaved, setShowSaved] = useState(false);
  const [helpError, setHelpError] = useState<string | null>(null);
  const handleHelpClick = useCallback(() => {
    const err = openGeometryHelpWindow();
    if (err) setHelpError(err);
  }, []);

  // Always-current dirtyKeys for the geometry-sync effect below.  Without this
  // the effect closed over a STALE dirtyKeys (it isn't in its deps), so a
  // geometry update that arrived while a field was edited-but-not-yet-saved
  // could WIPE the pending edit.  The ref always holds the latest set.
  const dirtyRef = useRef(dirtyKeys);
  dirtyRef.current = dirtyKeys;

  useEffect(() => {
    setLocalValues(prev => {
      const next: Record<string, number> = {};
      Object.entries(geometry).forEach(([k, v]) => {
        // Keep local edit if key is dirty, otherwise follow server
        next[k] = dirtyRef.current.has(k) && prev[k] !== undefined ? prev[k] : (v as number);
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

  // ── wire-stock hint (passive only — nothing here restricts the field) ───
  // The owner's warehouse table (2026-09-20): "не блокируем выбор, просто
  // подсказываем, если введённого размера физически нет на складе".
  const { data: wireStockData } = useWireStock();
  const currentWireH = localValues.wire_height ?? (geometry.wire_height as number | undefined) ?? 0;
  const currentWireW = localValues.wire_width ?? (geometry.wire_width as number | undefined) ?? 0;
  const wireStockNote = useMemo(
    () => stockHint(currentWireH, currentWireW, wireStockData?.available_sizes ?? []),
    [currentWireH, currentWireW, wireStockData]);

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
  const sendRecalculate = useCallback(async () => {
    const pending: Record<string, number> = {};
    dirtyKeys.forEach(k => { pending[k] = localValues[k]; });

    if (connectedToApi) {
      await updateGeometryViaApi(pending);   // PUT → persists to config.yaml + rebuild
    } else {
      updateGeometry(pending);
    }
    setDirtyKeys(new Set());
    setShowSaved(true);
  }, [dirtyKeys, localValues, connectedToApi, updateGeometryViaApi, updateGeometry]);

  const handleRecalculate = useCallback(async () => {
    if (!isDirty) return;
    // A die-defining edit under an active die: say what it means and ask.
    const hits = [...dirtyKeys].filter(k => dieDefining(k)
      && Number(localValues[k]) !== Number(geometry[k]));
    if (hits.length) {
      const what = hits.map(k => `${dieKeyLabel(k)} ${geometry[k]} → ${localValues[k]}`).join(', ');
      setAskNewDie({
        title: `${what}: this makes a NEW die`,
        body: `Die '${dieCtx.die}' keeps its diameter and slot/pole topology for life. `
          + 'After Recalculate the machine on screen is a different lamination: the die context is released '
          + 'and the header strip offers "Save as NEW die" (your work is kept there) or "discard and reload". '
          + 'The catalog die itself is not changed.',
        confirmLabel: 'Recalculate as a new lamination',
        onConfirm: () => { void sendRecalculate(); },
      });
      return;
    }
    await sendRecalculate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDirty, dirtyKeys, localValues, geometry, dieCtx.active, dieCtx.die, dieCtx.dieKeys, sendRecalculate]);

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
          startIcon={<HelpOutlineIcon sx={{ fontSize: 14 }} />}
          onClick={handleHelpClick}
          sx={{ fontSize: 11, py: 0.25, px: 1, minHeight: 24 }}
        >
          Help — dimensions
        </Button>
        <HelpTip title="Opens a picture of the machine with every parameter's
          name pointing at the dimension it controls, in a new window." />
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

      {/* FreeCAD round-trip: .FCStd with solids + parameter spreadsheet out,
          edited parameters back in through the normal geometry PUT guards. */}
      <Box sx={{ px: 0.5 }}>
        <FreeCADRoundTrip />
        <Fusion360RoundTrip />
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
          <SectionLabel sx={{ px: 0.5, py: 0.5 }}>{group.label}</SectionLabel>

          {group.params.map(param => {
            const variation = sweepConfig.variations[param.name];
            const isActive  = variation?.mode !== 'fixed' && variation?.mode !== undefined;
            const lockedBy  = paramLocked(param.name);
            const dirty     = !lockedBy && dirtyKeys.has(param.name);
            const localVal  = lockedBy ? undefined : localValues[param.name];
            const displayVal = localVal !== undefined
              ? localVal
              : (geometry[param.name] ?? 0);
            // Whitelist gate: only optimizable params may be ADDED to the sweep.
            // An already-active param always keeps its remove (✕) control.
            // A LOCKED param cannot be added either — it cannot move.
            const canSweep = isActive || (param.optimizable !== false && !lockedBy);

            return (
              <Box
                key={param.name}
                sx={{
                  // Fixed 3-column grid: label | value | action.  The value and
                  // action columns are a CONSTANT width on every row, so all the
                  // value boxes line up in one straight vertical column (instead
                  // of floating with the label length / ✕ presence).
                  display: 'grid',
                  gridTemplateColumns: '1fr 60px 28px',
                  alignItems: 'center',
                  columnGap: 1,
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
                        : 'var(--line-soft)',
                  },
                }}
              >
                {/* Parameter name (grid col 1 = 1fr) */}
                {/* The schema's own one-line description rides on the LABEL as a
                    hover tooltip.  It was served by /api/geometry/schema and
                    rendered nowhere, so a knob whose meaning is not obvious from
                    four words (wire_split, wire_parallel, tooth2_width) had
                    no explanation anywhere in the product. */}
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" noWrap
                    sx={{ lineHeight: 1.4, opacity: lockedBy ? 0.55 : 1 }}>
                    {lockedBy && (
                      <Tooltip title={`Locked by ${lockedBy} — unlock it in the Motors catalog to edit`}>
                        <span style={{ marginRight: 4, cursor: 'help', fontSize: 11 }}>🔒</span>
                      </Tooltip>
                    )}
                    {!lockedBy && dieDefining(param.name) && (
                      <Tooltip title={`Die-defining — changing it makes a NEW die: '${dieCtx.die}' keeps its ${dieKeyLabel(param.name)} for life. Recalculate asks first; the strip then offers "Save as new die".`}>
                        <span style={{ marginRight: 4, cursor: 'help', fontSize: 10,
                                       color: '#f59e0b', fontWeight: 700 }}>die</span>
                      </Tooltip>
                    )}
                    {param.description
                      ? <Tooltip title={param.description}>
                          <span style={{ cursor: 'help' }}>{param.label}</span>
                        </Tooltip>
                      : param.label}
                    {param.unit && (
                      <Typography
                        component="span" variant="caption"
                        color="text.disabled" sx={{ ml: 0.5 }}
                      >
                        ({param.unit})
                      </Typography>
                    )}
                  </Typography>
                  {/* Passive stock hint — wire_height/wire_width only, and
                      only when the CURRENT pair is not a stocked size. Does
                      not restrict the field; the owner decides that later. */}
                  {(param.name === 'wire_height' || param.name === 'wire_width') && wireStockNote && (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.3, mt: '-2px' }}>
                      <Typography noWrap sx={{ fontSize: '0.62rem', color: '#f59e0b' }}>
                        {wireStockNote}
                      </Typography>
                      <HelpTip title="Compared against the flat wire physically on the shelf (Materials tab → Flat wire in stock). Not enforced yet." />
                    </Box>
                  )}
                </Box>

                {/* Editable value — a SELECT of the admissible values when the
                    schema's topology table has an entry for the dependency's
                    current value (poles/segment given slots/segment: 5 or 7 —
                    owner 2026-09-20, "других комбинаций пока не бывает"), a
                    free-typing local draft otherwise. */}
                {(() => {
                  const dep = param.allowed_by ? Object.keys(param.allowed_by)[0] : null;
                  const depVal = dep ? Number(localValues[dep] ?? geometry[dep]) : NaN;
                  const allowed = dep && Number.isFinite(depVal)
                    ? param.allowed_by![dep][String(Math.round(depVal))] : undefined;
                  if (!allowed || !allowed.length) return null;
                  const cur = Number(displayVal);
                  return (
                    <TextField select size="small" value={allowed.includes(cur) ? cur : ''}
                      disabled={!!lockedBy}
                      SelectProps={{ native: true }}
                      onChange={(e) => { const n = Number(e.target.value); if (Number.isFinite(n)) commitValue(param.name, n); }}
                      sx={{ ...numFieldSx, '& select': { px: '4px', py: '4px', fontSize: 12, textAlign: 'right' },
                            '& .MuiOutlinedInput-root': dirty ? { '& fieldset': { borderColor: '#f59e0b55' } } : {} }}>
                      {!allowed.includes(cur) && <option value="">{Number.isFinite(cur) ? cur : ''}</option>}
                      {allowed.map(a => <option key={a} value={a}>{a}</option>)}
                    </TextField>
                  );
                })() ?? (
                <ParamValueField
                  value={typeof displayVal === 'number' ? displayVal : (Number(displayVal) || 0)}
                  type={param.type as 'float' | 'int'}
                  step={param.step}
                  min={param.min}
                  max={param.max}
                  dirty={dirty}
                  disabled={!!lockedBy}
                  onCommit={(v) => commitValue(param.name, v)}
                  onEnter={handleRecalculate}
                />
                )}

                {/* Add / remove from sweep — only for whitelisted params */}
                {canSweep ? (
                  <Tooltip title={isActive ? 'Remove from sweep' : 'Add to sweep'}>
                    <IconButton
                      size="small"
                      color={isActive ? 'primary' : 'default'}
                      onClick={() => toggleSweep(param.name)}
                      sx={{ p: 0.4, justifySelf: 'center',
                            opacity: isActive ? 1 : 0.35, '&:hover': { opacity: 1 } }}
                    >
                      {isActive
                        ? <CloseIcon     sx={{ fontSize: 15 }} />
                        : <ShowChartIcon sx={{ fontSize: 15 }} />}
                    </IconButton>
                  </Tooltip>
                ) : (
                  // Not optimizable → empty grid cell keeps the value column aligned
                  <Box />
                )}
              </Box>
            );
          })}
        </Box>
      ))}

      <Snackbar
        open={showSaved}
        autoHideDuration={2500}
        onClose={() => setShowSaved(false)}
        message="Geometry changes applied & saved to config"
      />
      <Snackbar
        open={!!helpError}
        autoHideDuration={5000}
        onClose={() => setHelpError(null)}
        message={helpError ?? ''}
      />
      <ConfirmDialog state={askNewDie} onClose={() => setAskNewDie(null)} />
    </Box>
  );
};

export default ParameterVariationTable;
