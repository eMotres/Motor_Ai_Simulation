/**
 * AddToCompareButton — "+ Compare" next to the Simulation summary: stores the
 * design on screen as a comparison point (server-side, so it is permanent) and
 * reports what happened inline.  Naming/removal live in the Compare tab.
 */
import React, { useState } from 'react';
import { Button, Typography, Tooltip, CircularProgress } from '@mui/material';
import AddchartIcon from '@mui/icons-material/Addchart';
import { addCurrentPointToCompare, defaultPointName, hasSummaryToSave } from './addToCompare';

const AddToCompareButton: React.FC = () => {
  const [busy, setBusy] = useState(false);
  const [msg,  setMsg]  = useState<string | null>(null);
  const ready = hasSummaryToSave();

  const add = async () => {
    setBusy(true); setMsg(null);
    try {
      await addCurrentPointToCompare(defaultPointName());
      setMsg('✓ added to Compare');
    } catch (e: any) {
      setMsg('✗ ' + String(e?.message ?? e));
    } finally { setBusy(false); }
  };

  return (
    <>
      <Tooltip placement="top" title={ready
        ? 'Save this design (geometry + mesh + operating point + these results) as a comparison point. '
          + 'Stored on the server, so it survives reloads and other browsers. Rename or delete it in the Compare tab.'
        : 'Run a simulation first — there are no results to snapshot yet.'}>
        <span>
          {/* Filled, not outlined: as a muted outline button in a dense header it
              read as decoration and users could not find it. */}
          <Button size="small" variant="contained" disableElevation disabled={busy || !ready}
            startIcon={busy ? <CircularProgress size={13} sx={{ color: '#fff' }} />
                            : <AddchartIcon sx={{ fontSize: 16 }} />}
            onClick={add}
            sx={{ py: 0.3, px: 1.25, fontSize: 11.5, fontWeight: 700, textTransform: 'none',
              bgcolor: '#1d4ed8', color: '#fff', whiteSpace: 'nowrap',
              '&:hover': { bgcolor: '#2563eb' } }}>
            + Add to Compare
          </Button>
        </span>
      </Tooltip>
      {msg && (
        <Typography sx={{ fontSize: 10.5,
          color: msg.startsWith('✓') ? '#4ade80' : '#fca5a5' }}>{msg}</Typography>
      )}
    </>
  );
};

export default AddToCompareButton;
