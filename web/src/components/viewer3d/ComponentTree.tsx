/**
 * ComponentTree — the 3-D viewport's binding of the shared tree.
 *
 * The LOOK lives in `ComponentTreeView` (user 2026-09-06: "используй то же
 * самое дерево, которое у нас уже есть, чтобы всё было универсально" — the
 * field viewer now drives the same view with its own model).  This file is only
 * the wiring to the global `useUIStore`: which rows the machine has, what each
 * eye toggles, what "isolate" and "Show All" mean here.  Rows are listed in the
 * order they always were, so the 3-D tab is unchanged down to the connectors.
 */
import React, { useMemo } from 'react';
import { useUIStore, useMotorStore, type CompKey } from '../../stores/motorStore';
import { useMotorMesh } from './ApiMotorMesh';
import { PART_COLORS } from '../../lib/partColors';
<<<<<<< Updated upstream
import ComponentTreeView, { type TreeRow, type TreeModel } from './ComponentTreeView';
=======
import { usePartStates, PART_STATES, MAGNETICALLY_ACTIVE_PARTS,
         type PartState } from '../materials/usePartStates';

// ─── Per-part accounting badge (included / reference / excluded) ─────────────
// The tree is where this control has to live: an EXCLUDED part is invisible in
// the viewport, so it can never be clicked back — the tree row is the one place
// that still exists for it.  One tiny cycling chip, no prose: the tooltip
// carries the meaning.

const STATE_CHIP: Record<PartState, { text: string; fg: string; bg: string }> = {
  included:  { text: 'INC', fg: 'var(--text-4)', bg: 'rgba(71,85,105,0.25)' },
  reference: { text: 'REF', fg: '#38bdf8',       bg: 'rgba(56,189,248,0.18)' },
  excluded:  { text: 'OFF', fg: '#f59e0b',       bg: 'rgba(245,158,11,0.18)' },
};

const STATE_TIP: Record<PartState, string> = {
  included:  'Included — ours: in the field, the mass, the inertia and the datasheet. Click to cycle.',
  reference: 'Reference — customer-supplied: solved with its material (its losses are real and reported), '
           + 'but out of the mass, the rotor inertia and every N·m/kg. Click to cycle.',
  excluded:  'Excluded — solved as air (µr 1, σ 0), zero mass, not drawn anywhere. Click to cycle.',
};

const PartStateBadge: React.FC<{ part: string; hovered: boolean }> = ({ part, hovered }) => {
  const { stateOf, setState, saving } = usePartStates();
  const st = stateOf(part);
  const chip = STATE_CHIP[st];
  const next = PART_STATES[(PART_STATES.indexOf(st) + 1) % PART_STATES.length];
  const willBeRisky = next === 'excluded' && MAGNETICALLY_ACTIVE_PARTS.includes(part);
  return (
    <button
      onClick={e => { e.stopPropagation(); if (!saving) setState(part, next); }}
      title={STATE_TIP[st]
        + (st === 'excluded' && MAGNETICALLY_ACTIVE_PARTS.includes(part)
            ? ' ⚠ This part is magnetically active — the machine solved here is not the real one.'
            : willBeRisky
              ? ' ⚠ Next: excluded — this part is magnetically active, so removing it changes the magnetics, not just the accounting.'
              : '')}
      style={{
        background: chip.bg, border: 'none', borderRadius: 3, cursor: 'pointer',
        padding: '0 3px', marginLeft: 2, flexShrink: 0,
        color: chip.fg, fontSize: 8, lineHeight: 1.5, fontWeight: 700,
        letterSpacing: 0.5,
        // Out of the way while the part is plain `included` (the default must
        // add no clutter); always visible once the accounting is non-default,
        // because that is a fact about the machine the user must not lose.
        opacity: st === 'included' ? (hovered ? 0.7 : 0) : 1,
        transition: 'opacity 0.15s',
      }}
    >
      {chip.text}
    </button>
  );
};

// ─── Icons ───────────────────────────────────────────────────────────────────

const EyeOn = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

const EyeOff = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
    <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </svg>
);

const MotorIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="10" fill="#1e40af" />
    <circle cx="12" cy="12" r="6"  fill="var(--line-accent)" />
    <circle cx="12" cy="12" r="2"  fill="#60a5fa" />
  </svg>
);

// ─── Shared row styles ────────────────────────────────────────────────────────

