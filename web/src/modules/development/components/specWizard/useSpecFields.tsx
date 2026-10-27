import { Fragment, useState } from 'react';
import { Alert, Box, Button, IconButton, InputAdornment, Menu, MenuItem, Paper, Select, Stack, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import ScheduleIcon from '@mui/icons-material/Schedule';
import { BatteryInputs, PackBar, type Battery } from '../../../../components/compare/BatteryPanel';
import { SPEC_PACK_PROP, specBatteryChemistry, specBatteryFieldEdit, specBatteryPackEdit, specBatteryView } from '../../specBattery';
import type { SchemaField, SchemaSuggestion } from '../../types';
import { hiddenCompanions, localized as localeValue } from './reviewSummary';
import { COOLING_MEDIA, airflowExcluded, airflowSuggestions, coolingChoices, coolingForLoad, coolingLoadProblems, coolingMedium, coolingPropertyIds, loadGroup, naturalAirflow, loadPropertyIds, loadSwitchChoices, withLoadType, type CoolingMedium } from '../../specChoices';
import { readLoadDraft, writeLoadDraft } from '../../loadDraftAdapter';
import type { LoadType } from '../../missionProfileState';
import type { SpecificationField } from '../../specificationLayout';
import { optionsMinWidth, specFieldLabelSx, specGridSx, specGroupHeaderSx, visibleProperties } from '../../specGrid';
import { OWN_DESIGN, applyControllerChoice, batteryVsController, catalogBrands, controllerLimits, controllerLimitsLine, linkedController, powerDevicesUnknown, prefillProvenance, statedPowerDevices, type CatalogController } from '../../controllerCatalog';
import { storedOverspeedRpm } from '../../speedLimits';
import { matchesFieldReference, productionFallback } from '../../reviewPresentation';
import { answerWithUnknown, fieldAnswer, fieldAnswerComplete, type FieldAnswer, fieldApplies, invertedPairs, fieldRequired, propertyRequired, setFieldAnswer, rangeWorst, withS1DerivedPower, type SpecDraft } from '../../wizardState';
import { keyPointsSlotState } from '../../keyPoints';
import { NARROW } from '../../../../lib/narrowLayout';
import type { MissionRow } from '../../missionProfileState';
import MissionProfileEditor from '../MissionProfileEditor';
import { MissionProfile, SuggestField } from './SpecInputs';
import { hiddenAirflowModes, wizardNaturalAirflow, type WizardProfile } from './profile';
import { answerPresent } from './steps';
import { legacyPointVoltages, legacyVoltageText, supplyEmpty, supplyType, withDcSourceV, withSupplyType, type SupplyType } from './supply';
import { missionLocks, readableRange } from './operatingPoints';
import type { ProjectThreadMessage, SpecState } from '../../types';
import type { WizardStep } from './steps';

export interface SpecFieldsContext {
  tr: (key: string, args?: Record<string, unknown>) => string;
  lang: string;
  draftRef: { current: SpecDraft };
  updateDraft: (fn: (d: SpecDraft) => SpecDraft) => void;
  spec: SpecState | null;
  steps: WizardStep[];
  profile: WizardProfile;
  canEditSpec: boolean; busy: boolean; migrationBusy: boolean;
  fieldErrors: Record<string, string>;
  messages: ProjectThreadMessage[];
  catalog: CatalogController[];
  openFieldReview: (ref: string) => void;
}

/** The field renderers of the specification (moved from DevelopmentPage, behaviour unchanged except where marked): the typed
 *  value inputs, the battery, cooling, ambient, load and mission blocks, the label row with its comment icon and the
 *  "answer later" action.  The wizard steps arrange them; none of them knows which step it sits in. */
export function useSpecFields(c: SpecFieldsContext) {
  const { tr, lang, draftRef, updateDraft, spec, steps: sections, canEditSpec, busy, migrationBusy, fieldErrors, messages, catalog, openFieldReview } = c;
  const [extraLoadProps, setExtraLoadProps] = useState<string[]>([]);
  const [addLoadAnchor, setAddLoadAnchor] = useState<HTMLElement | null>(null);
  const label = (v: unknown) => localeValue(v, lang);
  const blankValue = (value: unknown) => value === undefined || value === null || (typeof value === 'string' && !value.trim());
  const setAnswer = (field: SchemaField, value: unknown, production = false) => updateDraft((d) => {
    const old = production ? ((d.production as Record<string, { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' }>)?.[field.id] ?? {}) : fieldAnswer(d, field.id);
    const complete = fieldAnswerComplete(field, { value, status: 'filled' });
    const answer: FieldAnswer = { ...old, value, status: complete ? 'filled' : 'missing' };
    if (production) return { ...d, production: { ...(d.production as object), [field.id]: answer } };
    return setFieldAnswer(d, field.id, answer);
  });
  const markUnknown = (field: SchemaField, unknown: boolean, production = false) => updateDraft((d) => {
    if (field.id === 'mission_profile') return { ...d, mission_profile_status: unknown ? 'not_known_yet' : (d.mission_profile?.length ? 'filled' : 'missing') };
    const old = production ? ((d.production as Record<string, { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' }>)?.[field.id] ?? {}) : fieldAnswer(d, field.id);
    const answer: FieldAnswer = answerWithUnknown(field, old, unknown);
    if (production) return { ...d, production: { ...(d.production as object), [field.id]: answer } };
    return setFieldAnswer(d, field.id, answer);
  });
  /** A stored choice the options no longer list stays visible (and selected) instead of rendering blank. */
  const storedOutsideOptions = (value: unknown, options: Array<string | { value: string }>) => value != null && value !== '' && !options.some((o) => (typeof o === 'string' ? o : o.value) === String(value)) ? <MenuItem value={String(value)}>{String(value)}</MenuItem> : null;
  const sectionTitle = (v: unknown) => { const value = label(v); return value.startsWith('wizard.') ? tr(value) : value; };
  const renderTypedValue = (shape: { id: string; label: unknown; short_label?: unknown; help?: unknown; unit?: string; type?: string; required?: boolean; required_flag?: string; allow_range?: boolean; multiline?: boolean; placeholder?: string; min?: number; max?: number; options?: Array<string | { value: string; label?: unknown }>; suggestions?: SchemaSuggestion[]; excluded?: Record<string, string>; properties?: Array<any>; items?: { type?: string; properties?: Array<any> } }, value: unknown, onChange: (next: unknown) => void, keyPrefix: string, locked = false, _compact = false): React.ReactNode => {
    const kind = shape.type ?? 'string';
    const options = shape.options ?? [];
    const text = (v: unknown) => v == null ? '' : String(v);
    const unitAdornment = (unit: string) => <InputAdornment position="end" sx={{ alignSelf: 'stretch', height: 'auto', maxHeight: 'none', ml: 1, mr: -1.75, px: 1, borderLeft: '1px solid var(--line)', borderRadius: '0 8px 8px 0', bgcolor: 'var(--app-bg)', color: 'var(--text-3)', '& .MuiTypography-root': { fontSize: 11 } }}>{unit}</InputAdornment>;
    const input = (v: unknown, update: (next: unknown) => void, type: string, unit?: string) => <TextField size="small" fullWidth type={type === 'number' ? 'number' : type === 'date' || unit === 'date' ? 'date' : 'text'} multiline={!!shape.multiline && type !== 'number'} minRows={shape.multiline && type !== 'number' ? 2 : undefined} placeholder={shape.placeholder} value={text(v)} inputProps={{ 'aria-label': `${label(shape.short_label ?? shape.label)}${unit ? ` (${unit})` : ''}`, min: type === 'number' ? shape.min : undefined, max: type === 'number' ? shape.max : undefined, step: type === 'number' ? 'any' : undefined }} InputProps={unit && unit !== 'date' ? { endAdornment: unitAdornment(unit) } : undefined} disabled={!canEditSpec || busy || migrationBusy || locked} onChange={(e) => update(type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)} />;
    // a standard choice is a menu (schema `suggestions`): the localized label shows, the EN value is stored, any other text is kept
    const suggest = (v: unknown, update: (next: unknown) => void, type: string, unit?: string) => <SuggestField key={`${keyPrefix}-${lang}`} excluded={shape.excluded} suggestions={shape.suggestions!} value={v} onChange={update} lang={lang} numeric={type === 'number'} disabled={!canEditSpec || busy || migrationBusy || locked} ariaLabel={`${label(shape.short_label ?? shape.label)}${unit ? ` (${unit})` : ''}`} placeholder={shape.placeholder} multiline={!!shape.multiline && type !== 'number'} endAdornment={unit && unit !== 'date' ? unitAdornment(unit) : undefined} minWidth={optionsMinWidth(shape.suggestions as never)} />;
    if (shape.allow_range) {
      // ONE value, the maximum allowed / worst case for modelling (owner 2026-10-25: «всё должно быть однозначно»). A range
      // stored before shows its maximum; typing replaces it with one number. The stored range itself is never rewritten here.
      const stored = rangeWorst(shape, value);
      if (!stored) return shape.suggestions?.length ? suggest(value, onChange, 'number', shape.unit) : input(value, onChange, 'number', shape.unit);
      return <Tooltip title={tr('rangeWorst', { min: text(stored.min), max: text(stored.max) })}><Box>{input(stored.worst, onChange, 'number', shape.unit)}</Box></Tooltip>;
    }
    if (kind === 'array') {
      const rows = Array.isArray(value) ? value : [];
      const item = shape.items ?? {};
      if (item.type === 'object' && item.properties?.length) return <Stack spacing={0.8} sx={{ gridColumn: '1 / -1' }}>
        {rows.map((row, index) => <Paper key={`${keyPrefix}-${index}`} variant="outlined" sx={{ p: 0.8 }}><Stack spacing={0.6}>
          {item.properties!.map((prop: any) => <Box key={prop.id} sx={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0.3, alignItems: 'start' }}><Tooltip title={label(prop.help ?? prop.label)}><Typography tabIndex={0} sx={specFieldLabelSx}>{label(prop.short_label ?? prop.label)}{prop.unit ? ` (${prop.unit})` : ''}</Typography></Tooltip>{renderTypedValue(prop, (row as Record<string, unknown>)?.[prop.id], (next) => { const nextRows = [...rows]; nextRows[index] = { ...((row && typeof row === 'object') ? row as object : {}), [prop.id]: next }; onChange(nextRows); }, `${keyPrefix}-${index}-${prop.id}`, locked)}</Box>)}
          <Button size="small" disabled={!canEditSpec || busy || migrationBusy || locked} onClick={() => onChange(rows.filter((_, i) => i !== index))}>{tr('removeRow')}</Button>
        </Stack></Paper>)}
        <Button size="small" disabled={!canEditSpec || busy || migrationBusy || locked} onClick={() => onChange([...rows, Object.fromEntries((item.properties ?? []).map((prop: any) => [prop.id, '']))])}>{tr('addRow')}</Button>
      </Stack>;
      return <TextField size="small" fullWidth multiline minRows={2} disabled={!canEditSpec || busy || migrationBusy || locked} value={rows.join(', ')} onChange={(e) => onChange(e.target.value.split(',').map((part) => part.trim()).filter(Boolean).map((part) => item.type === 'number' ? Number(part) : part))} />;
    }
    if (kind === 'object' && shape.properties?.length) {
      const object = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
      return <Box sx={{ ...specGridSx, [NARROW]: { gridTemplateColumns: 'minmax(0,1fr)' } }}>{shape.properties.map((prop: any) => <Box key={prop.id} sx={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0.3, alignItems: 'start', minWidth: 0 }}><Tooltip title={label(prop.help ?? prop.label)}><Typography tabIndex={0} sx={specFieldLabelSx}>{label(prop.short_label ?? prop.label)}{(prop.required || prop.required_flag === 'E') && <Box component="span" sx={{ color: 'error.main' }}> *</Box>}{prop.unit ? ` (${prop.unit})` : ''}</Typography></Tooltip>{renderTypedValue(prop, object[prop.id], (next) => onChange({ ...object, [prop.id]: next }), `${keyPrefix}-${prop.id}`, locked)}</Box>)}</Box>;
    }
    if (options.length) return <Select size="small" fullWidth displayEmpty sx={{ minWidth: optionsMinWidth(options), maxWidth: '100%' }} inputProps={{ 'aria-label': label(shape.short_label ?? shape.label) }} disabled={!canEditSpec || busy || migrationBusy || locked} value={text(value)} onChange={(e) => onChange(e.target.value)}><MenuItem value="">?</MenuItem>{storedOutsideOptions(value, options)}{options.map((option) => { const v = typeof option === 'string' ? option : option.value; return <MenuItem key={v} value={v}>{typeof option === 'string' ? option : label(option.label) || v}</MenuItem>; })}</Select>;
    if (kind === 'boolean') return <Select size="small" fullWidth displayEmpty sx={{ minWidth: optionsMinWidth([tr('yes'), tr('no')]), maxWidth: '100%' }} inputProps={{ 'aria-label': label(shape.short_label ?? shape.label) }} disabled={!canEditSpec || busy || migrationBusy || locked} value={value == null || value === '' ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? '' : e.target.value === 'true')}><MenuItem value="">?</MenuItem><MenuItem value="true">{tr('yes')}</MenuItem><MenuItem value="false">{tr('no')}</MenuItem></Select>;
    if (shape.suggestions?.length && (kind === 'string' || kind === 'number')) return suggest(value, onChange, kind, shape.unit);
    return input(value, onChange, kind, shape.unit);
  };
  const openCount = (ref: string) => messages.filter((m) => !m.resolved && matchesFieldReference(m.field_ref, ref)).length;
  /** The field comment icon only appears while that field has open comments (the card's icon starts new ones). */
  const commentIcon = (ref: string) => { const open = openCount(ref); return open > 0 ? <Tooltip title={`${tr('fieldReview')} (${open})`}><IconButton size="small" aria-label={tr('fieldReview')} onClick={() => openFieldReview(ref)} sx={{ p: 0.3, color: 'primary.main' }}><ChatBubbleOutlineIcon sx={{ fontSize: 14 }} /><Box component="span" sx={{ fontSize: 10, ml: 0.2 }}>{open}</Box></IconButton></Tooltip> : null; };
  // The "not known yet" hourglass sits only on an EMPTY field (to mark it) or a marked one (to unmark it); a filled field shows none.
  const fieldFilled = (f: SchemaField, prod: boolean) => f.id === 'mission_profile' ? (draftRef.current.mission_profile?.length ?? 0) > 0
    : f.id === 'key_points' ? keyPointsSlotState(draftRef.current) !== 'missing'
    : answerPresent(((prod ? (draftRef.current.production as Record<string, unknown>) : draftRef.current.fields) ?? {})[f.id]);
  /** "Answer later" (the same `not_known_yet` status as before, in words instead of an hourglass): offered on an EMPTY field to mark
   *  it, and on a marked one to take the mark back; a filled field shows none.  A touch-sized quiet text button under the input. */
  const laterSx = { alignSelf: 'flex-start', minHeight: 44, textTransform: 'none', fontSize: 13, px: 1, ml: -1 } as const;
  const answerLater = (f: SchemaField, unknown: boolean, prod: boolean) => {
    if (f.allow_not_known_yet === false || !canEditSpec || (!unknown && fieldFilled(f, prod))) return null;
    const name = label(f.short_label ?? f.label);
    return unknown
      ? <Stack direction="row" alignItems="center" spacing={1} data-testid="answer-later-marked"><Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{tr('wizard.markedLater')}</Typography><Button size="small" aria-label={`${tr('wizard.answerNow')}: ${name}`} disabled={busy || migrationBusy} onClick={() => markUnknown(f, false, prod)} sx={laterSx}>{tr('wizard.answerNow')}</Button></Stack>
      : <Tooltip title={tr('wizard.answerLaterTip')}><span style={{ alignSelf: 'flex-start' }}><Button size="small" startIcon={<ScheduleIcon sx={{ fontSize: 16 }} />} aria-label={`${tr('wizard.answerLater')}: ${name}`} disabled={busy || migrationBusy} onClick={() => markUnknown(f, true, prod)} sx={laterSx}>{tr('wizard.answerLater')}</Button></span></Tooltip>;
  };
  const fieldRefFor = (f: SpecificationField, prod: boolean) => `${f.storage_namespace ?? (prod ? 'production' : 'fields')}.${f.id}`;
  /** Label (help text as its tooltip), required mark and open comments; "answer later" sits under the input. */
  const labelRow = (f: SpecificationField, prod: boolean, required: boolean, _unknown: boolean, text?: string, extra: string[] = []) => <Stack direction="row" alignItems="center" spacing={0.3} sx={{ mb: 0.5, minWidth: 0, flexWrap: 'nowrap', minHeight: 22 }}><Tooltip title={extra.length ? <>{label(f.help ?? f.label)}{extra.map((line) => <Fragment key={line}><br />{line}</Fragment>)}</> : label(f.help ?? f.label)}><Typography tabIndex={0} sx={specGroupHeaderSx}>{text ?? label(f.short_label ?? f.label)}{required && <Box component="span" sx={{ color: 'error.main' }}> *</Box>}</Typography></Tooltip>{commentIcon(fieldRefFor(f, prod))}</Stack>;
  const propLabel = (prop: { label: unknown; short_label?: unknown; help?: unknown; required?: boolean }, strong = false, extraTip?: string) => <Tooltip title={extraTip ? <>{label(prop.help ?? prop.label)}{extraTip.split('\n').map((line) => <Fragment key={line}><br />{line}</Fragment>)}</> : label(prop.help ?? prop.label)}><Typography tabIndex={0} sx={{ ...specFieldLabelSx, mb: 0.4, ...(strong ? { fontWeight: 800, color: 'var(--text-0)' } : {}) }}>{label(prop.short_label ?? prop.label)}{prop.required && <Box component="span" sx={{ color: 'error.main' }}> *</Box>}</Typography></Tooltip>;
  const storedAnswer = (f: SpecificationField, prod: boolean) => {
    const store = prod ? (draftRef.current.production as Record<string, unknown>) ?? {} : draftRef.current.fields ?? {};
    const raw = store[f.id];
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' } : { value: raw };
  };
  const objectOf = (value: unknown) => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  /** One property of a structured answer; the S1 point keeps its derived shaft power in step. */
  const setProperty = (f: SpecificationField, prod: boolean, objectValue: Record<string, unknown>, propId: string, value: unknown) => {
    const v2 = (spec?.schema_version ?? 1) >= 2;
    if (v2 && f.id === '1_4' && propId === 'shaft_power_W') return;
    const next = { ...objectValue, [propId]: value };
    setAnswer(f, v2 && f.id === '1_4' ? withS1DerivedPower(next) : next, prod);
  };
  const fieldMessages = (f: SpecificationField, answer: { value?: unknown; status?: string }, required: boolean) => <>
    {answer.status !== 'not_known_yet' && invertedPairs(f, answer.value).length > 0 && <Typography role="alert" data-testid="inverted-window" sx={{ color: 'error.main', fontSize: 11 }}>{tr('wizard.windowInverted')}</Typography>}
    {fieldErrors[f.id] && <Typography role="alert" sx={{ color: 'error.main', fontSize: 11 }}>{tr('validationError')}</Typography>}
    {required && !fieldAnswerComplete(f, { ...answer, not_known_yet: answer.status === 'not_known_yet' } as FieldAnswer) && <Typography role="alert" sx={{ color: 'error.main', fontSize: 11 }}>{tr('requiredMissing')}</Typography>}
  </>;
  /** "from catalogue <id>, page N (stated)" for a value that still holds the catalogue controller's value. */
  const catalogTip = (path: string): string | undefined => {
    const hit = prefillProvenance(draftRef.current, catalog)[path];
    return hit ? tr('controller.fromCatalog', { id: hit.id, page: hit.page ?? '-', basis: hit.basis ? tr(`controller.basis.${hit.basis}`) : '' }) : undefined;
  };
  const pickController = (text: string) => updateDraft((d) => applyControllerChoice(d, text === OWN_DESIGN ? { kind: 'own' } : { kind: 'text', text }, catalog));
  /** The controller's menu (catalogue entries + own design / other) and the supplier's (the catalogue brands); both take any typed text. */
  const controllerShape = <P extends { id: string }>(prop: P): P & { suggestions?: SchemaSuggestion[] } => {
    if (prop.id === 'manufacturer_model') return { ...prop, suggestions: [...catalog.map((entry) => ({ value: entry.display_name, label: entry.display_name })), { value: OWN_DESIGN, label: tr('controller.ownDesign') }] };
    if (prop.id === 'supplier') return { ...prop, suggestions: catalogBrands(catalog).map((brand) => ({ value: brand, label: brand })) };
    return prop;
  };
  /** The battery maximum above the linked controller's supply maximum: a loud error, next to the battery and the controller. */
  const batteryError = () => { const hit = batteryVsController(draftRef.current, catalog); return hit ? <Typography role="alert" data-testid="battery-controller-error" sx={{ color: 'error.main', fontSize: 11, fontWeight: 600 }}>{tr('controller.batteryTooHigh', { max: hit.max, limit: hit.limit, controller: hit.controller })}</Typography> : null; };
  /** The phase currents and the phase count are not asked (owner 2026-10-27): one read-only line, from the linked controller or
   *  the note that the simulation sets them. */
  const controllerLimitsNote = () => {
    const limits = controllerLimits(linkedController(draftRef.current, catalog));
    return limits
      ? <Tooltip title={tr('wizard.controllerLimits.tip', { controller: limits.controller, source: limits.source || '-' })}><Typography tabIndex={0} data-testid="controller-limits" sx={{ fontSize: 13, color: 'var(--text-2)', alignSelf: 'flex-start' }}>{controllerLimitsLine(limits, tr)}</Typography></Tooltip>
      : <Typography data-testid="controller-limits" sx={{ fontSize: 13, color: 'var(--text-3)' }}>{tr('wizard.controllerLimits.none')}</Typography>;
  };
  const controllerNotes = () => <>
    {controllerLimitsNote()}
    {powerDevicesUnknown(draftRef.current, catalog) && <Tooltip title={tr('controller.devicesUnknownTip')}><Alert severity="warning" data-testid="controller-devices-warning" sx={{ py: 0, '& .MuiAlert-message': { py: 0.4 } }}>{tr('controller.devicesUnknown')}</Alert></Tooltip>}
    {batteryError()}
  </>;
  /** The mission profile: the version-1 table, or the version-2 editor (catalogue samples, stale rows).  "Answer later" is the
   *  same action as on every other field (the editor's own checkbox is not used). */
  const renderMission = (f: SpecificationField) => {
    const v2 = (spec?.schema_version ?? 1) >= 2, d = draftRef.current;
    const unknown = d.mission_profile_status === 'not_known_yet';
    const setRows = (rows: Array<Record<string, unknown>>) => updateDraft((old) => ({ ...old, mission_profile: rows, mission_profile_status: rows.length ? 'filled' : 'missing' }));
    const locks = missionLocks({ canEdit: canEditSpec, busy, migrationBusy, unknown });
    if (!v2) return <Box key={f.id} id={`spec-field-${f.id}`}><MissionProfile draft={d} disabled={locks.toggle} t={tr} lang={lang} onUnknown={(u) => markUnknown(f, u, false)} onChange={setRows} label={label(f.label)} columns={f.columns ?? []} error={fieldErrors[f.id]} /></Box>;
    return <Box key={f.id} id={`spec-field-${f.id}`} sx={{ minWidth: 0 }}>
      {labelRow(f, false, fieldRequired(f, d), unknown)}
      <MissionProfileEditor rows={(d.mission_profile ?? []) as unknown as MissionRow[]} load={readLoadDraft(d)} canEdit={canEditSpec && !busy && !migrationBusy} onReview={openFieldReview} unknown={unknown} onChange={(rows) => setRows(rows as unknown as Array<Record<string, unknown>>)} t={tr} />
      {fieldErrors[f.id] && <Typography role="alert" sx={{ color: 'error.main', fontSize: 11 }}>{tr('validationError')}</Typography>}
      {answerLater(f, unknown, false)}
    </Box>;
  };
  const renderField = (f: SpecificationField, prod = f.storage_namespace === 'production', span = true) => {
    if (!fieldApplies(f, draftRef.current)) return null;
    if (f.handled_by && f.handled_by !== 'mission_profile') return null;
    if (f.id === 'mission_profile') return renderMission(f);
    const answer = storedAnswer(f, prod);
    const unknown = answer.status === 'not_known_yet';
    const val = answer.value == null ? '' : typeof answer.value === 'string' || typeof answer.value === 'number' ? String(answer.value) : '';
    const required = fieldRequired(f, draftRef.current);
    const options = f.options ?? f.choices;
    const objectValue = objectOf(answer.value);
    // properties the schema keeps in the data but does not ask (`ui_hidden`) are not drawn; a field with ONE visible property
    // shows only its header (the header says what the single input is)
    const properties = f.properties?.length ? visibleProperties(f.properties) : undefined;
    const wide = span && ((f.properties?.length ?? 0) > 2 || !!f.multiline || f.type === 'array');
    const fallback = productionFallback(draftRef.current, f.id);
    // the stored upper bounds the page does not ask (the generator output window shows ONE worst-case value per quantity) are readable in a tooltip
    const companions = hiddenCompanions(f, objectValue, lang, tr);
    const planField = fallback.length ? sections.flatMap((section) => section.fields).find((item) => item.storage_namespace === 'production' && item.id === (f.id === '6_1' ? 'quantities' : 'timeline')) : undefined;
    return <Box id={`spec-field-${f.id}`} key={`${prod ? 'p' : 'f'}-${f.id}`} sx={{ minWidth: 0, gridColumn: wide ? '1 / -1' : undefined }}>
      {labelRow(f, prod, required, unknown, undefined, [...(f.id === '2_4' && statedPowerDevices(draftRef.current) ? [tr('controller.statedDevices', { devices: statedPowerDevices(draftRef.current) })] : []), ...(f.id === '2_4' && linkedController(draftRef.current, catalog) ? [tr('controller.linked', { name: linkedController(draftRef.current, catalog)!.display_name })] : []), ...(f.id === '1_6' && storedOverspeedRpm(draftRef.current) ? [tr('wizard.storedOverspeed', { value: storedOverspeedRpm(draftRef.current) })] : []), ...(fallback.length ? [tr('wizard.fromProduction', { values: fallback.map(({ key, value }) => { const prop = planField?.properties?.find((item) => item.id === key); return `${prop ? label(prop.short_label ?? prop.label) : key} ${readableRange(value)}`; }).join(' · ') })] : [])])}
      <Stack spacing={0.5}>
        {properties?.length ? (
          <Box sx={{ ...specGridSx, [NARROW]: { gridTemplateColumns: 'minmax(0,1fr)', '& > *': { gridColumn: 'auto !important' } } }}>
            {properties.map((prop) => <Box key={prop.id} sx={{ minWidth: 0, gridColumn: prop.type === 'object' || prop.type === 'array' ? '1 / -1' : undefined }}>
              {properties.length > 1 && propLabel(prop, false, [catalogTip(`${f.id}.${prop.id}`), ...companions.filter((x) => x.low === prop.id).map((x) => tr('wizard.otherBound', { value: x.text }))].filter(Boolean).join('\n') || undefined)}
              {renderTypedValue(f.id === '2_4' ? controllerShape(prop) : prop, objectValue[prop.id], f.id === '2_4' && prop.id === 'manufacturer_model' ? (value) => pickController(String(value ?? '')) : (value) => setProperty(f, prod, objectValue, prop.id, value), `${f.id}-${prop.id}`, unknown)}
            </Box>)}
          </Box>
        ) : options?.length ? (
          <Select size="small" fullWidth displayEmpty sx={{ minWidth: optionsMinWidth(options), maxWidth: '100%' }} inputProps={{ 'aria-label': label(f.short_label ?? f.label) }} disabled={!canEditSpec || busy || migrationBusy || unknown} value={val} onChange={(ev) => setAnswer(f, ev.target.value, prod)}>
            <MenuItem value=""><em>?</em></MenuItem>{storedOutsideOptions(val, options)}{options.map((o) => { const value = typeof o === 'string' ? o : o.value; return <MenuItem key={value} value={value}>{typeof o === 'string' ? o : label(o.label) || value}</MenuItem>; })}
          </Select>
        ) : (
          renderTypedValue(f, answer.value, (next) => setAnswer(f, next, prod), f.id, unknown)
        )}
        {f.id === '2_4' && controllerNotes()}
        {fieldMessages(f, answer, required)}
        {answerLater(f, unknown, prod)}
      </Stack>
    </Box>;
  };
  /** 2_1, the project's ONE power supply block: a choice of Battery (the app's one battery block, the same as Configure's, with
   *  its pack bar; the stated pack voltages stay editable beside it) or a DC power source (one voltage).  The choice and the
   *  source voltage are the only new stored values; nothing stored for the other choice is touched. */
  const renderBattery = (f: SpecificationField) => {
    const allowed = new Set((f.properties ?? []).map((p) => p.id));
    if (!allowed.has('cell_voltage_nominal_V')) return renderField(f);
    const answer = storedAnswer(f, false), unknown = answer.status === 'not_known_yet', obj = objectOf(answer.value), required = fieldRequired(f, draftRef.current);
    const view = specBatteryView(obj);
    const choice = allowed.has('supply_type') && allowed.has('dc_source_V');
    const type = supplyType(obj);
    const locked = !canEditSpec || busy || migrationBusy || unknown;
    const dcProp = f.properties!.find((p) => p.id === 'dc_source_V');
    const pack = ['voltage_min_V', 'voltage_nominal_V', 'voltage_max_V'].map((id) => f.properties!.find((p) => p.id === id)!).filter(Boolean);
    const handled = new Set(['chemistry', 'series_cells', 'cell_voltage_min_V', 'cell_voltage_nominal_V', 'cell_voltage_max_V', 'supply_type', 'dc_source_V', ...pack.map((p) => p.id)]);
    // an earlier per-point voltage is shown once, in the tooltip, while the block is still empty
    const legacy = supplyEmpty(obj) ? legacyVoltageText(legacyPointVoltages(draftRef.current)) : '';
    const tips = [...(type === 'battery' ? [tr('wizard.packStated')] : []), ...(legacy ? [tr('wizard.supply.legacyPoint', { value: legacy })] : [])];
    const head = labelRow(f, false, required, unknown, undefined, tips);
    const stated = view.pack, barOk = [stated.min, stated.nom, stated.max].every((v) => typeof v === 'number' && Number.isFinite(v) && v > 0) && (stated.min as number) <= (stated.max as number);
    return <Box id={`spec-field-${f.id}`} sx={{ minWidth: 0, p: '10px 12px', border: '1px solid var(--line-soft)', borderRadius: '8px', bgcolor: 'var(--panel-2)' }}>
      {choice && <>
        {head}
        <ToggleButtonGroup exclusive size="small" value={type} disabled={locked} aria-label={label(f.short_label ?? f.label)} onChange={(_, v: SupplyType | null) => { if (v) setAnswer(f, withSupplyType(obj, v)); }} sx={{ mb: 1.25 }}>
          {(['battery', 'dc_source'] as SupplyType[]).map((v) => <ToggleButton key={v} value={v} sx={{ px: 1.75, py: 0.6, minHeight: 36, fontSize: 13, textTransform: 'none', color: 'var(--text-2)', borderColor: 'var(--line)', '&.Mui-selected': { bgcolor: 'primary.main', color: 'primary.contrastText', '&:hover': { bgcolor: 'primary.dark' } } }}>{tr(`wizard.supply.${v}`)}</ToggleButton>)}
        </ToggleButtonGroup>
      </>}
      {choice && type === 'dc_source' && dcProp ? <Box sx={{ maxWidth: 260, minWidth: 0 }}>
        {propLabel(dcProp, false)}
        {renderTypedValue(dcProp, obj.dc_source_V, (value) => setAnswer(f, withDcSourceV(obj, value)), `${f.id}-dc_source_V`, unknown)}
      </Box> : <>
        <BatteryInputs bat={view.bat as Battery} selectedType={view.selected} chemistryNote={view.chemistryNote} statedPack={view.pack} allowEmpty disabled={locked} title={choice ? <Typography sx={{ fontSize: 13, fontWeight: 800, color: 'var(--text-0)' }}>{tr('wizard.supply.battery')}</Typography> : head} onChemistry={(t) => setAnswer(f, specBatteryChemistry(obj, t, allowed))} onFieldEdit={(field, raw) => setAnswer(f, specBatteryFieldEdit(obj, field, raw, allowed))} />
        <Box sx={{ ...specGridSx, [NARROW]: { gridTemplateColumns: 'minmax(0,1fr)' } }}>
          {[...pack, ...(f.properties ?? []).filter((p) => !handled.has(p.id))].map((p) => { const volt = (Object.keys(SPEC_PACK_PROP) as Array<'min' | 'nom' | 'max'>).find((k) => SPEC_PACK_PROP[k] === p.id); return <Box key={p.id} sx={{ minWidth: 0 }}>{propLabel(p, false)}{renderTypedValue(p, obj[p.id], (value) => volt ? setAnswer(f, specBatteryPackEdit(obj, volt, value, allowed)) : setProperty(f, false, obj, p.id, value), `${f.id}-${p.id}`, unknown)}</Box>; })}
        </Box>
        {barOk && <Box sx={{ mt: 1.25, maxWidth: 520 }} data-testid="supply-pack-bar"><PackBar packMin={stated.min as number} packNom={(stated.nom ?? stated.min) as number} packMax={stated.max as number} /></Box>}
      </>}
      {batteryError()}
      {fieldMessages(f, answer, required)}
      {answerLater(f, unknown, false)}
    </Box>;
  };
  const propertyGrid = { ...specGridSx, [NARROW]: { gridTemplateColumns: 'minmax(0,1fr)' } } as const;
  const switchButton = { textTransform: 'none', px: 1.5, py: 0.4, fontSize: 12 } as const;
  /** 3_1 as a switch Air / Liquid / Underwater (`cooling_medium`) under the chosen load: a medium the load cannot run in
   *  is disabled (an air propeller never runs under water), each medium shows its own conditions (+ the airflow the load
   *  still blows over a liquid-cooled housing) and the airflow menu offers only what the load makes. The other media's
   *  values stay stored. Unset, the medium the stored values imply is shown, written only on a click. */
  const renderCooling = (f: SpecificationField, loadType?: string) => {
    const prop = (id: string) => f.properties?.find((p) => p.id === id);
    const mediumProp = prop('cooling_medium');
    if (!mediumProp) return renderField(f, false, false);
    const answer = storedAnswer(f, false), unknown = answer.status === 'not_known_yet', obj = objectOf(answer.value), required = fieldRequired(f, draftRef.current), airflow = objectOf(obj.airflow);
    const { medium, inferred } = coolingMedium(obj);
    const fits = coolingChoices(loadType), misfit = medium != null && !fits.includes(medium), problems = coolingLoadProblems(obj, loadType), loadGroupId = loadGroup(loadType);
    const mediumLabel = (m: CoolingMedium) => label(mediumProp.options?.map((o) => typeof o === 'string' ? { value: o, label: o } : o).find((o) => o.value === m)?.label) || m;
    const pick = (next: CoolingMedium | null) => {
      const target = next ?? (inferred ? medium : null);
      if (!target || target === obj.cooling_medium) return;
      const mode = wizardNaturalAirflow(loadType, naturalAirflow(target, loadType));
      setAnswer(f, { ...obj, cooling_medium: target, ...(mode && blankValue(airflow.mode) ? { airflow: { ...airflow, mode } } : {}) });
    };
    // what the switch cannot show stays readable in its tooltip: the customer's own cooling description and a stored
    // coolant text the shown form hides (Astra review 2026-10-25: a circuit description, a sea-water coolant)
    const hiddenText = [obj.cooling_type, medium && !coolingPropertyIds(medium, loadType).includes('coolant') ? obj.coolant : null]
      .filter((t): t is string => typeof t === 'string' && !!t.trim());
    const switchTip = [...(inferred ? [tr('coolingInferred')] : []), ...hiddenText];
    const cell = (p: NonNullable<SpecificationField['properties']>[number] & { excluded?: Record<string, string> }, value: unknown, update: (next: unknown) => void) => <Box key={p.id} sx={{ minWidth: 0 }}>{propLabel({ ...p, required: propertyRequired(p, obj) })}{renderTypedValue(p, value, update, `${f.id}-${p.id}`, unknown)}</Box>;
    // presets the load does not make are hidden (a turbine or a water propeller: no propeller wash, no ram air) unless already stored
    const hideModes = hiddenAirflowModes(loadType, airflow.mode);
    const modeOf = (s: SchemaSuggestion) => typeof s === 'object' ? String(s.value) : String(s);
    const airflowModeShape = (sub: NonNullable<SpecificationField['properties']>[number]) => ({
      ...sub,
      suggestions: [...(airflowSuggestions(sub.suggestions, loadType) ?? []), ...(sub.suggestions ?? []).filter((s) => airflowExcluded(loadType)[modeOf(s)])].filter((s) => !hideModes.includes(modeOf(s))),
      excluded: Object.fromEntries(Object.entries(airflowExcluded(loadType)).filter(([mode]) => !hideModes.includes(mode)).map(([mode, key]) => [mode, tr(key)])),
    });
    const shown = medium ? coolingPropertyIds(medium, loadType).map(prop).filter((p): p is NonNullable<typeof p> => !!p) : [];
    return <Box id={`spec-field-${f.id}`} key={`f-${f.id}`} sx={{ minWidth: 0 }}>
      {labelRow(f, false, required, unknown)}
      <Stack spacing={0.8}>
        <Tooltip title={switchTip.length ? <>{switchTip.map((t, i) => <Fragment key={i}>{i > 0 && <br />}{t}</Fragment>)}</> : ''} placement="top"><ToggleButtonGroup size="small" exclusive value={medium} disabled={!canEditSpec || busy || migrationBusy || unknown} onChange={(_, next: CoolingMedium | null) => pick(next)} aria-label={label(mediumProp.label)} sx={{ alignSelf: 'flex-start', flexWrap: 'wrap', '& .Mui-selected': inferred ? { borderStyle: 'dashed' } : {} }}>{COOLING_MEDIA.map((m) => <ToggleButton key={m} value={m} disabled={!fits.includes(m) && m !== medium} title={!fits.includes(m) ? tr(`coolingReason.${loadGroupId}.${m}`) : undefined} sx={{ ...switchButton, ...(misfit && m === medium ? { borderColor: 'error.main', color: 'error.main' } : {}) }}>{mediumLabel(m)}</ToggleButton>)}</ToggleButtonGroup></Tooltip>
        {problems.map((problem) => <Alert key={problem.field} severity="error" sx={{ py: 0 }}>{tr(problem.reason)}</Alert>)}
        {(shown.length > 0 || (!medium && !blankValue(obj.cooling_type))) && <Box sx={propertyGrid}>
          {shown.flatMap((p) => p.id === 'airflow' && p.properties?.length ? p.properties.map((sub) => cell(sub.id === 'mode' ? airflowModeShape(sub) : sub, airflow[sub.id], (value) => setProperty(f, false, obj, 'airflow', { ...airflow, [sub.id]: value }))) : [cell(p, obj[p.id], (value) => setProperty(f, false, obj, p.id, value))])}
          {/* the old free-text type only while the switch cannot tell the medium; otherwise it repeats the switch (owner 2026-10-25) */}
          {!medium && !blankValue(obj.cooling_type) && prop('cooling_type') && cell(prop('cooling_type')!, obj.cooling_type, (value) => setProperty(f, false, obj, 'cooling_type', value))}
        </Box>}
        {fieldMessages(f, answer, required)}
        {answerLater(f, unknown, false)}
      </Stack>
    </Box>;
  };
  /** 3_2 as ONE line: the maximum ambient temperature, the only thing asked (owner 2026-10-26; a stored minimum and altitude stay
   *  in the data, not on screen).  Shown for air cooling only - liquid cooling takes the coolant inlet temperature and underwater
   *  cooling the water temperature of 3_1 - and while no cooling is chosen (air is the default assumption). */
  const renderAmbient = (f: SpecificationField) => {
    if (!fieldApplies(f, draftRef.current)) return null;
    const prop = f.properties?.find((p) => p.id === 'temperature_max_C');
    if (!prop) return renderField(f, false, false);
    const answer = storedAnswer(f, false), unknown = answer.status === 'not_known_yet', obj = objectOf(answer.value), required = fieldRequired(f, draftRef.current);
    return <Box id={`spec-field-${f.id}`} key={`f-${f.id}`} sx={{ minWidth: 0 }}>
      {labelRow(f, false, required, unknown, undefined, [tr('wizard.ambientMaxNote')])}
      <Box sx={propertyGrid}><Box sx={{ minWidth: 0 }}>{renderTypedValue(prop, obj[prop.id], (value) => setProperty(f, false, obj, prop.id, value), `${f.id}-${prop.id}`, unknown)}</Box></Box>
      {fieldMessages(f, answer, required)}
      {answerLater(f, unknown, false)}
    </Box>;
  };
  /** 4_6 for the chosen load: the axial load first and required, the propeller or shaft loads, any other load only while it holds a value. */
  const renderLoads = (f: SpecificationField, loadType: string) => {
    const answer = storedAnswer(f, false), unknown = answer.status === 'not_known_yet', obj = objectOf(answer.value), required = fieldRequired(f, draftRef.current);
    const ids = loadPropertyIds(loadType, (f.properties ?? []).map((p) => p.id), obj);
    const shownIds = [...ids, ...extraLoadProps.filter((id) => !ids.includes(id))];
    const shown = shownIds.map((id) => f.properties!.find((p) => p.id === id)).filter((p): p is NonNullable<typeof p> => !!p);
    const addable = (f.properties ?? []).filter((p) => !shownIds.includes(p.id));
    return <Box id={`spec-field-${f.id}`} key={`f-${f.id}`} sx={{ minWidth: 0 }}>
      {labelRow(f, false, required, unknown)}
      <Box sx={propertyGrid}>{shown.map((p) => <Box key={p.id} sx={{ minWidth: 0 }}>{propLabel(p, p.id === 'axial_thrust_N')}{renderTypedValue(p, obj[p.id], (value) => setProperty(f, false, obj, p.id, value), `${f.id}-${p.id}`, unknown)}</Box>)}
        {addable.length > 0 && canEditSpec && !unknown && <Box sx={{ alignSelf: 'end' }}><Tooltip title={tr('addLoadComponent')}><IconButton size="small" aria-label={tr('addLoadComponent')} onClick={(e) => setAddLoadAnchor(e.currentTarget)}><AddIcon sx={{ fontSize: 18 }} /></IconButton></Tooltip>
          <Menu anchorEl={addLoadAnchor} open={!!addLoadAnchor} onClose={() => setAddLoadAnchor(null)}>{addable.map((p) => <MenuItem key={p.id} onClick={() => { setExtraLoadProps((list) => [...list, p.id]); setAddLoadAnchor(null); }}>{label(p.label)}</MenuItem>)}</Menu></Box>}
      </Box>
      {fieldMessages(f, answer, required)}
      {answerLater(f, unknown, false)}
    </Box>;
  };
  /** The load as a switch Air propeller / Water propeller / Wheel / Turbine / Shaft (a finer stored type — water-jet, pump,
   *  engine-driven generator, robot joint — sits under its button and is kept; it is named in the tooltip), changed
   *  through the load adapter with LoadSection's reset. A load also settles the cooling it cannot run with
   *  (`coolingForLoad`): an air propeller brings air + propeller wash, a water propeller underwater, a wheel ram air. */
  const renderLoadType = (cooling: SpecificationField | undefined) => {
    const typeField = sections.flatMap((section) => section.fields).find((f) => f.id === 'load_type');
    if (!typeField) return null;
    const load = readLoadDraft(draftRef.current), typeUnknown = load.answer_status?.load_type === 'not_known_yet';
    const group = loadGroup(load.load_type), finer = group && group !== load.load_type ? tr(`load.types.${load.load_type}`) : '';
    const choose = (next: LoadType | null) => {
      if (!next) return;
      updateDraft((d) => writeLoadDraft(d, withLoadType(readLoadDraft(d), next)));
      if (cooling && storedAnswer(cooling, false).status !== 'not_known_yet') {
        const before = storedAnswer(cooling, false).value as Record<string, unknown> | undefined;
        const storedMode = (before?.airflow as Record<string, unknown> | undefined)?.mode;
        const nextCooling = coolingForLoad(before, next, load.load_type);
        // a turbine / water load starts at still air, not at the ram air or propeller wash the shared matrix would bring
        const nextMode = (nextCooling?.airflow as Record<string, unknown> | undefined)?.mode;
        const fixed = nextCooling && typeof nextMode === 'string' && hiddenAirflowModes(next, storedMode).includes(nextMode) ? { ...nextCooling, airflow: { ...(nextCooling.airflow as object), mode: 'Still air' } } : nextCooling;
        if (fixed) setAnswer(cooling, fixed);
      }
    };
    return <Box id="spec-field-load_type" sx={{ minWidth: 0 }}>
      {labelRow(typeField, false, fieldRequired(typeField, draftRef.current), typeUnknown)}
      {/* ONE line: short visible names (the full name and a finer stored type are in the tooltip); on a narrow screen the row scrolls inside itself */}
      <Box sx={{ overflowX: 'auto', maxWidth: '100%' }}>
        <ToggleButtonGroup size="small" exclusive value={group ?? (load.load_type === 'unknown' ? null : load.load_type)} disabled={!canEditSpec || busy || migrationBusy || typeUnknown} onChange={(_, next: LoadType | null) => choose(next)} aria-label={label(typeField.label)} sx={{ flexWrap: 'nowrap', width: 'max-content' }}>{loadSwitchChoices(load.load_type).map((type) => <ToggleButton key={type} value={type} title={[tr(`load.types.${type}`), type === group && finer ? finer : ''].filter(Boolean).join(' · ')} sx={{ ...switchButton, px: 1, flex: '0 0 auto', whiteSpace: 'nowrap' }}>{tr(`load.short.${type}`, { defaultValue: tr(`load.types.${type}`) })}</ToggleButton>)}</ToggleButtonGroup>
      </Box>
      {answerLater(typeField, typeUnknown, false)}
    </Box>;
  };
  return { label, sectionTitle, setAnswer, markUnknown, setProperty, storedAnswer, objectOf, labelRow, propLabel, fieldMessages, answerLater, renderTypedValue, renderField, renderBattery, renderCooling, renderAmbient, renderLoads, renderLoadType, renderMission, commentIcon, openCount };
}
