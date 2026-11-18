import { useEffect, useRef, useState } from 'react';
import { ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import { readView, writeView } from '../../../../lib/viewMemory';
import SpecTable from './SpecTable';
import SpecWizard, { type SpecWizardProps } from './SpecWizard';
import { initialSpecView, parseSpecViewMode, type SpecViewMode } from './specTableModel';

/** The project's Specification view (owner 2026-11-18): a Table of every value on one page, or the step-by-step Wizard, with a
 *  Table | Wizard switch in the specification header.  The choice is remembered per viewer (`viewScope`); a specification that
 *  holds any answer opens on the remembered view, by default the Table; a brand-new empty one opens in the Wizard.  A row of the
 *  table opens the Wizard at that field through the page's own "reveal a field" path. */
export default function SpecView(p: SpecWizardProps & { viewScope: string | null; onRevealField: (fieldId: string) => void }) {
  const { viewScope, onRevealField, ...wizard } = p;
  const { tr } = wizard.env;
  const scope = viewScope ? `${viewScope}.specView` : null;
  const [mode, setMode] = useState<SpecViewMode>(() => initialSpecView(scope ? readView(scope, parseSpecViewMode) : null, wizard.env.draft));
  const choose = (next: SpecViewMode) => { setMode(next); if (scope) writeView(scope, next); };
  // a field the page asks to open (a submit error, a comment, a table row) is shown in the Wizard; a reveal that was already
  // served is not replayed when the viewer later switches back to the Wizard by hand
  const n = wizard.reveal?.n ?? 0;
  const seen = useRef(n);
  const [stale, setStale] = useState<number | null>(n || null);
  useEffect(() => { if (n !== seen.current) { seen.current = n; setStale(null); setMode('wizard'); } }, [n]);
  const byHand = (next: SpecViewMode) => { if (next === 'wizard') setStale(n || null); choose(next); };
  const toggle = <ToggleButtonGroup size="small" exclusive value={mode} aria-label={tr('wizard.table.switch')} data-testid="spec-view-switch"
    onChange={(_, next) => { const m = parseSpecViewMode(next); if (m) byHand(m); }}
    sx={{ flexShrink: 0, '& .MuiToggleButton-root': { py: 0.25, px: 1.25, textTransform: 'none', fontSize: 13, minHeight: 32 } }}>
    <ToggleButton value="table" data-testid="spec-view-table"><Tooltip title={tr('wizard.table.tableTip')}><span>{tr('wizard.table.table')}</span></Tooltip></ToggleButton>
    <ToggleButton value="wizard" data-testid="spec-view-wizard"><Tooltip title={tr('wizard.table.wizardTip')}><span>{tr('wizard.table.wizard')}</span></Tooltip></ToggleButton>
  </ToggleButtonGroup>;
  if (mode === 'wizard') return <SpecWizard {...wizard} reveal={wizard.reveal && wizard.reveal.n !== stale ? wizard.reveal : null} headerExtra={toggle} />;
  return <SpecTable env={wizard.env} project={wizard.project} completeness={wizard.completeness} messages={wizard.messages} headerExtra={toggle}
    // opening a field or a step is an edit, not a choice of view: the remembered view stays the Table
    onOpenField={(fieldId) => { setMode('wizard'); onRevealField(fieldId); }}
    onOpenStep={(index) => { setMode('wizard'); wizard.onStep(index); }} />;
}
