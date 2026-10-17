/** System Composer (stage 2) — visual editor for assembling and running software simulation graphs.
 *
 * Allows engineers and admins to:
 * - Drag capability blocks from the palette onto the canvas
 * - Wire outputs to inputs with strict type-contract validation and cycle prevention
 * - Inspect and adjust block parameters
 * - Run the topological study pipeline via /api/kernel/study and view per-block timings and results
 * - Save, load, and manage custom system graphs in the user workspace alongside templates
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  MenuItem,
  Snackbar,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import SaveIcon from '@mui/icons-material/Save';
import SaveAsIcon from '@mui/icons-material/SaveAs';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react';
import type {
  Connection,
  Edge,
  EdgeChange,
  Node,
  NodeChange,
  ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTranslation } from 'react-i18next';

import { formatApiErrorDetail } from '../../lib/apiErrorDetail';
import { typeColor } from '../../lib/systemGraph';
import { ComposeBlockNode } from './ComposeBlockNode';
import SystemPalette from './SystemPalette';
import BlockParamsDialog from './BlockParamsDialog';
import { validateWire } from './systemComposeTypes';
import type { ComposeNodeData, SavedGraphItem } from './systemComposeTypes';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const NODE_TYPES = {
  composeBlock: ComposeBlockNode,
};

function sanitizeName(n: string): string {
  return n.trim();
}

function isValidGraphName(n: string): { valid: boolean; reason?: string } {
  const trimmed = n.trim();
  if (!trimmed) return { valid: false, reason: 'Graph name cannot be empty.' };
  if (trimmed.length > 80) return { valid: false, reason: 'Graph name cannot exceed 80 characters.' };
  if (trimmed.includes('/') || trimmed.includes('\\')) return { valid: false, reason: 'Name cannot contain slashes.' };
  if (trimmed.includes('..')) return { valid: false, reason: "Name cannot contain '..'." };
  if (trimmed.startsWith('.')) return { valid: false, reason: 'Name cannot start with a dot.' };
  if (!/^[A-Za-z0-9_\-\. ]+$/.test(trimmed)) {
    return { valid: false, reason: 'Name can only contain alphanumeric characters, spaces, dots, dashes, and underscores.' };
  }
  return { valid: true };
}

export default function SystemComposer() {
  const { t: tr } = useTranslation('common');

  // React Flow state
  const [nodes, setNodes] = useState<Node<ComposeNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const rfInstance = useRef<ReactFlowInstance<any, any> | null>(null);

  // Graph metadata state
  const [savedGraphs, setSavedGraphs] = useState<SavedGraphItem[]>([]);
  const [selectedGraphName, setSelectedGraphName] = useState<string>('Propeller motor S1');
  const [currentGraphTitle, setCurrentGraphTitle] = useState<string>('Propeller motor S1');
  const [currentGraphSummary, setCurrentGraphSummary] = useState<string>('');
  const [isTemplate, setIsTemplate] = useState<boolean>(true);

  // Execution & UI state
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [toastSeverity, setToastSeverity] = useState<'info' | 'success' | 'warning' | 'error'>('info');

  // Dialogs
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState('');
  const [saveAsTitle, setSaveAsTitle] = useState('');
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  // -------------------------------------------------------------------------
  // Fetch Saved Graphs List
  // -------------------------------------------------------------------------
  const fetchGraphList = useCallback(async () => {
    try {
      const res = await fetch(`${API.replace(/\/$/, '')}/api/system/graphs`, {
        cache: 'no-store',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(formatApiErrorDetail(data?.detail) || `HTTP ${res.status}`);
      }
      const list = (data.graphs ?? []) as SavedGraphItem[];
      setSavedGraphs(list);
      return list;
    } catch (e: unknown) {
      setErrorBanner(String((e as Error)?.message ?? e));
      return [];
    }
  }, []);

  // -------------------------------------------------------------------------
  // Load Graph by Name
  // -------------------------------------------------------------------------
  const loadGraph = useCallback(
    async (name: string) => {
      setLoading(true);
      setErrorBanner(null);
      try {
        const res = await fetch(`${API.replace(/\/$/, '')}/api/system/graphs/${encodeURIComponent(name)}`, {
          cache: 'no-store',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(formatApiErrorDetail(data?.detail) || `HTTP ${res.status}`);
        }
        const doc = data.graph;
        if (!doc) throw new Error('Invalid graph response from server');

        setSelectedGraphName(doc.name || name);
        setCurrentGraphTitle(doc.title || doc.name || name);
        setCurrentGraphSummary(doc.summary || '');
        setIsTemplate(Boolean(doc.is_template));

        // Transform backend nodes to React Flow nodes
        const loadedNodes: Node<ComposeNodeData>[] = (doc.nodes || []).map((n: Record<string, unknown>, idx: number) => {
          const layout = (n.layout as Record<string, number> | undefined) || {};
          const nodeId = String(n.id || `node_${idx}`);
          return {
            id: nodeId,
            type: 'composeBlock',
            position: { x: layout.x ?? 60 + idx * 240, y: layout.y ?? 120 },
            data: {
              id: nodeId,
              title: String(n.title || n.capability || nodeId),
              capability: String(n.capability || nodeId),
              summary: String(n.summary || ''),
              inputs: (n.inputs as ComposeNodeData['inputs']) || [],
              outputs: (n.outputs as ComposeNodeData['outputs']) || [],
              parameters: (n.parameters as Record<string, unknown>) || {},
              status: (n.status as ComposeNodeData['status']) || { state: 'unknown' },
            },
          };
        });

        // Transform backend edges to React Flow edges
        const loadedEdges: Edge[] = (doc.edges || []).map((e: Record<string, unknown>, idx: number) => {
          const srcSpec = (e.from as Record<string, string> | undefined) || {};
          const dstSpec = (e.to as Record<string, string> | undefined) || {};
          const source = srcSpec.node || String(e.source || '');
          const target = dstSpec.node || String(e.target || '');
          const sourceHandle = srcSpec.port || (e.sourceHandle as string | undefined);
          const targetHandle = dstSpec.port || (e.targetHandle as string | undefined);
          const edgeType = String(e.type || '');
          const color = typeColor(edgeType);
          const isFeedback = Boolean(e.feedback);

          return {
            id: String(e.id || `e_${idx}`),
            source,
            target,
            sourceHandle,
            targetHandle,
            type: 'smoothstep',
            animated: false,
            style: {
              stroke: color,
              strokeWidth: 2,
              strokeDasharray: isFeedback ? '5 4' : undefined,
            },
            markerEnd: {
              type: MarkerType.ArrowClosed,
              color,
              width: 14,
              height: 14,
            },
            data: {
              type: edgeType,
              feedback: isFeedback,
            },
            label: edgeType,
            labelStyle: { fill: color, fontWeight: 700, fontSize: 10 },
            labelBgStyle: { fill: '#ffffff', fillOpacity: 0.9, stroke: color, strokeWidth: 1, rx: 4, ry: 4 },
          };
        });

        setNodes(loadedNodes);
        setEdges(loadedEdges);
      } catch (e: unknown) {
        setErrorBanner(String((e as Error)?.message ?? e));
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Initial load
  useEffect(() => {
    fetchGraphList().then((list) => {
      if (list && list.length > 0) {
        loadGraph(list[0].name);
      }
    });
  }, [fetchGraphList, loadGraph]);

  // -------------------------------------------------------------------------
  // React Flow Handlers
  // -------------------------------------------------------------------------
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds) as Node<ComposeNodeData>[]);
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((eds) => applyEdgeChanges(changes, eds));
  }, []);

  // Node parameter editing
  const handleEditParams = useCallback((nodeId: string) => {
    setEditingNodeId(nodeId);
  }, []);

  const handleSaveParams = useCallback((nodeId: string, newParams: Record<string, unknown>) => {
    setNodes((nds) =>
      nds.map((n) => {
        if (n.id !== nodeId) return n;
        return {
          ...n,
          data: {
            ...n.data,
            parameters: newParams,
          },
        };
      })
    );
  }, []);

  // Node deletion
  const handleDeleteNode = useCallback((nodeId: string) => {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId));
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
  }, []);

  // Wire connection validation
  const onConnect = useCallback(
    (params: Connection) => {
      const srcNode = nodes.find((n) => n.id === params.source);
      const dstNode = nodes.find((n) => n.id === params.target);
      if (!srcNode || !dstNode) return;

      const validation = validateWire(
        srcNode,
        params.sourceHandle,
        dstNode,
        params.targetHandle,
        edges.map((e) => ({
          source: e.source,
          target: e.target,
          data: e.data as { feedback?: boolean } | undefined,
        }))
      );

      if (!validation.valid) {
        const reason = validation.reason || 'Invalid connection';
        setToastSeverity('error');
        setToastMessage(tr('system.wireRefused', { reason, defaultValue: `Wire refused: ${reason}` }));
        return;
      }

      // Find port type
      const outPort = srcNode.data.outputs.find((p) => p.port === params.sourceHandle) ?? srcNode.data.outputs[0];
      const edgeType = outPort?.type || '';
      const color = typeColor(edgeType);

      const newEdge: Edge = {
        id: `${params.source}.${params.sourceHandle || 'out'}->${params.target}.${params.targetHandle || 'in'}`,
        source: params.source,
        target: params.target,
        sourceHandle: params.sourceHandle,
        targetHandle: params.targetHandle,
        type: 'smoothstep',
        style: { stroke: color, strokeWidth: 2 },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
        data: { type: edgeType, feedback: false },
        label: edgeType,
        labelStyle: { fill: color, fontWeight: 700, fontSize: 10 },
        labelBgStyle: { fill: '#ffffff', fillOpacity: 0.9, stroke: color, strokeWidth: 1, rx: 4, ry: 4 },
      };

      setEdges((eds) => addEdge(newEdge, eds));
    },
    [nodes, edges, tr]
  );

  // Drag-and-drop capability block from palette onto canvas
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData('application/system-graph-block');
      if (!raw) return;

      try {
        const cap = JSON.parse(raw);
        if (!rfInstance.current) return;
        const position = rfInstance.current.screenToFlowPosition({
          x: e.clientX,
          y: e.clientY,
        });

        // Generate unique node ID
        const baseId = cap.capability.replace(/[^a-zA-Z0-9_]/g, '_');
        let nodeId = baseId;
        let counter = 1;
        while (nodes.some((n) => n.id === nodeId)) {
          nodeId = `${baseId}_${counter++}`;
        }

        const newNode: Node<ComposeNodeData> = {
          id: nodeId,
          type: 'composeBlock',
          position,
          data: {
            id: nodeId,
            title: cap.title || cap.name || nodeId,
            capability: cap.capability,
            summary: cap.summary || '',
            inputs: [...(cap.inputs || [])],
            outputs: [...(cap.outputs || [])],
            parameters: { ...(cap.defaultParams || {}) },
            status: { state: 'unknown' },
          },
        };

        setNodes((nds) => [...nds, newNode]);
      } catch {
        // invalid drop data
      }
    },
    [nodes]
  );

  // Inject callbacks into nodes data
  const liveNodes = useMemo(() => {
    return nodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        onEditParams: handleEditParams,
        onDeleteNode: handleDeleteNode,
      },
    }));
  }, [nodes, handleEditParams, handleDeleteNode]);

  // Construct current backend graph document
  const buildGraphDoc = useCallback(
    (name: string, title: string, summary: string) => {
      return {
        name: sanitizeName(name),
        title: title || name,
        summary: summary || '',
        nodes: nodes.map((n) => ({
          id: n.id,
          capability: n.data.capability,
          title: n.data.title,
          summary: n.data.summary,
          inputs: n.data.inputs,
          outputs: n.data.outputs,
          parameters: n.data.parameters,
          layout: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
          status: n.data.status,
        })),
        edges: edges.map((e) => ({
          id: e.id,
          from: { node: e.source, port: e.sourceHandle },
          to: { node: e.target, port: e.targetHandle },
          type: (e.data as { type?: string } | undefined)?.type || '',
          feedback: Boolean((e.data as { feedback?: boolean } | undefined)?.feedback),
        })),
      };
    },
    [nodes, edges]
  );

  // -------------------------------------------------------------------------
  // Run Study
  // -------------------------------------------------------------------------
  const handleRunStudy = async () => {
    if (nodes.length === 0) {
      setToastSeverity('warning');
      setToastMessage('The graph has no nodes to execute.');
      return;
    }

    setRunning(true);
    setErrorBanner(null);

    // Set all nodes to running status
    setNodes((nds) =>
      nds.map((n) => ({
        ...n,
        data: {
          ...n.data,
          status: { state: 'running' },
        },
      }))
    );

    try {
      const graphDoc = buildGraphDoc(selectedGraphName, currentGraphTitle, currentGraphSummary);
      const res = await fetch(`${API.replace(/\/$/, '')}/api/system/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph: graphDoc }),
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        const errMsg = formatApiErrorDetail(data?.detail) || `HTTP ${res.status}`;
        throw new Error(errMsg);
      }

      // Update node results
      const results = (data.node_results || {}) as Record<
        string,
        { state?: string; time_s?: number; summary?: string; result?: unknown }
      >;

      setNodes((nds) =>
        nds.map((n) => {
          const nr = results[n.id];
          if (!nr) return n;
          return {
            ...n,
            data: {
              ...n.data,
              status: {
                state: (nr.state as ComposeNodeData['status']['state']) || 'done',
                time_s: nr.time_s,
                summary: nr.summary,
                result: nr.result,
              },
            },
          };
        })
      );

      const timeStr = data.total_time_s != null ? `${Number(data.total_time_s).toFixed(2)}s` : '';
      setToastSeverity('success');
      setToastMessage(tr('system.studySuccess', { time: timeStr, defaultValue: `Study completed in ${timeStr}` }));
    } catch (e: unknown) {
      const msg = String((e as Error)?.message ?? e);
      setErrorBanner(msg);
      setToastSeverity('error');
      setToastMessage(tr('system.studyFailed', { error: msg, defaultValue: `Study failed: ${msg}` }));

      // Mark uncompleted nodes as error
      setNodes((nds) =>
        nds.map((n) =>
          n.data.status?.state === 'running'
            ? { ...n, data: { ...n.data, status: { state: 'error', summary: msg } } }
            : n
        )
      );
    } finally {
      setRunning(false);
    }
  };

  // -------------------------------------------------------------------------
  // Save / Save As / Delete
  // -------------------------------------------------------------------------
  const handleSave = async () => {
    if (isTemplate || !selectedGraphName) {
      setSaveAsName(selectedGraphName === 'Propeller motor S1' ? 'My Propeller S1' : selectedGraphName);
      setSaveAsTitle(currentGraphTitle);
      setSaveAsOpen(true);
      return;
    }

    try {
      const doc = buildGraphDoc(selectedGraphName, currentGraphTitle, currentGraphSummary);
      const res = await fetch(`${API.replace(/\/$/, '')}/api/system/graphs/${encodeURIComponent(selectedGraphName)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph: doc }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(formatApiErrorDetail(data?.detail) || `HTTP ${res.status}`);
      }

      setToastSeverity('success');
      setToastMessage(tr('system.graphSaved', { name: selectedGraphName, defaultValue: `Graph '${selectedGraphName}' saved.` }));
      fetchGraphList();
    } catch (e: unknown) {
      const msg = String((e as Error)?.message ?? e);
      setErrorBanner(msg);
      setToastSeverity('error');
      setToastMessage(msg);
    }
  };

  const handleConfirmSaveAs = async () => {
    const check = isValidGraphName(saveAsName);
    if (!check.valid) {
      setToastSeverity('error');
      setToastMessage(check.reason || 'Invalid graph name');
      return;
    }

    const newName = sanitizeName(saveAsName);
    const newTitle = saveAsTitle.trim() || newName;

    try {
      const doc = buildGraphDoc(newName, newTitle, currentGraphSummary);
      const res = await fetch(`${API.replace(/\/$/, '')}/api/system/graphs/${encodeURIComponent(newName)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph: doc }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(formatApiErrorDetail(data?.detail) || `HTTP ${res.status}`);
      }

      setSelectedGraphName(newName);
      setCurrentGraphTitle(newTitle);
      setIsTemplate(false);
      setSaveAsOpen(false);

      setToastSeverity('success');
      setToastMessage(tr('system.graphSaved', { name: newName, defaultValue: `Graph '${newName}' saved.` }));
      fetchGraphList();
    } catch (e: unknown) {
      const msg = String((e as Error)?.message ?? e);
      setErrorBanner(msg);
      setToastSeverity('error');
      setToastMessage(msg);
    }
  };

  const handleConfirmDelete = async () => {
    if (isTemplate || !selectedGraphName) return;

    try {
      const res = await fetch(`${API.replace(/\/$/, '')}/api/system/graphs/${encodeURIComponent(selectedGraphName)}`, {
        method: 'DELETE',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(formatApiErrorDetail(data?.detail) || `HTTP ${res.status}`);
      }

      setToastSeverity('success');
      setToastMessage(tr('system.graphDeleted', { name: selectedGraphName, defaultValue: `Graph '${selectedGraphName}' deleted.` }));
      setDeleteConfirmOpen(false);

      const list = await fetchGraphList();
      if (list && list.length > 0) {
        loadGraph(list[0].name);
      } else {
        handleClear();
      }
    } catch (e: unknown) {
      const msg = String((e as Error)?.message ?? e);
      setErrorBanner(msg);
      setToastSeverity('error');
      setToastMessage(msg);
    }
  };

  const handleClear = () => {
    setNodes([]);
    setEdges([]);
    setSelectedGraphName('');
    setCurrentGraphTitle('Untitled Graph');
    setCurrentGraphSummary('');
    setIsTemplate(false);
  };

  const activeEditingNode = useMemo(() => {
    return nodes.find((n) => n.id === editingNodeId)?.data || null;
  }, [nodes, editingNodeId]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, bgcolor: '#ffffff', color: '#111827' }}>
      {/* Top Toolbar */}
      <Stack
        direction="row"
        spacing={1.2}
        alignItems="center"
        sx={{
          px: 2,
          py: 1,
          borderBottom: '1px solid #e2e8f0',
          bgcolor: '#ffffff',
          flexWrap: 'wrap',
          rowGap: 1,
        }}
      >
        {/* Graph Selector */}
        <TextField
          select
          size="small"
          label={tr('system.selectGraph', { defaultValue: 'Select Graph' })}
          value={selectedGraphName}
          onChange={(e) => loadGraph(e.target.value)}
          sx={{ minWidth: 220, '& .MuiInputBase-root': { fontSize: 13, height: 34 } }}
        >
          {savedGraphs.map((g) => (
            <MenuItem key={g.name} value={g.name}>
              <Stack direction="row" spacing={0.8} alignItems="center" sx={{ width: '100%' }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {g.title || g.name}
                </Typography>
                {g.is_template && (
                  <Chip size="small" label={tr('system.template', { defaultValue: 'Template' })} sx={{ height: 16, fontSize: 9.5, bgcolor: '#e0e7ff', color: '#3730a3' }} />
                )}
              </Stack>
            </MenuItem>
          ))}
        </TextField>

        {/* Run Button */}
        <Button
          variant="contained"
          color="primary"
          size="small"
          startIcon={running ? <CircularProgress size={14} color="inherit" /> : <PlayArrowIcon />}
          disabled={running || nodes.length === 0}
          onClick={handleRunStudy}
          sx={{ height: 34, fontWeight: 700, px: 1.8 }}
        >
          {running ? tr('system.running', { defaultValue: 'Running...' }) : tr('system.run', { defaultValue: 'Run' })}
        </Button>

        {/* Save Button */}
        <Button
          variant="outlined"
          size="small"
          startIcon={<SaveIcon />}
          onClick={handleSave}
          disabled={running}
          sx={{ height: 34 }}
        >
          {tr('system.save', { defaultValue: 'Save' })}
        </Button>

        {/* Save As Button */}
        <Button
          variant="outlined"
          size="small"
          startIcon={<SaveAsIcon />}
          onClick={() => {
            setSaveAsName(selectedGraphName === 'Propeller motor S1' ? 'My Propeller S1' : `${selectedGraphName} copy`);
            setSaveAsTitle(currentGraphTitle);
            setSaveAsOpen(true);
          }}
          disabled={running}
          sx={{ height: 34 }}
        >
          {tr('system.saveAs', { defaultValue: 'Save As' })}
        </Button>

        {/* Delete Button */}
        {!isTemplate && selectedGraphName && (
          <Tooltip title={tr('system.deleteGraph', { defaultValue: 'Delete Graph' })}>
            <IconButton
              size="small"
              color="error"
              onClick={() => setDeleteConfirmOpen(true)}
              disabled={running}
              sx={{ border: '1px solid #fecaca', borderRadius: 1 }}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}

        {/* New Graph Button */}
        <Tooltip title={tr('system.newGraph', { defaultValue: 'New Graph' })}>
          <IconButton size="small" onClick={handleClear} disabled={running} sx={{ border: '1px solid #e2e8f0', borderRadius: 1 }}>
            <AddIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        {/* Refresh Button */}
        <Tooltip title={tr('system.refresh', { defaultValue: 'Reload' })}>
          <IconButton
            size="small"
            onClick={() => {
              fetchGraphList();
              if (selectedGraphName) loadGraph(selectedGraphName);
            }}
            disabled={running || loading}
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        <Box sx={{ flexGrow: 1 }} />

        {/* Graph Meta Info */}
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 600 }}>
            {nodes.length} {nodes.length === 1 ? 'block' : 'blocks'} · {edges.length} {edges.length === 1 ? 'wire' : 'wires'}
          </Typography>
          {loading && <CircularProgress size={16} />}
        </Stack>
      </Stack>

      {/* Error Alert Banner */}
      {errorBanner && (
        <Alert severity="error" onClose={() => setErrorBanner(null)} sx={{ mx: 2, mt: 1 }}>
          {errorBanner}
        </Alert>
      )}

      {/* Main Composer Area: Palette + React Flow Canvas */}
      <Box sx={{ flex: 1, minHeight: 0, display: 'flex', position: 'relative' }}>
        {/* Left Palette */}
        <SystemPalette />

        {/* React Flow Canvas */}
        <Box sx={{ flex: 1, height: '100%', position: 'relative' }} onDragOver={onDragOver} onDrop={onDrop}>
          <ReactFlow
            nodes={liveNodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onInit={(inst) => {
              rfInstance.current = inst;
            }}
            fitView
            minZoom={0.1}
            maxZoom={2}
            colorMode="light"
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} color="#e2e8f0" />
            <Controls />
            <MiniMap pannable zoomable nodeStrokeWidth={2} />
          </ReactFlow>
        </Box>
      </Box>

      {/* Block Parameters Dialog */}
      <BlockParamsDialog
        open={Boolean(editingNodeId)}
        node={activeEditingNode}
        onClose={() => setEditingNodeId(null)}
        onSave={handleSaveParams}
        onDeleteNode={handleDeleteNode}
      />

      {/* Save As Dialog */}
      <Dialog open={saveAsOpen} onClose={() => setSaveAsOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{tr('system.saveAs', { defaultValue: 'Save Graph As' })}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              size="small"
              label={tr('system.enterGraphName', { defaultValue: 'Graph Name (file name)' })}
              fullWidth
              value={saveAsName}
              onChange={(e) => setSaveAsName(e.target.value)}
              helperText="Allowed: alphanumeric, spaces, _, -, . (max 80 chars)"
            />
            <TextField
              size="small"
              label="Graph Title (display name)"
              fullWidth
              value={saveAsTitle}
              onChange={(e) => setSaveAsTitle(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveAsOpen(false)}>{tr('system.close', { defaultValue: 'Cancel' })}</Button>
          <Button variant="contained" onClick={handleConfirmSaveAs}>
            {tr('system.save', { defaultValue: 'Save' })}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteConfirmOpen} onClose={() => setDeleteConfirmOpen(false)}>
        <DialogTitle>{tr('system.deleteGraph', { defaultValue: 'Delete Graph' })}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {tr('system.deleteGraphConfirm', {
              name: selectedGraphName,
              defaultValue: `Are you sure you want to delete '${selectedGraphName}'?`,
            })}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteConfirmOpen(false)}>{tr('system.close', { defaultValue: 'Cancel' })}</Button>
          <Button variant="contained" color="error" onClick={handleConfirmDelete}>
            {tr('system.deleteGraph', { defaultValue: 'Delete' })}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Toast Notification Snackbar */}
      <Snackbar
        open={Boolean(toastMessage)}
        autoHideDuration={4500}
        onClose={() => setToastMessage(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={toastSeverity} onClose={() => setToastMessage(null)} sx={{ width: '100%', boxShadow: 3 }}>
          {toastMessage}
        </Alert>
      </Snackbar>
    </Box>
  );
}