const rowStyle = (hovered: boolean, selected: boolean, indent: number = 0): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  padding: `3px ${8}px 3px ${10 + indent}px`,
  gap: 5,
  background: selected
    ? 'rgba(59,130,246,0.22)'
    : hovered ? 'rgba(30,58,138,0.18)' : 'transparent',
  borderLeft: selected ? '2px solid #3b82f6' : '2px solid transparent',
  transition: 'background 0.1s',
  cursor: 'pointer',
});

// ─── Simple leaf row (Stator, Rotor, Shaft) ───────────────────────────────────

interface LeafRowProps {
  label: string;
  color: string;
  visible: boolean;
  isLast: boolean;
  indent?: number;
  selected?: boolean;
  /** Material-assignment part key, when this row carries an accounting state. */
  partKey?: string;
  onToggle: () => void;
  onSelect?: () => void;
  onIsolate?: () => void;
}

const LeafRow: React.FC<LeafRowProps> = ({ label, color, visible, isLast, indent = 0, selected = false, partKey, onToggle, onSelect, onIsolate }) => {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onSelect}
      style={rowStyle(hovered, selected, indent)}
    >
      <span style={{ color: 'var(--text-4)', fontSize: 10, flexShrink: 0 }}>
        {isLast ? '└' : '├'}
      </span>

      <button
        onClick={e => { e.stopPropagation(); onToggle(); }}
        title={visible ? 'Hide' : 'Show'}
        style={{
          background: 'none', border: 'none', cursor: 'pointer', padding: 0,
          display: 'flex', alignItems: 'center', flexShrink: 0,
          color: visible ? 'var(--text-3)' : 'var(--text-4)',
          transition: 'color 0.15s',
        }}
      >
        {visible ? <EyeOn /> : <EyeOff />}
      </button>

      <div style={{
        width: 8, height: 8, borderRadius: 2,
        background: color,
        opacity: visible ? 1 : 0.25,
        flexShrink: 0,
        transition: 'opacity 0.15s',
      }} />

      <span style={{
        flex: 1,
        color: visible ? 'var(--text-1)' : 'var(--text-4)',
        transition: 'color 0.15s',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        fontSize: 11,
      }}>
        {label}
      </span>

      {partKey && <PartStateBadge part={partKey} hovered={hovered} />}

      {onIsolate && (
        <button
          onClick={onIsolate}
          title="Isolate"
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            padding: '0 2px', flexShrink: 0,
            color: '#3b82f6',
            fontSize: 9, lineHeight: 1,
            opacity: hovered ? 0.8 : 0,
            transition: 'opacity 0.15s',
          }}
        >
          ◎
        </button>
      )}
    </div>
  );
};

// ─── Expandable group row ─────────────────────────────────────────────────────

interface GroupRowProps {
  label: string;
  color: string;
  groupVisible: boolean;
  expanded: boolean;
  isLast: boolean;
  childCount: number;
  hiddenChildCount: number;
  selected?: boolean;
  /** Material-assignment part key, when this row carries an accounting state. */
  partKey?: string;
  onToggleGroup: () => void;
  onToggleExpand: () => void;
  onSelect?: () => void;
  onIsolate: () => void;
  children: React.ReactNode;
}

