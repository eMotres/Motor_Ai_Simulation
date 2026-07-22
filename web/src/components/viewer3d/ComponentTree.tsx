import React, { useState, useMemo } from 'react';
import { useUIStore, type CompKey } from '../../stores/motorStore';
import { useMotorMesh } from './ApiMotorMesh';
import { PART_COLORS } from '../../lib/partColors';

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
  onToggle: () => void;
  onSelect?: () => void;
  onIsolate?: () => void;
}

const LeafRow: React.FC<LeafRowProps> = ({ label, color, visible, isLast, indent = 0, selected = false, onToggle, onSelect, onIsolate }) => {
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
  onToggleGroup: () => void;
  onToggleExpand: () => void;
  onSelect?: () => void;
  onIsolate: () => void;
  children: React.ReactNode;
}

const GroupRow: React.FC<GroupRowProps> = ({
  label, color, groupVisible, expanded, isLast,
  hiddenChildCount, selected = false, onToggleGroup, onToggleExpand, onSelect, onIsolate, children,
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

const ComponentTree: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [windingsExpanded, setWindingsExpanded] = useState(false);
  const [magnetsExpanded, setMagnetsExpanded] = useState(false);

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

  const selectPart = (key: CompKey) => () => setSelectedPart(selectedPart === key ? null : key);

  // Read mesh data to know how many coils / magnets exist
  const meshData = useMotorMesh();

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

  return (
    <div
      style={{
        position: 'absolute',
        top: 48,
        left: 8,
        zIndex: 1100,
        width: 220,
        background: 'var(--overlay)',
        backdropFilter: 'blur(8px)',
        border: '1px solid var(--line)',
        borderRadius: 6,
        fontSize: 11,
        color: 'var(--text-2)',
        userSelect: 'none',
        boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
      }}
    >
      {/* ── Header ── */}
      <div
        onClick={() => setCollapsed(c => !c)}
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          padding: '5px 8px',
          borderBottom: collapsed ? 'none' : '1px solid var(--line)',
          cursor: 'pointer',
        }}
      >
        <span style={{ fontSize: 9, color: 'var(--text-4)', lineHeight: 1 }}>
          {collapsed ? '▶' : '▼'}
        </span>
        <MotorIcon />
        <span style={{ flex: 1, fontWeight: 600, color: 'var(--text-0)', fontSize: 11 }}>
          Motor Assembly
        </span>
        {!collapsed && !allVisible && (
          <button
            onClick={e => { e.stopPropagation(); showAllComponents(); }}
            style={{
              background: 'none',
              border: '1px solid rgba(51,65,85,0.6)',
              borderRadius: 3,
              color: 'var(--text-3)',
              cursor: 'pointer',
              fontSize: 9,
              padding: '1px 5px',
              lineHeight: 1.4,
            }}
          >
            Show All
          </button>
        )}
      </div>

      {/* ── Rows ── */}
      {!collapsed && (
        <div style={{ padding: '3px 0 4px', maxHeight: 'calc(100vh - 120px)', overflowY: 'auto' }}>

          {/* Stator Core */}
          <LeafRow
            label="Stator Core"
            color={PART_COLORS.statorIron}
            visible={componentVisibility.stator}
            isLast={false}
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
            selected={selectedPart === 'shaft'}
            onToggle={() => toggleComponentVisibility('shaft')}
            onSelect={selectPart('shaft')}
            onIsolate={() => isolateComponent('shaft')}
          />

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
};

export default ComponentTree;
