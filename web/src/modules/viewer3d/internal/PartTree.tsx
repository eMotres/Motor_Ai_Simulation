import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

export interface PartTreeProps {
  nodes: Array<{ key: string; label: string; colour?: string; sceneNames?: string[]; children?: PartTreeProps['nodes']; defaultExpanded?: boolean; defaultVisible?: boolean; present?: boolean }>;
  hiddenKeys: ReadonlySet<string>;
  onToggle: (key: string) => void;
  selectedKey?: string | null;
  onSelect?: (key: string) => void;
  title?: string;
  style?: React.CSSProperties;
  rowsMaxHeight?: number | string;
  defaultCollapsed?: boolean;
}

const PartTree: React.FC<PartTreeProps> = ({ nodes, hiddenKeys, onToggle, selectedKey, onSelect, title, style, rowsMaxHeight = 'calc(100vh - 120px)', defaultCollapsed = false }) => {
  const { t } = useTranslation('geometry');
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const render = (rows: PartTreeProps['nodes'], depth: number, concealedByParent = false): React.ReactNode => rows.map((node) => {
    const children = node.children ?? [];
    const hasChildren = children.length > 0;
    const open = expanded[node.key] ?? !!node.defaultExpanded;
    const visible = !concealedByParent && !hiddenKeys.has(node.key);
    const absent = node.present === false;
    return <React.Fragment key={node.key}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, minHeight: 25, padding: '2px 6px 2px 4px', paddingLeft: 4 + depth * 9,
        opacity: visible ? 1 : 0.48, background: selectedKey === node.key ? 'rgba(96,165,250,.10)' : undefined }}>
        {hasChildren ? <button type="button" aria-label={t(open ? 'assemblyCollapse' : 'assemblyExpand')} onClick={() => setExpanded((s) => ({ ...s, [node.key]: !open }))}
          style={{ width: 13, padding: 0, border: 0, background: 'none', color: 'var(--text-4)', fontSize: 9, cursor: 'pointer' }}>{open ? '▼' : '▶'}</button> : <span style={{ width: 13 }} />}
        {node.colour && <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 2, background: node.colour, flex: '0 0 auto' }} />}
        {onSelect && !absent ? <button type="button" onClick={() => onSelect(node.key)} style={{ flex: 1, minWidth: 0, padding: 0, border: 0, textAlign: 'left', color: 'var(--text-1)', background: 'none', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'pointer' }}>{node.label}</button>
          : <span style={{ flex: 1, minWidth: 0, color: 'var(--text-1)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.label}</span>}
        <button type="button" disabled={absent} aria-label={t(absent ? 'assemblyAbsent' : visible ? 'assemblyHide' : 'assemblyShow')} aria-pressed={visible} onClick={() => onToggle(node.key)}
          style={{ width: 20, height: 20, display:'grid', placeItems:'center', padding: 0, border: 0, background: 'none', color: visible ? 'var(--text-2)' : 'var(--text-4)', cursor: absent ? 'default' : 'pointer', opacity: absent ? .35 : 1 }}>
          {visible ? <EyeOn /> : <EyeOff />}
        </button>
      </div>
      {hasChildren && open && render(children, depth + 1, concealedByParent || hiddenKeys.has(node.key))}
    </React.Fragment>;
  });
  return <div data-testid="assembly-tree" style={{ position: 'absolute', top: 8, left: 8, zIndex: 4, width: 280, maxWidth: 'calc(100% - 16px)', color: 'var(--text-2)', background: 'var(--overlay)', backdropFilter: 'blur(8px)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 11, userSelect: 'none', boxShadow: '0 4px 16px rgba(0,0,0,.5)', ...style }}>
    <button type="button" aria-expanded={!collapsed} onClick={() => setCollapsed((s) => !s)} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 5, padding: '5px 8px', border: 0, borderBottom: collapsed ? 0 : '1px solid var(--line)', color: 'var(--text-0)', background: 'none', textAlign: 'left', cursor: 'pointer' }}>
      <span style={{ fontSize: 9, color: 'var(--text-4)' }}>{collapsed ? '▶' : '▼'}</span><span style={{ fontWeight: 600 }}>{title ?? t('assemblyTitle')}</span>
    </button>
    {!collapsed && <div style={{ padding: '3px 0 4px', maxHeight: rowsMaxHeight, overflowY: 'auto' }}>{render(nodes, 0)}</div>}
  </div>;
};

const EyeOn = () => <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>;
const EyeOff = () => <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>;

export default PartTree;