const GroupRow: React.FC<GroupRowProps> = ({
  label, color, groupVisible, expanded, isLast,
  hiddenChildCount, selected = false, partKey, onToggleGroup, onToggleExpand, onSelect, onIsolate, children,
}) => {
  const [hovered, setHovered] = useState(false);

  return (
    <>
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={onSelect}
        style={rowStyle(hovered, selected)}
      >
        {/* Tree connector */}
        <span style={{ color: 'var(--text-4)', fontSize: 10, flexShrink: 0 }}>
          {isLast ? '└' : '├'}
        </span>

        {/* Expand / collapse arrow */}
        <button
          onClick={e => { e.stopPropagation(); onToggleExpand(); }}
          title={expanded ? 'Collapse' : 'Expand'}
          style={{
            background: 'none', border: 'none', cursor: 'pointer', padding: 0,
            color: 'var(--text-4)', fontSize: 8, lineHeight: 1, flexShrink: 0,
            display: 'flex', alignItems: 'center',
          }}
        >
          {expanded ? '▼' : '▶'}
        </button>

        {/* Eye toggle (group-level) */}
        <button
          onClick={e => { e.stopPropagation(); onToggleGroup(); }}
          title={groupVisible ? 'Hide all' : 'Show all'}
          style={{
            background: 'none', border: 'none', cursor: 'pointer', padding: 0,
            display: 'flex', alignItems: 'center', flexShrink: 0,
            color: groupVisible ? 'var(--text-3)' : 'var(--text-4)',
            transition: 'color 0.15s',
          }}
        >
          {groupVisible ? <EyeOn /> : <EyeOff />}
        </button>

        {/* Color dot */}
        <div style={{
          width: 8, height: 8, borderRadius: 2,
          background: color,
          opacity: groupVisible ? 1 : 0.25,
          flexShrink: 0,
          transition: 'opacity 0.15s',
        }} />

        {/* Label + hidden-count badge */}
        <span style={{
          flex: 1,
          color: groupVisible ? 'var(--text-1)' : 'var(--text-4)',
          transition: 'color 0.15s',
          fontSize: 11,
          display: 'flex', alignItems: 'center', gap: 4,
        }}>
          {label}
          {groupVisible && hiddenChildCount > 0 && (
            <span style={{
              fontSize: 8, color: 'var(--text-4)',
              background: 'rgba(71,85,105,0.25)',
              borderRadius: 3, padding: '0 3px',
            }}>
              {hiddenChildCount} hidden
            </span>
          )}
        </span>

        {partKey && <PartStateBadge part={partKey} hovered={hovered} />}

        {/* Isolate */}
        <button
          onClick={onIsolate}
          title="Isolate"
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            padding: '0 2px', flexShrink: 0,
            color: '#3b82f6', fontSize: 9, lineHeight: 1,
            opacity: hovered ? 0.8 : 0,
            transition: 'opacity 0.15s',
          }}
        >
          ◎
        </button>
      </div>

      {/* Children */}
      {expanded && groupVisible && (
        <div style={{ paddingLeft: 8 }}>
          {children}
        </div>
      )}
    </>
  );
};

// ─── ComponentTree ────────────────────────────────────────────────────────────
>>>>>>> Stashed changes

