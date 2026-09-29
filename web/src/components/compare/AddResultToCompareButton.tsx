/**
 * "+ Add to comparison" for the Mechanical and Thermal tabs.
 *
 * User 2026-09-07: *"нужно везде сделать такую же кнопку для сравнения всех
 * величин в механических и температурных моделированиях"* — the same button the
 * Configure tab has (filled, primary blue, a "+" and the words), so it reads as
 * the same action wherever it appears, and the row it stores lands in the SAME
 * Compare library the Electromagnetic tab's points do.
 *
 * ONE short line + a tooltip for the rest, never a paragraph (project UI rule):
 * the outcome is a single ✓/✗ line whose tooltip carries the whole sentence, and
 * a disabled button explains itself in its own tooltip instead of leaving the
 * user to guess why nothing happens.
 */
import React, { useState } from 'react';
import { Button, CircularProgress, Tooltip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';

import { useMechanicalStore } from '../../stores/mechanicalStore';
import { useMotorStore } from '../../stores/motorStore';
import { useThermalStore } from '../../stores/thermalStore';
import {
  addMechanicalPointToCompare, addThermalPointToCompare, defaultPointName,
  mechanicalPointIssue, thermalPointIssue,
} from './addToCompare';

const READY_TIP: Record<'mechanical' | 'thermal', string> = {
  mechanical: 'Save this machine (geometry + materials + operating point) together with '
    + 'the stress, modal and critical-speed numbers on screen as a row of the Compare '
    + 'table. Stored on the server, so it survives a reload and another browser; rename '
    + 'or delete it in the Compare tab. The same press also stacks the variant in the '
    + 'comparison table on this tab, where the inputs that DIFFER are the columns.',
  thermal: 'Save this machine (geometry + materials + operating point + the cooling set '
    + 'above) together with the MAXIMUM temperature of every part on screen as a row of '
    + 'the Compare table. Stored on the server, so it survives a reload and another '
    + 'browser; rename or delete it in the Compare tab. The same press also stacks the '
    + 'variant in the comparison table on this tab, where the inputs that DIFFER are '
    + 'the columns.',
};

/**
 * `onLocalAdd` is the TAB's own stack (user 2026-09-07: *"сделай локальное
 * сравнение … только как в Configure"*).  ONE press does both, because they are
 * one action — "keep this variant" — and two buttons would make the user choose
 * between a table and a library they both want.  The local half goes first: it
 * needs no network, and nobody should have to press twice because the server
 * was slow.  Its own failure (the stack is full) is reported BESIDE the Compare
 * outcome, never instead of it.
 */
const AddResultToCompareButton: React.FC<{
  kind: 'mechanical' | 'thermal';
  onLocalAdd?: () => void;
}> = ({ kind, onLocalAdd }) => {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; line: string; tip: string } | null>(null);

  /* The three things the verdict below depends on, subscribed to so that it is
     re-evaluated when any of them moves: the result slices (a Solve, or an
     error) and the live geometry (which is what makes a result STALE — the
     staleness test compares against it, and a zustand selector on the physics
     store would never re-run on a geometry edit). */
  const stress = useMechanicalStore((s) => s.stress);
  const field = useThermalStore((s) => s.field);
  const liveGeometry = useMotorStore((s) => s.geometry);
  void stress; void field; void liveGeometry;

  const issue = kind === 'mechanical' ? mechanicalPointIssue() : thermalPointIssue();
  const label = kind === 'mechanical' ? 'Mechanical' : 'Thermal';

  /** One short line for whatever the caller's text is, the whole of it in the
   *  tooltip — the project's no-walls-of-text rule, applied to errors too. */
  const clip = (t: string) => `${t.slice(0, 60)}${t.length > 60 ? '…' : ''}`;

  const add = async () => {
    setBusy(true); setMsg(null);
    // The TAB's own table first — no network, so a Compare failure never costs
    // the user the variant they were stacking.
    let head = ''; let headTip = ''; let localBad = false;
    if (onLocalAdd) {
      try {
        onLocalAdd();
        head = '✓ stacked below';
        headTip = 'The variant is now a row of the comparison table on this tab. ';
      } catch (e) {
        const text = e instanceof Error ? e.message : String(e);
        localBad = true;
        head = `✗ ${clip(text)}`;
        headTip = `${text} `;
      }
    }
    const join = (rest: string) => (head ? `${head} · ${rest}` : rest);
    try {
      // The name is the die / configuration / duty and the time, prefixed with
      // the physics it came from — so a Compare library holding all three kinds
      // of row says at a glance which is which.  Renameable in the Compare tab.
      const name = `${label} · ${await defaultPointName()}`;
      if (kind === 'mechanical') await addMechanicalPointToCompare(name);
      else await addThermalPointToCompare(name);
      setMsg({ ok: !localBad, line: join('✓ added to Compare'),
        tip: `${headTip}"${name}" is now a row of the Compare table. Tick it there `
          + 'against another point to see the two side by side.' });
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      setMsg({ ok: false, line: join(`✗ Compare: ${clip(text)}`),
        tip: `${headTip}${text}` });
    } finally { setBusy(false); }
  };

  return (
    <>
      <Tooltip placement="top" title={issue
        ? `Nothing to add: ${issue}.`
        : READY_TIP[kind]}>
        <span>
          {/* The Configure tab's button, to the pixel — same fill, same weight,
              same words: one action, one look, wherever it is. */}
          <Button onClick={() => { void add(); }} variant="contained" size="small"
            disabled={busy || !!issue}
            startIcon={busy ? <CircularProgress size={13} sx={{ color: '#fff' }} />
                            : <AddIcon />}
            sx={{ textTransform: 'none', fontWeight: 700, bgcolor: '#1d4ed8',
              '&:hover': { bgcolor: '#2563eb' }, alignSelf: 'flex-start',
              whiteSpace: 'nowrap' }}>
            Add to comparison
          </Button>
        </span>
      </Tooltip>
      {msg && (
        <Tooltip title={msg.tip}>
          <Typography sx={{ fontSize: 10.5, cursor: 'help',
            color: msg.ok ? '#4ade80' : '#fca5a5' }}>
            {msg.line}
          </Typography>
        </Tooltip>
      )}
    </>
  );
};

export default AddResultToCompareButton;
