/** Types and validation for user-composed system graphs (stage 2). */

export interface PortDef {
  port: string;
  type: string;
}

export interface CapabilityDef {
  capability: string;
  name: string;
  category: string;
  title: string;
  summary: string;
  inputs: PortDef[];
  outputs: PortDef[];
  defaultParams?: Record<string, unknown>;
}

export interface ComposeNodeData extends Record<string, unknown> {
  id: string;
  title: string;
  capability: string;
  summary: string;
  inputs: PortDef[];
  outputs: PortDef[];
  parameters: Record<string, unknown>;
  status: {
    state: 'done' | 'running' | 'error' | 'cached' | 'unknown';
    time_s?: number | null;
    summary?: string;
    result?: unknown;
  };
  onEditParams?: (nodeId: string) => void;
  onDeleteNode?: (nodeId: string) => void;
}

export interface SavedGraphItem {
  name: string;
  title: string;
  summary?: string;
  updated_at?: string | null;
  node_count: number;
  edge_count: number;
  is_template?: boolean;
}

export interface WireValidationResult {
  valid: boolean;
  reason?: string;
}

/** Check whether connecting source port to target port is permitted by type contracts. */
export function validateWire(
  srcNode: { id: string; data: ComposeNodeData },
  srcPort: string | null | undefined,
  dstNode: { id: string; data: ComposeNodeData },
  dstPort: string | null | undefined,
  currentEdges: Array<{ source: string; target: string; data?: { feedback?: boolean } }>
): WireValidationResult {
  if (srcNode.id === dstNode.id) {
    return { valid: false, reason: 'Self-connections are not permitted.' };
  }

  // Look up source output port
  const outPort = srcNode.data.outputs.find((p) => p.port === srcPort) ?? srcNode.data.outputs[0];
  const inPort = dstNode.data.inputs.find((p) => p.port === dstPort) ?? dstNode.data.inputs[0];

  if (!outPort) {
    return { valid: false, reason: `Source node '${srcNode.data.title}' has no matching output port.` };
  }
  if (!inPort) {
    return { valid: false, reason: `Target node '${dstNode.data.title}' has no matching input port.` };
  }

  if (outPort.type !== inPort.type) {
    return {
      valid: false,
      reason: `Type mismatch: output '${outPort.port}' produces '${outPort.type}' but input '${inPort.port}' expects '${inPort.type}'.`,
    };
  }

  // Cycle check: would adding srcNode -> dstNode introduce an undeclared cycle?
  const adj: Record<string, string[]> = {};
  for (const e of currentEdges) {
    if (e.data?.feedback) continue; // skip declared feedback
    (adj[e.source] ||= []).push(e.target);
  }
  (adj[srcNode.id] ||= []).push(dstNode.id);

  // Check if dstNode can reach srcNode (which would create a cycle)
  const visited = new Set<string>();
  const queue = [dstNode.id];
  while (queue.length > 0) {
    const curr = queue.shift()!;
    if (curr === srcNode.id) {
      return {
        valid: false,
        reason: `Cycle detected: connecting '${srcNode.data.title}' to '${dstNode.data.title}' creates an undeclared loop.`,
      };
    }
    if (!visited.has(curr)) {
      visited.add(curr);
      for (const next of adj[curr] ?? []) {
        if (!visited.has(next)) queue.push(next);
      }
    }
  }

  return { valid: true };
}

/** Topological sort of nodes for client-side pipeline verification. */
export function clientTopologicalSort(
  nodeIds: string[],
  edges: Array<{ source: string; target: string; data?: { feedback?: boolean } }>
): { order: string[]; cycle?: string } {
  const inDegree: Record<string, number> = {};
  const adj: Record<string, string[]> = {};

  for (const id of nodeIds) {
    inDegree[id] = 0;
    adj[id] = [];
  }

  for (const e of edges) {
    if (e.data?.feedback) continue;
    if (adj[e.source] && inDegree[e.target] !== undefined) {
      adj[e.source].push(e.target);
      inDegree[e.target] += 1;
    }
  }

  const queue = nodeIds.filter((id) => inDegree[id] === 0);
  const order: string[] = [];

  while (queue.length > 0) {
    const curr = queue.shift()!;
    order.push(curr);
    for (const next of adj[curr] ?? []) {
      inDegree[next] -= 1;
      if (inDegree[next] === 0) {
        queue.push(next);
      }
    }
  }

  if (order.length !== nodeIds.length) {
    return { order, cycle: 'Cycle detected in dependency chain.' };
  }

  return { order };
}

