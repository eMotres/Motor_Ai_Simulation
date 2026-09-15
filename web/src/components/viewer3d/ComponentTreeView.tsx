/**
 * ComponentTreeView — the "Motor Assembly" tree, as a PURE VIEW over a model.
 *
 * User 2026-09-06, shown the 3-D tab's tree next to the field viewer's new Part
 * dropdown: "используй то же самое дерево, которое у нас уже есть, чтобы всё
 * было универсально".  So the tree stops being a piece of the 3-D viewer and
 * becomes the one part picker in the app: this file draws it, and each host
 * binds it to whatever it means by "a part" —
 *
 *   • `ComponentTree` (same folder)      → the global `useUIStore` visibility,
 *                                          pixel-identical to what the 3-D tab
 *                                          has always shown;
 *   • `common/FieldViewer`               → the parts actually present in the
 *                                          field output being drawn.
 *
 * Nothing here reaches for a store, a mesh or an output — a row is a label, a
 * colour and a visible flag, and the four callbacks are the whole vocabulary
 * (toggle / isolate / show-all / select).  That is what lets one look serve two
 * completely different notions of a part.
 */
import React, { useState } from 'react';
import { usePartStates, PART_STATES, MAGNETICALLY_ACTIVE_PARTS,
         type PartState } from '../materials/usePartStates';

/* ── the model ────────────────────────────────────────────────────────────── */

/** One line of the tree. */
export interface TreeRow {
  /** stable identity — what every callback is given back */
  key: string;
  label: string;
  /** the swatch; hosts read it from `lib/partColors` so the tree, the geometry
   *  and the material chips always agree on what colour a part is */
  colour: string;
  visible: boolean;
  /** false = the part exists in the machine but not in what is being shown
   *  right now (J is windings-only, Demag is magnets-only).  Drawn greyed and
   *  inert rather than dropped, so the tree does not reshuffle under the cursor
   *  every time the output changes. */
  present?: boolean;
  /** children (individual coils / magnets) — makes this an expandable group */
  group?: TreeRow[];
  /** material-accounting key: draws the INC / REF / OFF chip.  Only the 3-D
   *  binding sets it — a field picture has no accounting to change. */
  partKey?: string;
}

export interface TreeModel {
  rows: TreeRow[];
  /** highlighted row, or null */
  selected?: string | null;
  /** whether the header offers "Show All".  The BINDING decides what
   *  "everything visible" means — the 3-D tree deliberately ignores the
   *  analysis-only air bands and insulation layers, which default off. */
  allVisible: boolean;
  onToggle: (key: string) => void;
  onIsolate: (key: string) => void;
  onShowAll: () => void;
  onSelect?: (key: string) => void;
}

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
  /** absent from what is being shown — greyed and inert */
  absent?: boolean;
  /** Material-assignment part key, when this row carries an accounting state. */
  partKey?: string;
  onToggle: () => void;
  onSelect?: () => void;
  onIsolate?: () => void;
}

const LeafRow: React.FC<LeafRowProps> = ({ label, color, visible, isLast, indent = 0, selected = false, absent = false, partKey, onToggle, onSelect, onIsolate }) => {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={absent ? undefined : onSelect}
      title={absent ? 'Not drawn on this output' : undefined}
      style={{ ...rowStyle(hovered && !absent, selected, indent),
        ...(absent ? { opacity: 0.4, cursor: 'default' } : null) }}
    >
      <span style={{ color: 'var(--text-4)', fontSize: 10, flexShrink: 0 }}>
        {isLast ? '└' : '├'}
      </span>

      <button
        onClick={e => { e.stopPropagation(); if (!absent) onToggle(); }}
        title={absent ? 'Not drawn on this output' : visible ? 'Hide' : 'Show'}
        style={{
          background: 'none', border: 'none', cursor: absent ? 'default' : 'pointer', padding: 0,
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

      {onIsolate && !absent && (
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
  hiddenChildCount: number;
  selected?: boolean;
  absent?: boolean;
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
  hiddenChildCount, selected = false, absent = false, partKey, onToggleGroup, onToggleExpand, onSelect, onIsolate, children,
}) => {
  const [hovered, setHovered] = useState(false);

  return (
    <>
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={absent ? undefined : onSelect}
        title={absent ? 'Not drawn on this output' : undefined}
        style={{ ...rowStyle(hovered && !absent, selected),
          ...(absent ? { opacity: 0.4, cursor: 'default' } : null) }}
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
          onClick={e => { e.stopPropagation(); if (!absent) onToggleGroup(); }}
          title={absent ? 'Not drawn on this output' : groupVisible ? 'Hide all' : 'Show all'}
          style={{
            background: 'none', border: 'none', cursor: absent ? 'default' : 'pointer', padding: 0,
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
            opacity: hovered && !absent ? 0.8 : 0,
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

/* ── the panel ────────────────────────────────────────────────────────────── */

export interface ComponentTreeViewProps {
  model: TreeModel;
  /** header text — "Motor Assembly" everywhere so the two hosts read as one UI */
  title?: string;
  /** where the panel sits; the default is the 3-D viewport's top-left corner */
  style?: React.CSSProperties;
  /** cap on the scrolling row area (the 3-D tab has a full viewport, a field
   *  picture only has its canvas) */
  rowsMaxHeight?: number | string;
  defaultCollapsed?: boolean;
}

const ComponentTreeView: React.FC<ComponentTreeViewProps> = ({
  model, title = 'Motor Assembly', style, rowsMaxHeight = 'calc(100vh - 120px)',
  defaultCollapsed = false,
}) => {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  // Expansion is pure presentation, so it lives here and not in any model.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const { rows } = model;

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
        ...style,
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
          {title}
        </span>
        {!collapsed && !model.allVisible && (
          <button
            onClick={e => { e.stopPropagation(); model.onShowAll(); }}
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
        <div style={{ padding: '3px 0 4px', maxHeight: rowsMaxHeight, overflowY: 'auto' }}>
          {rows.map((row, i) => {
            const isLast = i === rows.length - 1;
            const absent = row.present === false;
            if (row.group) {
              return (
                <GroupRow
                  key={row.key}
                  label={row.label}
                  color={row.colour}
                  groupVisible={row.visible}
                  expanded={!!expanded[row.key]}
                  isLast={isLast}
                  hiddenChildCount={row.group.filter(c => !c.visible).length}
                  selected={model.selected === row.key}
                  absent={absent}
                  partKey={row.partKey}
                  onToggleGroup={() => model.onToggle(row.key)}
                  onToggleExpand={() => setExpanded(e => ({ ...e, [row.key]: !e[row.key] }))}
                  onSelect={model.onSelect ? () => model.onSelect!(row.key) : undefined}
                  onIsolate={() => model.onIsolate(row.key)}
                >
                  {row.group.map((child, j) => (
                    <LeafRow
                      key={child.key}
                      label={child.label}
                      color={child.colour}
                      visible={child.visible}
                      isLast={j === row.group!.length - 1}
                      indent={8}
                      absent={child.present === false}
                      onToggle={() => model.onToggle(child.key)}
                    />
                  ))}
                </GroupRow>
              );
            }
            return (
              <LeafRow
                key={row.key}
                label={row.label}
                color={row.colour}
                visible={row.visible}
                isLast={isLast}
                selected={model.selected === row.key}
                absent={absent}
                partKey={row.partKey}
                onToggle={() => model.onToggle(row.key)}
                onSelect={model.onSelect ? () => model.onSelect!(row.key) : undefined}
                onIsolate={() => model.onIsolate(row.key)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ComponentTreeView;