const ComponentTree: React.FC = () => {
  const {
    componentVisibility,
    coilVisibility,
    magnetVisibility,
    toggleComponentVisibility,
    toggleCoilVisibility,
    toggleMagnetVisibility,
    isolateComponent,
    showAllComponents,
    selectedPart,
    setSelectedPart,
  } = useUIStore();

  // Read mesh data to know how many coils / magnets exist
  const meshData = useMotorMesh();
  // The sleeve row keys on the GEOMETRY, not only on the CadQuery mesh: in the
  // flat / extruded viewer modes that mesh is never fetched, so the row was
  // missing there while the ring was drawn (user 2026-09-05: "не вижу sleeve
  // в дереве").  The geometry says whether the machine has one.
  const sleeveT = Number(useMotorStore(s => (s.geometry as Record<string, unknown> | null)?.sleeve_thickness ?? 0));
  const hasSleeve = sleeveT > 0 || !!(meshData && (meshData as Record<string, unknown>).sleeve);

  const coilCount = useMemo(() => {
    if (!meshData) return 0;
    return Object.keys(meshData).filter(k => k.startsWith('coil_')).length;
  }, [meshData]);

  const magnetCount = useMemo(() => {
    if (!meshData) return 0;
    return Object.keys(meshData).filter(k => k.startsWith('magnet_')).length;
  }, [meshData]);

  const hiddenCoils   = Object.values(coilVisibility).filter(v => !v).length;
  const hiddenMagnets = Object.values(magnetVisibility).filter(v => !v).length;

  const allVisible =
    componentVisibility.stator &&
    componentVisibility.rotor &&
    componentVisibility.magnets &&
    componentVisibility.coils &&
    componentVisibility.shaft &&
    hiddenCoils === 0 &&
    hiddenMagnets === 0;

  // Child keys are prefixed so one flat `onToggle(key)` can route them: the
  // model is a list of strings, not a union of component kinds.
  const model: TreeModel = useMemo(() => {
    const rows: TreeRow[] = [
      { key: 'stator', label: 'Stator Core', colour: PART_COLORS.statorIron,
        visible: componentVisibility.stator, partKey: 'stator_core' },
      { key: 'rotor', label: 'Rotor Core', colour: PART_COLORS.rotorIron,
        visible: componentVisibility.rotor, partKey: 'rotor_core' },
      { key: 'coils', label: 'Windings', colour: PART_COLORS.copper,
        visible: componentVisibility.coils, partKey: 'slot',
        group: Array.from({ length: coilCount }, (_, i) => ({
          key: `coil:${i}`, label: `Coil ${i + 1}`, colour: PART_COLORS.copper,
          visible: coilVisibility[i] ?? true,
        })) },
      { key: 'magnets', label: 'Magnets', colour: PART_COLORS.magnetN,
        visible: componentVisibility.magnets, partKey: 'magnet',
        group: Array.from({ length: magnetCount }, (_, i) => ({
          key: `magnet:${i}`,
          label: `Magnet ${i + 1}${i % 2 === 0 ? ' N' : ' S'}`,
          colour: i % 2 === 0 ? PART_COLORS.magnetN : PART_COLORS.magnetS,
          visible: magnetVisibility[i] ?? true,
        })) },
      { key: 'shaft', label: 'Shaft', colour: PART_COLORS.shaft,
        visible: componentVisibility.shaft, partKey: 'shaft' },
      // Retaining sleeve — only when the machine actually has one, so the row
      // cannot appear for a part that is not built (and the server drops the
      // mesh key for an EXCLUDED sleeve, which hides the row too).
      ...(hasSleeve ? [{ key: 'sleeve', label: 'Retaining sleeve',
        colour: PART_COLORS.sleeve, visible: componentVisibility.sleeve,
        partKey: 'sleeve' } as TreeRow] : []),
      { key: 'slot_insulation', label: 'Insulation', colour: PART_COLORS.slotLiner,
        visible: componentVisibility.slot_insulation },
      { key: 'wire_insulation', label: 'Wire enamel', colour: PART_COLORS.enamel,
        visible: componentVisibility.wire_insulation },
      // Sliding-band air rings: rotor-side, then stator-side.
      { key: 'in_band', label: 'In Band (rotating air)', colour: PART_COLORS.inBand,
        visible: componentVisibility.in_band },
      { key: 'out_band', label: 'Out Band (static air)', colour: PART_COLORS.outBand,
        visible: componentVisibility.out_band },
    ];
    return {
      rows,
      selected: selectedPart,
      allVisible,
      onToggle: (key) => {
        if (key.startsWith('coil:')) return toggleCoilVisibility(Number(key.slice(5)));
        if (key.startsWith('magnet:')) return toggleMagnetVisibility(Number(key.slice(7)));
        toggleComponentVisibility(key as CompKey);
      },
      onIsolate: (key) => isolateComponent(key as CompKey),
      onShowAll: showAllComponents,
      onSelect: (key) => setSelectedPart(selectedPart === key ? null : key as CompKey),
    };
  }, [componentVisibility, coilVisibility, magnetVisibility, coilCount,
      magnetCount, hasSleeve, allVisible, selectedPart, toggleComponentVisibility,
      toggleCoilVisibility, toggleMagnetVisibility, isolateComponent,
      showAllComponents, setSelectedPart]);

<<<<<<< Updated upstream
  return <ComponentTreeView model={model} />;
=======
      {/* ── Rows ── */}
      {!collapsed && (
        <div style={{ padding: '3px 0 4px', maxHeight: 'calc(100vh - 120px)', overflowY: 'auto' }}>

          {/* Stator Core */}
          <LeafRow
            label="Stator Core"
            color={PART_COLORS.statorIron}
            visible={componentVisibility.stator}
            isLast={false}
            partKey="stator_core"
            selected={selectedPart === 'stator'}
            onToggle={() => toggleComponentVisibility('stator')}
            onSelect={selectPart('stator')}
            onIsolate={() => isolateComponent('stator')}
          />

          {/* Rotor Core */}
          <LeafRow
            label="Rotor Core"
            color={PART_COLORS.rotorIron}
            visible={componentVisibility.rotor}
            isLast={false}
            partKey="rotor_core"
            selected={selectedPart === 'rotor'}
            onToggle={() => toggleComponentVisibility('rotor')}
            onSelect={selectPart('rotor')}
            onIsolate={() => isolateComponent('rotor')}
          />

          {/* Windings (expandable) */}
          <GroupRow
            label="Windings"
            color={PART_COLORS.copper}
            groupVisible={componentVisibility.coils}
            expanded={windingsExpanded}
            isLast={false}
            childCount={coilCount}
            hiddenChildCount={hiddenCoils}
            partKey="slot"
            selected={selectedPart === 'coils'}
            onToggleGroup={() => toggleComponentVisibility('coils')}
            onToggleExpand={() => setWindingsExpanded(e => !e)}
            onSelect={selectPart('coils')}
            onIsolate={() => isolateComponent('coils')}
          >
            {Array.from({ length: coilCount }, (_, i) => {
              const vis = coilVisibility[i] ?? true;
              return (
                <LeafRow
                  key={i}
                  label={`Coil ${i + 1}`}
                  color={PART_COLORS.copper}
                  visible={vis}
                  isLast={i === coilCount - 1}
                  indent={8}
                  onToggle={() => toggleCoilVisibility(i)}
                />
              );
            })}
          </GroupRow>

          {/* Magnets (expandable) */}
          <GroupRow
            label="Magnets"
            color={PART_COLORS.magnetN}
            groupVisible={componentVisibility.magnets}
            expanded={magnetsExpanded}
            isLast={false}
            childCount={magnetCount}
            hiddenChildCount={hiddenMagnets}
            partKey="magnet"
            selected={selectedPart === 'magnets'}
            onToggleGroup={() => toggleComponentVisibility('magnets')}
            onToggleExpand={() => setMagnetsExpanded(e => !e)}
            onSelect={selectPart('magnets')}
            onIsolate={() => isolateComponent('magnets')}
          >
            {Array.from({ length: magnetCount }, (_, i) => {
              const vis = magnetVisibility[i] ?? true;
              const isNorth = i % 2 === 0;
              return (
                <LeafRow
                  key={i}
                  label={`Magnet ${i + 1}${isNorth ? ' N' : ' S'}`}
                  color={isNorth ? PART_COLORS.magnetN : PART_COLORS.magnetS}
                  visible={vis}
                  isLast={i === magnetCount - 1}
                  indent={8}
                  onToggle={() => toggleMagnetVisibility(i)}
                />
              );
            })}
          </GroupRow>

          {/* Shaft */}
          <LeafRow
            label="Shaft"
            color={PART_COLORS.shaft}
            visible={componentVisibility.shaft}
            isLast={false}
            partKey="shaft"
            selected={selectedPart === 'shaft'}
            onToggle={() => toggleComponentVisibility('shaft')}
            onSelect={selectPart('shaft')}
            onIsolate={() => isolateComponent('shaft')}
          />

          {/* Retaining sleeve — only when the machine actually has one:
              the mesh payload carries a `sleeve` key exactly then, so the row
              cannot appear for a part that is not built (and the server drops
              the key for an EXCLUDED sleeve, which hides the row too). */}
          {meshData && (meshData as Record<string, unknown>).sleeve && (
            <LeafRow
              label="Retaining sleeve"
              color={PART_COLORS.sleeve}
              visible={componentVisibility.sleeve}
              isLast={false}
              partKey="sleeve"
              selected={selectedPart === 'sleeve'}
              onToggle={() => toggleComponentVisibility('sleeve')}
              onSelect={selectPart('sleeve')}
              onIsolate={() => isolateComponent('sleeve')}
            />
          )}

          {/* Slot liner (Nomex / ceramic) */}
          <LeafRow
            label="Slot liner"
            color={PART_COLORS.slotLiner}
            visible={componentVisibility.slot_insulation}
            isLast={false}
            selected={selectedPart === 'slot_insulation'}
            onToggle={() => toggleComponentVisibility('slot_insulation')}
            onSelect={selectPart('slot_insulation')}
            onIsolate={() => isolateComponent('slot_insulation')}
          />

          {/* Wire enamel (polyimide) */}
          <LeafRow
            label="Wire enamel"
            color={PART_COLORS.enamel}
            visible={componentVisibility.wire_insulation}
            isLast={false}
            selected={selectedPart === 'wire_insulation'}
            onToggle={() => toggleComponentVisibility('wire_insulation')}
            onSelect={selectPart('wire_insulation')}
            onIsolate={() => isolateComponent('wire_insulation')}
          />

          {/* Sliding-band: in_band (rotor-side air ring) */}
          <LeafRow
            label="In Band (rotating air)"
            color={PART_COLORS.inBand}
            visible={componentVisibility.in_band}
            isLast={false}
            selected={selectedPart === 'in_band'}
            onToggle={() => toggleComponentVisibility('in_band')}
            onSelect={selectPart('in_band')}
            onIsolate={() => isolateComponent('in_band')}
          />

          {/* Sliding-band: out_band (stator-side air ring) */}
          <LeafRow
            label="Out Band (static air)"
            color={PART_COLORS.outBand}
            visible={componentVisibility.out_band}
            isLast={true}
            selected={selectedPart === 'out_band'}
            onToggle={() => toggleComponentVisibility('out_band')}
            onSelect={selectPart('out_band')}
            onIsolate={() => isolateComponent('out_band')}
          />

        </div>
      )}
    </div>
  );
>>>>>>> Stashed changes
};

export default ComponentTree;
