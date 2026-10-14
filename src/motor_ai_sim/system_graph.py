"""The "System" graph - how the simulations are chained (VIEW ONLY, stage 1).

Two views, one schema (``docs/SYSTEM_GRAPH.md``):

* ``modules``      - every registered portal module (``modules/registry``) as a node, the
                     data links its manifests declare as edges, the feedback groups the
                     code defines as loops;
* ``propeller_s1`` - the chain ``propeller_s1.solve_point`` / ``s1_rating`` actually runs
                     (propeller load -> current -> losses -> slipstream film -> lumped
                     thermal network -> limits -> speed search).

Nothing here computes a motor.  The structure is read from the manifests and written next
to the code it describes; every node carries ``source`` = {function, file, line}, resolved
with ``ast`` at request time so the line numbers follow the code (a function that is
renamed or removed is a loud test failure, not a stale hover).  Constants of the loops
(max iterations, tolerances) are read from the modules' source the same way, never typed in
twice.  The optional status layer (``die`` / ``config`` / ``duty``) only READS records that
already exist (the caller's job queue, the run history); it never starts a computation.
"""
from __future__ import annotations

import ast
import functools
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

VERSION = "system-graph-1"
VIEWS = ("modules", "propeller_s1")
STATES = ("done", "running", "error", "cached", "unknown")

_PKG = Path(__file__).resolve().parent                    # .../src/motor_ai_sim
_REPO = _PKG.parents[1]


# ---------------------------------------------------------------------------
# Source anchors: file:line resolved from the code itself
# ---------------------------------------------------------------------------

def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO).as_posix()
    except ValueError:
        return path.name


@functools.lru_cache(maxsize=None)
def _parse(rel_in_pkg: str) -> Tuple[ast.Module, Tuple[str, ...]]:
    text = (_PKG / rel_in_pkg).read_text(encoding="utf-8")
    return ast.parse(text), tuple(text.splitlines())


def _find_def(tree: ast.AST, name: str):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name:
            return n
    return None


def src(rel_in_pkg: str, function: str, anchor: Optional[str] = None) -> Dict[str, Any]:
    """``{function, file, line}`` of a def in ``src/motor_ai_sim/<rel_in_pkg>``.

    ``anchor``: a text searched for from the def's first line to its end; the node then
    points at the line of the step INSIDE the function (e.g. where ``q_em`` is formed).
    A def that does not exist raises: the graph must not describe code that is gone."""
    tree, lines = _parse(rel_in_pkg)
    d = _find_def(tree, function)
    if d is None:
        raise LookupError(f"{rel_in_pkg}: no function/class {function!r}")
    line = int(d.lineno)
    if anchor:
        end = int(getattr(d, "end_lineno", line))
        for i in range(line - 1, min(end, len(lines))):
            if anchor in lines[i]:
                line = i + 1
                break
        else:
            raise LookupError(f"{rel_in_pkg}:{function}: anchor {anchor!r} not found")
    return {"function": function, "file": _rel(_PKG / rel_in_pkg), "line": line}


def const(rel_in_pkg: str, name: str) -> Tuple[Any, int]:
    """``(value, line)`` of a module-level literal constant, read from the source."""
    tree, _ = _parse(rel_in_pkg)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
                return ast.literal_eval(n.value), int(n.lineno)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name and n.value:
            return ast.literal_eval(n.value), int(n.lineno)
    raise LookupError(f"{rel_in_pkg}: no constant {name!r}")


def default_arg(rel_in_pkg: str, function: str, arg: str) -> Any:
    """The literal default of a keyword argument (e.g. ``solve_coupled(max_iter=12)``)."""
    tree, _ = _parse(rel_in_pkg)
    d = _find_def(tree, function)
    if d is None:
        raise LookupError(f"{rel_in_pkg}: no function {function!r}")
    a = d.args
    pos = a.posonlyargs + a.args
    for p, dv in zip(pos[len(pos) - len(a.defaults):], a.defaults):
        if p.arg == arg:
            return ast.literal_eval(dv)
    for p, dv in zip(a.kwonlyargs, a.kw_defaults):
        if p.arg == arg and dv is not None:
            return ast.literal_eval(dv)
    raise LookupError(f"{rel_in_pkg}:{function}: no literal default for {arg!r}")


# ---------------------------------------------------------------------------
# Small constructors (the schema in one place)
# ---------------------------------------------------------------------------

def _port(name: str, typ: str) -> Dict[str, str]:
    return {"port": name, "type": typ}