/** Built-in capability catalog definitions with contract schemas and default parameters. */
export const BUILTIN_CAPABILITIES: CapabilityDef[] = [
  // Propeller / S1 System Chain
  {
    capability: 'propeller',
    name: 'propeller',
    category: 'Load / Aero',
    title: 'Propeller Load',
    summary: 'Propeller thrust, torque and wash speed evaluation',
    inputs: [
      { port: 'rpm', type: 'rpm' },
      { port: 'air_density', type: 'air_density' },
    ],
    outputs: [
      { port: 'torque', type: 'torque' },
      { port: 'air_speed', type: 'air_speed' },
    ],
    defaultParams: { diameter_in: 10.0, pitch_in: 4.5, rpm: 6000.0 },
  },
  {
    capability: 'motor.passport',
    name: 'motor-passport',
    category: 'Motor',
    title: 'Motor Passport',
    summary: 'Motor passport lookup (current, flux, voltage)',
    inputs: [{ port: 'torque', type: 'torque' }],
    outputs: [
      { port: 'current', type: 'current' },
      { port: 'passport_grid', type: 'passport_grid' },
    ],
    defaultParams: { die: 'CIANO14 40 new', config: 'L12' },
  },
  {
    capability: 'losses',
    name: 'losses',
    category: 'Motor',
    title: 'Motor Losses',
    summary: 'Copper, iron, eddy and mechanical losses calculation',
    inputs: [
      { port: 'current', type: 'current' },
      { port: 'temperature', type: 'temperature' },
    ],
    outputs: [{ port: 'loss_W', type: 'loss_W' }],
    defaultParams: {},
  },
  {
    capability: 'thermal',
    name: 'thermal',
    category: 'Thermal',
    title: 'Thermal Network',
    summary: 'Lumped thermal network predicting temperatures',
    inputs: [
      { port: 'loss_W', type: 'loss_W' },
      { port: 'air_speed', type: 'air_speed' },
    ],
    outputs: [{ port: 'temperature', type: 'temperature' }],
    defaultParams: { ambient_C: 25.0 },
  },
  {
    capability: 's1_limit',
    name: 's1-limit',
    category: 'Rating / Limits',
    title: 'S1 Limit Check',
    summary: 'Evaluate continuous S1 thermal and demag rating',
    inputs: [{ port: 'temperature', type: 'temperature' }],
    outputs: [{ port: 'rating', type: 's1_rating' }],
    defaultParams: { winding_limit_C: 180.0, magnet_limit_C: 150.0 },
  },

  // Core Engineering Capabilities
  {
    capability: 'geometry.2d',
    name: 'geometry-2d-aerostator',
    category: 'Geometry',
    title: 'Geometry 2D',
    summary: '2D cross-section CadQuery motor geometry builder',
    inputs: [{ port: 'params', type: 'ParameterSet' }],
    outputs: [{ port: 'geometry', type: 'GeometryIR' }],
    defaultParams: { stator_diameter: 30.0, slot_height: 5.0, motor_length: 10.0 },
  },
  {
    capability: 'mesh',
    name: 'mesh-skfem',
    category: 'Mesh',
    title: 'Mesh 2D (skfem)',
    summary: 'Triangular mesh generator with sliding band',
    inputs: [{ port: 'geometry', type: 'GeometryIR' }],
    outputs: [{ port: 'mesh', type: 'MeshIR' }],
    defaultParams: { mesh_size: 1.0 },
  },
  {
    capability: 'solver.em_static',
    name: 'solver-em-static',
    category: 'Solvers',
    title: 'EM Static Solver',
    summary: 'Nonlinear magnetostatics P2 solve for flux and torque',
    inputs: [
      { port: 'mesh', type: 'MeshIR' },
      { port: 'excitation', type: 'Excitation' },
    ],
    outputs: [{ port: 'result', type: 'ResultIR' }],
    defaultParams: { I_phase: 20.0, gamma_deg: 0.0 },
  },
  {
    capability: 'solver.em_transient',
    name: 'solver-em-transient',
    category: 'Solvers',
    title: 'EM Transient Solver',
    summary: 'Time-stepping sliding band transient solve with eddy losses',
    inputs: [
      { port: 'mesh', type: 'MeshIR' },
      { port: 'excitation', type: 'Excitation' },
    ],
    outputs: [
      { port: 'result', type: 'ResultIR' },
      { port: 'state', type: 'MachineState' },
    ],
    defaultParams: { rpm: 6000.0, I_phase: 20.0, gamma_deg: 0.0 },
  },
  {
    capability: 'solver.thermal',
    name: 'solver-thermal',
    category: 'Solvers',
    title: 'Thermal Field Solver',
    summary: 'Steady-state heat conduction solve on motor mesh',
    inputs: [
      { port: 'mesh', type: 'MeshIR' },
      { port: 'losses', type: 'ResultIR' },
    ],
    outputs: [{ port: 'result', type: 'ResultIR' }],
    defaultParams: { ambient_C: 25.0 },
  },
  {
    capability: 'controller',
    name: 'controller-foc',
    category: 'Controller',
    title: 'FOC Controller',
    summary: 'Field-oriented controller and PWM voltage inverter',
    inputs: [{ port: 'state', type: 'MachineState' }],
    outputs: [{ port: 'control', type: 'ControlSignal' }],
    defaultParams: { v_dc: 24.0, f_pwm: 20000.0 },
  },
  {
    capability: 'cost',
    name: 'cost-basic',
    category: 'Cost',
    title: 'Basic Cost Model',
    summary: 'BOM mass and manufacturing cost breakdown',
    inputs: [{ port: 'geometry', type: 'GeometryIR' }],
    outputs: [{ port: 'cost', type: 'CostIR' }],
    defaultParams: { batch_size: 100 },
  },
  {
    capability: 'transmission.gearbox_single_stage',
    name: 'transmission-gearbox-single-stage',
    category: 'Transmission',
    title: 'Gearbox Single Stage',
    summary: 'Gear reduction ratio and transmission efficiency',
    inputs: [{ port: 'motor', type: 'MotorPerformanceIR' }],
    outputs: [{ port: 'transmission', type: 'TransmissionIR' }],
    defaultParams: { gear_ratio: 4.0, efficiency: 0.95 },
  },
];
