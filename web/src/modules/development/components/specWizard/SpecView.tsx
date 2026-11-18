import { useEffect, useRef, useState } from 'react';
import { ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import { readView, writeView } from '../../../../lib/viewMemory';
import type { SpecDraft } from '../../wizardState';
import SpecTable from './SpecTable';
import SpecWizard, { type SpecWizardProps } from './SpecWizard';
import { initialSpecView, parseSpecViewMode, type SpecViewMode } from './specTableModel';

type Reveal = { field: string; n: number } | null;

/** Which view the project's Specification shows (owner 2026-11-18): the Table of every value or the step-by-step Wizard.  The
 *  page owns it, because the Table | Wizard switch sits in the project TITLE row (owner, after #757: "the specification modes
 *  are better put in this row").  The view is decided once per opened project: a specification that holds any answer opens on
 *  the viewer's remembered view, by default the Table; a brand-new empty one opens in the Wizard (typing the first answer does
 *  not flip it).  Only the switch changes the remembered view.  A field the page asks to open (a submit error, a comment, a
 *  table row) is shown in the Wizard; a reveal already served is not replayed when the viewer later switches back by hand. */
export function useSpecViewMode(viewScope: string | null, projectId: string | null, draft: SpecDraft | null | undefined, reveal: Reveal) {
  const scope = viewScope ? `${viewScope}.specView` : null;
  const first = useRef<{ project: string; mode: SpecViewMode } | null>(null);
  if (projectId && draft && first.current?.project !== projectId) first.current = { project: projectId, mode: initialSpecView(scope ? readView(scope, parseSpecViewMode) : null, draft) };
  const [chosen, setChosen] = useState<{ project: string; mode: SpecViewMode } | null>(null);
  const mode: SpecViewMode | null = !projectId || !draft ? null : chosen?.project === projectId ? chosen.mode : first.current?.project === projectId ? first.current.mode : null;
  const show = (next: SpecViewMode) => { if (projectId) setChosen({ project: projectId, mode: next }); };
  const n = reveal?.n ?? 0;
  const seen = useRef(n);
  const [stale, setStale] = useState<number | null>(n || null);
  useEffect(() => { if (n !== seen.current) { seen.current = n; setStale(null); show('wizard'); } }, [n]);
  const byHand = (next: SpecViewMode) => { if (next === 'wizard') setStale(n || null); show(next); if (scope) writeView(scope, next); };
  return { mode, byHand, openWizard: () => show('wizard'), wizardReveal: reveal && reveal.n !== stale ? reveal : null };
}

/** The Table | Wizard switch, drawn by the page in the project title row while the Specification tab is open. */
export function SpecViewSwitch({ mode, onChange, tr }: { mode: SpecViewMode; onChange: (mode: SpecViewMode) => void; tr: (key: string) => string }) {
  return <ToggleButtonGroup size="small" exclusive value={mode} aria-label={tr('wizard.table.switch')} data-testid="spec-view-switch"
    onChange={(_, next) => { const m = parseSpecViewMode(next); if (m) onChange(m); }}
    sx={{ flexShrink: 0, '& .MuiToggleButton-root': { py: 0.25, px: 1.25, textTransform: 'none', fontSize: 13, minHeight: 30 } }}>
    <ToggleButton value="table" data-testid="spec-view-table"><Tooltip title={tr('wizard.table.tableTip')}><span>{tr('wizard.table.table')}</span></Tooltip></ToggleButton>
    <ToggleButton value="wizard" data-testid="spec-view-wizard"><Tooltip title={tr('wizard.table.wizardTip')}><span>{tr('wizard.table.wizard')}</span></Tooltip></ToggleButton>
  </ToggleButtonGroup>;
}

/** The project's Specification body: the view the page chose.  A row of the table (or a section's Edit) opens the Wizard at that
 *  field / step through the page's own "reveal a field" path; that is a step into editing, not a choice of view. */
export default function SpecView(p: SpecWizardProps & { mode: SpecViewMode; onOpenWizard: () => void; onRevealField: (fieldId: string) => void }) {
  const { mode, onOpenWizard, onRevealField, ...wizard } = p;
  if (mode === 'wizard') return <SpecWizard {...wizard} />;
  return <SpecTable env={wizard.env} project={wizard.project} completeness={wizard.completeness} messages={wizard.messages}
    onOpenField={(fieldId) => { onOpenWizard(); onRevealField(fieldId); }}
    onOpenStep={(index) => { onOpenWizard(); wizard.onStep(index); }} />;
}