def _node(id_: str, kind: str, title: str, summary: str, *, ins=(), outs=(), source=None,
          group: Optional[str] = None, context: bool = False, at: Optional[Tuple[float, float]] = None,
          **extra: Any) -> Dict[str, Any]:
    """``context``: a parameter source feeding many blocks - the view draws it in an Inputs column and puts chips on
    its consumers instead of wires.  ``at`` = (col, row): the layout hint (the main chain on row 0)."""
    n: Dict[str, Any] = {"id": id_, "kind": kind, "title": title, "summary": summary,
                         "group": group or kind, "context": bool(context),
                         "layout": ({"col": at[0], "row": at[1]} if at else None),
                         "inputs": [_port(*p) for p in ins], "outputs": [_port(*p) for p in outs],
                         "source": source, "status": {"state": "unknown"}}
    n.update(extra)
    return n


def _edge(src_node: str, src_port: Optional[str], dst_node: str, dst_port: Optional[str], typ: str,
          *, label: Optional[str] = None, feedback: bool = False, via: str = "data", minor: bool = False,
          lane: bool = False) -> Dict[str, Any]:
    """``minor``: a parameter-like link (drawn as a chip on the target unless all wires are shown); ``lane``: routed
    under the loop group like a feedback wire instead of across the blocks."""
    e: Dict[str, Any] = {
        "id": f"{src_node}.{src_port or '*'}->{dst_node}.{dst_port or '*'}",
        "from": {"node": src_node, "port": src_port}, "to": {"node": dst_node, "port": dst_port},
        "type": typ, "feedback": bool(feedback), "via": via, "minor": bool(minor),
        "route": "lane" if lane else "direct"}
    if label:
        e["label"] = label
    return e


