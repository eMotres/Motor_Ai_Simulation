"""System Graph Stage 2 — editing, wiring validation, topological sorting, storage and study execution.

Allows engineers and admins to:
1. Compose: validate type-checked connections, detect cycles, manage block parameters.
2. Run: topological sort into ordered capability study, execute via kernel, attach per-block status.
3. Save/load: per-user workspace persistence with name validation (path escape prevention) and templates.
"""
from __future__ import annotations

import copy
import datetime
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from motor_ai_sim.json_store import atomic_write_json, read_json

log = logging.getLogger(__name__)

VERSION = "system-graph-2"


class WiringError(ValueError):
    """Raised when an edge/wire fails contract type validation."""


class CycleRefusalError(ValueError):
    """Raised when an undeclared cycle is detected in the graph."""


# ---------------------------------------------------------------------------
# Name Validation
# ---------------------------------------------------------------------------

def validate_graph_name(name: str) -> str:
    """Validate a graph name to prevent path escape and invalid filenames.

    Raises ValueError if invalid, otherwise returns the stripped name.
    """
    if not name or not isinstance(name, str):
        raise ValueError("graph name cannot be empty")
    n = name.strip()
    if not n:
        raise ValueError("graph name cannot be whitespace only")
    if len(n) > 80:
        raise ValueError("graph name cannot exceed 80 characters")
    if "/" in n or "\\" in n:
        raise ValueError("graph name cannot contain path separators ('/' or '\\')")
    if ".." in n:
        raise ValueError("graph name cannot contain '..'")
    if n.startswith("."):
        raise ValueError("graph name cannot start with a dot")
    if not re.match(r"^[A-Za-z0-9_\-\. ]+$", n):
        raise ValueError("graph name contains invalid characters (allowed: alphanumeric, spaces, _, -, .)")
    return n


# ---------------------------------------------------------------------------
# Template: Propeller Motor S1
# ---------------------------------------------------------------------------

TEMPLATE_PROPELLER_S1_NAME = "Propeller motor S1"


