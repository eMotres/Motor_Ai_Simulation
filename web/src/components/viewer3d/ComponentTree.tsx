import React, { useState } from 'react';
import { useUIStore, type CompKey } from '../../stores/motorStore';

// ─── Component definitions ────────────────────────────────────────────────────

const COMPONENTS: { key: CompKey; label: string; color: string }[] = [
  { key: 'stator',  label: 'Stator Core', color: '#7f8c8d' },
  { key: 'rotor',   label: 'Rotor Core',  color: '#5d6d7e' },
  { key: 'magnets', label: 'Magnets',     color: '#ef4444' },
  { key: 'coils',   label: 'Coils',       color: '#b87333' },
  { key: 'shaft',   label: 'Shaft',       color: '#374151' },
];

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
    <circle cx="12" cy="12" r="6"  fill="#1e3a5f" />
    <circle cx="12" cy="12" r="2"  fill="#60a5fa" />
  </svg>
);

// ─── ComponentTree ────────────────────────────────────────────────────────────

const ComponentTree: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [hoveredKey, setHoveredKey] = useState<CompKey | null>(null);

  const {
    componentVisibility,
    componentOpacity,
    toggleComponentVisibility,
    setComponentOpacity,
    isolateComponent,
    showAllComponents,
  } = useUIStore();

  const allVisible = COMPONENTS.every(c => componentVisibility[c.key]);

  return (
    <div
      style={{
        position: 'absolute',
        top: 48,
        left: 8,
        zIndex: 1100,
        width: 230,
        background: 'rgba(10, 17, 30, 0.88)',
        backdropFilter: 'blur(8px)',
        border: '1px solid rgba(51, 65, 85, 0.6)',
        borderRadius: 6,
        fontSize: 11,
        color: '#94a3b8',
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
          borderBottom: collapsed ? 'none' : '1px solid rgba(51,65,85,0.5)',
          cursor: 'pointer',
        }}
      >
        <span style={{ fontSize: 9, color: '#475569', lineHeight: 1 }}>
          {collapsed ? '▶' : '▼'}
        </span>
        <MotorIcon />
        <span style={{ flex: 1, fontWeight: 600, color: '#e2e8f0', fontSize: 11 }}>
          Motor Assembly
        </span>
        {!collapsed && !allVisible && (
          <button
            onClick={e => { e.stopPropagation(); showAllComponents(); }}
            style={{
              background: 'none',
              border: '1px solid rgba(51,65,85,0.6)',
              borderRadius: 3,
              color: '#64748b',
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
        <div style={{ padding: '3px 0 4px' }}>
          {COMPONENTS.map((comp, i) => {
            const visible  = componentVisibility[comp.key];
            const opacity  = componentOpacity[comp.key];
            const isLast   = i === COMPONENTS.length - 1;
            const hovered  = hoveredKey === comp.key;

            return (
              <div
                key={comp.key}
                onMouseEnter={() => setHoveredKey(comp.key)}
                onMouseLeave={() => setHoveredKey(null)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '3px 8px 3px 10px',
                  gap: 5,
                  background: hovered ? 'rgba(30,58,138,0.18)' : 'transparent',
                  transition: 'background 0.1s',
                }}
              >
                {/* Tree connector */}
                <span style={{ color: '#1e293b', fontSize: 10, flexShrink: 0 }}>
                  {isLast ? '└' : '├'}
                </span>

                {/* Eye toggle */}
                <button
                  onClick={() => toggleComponentVisibility(comp.key)}
                  title={visible ? 'Hide' : 'Show'}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                    display: 'flex', alignItems: 'center', flexShrink: 0,
                    color: visible ? '#64748b' : '#1e293b',
                    transition: 'color 0.15s',
                  }}
                >
                  {visible ? <EyeOn /> : <EyeOff />}
                </button>

                {/* Color dot */}
                <div style={{
                  width: 8, height: 8, borderRadius: 2,
                  background: comp.color,
                  opacity: visible ? 1 : 0.25,
                  flexShrink: 0,
                  transition: 'opacity 0.15s',
                }} />

                {/* Label */}
                <span style={{
                  flex: 1,
                  color: visible ? '#cbd5e1' : '#334155',
                  transition: 'color 0.15s',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {comp.label}
                </span>

                {/* Isolate button — visible on hover */}
                <button
                  onClick={() => isolateComponent(comp.key)}
                  title="Isolate (hide all others)"
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

                {/* Opacity slider */}
                <input
                  type="range"
                  min={0} max={1} step={0.05}
                  value={opacity}
                  onChange={e => setComponentOpacity(comp.key, parseFloat(e.target.value))}
                  title={`Opacity: ${Math.round(opacity * 100)}%`}
                  style={{
                    width: 50, height: 2, flexShrink: 0,
                    cursor: 'pointer', accentColor: '#3b82f6',
                    opacity: visible ? 1 : 0.3,
                  }}
                />

                {/* Opacity % */}
                <span style={{ width: 24, textAlign: 'right', color: '#475569', fontSize: 10, flexShrink: 0 }}>
                  {Math.round(opacity * 100)}%
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ComponentTree;