def _doc(view: str, title: str, nodes, edges, loops, notes=()) -> Dict[str, Any]:
    ids = [n["id"] for n in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate node ids in the system graph")
    eids = [e["id"] for e in edges]
    if len(eids) != len(set(eids)):
        raise ValueError("duplicate edge ids in the system graph")
    return {"version": VERSION, "view": view, "title": title, "nodes": list(nodes),
            "edges": list(edges), "loops": list(loops), "notes": list(notes),
            "status_context": None}


# ---------------------------------------------------------------------------
# View 1: all modules
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _registry():
    from motor_ai_sim.modules.bootstrap import default_registry
    return default_registry()                      # cheap: registers providers, runs no geometry


def _module_source(module: Any) -> Optional[Dict[str, Any]]:
    import inspect
    try:
        cls = type(module)
        file = Path(inspect.getsourcefile(cls) or "")
        line = inspect.getsourcelines(cls)[1]
        return {"function": cls.__name__, "file": _rel(file), "line": int(line)}
    except Exception:                               # noqa: BLE001 - a plugin without source
        return None


def build_modules_graph() -> Dict[str, Any]:
    reg = _registry()
    mans = [(m, reg.get(m.name)) for m in reg.manifests()]
    by_name = {m.name: m for m, _ in mans}
    nodes = []
    for m, mod in mans:
        title = (m.ui.title if m.ui else None) or m.name
        nodes.append(_node(
            m.name, m.kind, title, m.summary, group=m.capability.split(".")[0],
            ins=[(t, t) for t in m.inputs], outs=[(t, t) for t in m.outputs],
            source=_module_source(mod), capability=m.capability, version=m.version,
            trust=m.status, roadmap=type(mod).__name__ == "StubModule"))

    def providers_of(cap: str):
        # an extension (geometry.2d.ring_spm) is an alternative provider of its base (geometry.2d)
        return [m for m, _ in mans if m.capability == cap or m.capability.startswith(cap + ".")]

    edges: List[Dict[str, Any]] = []
    seen = set()

    def add(a, b, typ, via, label=None, a_port=None, b_port=None):
        e = _edge(a, a_port, b, b_port, typ, via=via, label=label)
        if e["id"] not in seen:
            seen.add(e["id"])
            edges.append(e)

    satisfied: Dict[str, set] = {m.name: set() for m, _ in mans}
    for m, _ in mans:                                          # rule 1: declared depends_on
        for cap in m.depends_on:
            for p in providers_of(cap):
                shared = [t for t in p.outputs if t in m.inputs]
                if shared:
                    for t in shared:
                        add(p.name, m.name, t, "depends_on", a_port=t, b_port=t,
                            label="alternative" if p.capability != cap else None)
                        satisfied[m.name].add(t)
                else:                                          # control dependency (no data type shared)
                    add(p.name, m.name, "dependency", "depends_on",
                        label=f"needs {cap}")
    for m, _ in mans:                                          # rule 2: an input nobody was wired to
        for t in m.inputs:
            if t in satisfied[m.name] or t == "ResultIR":
                continue
            for p, _mod in mans:
                if p.name != m.name and t in p.outputs:
                    if any(e["from"]["node"] == m.name and e["to"]["node"] == p.name for e in edges):
                        continue                               # the reverse link already exists (no 2-cycles)
                    add(p.name, m.name, t, "port_type", a_port=t, b_port=t)

    loops = []
    try:
        tol_c, tol_l = const("routes/thermal.py", "COUPLED_TOL_C")
        mx = default_arg("routes/thermal.py", "solve_coupled", "max_iter")
        relax = default_arg("routes/thermal.py", "solve_coupled", "relax")
        loops.append({
            "id": "em_thermal_module", "kind": "fixed_point",
            "title": "EM <-> thermal (module)", "nodes": ["solver-thermal", "solver-em-thermal"],
            "edges": [], "max_iterations": mx, "tolerance": tol_c, "tolerance_unit": "K",
            "detail": {"relaxation": relax, "what": "winding temperature fed back into the loss map"},
            "source": src("routes/thermal.py", "solve_coupled"), "convergence": None})
        t2, _ = const("routes/coupled.py", "DEFAULT_TOL_K")
        bt, _ = const("routes/coupled.py", "BEARING_TOL_K")
        mi, _ = const("routes/coupled.py", "DEFAULT_MAX_ITER")
        ed, _ = const("coupled_duty_cycle.py", "ED_TOL_PCT")
        loops.append({
            "id": "em_thermal_orchestrator", "kind": "fixed_point",
            "title": "EM <-> thermal (/api/coupled)", "nodes": ["solver-em-transient", "solver-thermal"],
            "edges": [], "max_iterations": mi, "tolerance": t2, "tolerance_unit": "K",
            "detail": {"bearing_tolerance_K": bt, "duty_ratio_tolerance_pct": ed,
                       "what": "coil / magnet / bearing temperatures (and the impulse-duty ratio) to the fixed point"},
            "source": src("routes/coupled.py", "_run"), "convergence": None})
    except LookupError as exc:                                 # a moved constant must not take the page down
        log.warning("system graph: coupling loop not described: %s", exc)
    names = {"optimization-descent", "geometry-2d-aerostator", "mesh-skfem", "solver-em-transient"}
    if names <= set(by_name):
        loops.append({
            "id": "design_search", "kind": "search",
            "title": "Design search", "nodes": sorted(names), "edges": [],
            "max_iterations": None, "tolerance": None, "tolerance_unit": None,
            "detail": {"what": "the optimizer proposes a ParameterSet, the FEM confirms it (budget set per run)"},
            "source": src("modules/optimization.py", "Optimizer"), "convergence": None})
    for lp in loops:                                           # a loop owns the edges that stay inside it
        members = set(lp["nodes"])
        lp["edges"] = [e["id"] for e in edges if e["from"]["node"] in members and e["to"]["node"] in members]
    for n in nodes:                                            # cross-cutting services nobody is wired to
        n["context"] = n["id"] in ("users", "materials-library")
    # layout hints (col, row): the geometry -> mesh -> EM -> thermal chain on row 0; a module that is not listed
    # (a plugin) makes the view fall back to the automatic layout
    at = {"geometry-2d-ring-spm": (1, -1.5), "geometry-2d-aerostator": (1, 0), "surrogate-rf": (2, 2.8),
          "cost-basic": (2, -2.4), "geometry-3d-aerostator": (2, -1.2), "mesh-skfem": (2, 0),
          "mechanical-stub": (3, -2.4), "solver-mechanical": (3, -2.4), "solver-em-static": (3, -1.2),
          "solver-em-transient": (3, 0), "optimization-descent": (3, 1.5),
          "controller-foc": (4, -1.2), "solver-thermal": (4, 0), "solver-em-thermal": (5, 0),
          "transmission-gearbox-single-stage": (5, 1.5)}
    for n in nodes:
        if n["id"] in at:
            n["layout"] = {"col": at[n["id"]][0], "row": at[n["id"]][1]}
    for e in edges:                                            # the mesh handed to a solver that also needs another upstream
        if e["via"] == "port_type" and e["type"] == "MeshIR":
            e["minor"], e["label"] = True, "mesh"
        if e["via"] == "port_type" and e["to"]["node"] == "geometry-2d-ring-spm":   # the alternative geometry provider
            e["minor"], e["label"] = True, e["type"]
    notes = ["Edges come from the modules' manifests: `depends_on` capabilities (an extension such as "
             "geometry.2d.ring_spm is an alternative provider of its base capability) and, for an input no "
             "dependency covers, the module that outputs that contract type.",
             "A node with `roadmap: true` is a registered stub: it describes a capability that is not "
             "implemented yet."]
    return _doc("modules", "All modules", nodes, edges, loops, notes)


# ---------------------------------------------------------------------------
# View 2: propeller S1 (what propeller_s1.py actually runs)
# ---------------------------------------------------------------------------

_S1 = "propeller_s1.py"


def build_propeller_s1_graph() -> Dict[str, Any]:
    S = lambda fn, anchor=None, rel=_S1: src(rel, fn, anchor)       # noqa: E731
    N: List[Dict[str, Any]] = [
        _node("rpm_search", "search", "Speed search",
              "Scan 41 speeds, bisect every pass/fail edge to 1 rpm; S1 = top of the highest passing band",
              ins=[("verdict", "verdict"), ("rpm_max", "limits")], outs=[("rpm", "rpm"), ("rating", "s1_rating")],
              source=S("s1_rating"), group="search"),
        _node("conditions", "input", "Ambient and air",
              "Ambient temperature and altitude give the air density",
              outs=[("rho", "air_density"), ("ambient", "temperature")],
              source=src("propeller.py", "air_density"), group="input"),
        _node("model", "data", "Motor model",
              "Compact model of the machine from its passport: torque and loss grids, thermal geometry, limits",
              outs=[("grid", "passport_grid"), ("mech", "passport_grid"), ("geometry", "thermal_geometry"),
                    ("calibration", "thermal_calibration"), ("limits", "limits")],
              source=S("load_model"), group="input"),
        _node("limits", "data", "Limits",
              "Winding and magnet temperature limits, current and speed caps",
              ins=[("limits", "limits")], outs=[("winding", "limits"), ("magnet", "limits"), ("rpm_max", "limits")],
              source=S("limits_of"), group="input"),
        _node("drive", "data", "Drive and pack",
              "Sine or PWM board / external ESC, board cooling class, pack voltage and current",
              outs=[("drive", "drive"), ("pack", "pack")], source=S("resolve_drive"), group="input"),
        _node("slipstream", "compute", "Slipstream factor",
              "Air speed at the motor over the developed wake speed: model-calibrated, else 0.4",
              ins=[("calibration", "thermal_calibration")], outs=[("factor", "slipstream_factor")],
              source=S("model_slipstream"), group="air"),
        _node("propeller", "compute", "Propeller load",
              "Thrust, torque Q(n), shaft power and air speed at the motor from the propeller's coefficients",
              ins=[("rpm", "rpm"), ("rho", "air_density"), ("factor", "slipstream_factor")],
              outs=[("torque", "torque"), ("shaft_power", "power"), ("air_speed", "air_speed")],
              source=src("propeller.py", "operating_point"), group="air"),
        _node("torque_demand", "compute", "Shaft torque",
              "Q_em = Q_prop(n) + bearing and windage power / omega",
              ins=[("rpm", "rpm"), ("q_prop", "torque"), ("mech", "passport_grid")], outs=[("torque_em", "torque")],
              source=S("solve_point", "q_em = op"), group="motor"),
        _node("housing_film", "compute", "Housing film",
              "Heat-transfer coefficient of the housing in the slipstream",
              ins=[("air_speed", "air_speed"), ("ambient", "temperature")], outs=[("h", "heat_transfer")],
              source=S("housing_film"), group="air"),
        _node("current", "compute", "Current",
              "Smallest current that makes Q_em on the passport torque map, flux scaled by magnet temperature",
              ins=[("rpm", "rpm"), ("torque", "torque"), ("grid", "passport_grid"), ("T_magnet", "temperature")],
              outs=[("I", "current")], source=S("current_for_torque"), group="motor", in_loop=True),
        _node("voltage", "compute", "Voltage re-check",
              "Phase voltage at the minimum pack voltage; a deeper field-weakening angle may need more current",
              ins=[("rpm", "rpm"), ("I", "current"), ("T_magnet", "temperature"), ("T_winding", "temperature"),
                   ("drive", "drive"), ("pack", "pack")], outs=[("I", "current")],
              source=S("voltage_check"), group="motor", in_loop=True),
        _node("losses", "compute", "Losses",
              "Copper (R at winding temperature), stator and rotor iron, rotor eddy, PWM extra, bearings",
              ins=[("rpm", "rpm"), ("I", "current"), ("T_winding", "temperature"), ("grid", "passport_grid"),
                   ("drive", "drive")], outs=[("losses", "loss_W")],
              source=S("losses"), group="motor", in_loop=True),
        _node("thermal", "compute", "Thermal network",
              "Lumped 3-node network (housing, winding, rotor), calibrated to FEM / bench anchors",
              ins=[("losses", "loss_W"), ("h", "heat_transfer"), ("ambient", "temperature"),
                   ("geometry", "thermal_geometry"), ("calibration", "thermal_calibration")],
              outs=[("T_winding", "temperature"), ("T_magnet", "temperature"), ("T_housing", "temperature")],
              source=S("temperatures"), group="thermal", in_loop=True),
        _node("powers", "compute", "Electrical and battery power",
              "Shaft power + motor losses, plus controller loss on the battery side only",
              ins=[("losses", "loss_W"), ("shaft_power", "power"), ("drive", "drive"), ("pack", "pack")],
              outs=[("I_batt", "current"), ("P_batt", "power")],
              source=S("solve_point", "p_in = "), group="motor"),
        _node("board_limit", "compute", "Controller board limit",
              "Continuous board current from the board's thermal model at this ambient and wash speed",
              ins=[("drive", "drive"), ("air_speed", "air_speed"), ("ambient", "temperature")],
              outs=[("I_board", "limit")], source=S("board_limit_A"), group="limit"),
        _node("demag_limit", "compute", "Demagnetisation limit",
              "Demagnetising current at the operating magnet temperature",
              ins=[("T_magnet", "temperature")], outs=[("I_demag", "limit")],
              source=S("demag_limit_at"), group="limit"),
        _node("chk_winding", "limit", "Winding temperature",
              "T_winding <= limit (model limit, else the project's insulation class)",
              ins=[("T", "temperature"), ("limit", "limits")], outs=[("ok", "verdict")],
              source=S("solve_point", 'viol.append(("winding"'), group="limit"),
        _node("chk_magnet", "limit", "Magnet temperature",
              "T_magnet <= limit of the magnet card",
              ins=[("T", "temperature"), ("limit", "limits")], outs=[("ok", "verdict")],
              source=S("solve_point", 'viol.append(("magnet"'), group="limit"),
        _node("chk_demag", "limit", "Demagnetisation",
              "I <= demagnetising current at the magnet temperature",
              ins=[("I", "current"), ("limit", "limit")], outs=[("ok", "verdict")],
              source=S("solve_point", 'viol.append(("demag"'), group="limit"),
        _node("chk_controller", "limit", "Controller current",
              "I <= board continuous current; external ESC: battery current <= its bus rating",
              ins=[("I", "current"), ("I_batt", "current"), ("limit", "limit")], outs=[("ok", "verdict")],
              source=S("solve_point", 'viol.append(("controller"'), group="limit"),
        _node("chk_battery", "limit", "Battery current",
              "Battery current at the minimum pack voltage <= the pack's continuous current",
              ins=[("I_batt", "current"), ("pack", "pack")], outs=[("ok", "verdict")],
              source=S("solve_point", 'viol.append(("battery_current"'), group="limit"),
        _node("verdict", "compute", "Point verdict",
              "Every broken limit, worst first; voltage, current cap, loss data and runaway end the point earlier",
              ins=[("winding", "verdict"), ("magnet", "verdict"), ("demag", "verdict"),
                   ("controller", "verdict"), ("battery", "verdict")], outs=[("verdict", "verdict")],
              source=S("solve_point", "viol.sort("), group="limit"),
        _node("result", "output", "S1 rating",
              "Highest passing speed with its thrust, current, temperatures and the limiting cause",
              ins=[("rating", "s1_rating")], source=S("s1_rating", '"limit": limit'), group="output"),
    ]
    E = [
        _edge("model", "limits", "limits", "limits", "limits"),
        _edge("limits", "rpm_max", "rpm_search", "rpm_max", "limits", label="scan top"),
        _edge("model", "calibration", "slipstream", "calibration", "thermal_calibration"),
        _edge("slipstream", "factor", "propeller", "factor", "slipstream_factor"),
        _edge("conditions", "rho", "propeller", "rho", "air_density"),
        _edge("rpm_search", "rpm", "propeller", "rpm", "rpm"),
        _edge("rpm_search", "rpm", "torque_demand", "rpm", "rpm"),
        _edge("rpm_search", "rpm", "current", "rpm", "rpm"),
        _edge("rpm_search", "rpm", "voltage", "rpm", "rpm"),
        _edge("rpm_search", "rpm", "losses", "rpm", "rpm"),
        _edge("propeller", "torque", "torque_demand", "q_prop", "torque"),
        _edge("model", "mech", "torque_demand", "mech", "passport_grid"),
        _edge("propeller", "air_speed", "housing_film", "air_speed", "air_speed"),
        _edge("conditions", "ambient", "housing_film", "ambient", "temperature"),
        _edge("torque_demand", "torque_em", "current", "torque", "torque"),
        _edge("model", "grid", "current", "grid", "passport_grid"),
        _edge("model", "grid", "losses", "grid", "passport_grid"),
        _edge("drive", "drive", "voltage", "drive", "drive"),
        _edge("drive", "pack", "voltage", "pack", "pack"),
        _edge("drive", "drive", "losses", "drive", "drive"),
        _edge("current", "I", "voltage", "I", "current"),
        _edge("voltage", "I", "losses", "I", "current"),
        _edge("losses", "losses", "thermal", "losses", "loss_W"),
        _edge("housing_film", "h", "thermal", "h", "heat_transfer"),
        _edge("conditions", "ambient", "thermal", "ambient", "temperature"),
        _edge("model", "geometry", "thermal", "geometry", "thermal_geometry"),
        _edge("model", "calibration", "thermal", "calibration", "thermal_calibration"),
        # the feedback of the fixed point: the temperatures change the flux, the voltage margin and R
        _edge("thermal", "T_magnet", "current", "T_magnet", "temperature", feedback=True, label="flux k_psi(T_m)"),
        _edge("thermal", "T_magnet", "voltage", "T_magnet", "temperature", feedback=True, label="PM flux"),
        _edge("thermal", "T_winding", "voltage", "T_winding", "temperature", feedback=True, label="R(T_w)"),
        _edge("thermal", "T_winding", "losses", "T_winding", "temperature", feedback=True, label="R(T_w)"),
        # after convergence
        _edge("losses", "losses", "powers", "losses", "loss_W"),
        _edge("propeller", "shaft_power", "powers", "shaft_power", "power"),
        _edge("drive", "drive", "powers", "drive", "drive"),
        _edge("drive", "pack", "powers", "pack", "pack"),
        _edge("drive", "drive", "board_limit", "drive", "drive"),
        _edge("propeller", "air_speed", "board_limit", "air_speed", "air_speed"),
        _edge("conditions", "ambient", "board_limit", "ambient", "temperature"),
        _edge("thermal", "T_magnet", "demag_limit", "T_magnet", "temperature"),
        _edge("thermal", "T_winding", "chk_winding", "T", "temperature"),
        _edge("limits", "winding", "chk_winding", "limit", "limits"),
        _edge("thermal", "T_magnet", "chk_magnet", "T", "temperature"),
        _edge("limits", "magnet", "chk_magnet", "limit", "limits"),
        _edge("voltage", "I", "chk_demag", "I", "current"),
        _edge("demag_limit", "I_demag", "chk_demag", "limit", "limit"),
        _edge("voltage", "I", "chk_controller", "I", "current"),
        _edge("powers", "I_batt", "chk_controller", "I_batt", "current"),
        _edge("board_limit", "I_board", "chk_controller", "limit", "limit"),
        _edge("powers", "I_batt", "chk_battery", "I_batt", "current"),
        _edge("drive", "pack", "chk_battery", "pack", "pack"),
        _edge("chk_winding", "ok", "verdict", "winding", "verdict"),
        _edge("chk_magnet", "ok", "verdict", "magnet", "verdict"),
        _edge("chk_demag", "ok", "verdict", "demag", "verdict"),
        _edge("chk_controller", "ok", "verdict", "controller", "verdict"),
        _edge("chk_battery", "ok", "verdict", "battery", "verdict"),
        _edge("verdict", "verdict", "rpm_search", "verdict", "verdict", feedback=True, label="pass / fail + cause"),
        _edge("rpm_search", "rating", "result", "rating", "s1_rating"),
    ]
    # How the chain is drawn (the view reads these hints; the data above is untouched).  Row 0 is the main chain
    # speed search -> propeller load -> shaft torque -> current -> voltage -> losses -> thermal network -> limit
    # checks (a column in front of the verdict) -> point verdict -> rating; helper blocks sit above / below it.
    at = {"rpm_search": (1, 0), "propeller": (2, 0), "torque_demand": (3, 0), "housing_film": (3, -1.5),
          "current": (4, 0), "voltage": (5, 0), "losses": (6, 0), "thermal": (7, 0),
          "demag_limit": (8, -1), "board_limit": (8, 1), "powers": (8, 2),
          "chk_winding": (9, -2), "chk_magnet": (9, -1), "chk_demag": (9, 0), "chk_controller": (9, 1),
          "chk_battery": (9, 2), "verdict": (10, 0), "result": (11, 0)}
    context = {"conditions", "model", "limits", "drive", "slipstream"}      # parameter sources: chips, not wires
    minor = {"rpm_search.rpm->torque_demand.rpm": "rpm", "rpm_search.rpm->current.rpm": "rpm",
             "rpm_search.rpm->voltage.rpm": "rpm", "rpm_search.rpm->losses.rpm": "rpm",
             "propeller.shaft_power->powers.shaft_power": "shaft power",
             "propeller.air_speed->board_limit.air_speed": "air speed",
             "voltage.I->chk_demag.I": "converged current", "voltage.I->chk_controller.I": "converged current"}
    lane = {"rpm_search.rating->result.rating"}
    for n in N:
        if n["id"] in at:
            n["layout"] = {"col": at[n["id"]][0], "row": at[n["id"]][1]}
        n["context"] = n["id"] in context
    for e in E:
        if e["id"] in minor:
            e["minor"], e["label"] = True, minor[e["id"]]
        if e["id"] in lane:
            e["route"] = "lane"
    missing = (set(at) | context) - {n["id"] for n in N}
    stale = (set(minor) | lane) - {e["id"] for e in E}
    if missing or stale:
        raise LookupError(f"system graph layout hints name unknown nodes/edges: {sorted(missing | stale)}")
    tol_k, tol_l = const(_S1, "FIXED_POINT_TOL_K")
    max_it, it_l = const(_S1, "FIXED_POINT_MAX_IT")
    runaway, _ = const(_S1, "RUNAWAY_C")
    fp_nodes = ["current", "voltage", "losses", "thermal"]
    fp_edges = [e["id"] for e in E if e["feedback"] and e["to"]["node"] in fp_nodes] + [
        "current.I->voltage.I", "voltage.I->losses.I", "losses.losses->thermal.losses"]
    loops = [
        {"id": "thermal_fixed_point", "kind": "fixed_point", "title": "Current - losses - temperature",
         "nodes": fp_nodes, "edges": fp_edges, "max_iterations": max_it, "tolerance": tol_k,
         "tolerance_unit": "K",
         "detail": {"start_damping": 1.0, "damping_halved_when_residual_stalls": True, "min_damping": 0.05,
                    "retry_if_not_converged": {"damping": 0.2, "max_iterations": 3 * max_it},
                    "runaway_above_C": runaway,
                    "outcomes": ["converged", "no_current", "loss_data", "runaway", "nonconverged"]},
         "source": S("_fixed_point"), "convergence": None},
        {"id": "speed_search", "kind": "search", "title": "Speed search",
         "nodes": [n["id"] for n in N if n["id"] not in ("conditions", "model", "limits", "drive", "slipstream", "result")],
         "edges": [], "max_iterations": None, "tolerance": 1.0, "tolerance_unit": "rpm",
         "detail": {"scan_points": 41, "refinement_passes": 2, "bisection": "every pass/fail edge to the tolerance",
                    "limit": "upper edge of the highest passing band; lower bound if the scan ends passing"},
         "source": S("s1_rating"), "convergence": None},
    ]
    for lp in loops[1:]:
        lp["edges"] = [e["id"] for e in E if e["from"]["node"] in lp["nodes"] and e["to"]["node"] in lp["nodes"]]
    doc = _doc("propeller_s1", "Propeller S1 rating", N, E, loops, [
        "Drawn from propeller_s1.solve_point and s1_rating; each node's hover gives function, file and line.",
        "The slipstream factor of a model comes from its thermal calibration (bench fit); only a model without one "
        "uses the propeller module's default 0.4.",
        "Controller losses enter the battery power only; they are not heat in the motor's thermal network."])
    return doc


# ---------------------------------------------------------------------------
# Status layer: read-only, from records that already exist
# ---------------------------------------------------------------------------

#: job kind (prefix) -> node of the modules view
_JOB_NODE = (("transient", "solver-em-transient"), ("thermal.coupled", "solver-em-thermal"),
             ("coupled", "solver-em-thermal"), ("thermal", "solver-thermal"),
             ("mechanical", "solver-mechanical"))
#: run-history kind -> node of the modules view
_HISTORY_NODE = {"thermal.field": "solver-thermal", "coupled.run": "solver-em-thermal",
                 "controller.solve": "controller-foc", "mechanical.rotor_stress": "solver-mechanical",
                 "mechanical.limit_speed": "solver-mechanical"}


def _matches(blob: Any, want: Dict[str, Optional[str]]) -> bool:
    """Every given filter value appears in the record's request (no filter = match all)."""
    flt = [v for v in want.values() if v]
    if not flt:
        return True
    try:
        text = json.dumps(blob, default=str, ensure_ascii=False)
    except Exception:                               # noqa: BLE001
        return False
    return all(v in text for v in flt)


def _job_status(want) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from motor_ai_sim import jobs as _J
        recs = _J.queue().list_for_owner(_J.current_owner(), limit=100, is_admin=False)
    except Exception as exc:                        # noqa: BLE001 - no queue, no status
        log.debug("system graph: jobs unavailable: %s", exc)
        return out
    for r in recs:                                  # newest first
        node = next((n for pfx, n in _JOB_NODE if str(r.kind).startswith(pfx)), None)
        if node is None or node in out or not _matches(r.body, want):
            continue
        pub = r.public()
        state = {"queued": "running", "running": "running", "done": "done"}.get(str(r.state), "error")
        out[node] = {"state": state, "time_s": pub.get("elapsed_s") or None,
                     "finished_at": r.finished_at or None, "run_id": r.run_id, "source": f"job:{r.kind}",
                     "summary": (r.error or str(r.state))[:160]}
    return out


def _history_status(want) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from motor_ai_sim import run_history as _RH
    except Exception:                               # noqa: BLE001
        return out
    for kind, node in _HISTORY_NODE.items():
        try:
            for e in _RH.history_for(kind).list(limit=20):
                if node in out or not _matches(e.get("params"), want):
                    continue
                out[node] = {"state": "cached", "time_s": e.get("elapsed_s"), "finished_at": e.get("computed_at"),
                             "source": f"run_history:{kind}", "summary": str(e.get("summary") or "")[:160]}
        except Exception as exc:                    # noqa: BLE001
            log.debug("system graph: run history %s unavailable: %s", kind, exc)
    return out


def _s1_model_status(die: str, config: str) -> Dict[str, Dict[str, Any]]:
    try:
        from motor_ai_sim import propeller_s1 as S1
        m = S1.load_model(die, config)
    except Exception as exc:                        # noqa: BLE001 - no model for this machine
        return {"model": {"state": "unknown", "summary": str(exc)[:160]}}
    cal = m.get("thermal_calibration") or {}
    return {"model": {
        "state": "cached", "source": "propeller_s1.load_model", "summary": f"{die} {config}",
        "values": {"schema": m.get("schema"), "R_phase_ohm": m.get("R_phase_ohm"),
                   "slipstream_factor": cal.get("slipstream_factor"),
                   "slipstream_factor_status": cal.get("slipstream_factor_status"),
                   "magnet_limit_C": (m.get("limits") or {}).get("magnet_C")}}}


def attach_status(doc: Dict[str, Any], die: Optional[str] = None, config: Optional[str] = None,
                  duty: Optional[str] = None) -> Dict[str, Any]:
    """Fill ``node['status']`` from existing records.  Never computes; no record = ``unknown``."""
    want = {"die": die, "config": config, "duty": duty}
    doc["status_context"] = {k: v for k, v in want.items() if v} or None
    found: Dict[str, Dict[str, Any]] = {}
    if doc["view"] == "modules":
        found.update(_history_status(want))
        found.update(_job_status(want))             # a job (running / finished / failed) is fresher than a stored result
    elif die and config:
        found.update(_s1_model_status(die, config))
    for n in doc["nodes"]:
        st = found.get(n["id"])
        n["status"] = st if st else {"state": "unknown"}
    mv = (found.get("model") or {}).get("values")
    if doc["view"] == "propeller_s1" and mv:                   # what the model hands over, on the wires it travels
        vals = {"model.calibration->slipstream.calibration":
                    {"slipstream_factor": mv.get("slipstream_factor"), "status": mv.get("slipstream_factor_status")},
                "model.limits->limits.limits": {"magnet_limit_C": mv.get("magnet_limit_C")}}
        for e in doc["edges"]:
            v = vals.get(e["id"])
            if v and any(x is not None for x in v.values()):
                e["values"] = {k: x for k, x in v.items() if x is not None}
    return doc


def build(view: str = "modules", die: Optional[str] = None, config: Optional[str] = None,
          duty: Optional[str] = None) -> Dict[str, Any]:
    if view == "modules":
        doc = build_modules_graph()
    elif view == "propeller_s1":
        doc = build_propeller_s1_graph()
    else:
        raise ValueError(f"unknown view {view!r}; one of {VIEWS}")
    if die or config or duty:
        attach_status(doc, die, config, duty)
    return doc