def get_propeller_s1_template() -> Dict[str, Any]:
    """Returns the pre-shipped 'Propeller motor S1' template graph.

    5-stage chain: propeller -> motor passport -> losses -> thermal -> S1 limit,
    with a declared thermal feedback loop (thermal temperature back to losses).
    """
    return {
        "version": VERSION,
        "name": TEMPLATE_PROPELLER_S1_NAME,
        "title": "Propeller motor S1",
        "summary": "Propeller -> motor passport -> losses -> thermal -> S1 limit",
        "is_template": True,
        "nodes": [
            {
                "id": "propeller",
                "capability": "propeller",
                "title": "Propeller load",
                "summary": "Thrust, torque and air speed from propeller operating point",
                "group": "propeller",
                "inputs": [
                    {"port": "rpm", "type": "rpm"},
                    {"port": "air_density", "type": "air_density"},
                ],
                "outputs": [
                    {"port": "torque", "type": "torque"},
                    {"port": "air_speed", "type": "air_speed"},
                ],
                "parameters": {"diameter_in": 10.0, "pitch_in": 4.5, "rpm": 6000.0},
                "layout": {"col": 1, "row": 0, "x": 60, "y": 140},
                "status": {"state": "unknown"},
            },
            {
                "id": "motor_passport",
                "capability": "motor.passport",
                "title": "Motor passport",
                "summary": "Passport torque and loss map evaluation for demanded torque",
                "group": "motor",
                "inputs": [
                    {"port": "torque", "type": "torque"},
                ],
                "outputs": [
                    {"port": "current", "type": "current"},
                    {"port": "passport_grid", "type": "passport_grid"},
                ],
                "parameters": {"die": "CIANO14 40 new", "config": "L12"},
                "layout": {"col": 2, "row": 0, "x": 280, "y": 140},
                "status": {"state": "unknown"},
            },
            {
                "id": "losses",
                "capability": "losses",
                "title": "Losses",
                "summary": "Copper, iron, eddy and mechanical losses at operating temperature",
                "group": "motor",
                "inputs": [
                    {"port": "current", "type": "current"},
                    {"port": "temperature", "type": "temperature"},
                ],
                "outputs": [
                    {"port": "loss_W", "type": "loss_W"},
                ],
                "parameters": {},
                "layout": {"col": 3, "row": 0, "x": 500, "y": 140},
                "status": {"state": "unknown"},
            },
            {
                "id": "thermal",
                "capability": "thermal",
                "title": "Thermal network",
                "summary": "Lumped thermal network predicting winding, magnet and housing temperatures",
                "group": "thermal",
                "inputs": [
                    {"port": "loss_W", "type": "loss_W"},
                    {"port": "air_speed", "type": "air_speed"},
                ],
                "outputs": [
                    {"port": "temperature", "type": "temperature"},
                ],
                "parameters": {"ambient_C": 25.0},
                "layout": {"col": 4, "row": 0, "x": 720, "y": 140},
                "status": {"state": "unknown"},
            },
            {
                "id": "s1_limit",
                "capability": "s1_limit",
                "title": "S1 limit check",
                "summary": "Thermal and demag limits check for continuous S1 rating",
                "group": "limit",
                "inputs": [
                    {"port": "temperature", "type": "temperature"},
                ],
                "outputs": [
                    {"port": "rating", "type": "s1_rating"},
                ],
                "parameters": {"winding_limit_C": 180.0, "magnet_limit_C": 150.0},
                "layout": {"col": 5, "row": 0, "x": 940, "y": 140},
                "status": {"state": "unknown"},
            },
        ],
        "edges": [
            {
                "id": "propeller.torque->motor_passport.torque",
                "from": {"node": "propeller", "port": "torque"},
                "to": {"node": "motor_passport", "port": "torque"},
                "type": "torque",
                "feedback": False,
            },
            {
                "id": "motor_passport.current->losses.current",
                "from": {"node": "motor_passport", "port": "current"},
                "to": {"node": "losses", "port": "current"},
                "type": "current",
                "feedback": False,
            },
            {
                "id": "losses.loss_W->thermal.loss_W",
                "from": {"node": "losses", "port": "loss_W"},
                "to": {"node": "thermal", "port": "loss_W"},
                "type": "loss_W",
                "feedback": False,
            },
            {
                "id": "propeller.air_speed->thermal.air_speed",
                "from": {"node": "propeller", "port": "air_speed"},
                "to": {"node": "thermal", "port": "air_speed"},
                "type": "air_speed",
                "feedback": False,
            },
            {
                "id": "thermal.temperature->s1_limit.temperature",
                "from": {"node": "thermal", "port": "temperature"},
                "to": {"node": "s1_limit", "port": "temperature"},
                "type": "temperature",
                "feedback": False,
            },
            {
                "id": "thermal.temperature->losses.temperature",
                "from": {"node": "thermal", "port": "temperature"},
                "to": {"node": "losses", "port": "temperature"},
                "type": "temperature",
                "feedback": True,
                "label": "R(T_w)",
            },
        ],
        "loops": [
            {
                "id": "thermal_feedback",
                "title": "Thermal feedback",
                "kind": "fixed_point",
                "nodes": ["losses", "thermal"],
                "edges": ["thermal.temperature->losses.temperature", "losses.loss_W->thermal.loss_W"],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Storage Layer: Per-User Workspace Files
# ---------------------------------------------------------------------------

def get_user_graphs_dir() -> Path:
    """Return the `system_graphs/` directory inside the current user's workspace root."""
    from motor_ai_sim.workspace import root as ws_root
    d = ws_root() / "system_graphs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_user_graphs() -> List[Dict[str, Any]]:
    """List all saved graphs in the current workspace, plus the built-in template."""
    d = get_user_graphs_dir()
    graphs: List[Dict[str, Any]] = []
    seen_names = set()

    for p in sorted(d.glob("*.json")):
        doc = read_json(p, {})
        if isinstance(doc, dict) and "name" in doc:
            name = str(doc["name"])
            seen_names.add(name)
            graphs.append({
                "name": name,
                "title": doc.get("title", name),
                "summary": doc.get("summary", ""),
                "updated_at": doc.get("updated_at"),
                "node_count": len(doc.get("nodes", [])),
                "edge_count": len(doc.get("edges", [])),
                "is_template": bool(doc.get("is_template", False)),
            })

    # Include template if not shadowed by a saved graph of the same name
    if TEMPLATE_PROPELLER_S1_NAME not in seen_names:
        tmpl = get_propeller_s1_template()
        graphs.insert(0, {
            "name": tmpl["name"],
            "title": tmpl["title"],
            "summary": tmpl["summary"],
            "updated_at": None,
            "node_count": len(tmpl["nodes"]),
            "edge_count": len(tmpl["edges"]),
            "is_template": True,
        })

    return graphs


def load_user_graph(name: str) -> Dict[str, Any]:
    """Load a graph by name from the user's workspace.

    Raises FileNotFoundError if the graph does not exist.
    """
    valid_name = validate_graph_name(name)
    p = get_user_graphs_dir() / f"{valid_name}.json"
    if p.is_file():
        doc = read_json(p, None)
        if isinstance(doc, dict):
            return doc
        raise FileNotFoundError(f"graph '{valid_name}' is corrupt or empty")

    if valid_name == TEMPLATE_PROPELLER_S1_NAME:
        return copy.deepcopy(get_propeller_s1_template())

    raise FileNotFoundError(f"graph '{valid_name}' not found")


def save_user_graph(name: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and save a graph to the user's workspace.

    Validates name, ensures type-checked wiring, and writes atomically.
    """
    valid_name = validate_graph_name(name)
    if not isinstance(doc, dict):
        raise ValueError("graph document must be a JSON object")

    nodes = doc.get("nodes", [])
    edges = doc.get("edges", [])
    loops = doc.get("loops", [])

    # Validate wiring and check for unallowed cycles
    validate_graph_wiring(nodes, edges)
    # Check topological order can be formed (cycles without declared loops raise CycleRefusalError)
    topological_sort(nodes, edges, loops)

    save_doc = copy.deepcopy(doc)
    save_doc["name"] = valid_name
    save_doc["version"] = VERSION
    save_doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    target_path = get_user_graphs_dir() / f"{valid_name}.json"
    atomic_write_json(target_path, save_doc)
    return save_doc


def delete_user_graph(name: str) -> bool:
    """Delete a graph from the user's workspace.

    Raises FileNotFoundError if the file does not exist on disk.
    """
    valid_name = validate_graph_name(name)
    target_path = get_user_graphs_dir() / f"{valid_name}.json"
    if not target_path.is_file():
        raise FileNotFoundError(f"graph '{valid_name}' not found")
    target_path.unlink()
    return True


# ---------------------------------------------------------------------------
# Type-Checked Wiring
# ---------------------------------------------------------------------------

def _port_type_map(ports: List[Any]) -> Dict[str, str]:
    """Build a mapping of port_name -> type from a list of port definitions."""
    res = {}
    for p in ports:
        if isinstance(p, dict):
            pname = p.get("port") or p.get("name") or p.get("type")
            ptype = p.get("type") or pname
            if pname:
                res[str(pname)] = str(ptype)
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            res[str(p[0])] = str(p[1])
        elif isinstance(p, str):
            res[p] = p
    return res


def validate_wire(nodes_by_id: Dict[str, Dict[str, Any]], edge: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate a single wire edge against the nodes' input and output contracts.

    Returns (is_valid, error_reason).
    """
    src_spec = edge.get("from") or {}
    dst_spec = edge.get("to") or {}
    src_id = src_spec.get("node")
    dst_id = dst_spec.get("node")

    if not src_id or not dst_id:
        return False, "edge must define both 'from.node' and 'to.node'"
    if src_id not in nodes_by_id:
        return False, f"source node '{src_id}' does not exist in the graph"
    if dst_id not in nodes_by_id:
        return False, f"target node '{dst_id}' does not exist in the graph"

    src_node = nodes_by_id[src_id]
    dst_node = nodes_by_id[dst_id]

    src_outs = _port_type_map(src_node.get("outputs", []))
    dst_ins = _port_type_map(dst_node.get("inputs", []))

    src_port = src_spec.get("port")
    dst_port = dst_spec.get("port")

    # If port specified, verify it exists on the node
    if src_port:
        if src_port not in src_outs:
            return False, f"node '{src_id}' has no output port '{src_port}'. Available: {list(src_outs.keys())}"
        src_type = src_outs[src_port]
    else:
        # Fall back to edge type if port omitted
        src_type = edge.get("type")
        if not src_type and src_outs:
            src_type = next(iter(src_outs.values()))

    if dst_port:
        if dst_port not in dst_ins:
            return False, f"node '{dst_id}' has no input port '{dst_port}'. Available: {list(dst_ins.keys())}"
        dst_type = dst_ins[dst_port]
    else:
        dst_type = edge.get("type")
        if not dst_type and dst_ins:
            dst_type = next(iter(dst_ins.values()))

    if not src_type or not dst_type:
        return False, f"cannot determine port types for wire '{src_id}.{src_port}' -> '{dst_id}.{dst_port}'"

    # Contract Type check
    if src_type != dst_type:
        return False, (
            f"Type mismatch: '{src_id}.{src_port or src_type}' outputs '{src_type}' "
            f"but '{dst_id}.{dst_port or dst_type}' expects '{dst_type}'"
        )

    return True, None


def validate_graph_wiring(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[str]:
    """Validate all wires in a graph. Raises WiringError on any mismatch."""
    nodes_by_id = {n["id"]: n for n in nodes if "id" in n}
    errors = []

    for e in edges:
        ok, reason = validate_wire(nodes_by_id, e)
        if not ok and reason:
            errors.append(reason)

    if errors:
        raise WiringError(f"Wiring validation failed with {len(errors)} error(s): " + "; ".join(errors))
    return errors


# ---------------------------------------------------------------------------
# Cycle Detection and Topological Sort
# ---------------------------------------------------------------------------

# Declared loop node pairs that stage 1 models as feedback loops
KNOWN_FEEDBACK_PAIRS = {
    ("solver-thermal", "solver-em-thermal"),
    ("solver-em-thermal", "solver-thermal"),
    ("solver-thermal", "solver-em-transient"),
    ("thermal", "current"),
    ("thermal", "voltage"),
    ("thermal", "losses"),
}


def _is_declared_feedback(u: str, v: str, edge: Dict[str, Any], loops: List[Dict[str, Any]]) -> bool:
    """Check if an edge from u to v is part of a declared feedback loop."""
    if edge.get("feedback") is True:
        return True

    eid = edge.get("id") or f"{u}->{v}"
    # Check if edge is explicitly listed in a declared loop
    for lp in loops:
        loop_nodes = set(lp.get("nodes", []))
        loop_edges = set(lp.get("edges", []))
        if eid in loop_edges or (u in loop_nodes and v in loop_nodes and lp.get("kind") in ("fixed_point", "search")):
            # If marked feedback in edge or if u and v are known feedback partner
            if edge.get("feedback") is True or (u, v) in KNOWN_FEEDBACK_PAIRS:
                return True

    if (u, v) in KNOWN_FEEDBACK_PAIRS:
        return True

    return False


def topological_sort(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                     loops: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Perform topological sort of the graph nodes into an ordered study sequence.

    Excludes declared feedback loops (which are resolved via fixed-point iterations).
    Refuses any undeclared cycles with CycleRefusalError.
    """
    declared_loops = loops or []
    node_ids = [n["id"] for n in nodes if "id" in n]
    nodes_by_id = {n["id"]: n for n in nodes if "id" in n}

    # Graph adjacency for feedforward edges
    adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}

    # Track feedforward edges
    feedforward_edges: List[Tuple[str, str]] = []
    for e in edges:
        src = (e.get("from") or {}).get("node")
        dst = (e.get("to") or {}).get("node")
        if not src or not dst or src not in adj or dst not in adj:
            continue

        if _is_declared_feedback(src, dst, e, declared_loops):
            continue

        adj[src].append(dst)
        in_degree[dst] += 1
        feedforward_edges.append((src, dst))

    # Cycle detection via DFS to produce clear error message if a cycle exists
    visited: Dict[str, int] = {nid: 0 for nid in node_ids}  # 0: unvisited, 1: visiting, 2: visited
    parent: Dict[str, Optional[str]] = {nid: None for nid in node_ids}
    cycle_path: List[str] = []

    def dfs(u: str) -> bool:
        visited[u] = 1
        for v in adj[u]:
            if visited[v] == 1:
                # Cycle found!
                cycle = [v, u]
                curr = u
                while curr != v and parent[curr] is not None:
                    curr = parent[curr]  # type: ignore
                    cycle.append(curr)
                cycle.reverse()
                cycle_path.extend(cycle)
                return True
            if visited[v] == 0:
                parent[v] = u
                if dfs(v):
                    return True
        visited[u] = 2
        return False

    for nid in node_ids:
        if visited[nid] == 0:
            if dfs(nid):
                cycle_str = " -> ".join(cycle_path) if cycle_path else f"{nid} cycle"
                raise CycleRefusalError(
                    f"Cycle detected: {cycle_str}. Cycles are refused unless declared as a feedback loop."
                )

    # Kahn's algorithm for deterministic topological order
    queue = [nid for nid in node_ids if in_degree[nid] == 0]
    ordered_ids: List[str] = []

    while queue:
        # Sort queue by original node order to ensure deterministic stability
        queue.sort(key=lambda nid: node_ids.index(nid))
        curr = queue.pop(0)
        ordered_ids.append(curr)

        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(ordered_ids) != len(node_ids):
        remaining = [nid for nid in node_ids if nid not in ordered_ids]
        raise CycleRefusalError(f"Cycle detected involving nodes: {remaining}")

    return [nodes_by_id[nid] for nid in ordered_ids]


# ---------------------------------------------------------------------------
# Graph Study Execution
# ---------------------------------------------------------------------------

def graph_to_study_steps(graph: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Convert a graph into an ordered list of (capability, payload) for Kernel.run_study.

    Topologically sorts nodes and maps node parameters into payload.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    loops = graph.get("loops", [])

    sorted_nodes = topological_sort(nodes, edges, loops)
    steps: List[Tuple[str, Dict[str, Any]]] = []

    for node in sorted_nodes:
        cap = node.get("capability") or node.get("id")
        params = dict(node.get("parameters") or {})
        steps.append((cap, params))

    return steps


def run_graph_study(graph: Dict[str, Any], kernel: Optional[Any] = None) -> Dict[str, Any]:
    """Execute a study from a graph document through the kernel.

    Performs topological sort, runs Kernel.run_study, and returns the graph
    with updated per-node status and timings.
    """
    import time
    if kernel is None:
        from motor_ai_sim.modules.kernel import Kernel
        kernel = Kernel()

    steps = graph_to_study_steps(graph)
    t0 = time.time()
    study_res = kernel.run_study(steps)
    total_time_s = round(time.time() - t0, 3)

    # Update graph nodes with execution status
    out_graph = copy.deepcopy(graph)
    steps_out = study_res.get("steps", {})

    for node in out_graph.get("nodes", []):
        cap = node.get("capability") or node.get("id")
        res = steps_out.get(cap)
        if res is not None:
            if res.get("ok"):
                node["status"] = {
                    "state": "done",
                    "time_s": total_time_s,
                    "summary": f"Completed via module {res.get('module', cap)}",
                    "result": res.get("result"),
                }
            else:
                node["status"] = {
                    "state": "error",
                    "time_s": total_time_s,
                    "summary": res.get("error", "execution failed"),
                }
        else:
            node["status"] = {"state": "unknown"}

    return {
        "ok": bool(study_res.get("ok")),
        "time_s": total_time_s,
        "graph": out_graph,
        "steps": steps_out,
    }
