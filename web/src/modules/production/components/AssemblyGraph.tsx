/**
 * Assembly Graph View (React Flow, top-down tree).
 * Hierarchy: Motor -> Sub-assemblies -> Parts.
 * Features:
 * - Each node shows name, quantity, and (when costs are visible) unit price / roll-up cost.
 * - Clicking a node selects the part, synchronized with table row selection.
 * - Collapsible sub-assemblies.
 * - Bilingual support (EN + zh-CN).
 */
import React, { useCallback, useMemo, useState } from 'react';
import {
  Box,
  Chip,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import ZoomInIcon from '@mui/icons-material/ZoomIn';
import ZoomOutIcon from '@mui/icons-material/ZoomOut';
import CenterFocusStrongIcon from '@mui/icons-material/CenterFocusStrong';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess';
import {
  Background,
  Handle,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';
import type { Node, Edge, NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from '@dagrejs/dagre';
import { useContentLang } from './BilingualText';
import { displayText } from '../helpers';
import type { AssemblyTree } from '../types';

const NODE_WIDTH = 190;
const NODE_HEIGHT = 68;

export interface GraphNodeData {
  id: string;
  label: string;
  kind: 'motor' | 'assembly' | 'made' | 'purchased' | 'consumable';
  qty?: number;
  cost?: number | null;
  currency?: string;
  hasCostAccess: boolean;
  isSelected: boolean;
  isCollapsed?: boolean;
  childrenCount?: number;
  onToggleCollapse?: (id: string) => void;
  onSelect: (id: string) => void;
  [key: string]: unknown;
}

// Custom Node for React Flow
const AssemblyTreeNode: React.FC<NodeProps<Node<GraphNodeData>>> = ({ data }) => {
  const {
    id,
    label,
    kind,
    qty,
    cost,
    currency = 'CNY',
    hasCostAccess,
    isSelected,
    isCollapsed,
    childrenCount = 0,
    onToggleCollapse,
    onSelect,
  } = data;

  const isMotor = kind === 'motor';
  const isAssembly = kind === 'assembly';

  const borderColor = isSelected
    ? '#38bdf8'
    : isMotor
    ? '#6366f1'
    : isAssembly
    ? '#8b5cf6'
    : 'var(--line-soft)';

  const bgColor = isSelected
    ? 'rgba(56, 189, 248, 0.16)'
    : isMotor
    ? 'rgba(99, 102, 241, 0.14)'
    : isAssembly
    ? 'rgba(139, 92, 246, 0.10)'
    : 'var(--panel)';

  return (
    <Box
      onClick={() => onSelect(id)}
      sx={{
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        p: 1,
        borderRadius: 1.5,
        bgcolor: bgColor,
        border: `1.5px solid ${borderColor}`,
        boxShadow: isSelected ? '0 0 10px rgba(56, 189, 248, 0.45)' : '0 1px 3px rgba(0,0,0,0.3)',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        position: 'relative',
        transition: 'all 0.15s ease',
        '&:hover': {
          borderColor: isSelected ? '#38bdf8' : 'var(--line-hover)',
          transform: 'translateY(-1px)',
        },
      }}
    >
      {!isMotor && (
        <Handle
          type="target"
          position={Position.Top}
          style={{ background: '#94a3b8', width: 6, height: 6, top: -4 }}
        />
      )}

      {/* Header Row */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 0.5 }}>
        <Typography
          noWrap
          title={label}
          sx={{
            fontSize: isMotor ? 13 : isAssembly ? 12 : 11.5,
            fontWeight: isMotor ? 800 : isAssembly ? 700 : 500,
            color: isMotor ? '#e0e7ff' : isAssembly ? '#f3e8ff' : 'var(--text-0)',
            flex: 1,
          }}
        >
          {label}
        </Typography>

        {isAssembly && childrenCount > 0 && onToggleCollapse && (
          <Tooltip title={isCollapsed ? 'Expand' : 'Collapse'}>
            <Box
              component="span"
              onClick={(e) => {
                e.stopPropagation();
                onToggleCollapse(id);
              }}
              sx={{
                fontSize: 10,
                px: 0.6,
                py: 0.1,
                borderRadius: 0.75,
                bgcolor: 'rgba(139, 92, 246, 0.25)',
                color: '#c084fc',
                fontWeight: 700,
                cursor: 'pointer',
                '&:hover': { bgcolor: 'rgba(139, 92, 246, 0.4)' },
              }}
            >
              {isCollapsed ? `+${childrenCount}` : '−'}
            </Box>
          </Tooltip>
        )}
      </Box>

      {/* Footer Row: Quantity & Cost */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 11, mt: 0.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          {qty !== undefined && qty > 0 && !isMotor && (
            <Chip
              size="small"
              label={`× ${qty}`}
              sx={{
                height: 17,
                fontSize: 10,
                fontWeight: 600,
                bgcolor: 'rgba(148, 163, 184, 0.15)',
                color: 'var(--text-2)',
              }}
            />
          )}
        </Box>

        {hasCostAccess && cost !== null && cost !== undefined && (
          <Typography
            sx={{
              fontSize: 11,
              fontWeight: 700,
              fontFamily: 'monospace',
              color: isMotor || isAssembly ? '#34d399' : 'var(--text-1)',
            }}
          >
            {cost.toFixed(2)} {currency}
          </Typography>
        )}
      </Box>

      {/* Bottom handle for nodes that have children */}
      {(isMotor || (isAssembly && !isCollapsed)) && (
        <Handle
          type="source"
          position={Position.Bottom}
          style={{ background: '#94a3b8', width: 6, height: 6, bottom: -4 }}
        />
      )}
    </Box>
  );
};

const nodeTypes = {
  assemblyNode: AssemblyTreeNode,
};

interface AssemblyGraphProps {
  tree: AssemblyTree;
  selectedPartId: string | null;
  onSelectPart: (partId: string) => void;
  hasCostAccess?: boolean;
  die?: string;
  config?: string;
}

const FlowInner: React.FC<AssemblyGraphProps> = ({
  tree,
  selectedPartId,
  onSelectPart,
  hasCostAccess = false,
  die,
  config,
}) => {
  const ui = useContentLang();
  const { fitView, zoomIn, zoomOut } = useReactFlow();

  const [collapsedSubs, setCollapsedSubs] = useState<Set<string>>(new Set());

  const handleToggleCollapse = useCallback((subId: string) => {
    setCollapsedSubs((prev) => {
      const next = new Set(prev);
      if (next.has(subId)) next.delete(subId);
      else next.add(subId);
      return next;
    });
  }, []);

  const handleExpandAll = () => setCollapsedSubs(new Set());
  const handleCollapseAll = () => {
    const all = new Set(tree.sub_assemblies.map((s) => s.node.id));
    setCollapsedSubs(all);
  };

  // Build visible DAG nodes and edges based on collapse state
  const { rawNodes, rawEdges } = useMemo(() => {
    const nodesList: Array<Node<GraphNodeData>> = [];
    const edgesList: Edge[] = [];

    const motorTitle = config ? `${die ?? ''} ${config}`.trim() : displayText(tree.name, ui).text;
    const motorCost = tree.sub_assemblies.reduce((sum, s) => sum + (s.node.unit_price ?? 0), 0);

    // Root motor node
    nodesList.push({
      id: 'motor',
      type: 'assemblyNode',
      position: { x: 0, y: 0 },
      data: {
        id: 'motor',
        label: motorTitle || 'Motor',
        kind: 'motor',
        cost: motorCost > 0 ? motorCost : null,
        hasCostAccess,
        isSelected: selectedPartId === 'motor',
        onSelect: () => onSelectPart('motor'),
      },
    });

    for (const sub of tree.sub_assemblies) {
      const subId = sub.node.id;
      const subName = displayText(sub.node.name, ui).text;
      const isCollapsed = collapsedSubs.has(subId);
      const isSelected = selectedPartId === subId;

      nodesList.push({
        id: subId,
        type: 'assemblyNode',
        position: { x: 0, y: 0 },
        data: {
          id: subId,
          label: subName,
          kind: 'assembly',
          qty: sub.node.qty_per_motor,
          cost: sub.node.unit_price,
          hasCostAccess,
          isSelected,
          isCollapsed,
          childrenCount: sub.children.length,
          onToggleCollapse: handleToggleCollapse,
          onSelect: () => onSelectPart(subId),
        },
      });

      edgesList.push({
        id: `e_motor_${subId}`,
        source: 'motor',
        target: subId,
        type: 'smoothstep',
        style: { stroke: isSelected ? '#38bdf8' : '#64748b', strokeWidth: isSelected ? 2 : 1.25 },
      });

      if (!isCollapsed) {
        for (const child of sub.children) {
          const childId = child.id;
          const childName = displayText(child.name, ui).text;
          const isChildSelected = selectedPartId === childId;

          nodesList.push({
            id: childId,
            type: 'assemblyNode',
            position: { x: 0, y: 0 },
            data: {
              id: childId,
              label: childName,
              kind: child.kind,
              qty: child.qty_per_motor,
              cost: child.unit_price,
              hasCostAccess,
              isSelected: isChildSelected,
              onSelect: () => onSelectPart(childId),
            },
          });

          edgesList.push({
            id: `e_${subId}_${childId}`,
            source: subId,
            target: childId,
            type: 'smoothstep',
            style: { stroke: isChildSelected ? '#38bdf8' : '#475569', strokeWidth: isChildSelected ? 2 : 1 },
          });
        }
      }
    }

    return { rawNodes: nodesList, rawEdges: edgesList };
  }, [tree, config, die, ui, hasCostAccess, selectedPartId, collapsedSubs, handleToggleCollapse, onSelectPart]);

  // Compute Dagre layout
  const { layoutedNodes, layoutedEdges } = useMemo(() => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: 'TB', nodesep: 25, ranksep: 45 });
    g.setDefaultEdgeLabel(() => ({}));

    for (const n of rawNodes) {
      g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }
    for (const e of rawEdges) {
      g.setEdge(e.source, e.target);
    }

    dagre.layout(g);

    const nodesWithPositions = rawNodes.map((node) => {
      const pos = g.node(node.id);
      return {
        ...node,
        position: {
          x: pos ? pos.x - NODE_WIDTH / 2 : 0,
          y: pos ? pos.y - NODE_HEIGHT / 2 : 0,
        },
      };
    });

    return { layoutedNodes: nodesWithPositions, layoutedEdges: rawEdges };
  }, [rawNodes, rawEdges]);

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutedEdges);

  // Sync state when layout changes
  React.useEffect(() => {
    setNodes(layoutedNodes);
    setEdges(layoutedEdges);
  }, [layoutedNodes, layoutedEdges, setNodes, setEdges]);

  React.useEffect(() => {
    // Initial fit view
    const timer = setTimeout(() => {
      fitView({ padding: 0.15, duration: 250 });
    }, 100);
    return () => clearTimeout(timer);
  }, [collapsedSubs, fitView]);

  return (
    <Box sx={{ width: '100%', height: 360, position: 'relative' }}>
      {/* Controls Overlay */}
      <Box
        sx={{
          position: 'absolute',
          top: 10,
          right: 12,
          zIndex: 5,
          display: 'flex',
          gap: 0.5,
          bgcolor: 'rgba(15, 23, 42, 0.75)',
          backdropFilter: 'blur(4px)',
          border: '1px solid var(--line-soft)',
          borderRadius: 1.5,
          p: 0.5,
        }}
      >
        <Tooltip title="Expand all">
          <IconButton size="small" onClick={handleExpandAll} sx={{ p: 0.5, color: '#cbd5e1' }}>
            <UnfoldMoreIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Collapse all">
          <IconButton size="small" onClick={handleCollapseAll} sx={{ p: 0.5, color: '#cbd5e1' }}>
            <UnfoldLessIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Zoom in">
          <IconButton size="small" onClick={() => zoomIn()} sx={{ p: 0.5, color: '#cbd5e1' }}>
            <ZoomInIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Zoom out">
          <IconButton size="small" onClick={() => zoomOut()} sx={{ p: 0.5, color: '#cbd5e1' }}>
            <ZoomOutIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Fit view">
          <IconButton size="small" onClick={() => fitView({ padding: 0.15, duration: 250 })} sx={{ p: 0.5, color: '#cbd5e1' }}>
            <CenterFocusStrongIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background color="#1e293b" gap={18} size={1} />
      </ReactFlow>
    </Box>
  );
};

export const AssemblyGraph: React.FC<AssemblyGraphProps> = (props) => {
  return (
    <ReactFlowProvider>
      <FlowInner {...props} />
    </ReactFlowProvider>
  );
};

export default AssemblyGraph;
