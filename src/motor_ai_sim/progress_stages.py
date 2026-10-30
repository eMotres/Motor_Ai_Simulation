"""The ordered STAGES of a long solve, for the one-row progress strip.

WHY THIS EXISTS
---------------
A transient's progress bar used to be one counter, ``step / total``, with the
stage as a text label.  The total is not a constant: the eddy warm-up is
adaptive (a march that has not settled splices a longer one in front), the demag
pre-pass is spliced in behind it, and the reported window restarts the counter at
0.  So the bar jumped, the ETA jumped with it, and the user could not tell a run
that was 40 % through the warm-up from one that was 40 % through the answer
(owner 2026-10-30, with a screenshot of "Computing 148 / 161 steps").

The solver already says everything needed — every frame calls
``progress(done, total, phase, composition)`` with a phase label per stage and a
stage-local counter — so this module only READS that stream and keeps an ordered
list, one entry per stage: ``pending → running → done`` (or ``skipped`` when a
later stage began without it ever running — a warm-up served from the cache).
No solver line changes; the old flat fields stay in every payload.

THE SHAPE (one dict per stage, ``public()``)
--------------------------------------------
``key``       stable identifier, what the UI translates (``warm_up``…)
``name``      short English name, the fallback label
``state``     ``pending | running | done | skipped | stopped``
``done``      steps finished in THIS stage (each step is one FEM frame for the
              EM stages; one electromagnetic or thermal run for coupled ones)
``planned``   steps the stage expects.  For an ``adaptive`` stage this is a
              moving target and may be 0 (not known yet) or GROW — the UI must
              say so.  When an adaptive stage ends, ``planned`` snaps to what it
              actually took.
``adaptive``  the planned count is a moving target
``unit``      ``frames`` or ``steps`` (the UI's word)
``elapsed_s`` wall-clock spent in the stage so far (frozen once it ends)
``note``      the solver's own breakdown for the stage, when it gave one

Everything here is a pure function over a plain ``list`` of plain ``dict`` —
that is what lets the transient's legacy progress dict (copied with ``{**p}``
and serialised straight to JSON) carry it, and what keeps it testable without a
solver.  Internal bookkeeping lives in underscore-prefixed keys that
:func:`public` strips.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

__all__ = ["EM", "COUPLED", "plan_em", "plan_coupled", "observe", "finalize",
           "public", "classify_em", "classify_coupled"]

#: Execution order of the electromagnetic transient's stages.  ``calibration``
#: is the once-per-geometry d-axis reference; ``extra`` is a second whole solve
#: made for the answer (harm-ref, six-phase L_xy) — it counts nothing, so it is
#: shown as running without a bar.
EM = ("calibration", "gap_probe", "warm_up", "demag_prepass", "final",
      "extra", "post")
#: The coupled thermal loop: the sine/current iterations, then the controller's
#: PWM passes.
COUPLED = ("loop", "pwm_final")

_NAMES = {
    "calibration": "d-axis calibration",
    "gap_probe": "gap probe",
    "warm_up": "eddy warm-up",
    "demag_prepass": "demag pre-pass",
    "final": "final periods",
    "extra": "reference solve",
    "post": "post-processing",
    "loop": "coupled iterations",
    "pwm_final": "controller PWM passes",
}
_ORDERS = {"em": EM, "coupled": COUPLED}


# ── construction ─────────────────────────────────────────────────────────────

def _new(kind: str, key: str, *, planned: int = 0, adaptive: bool = False,
         unit: str = "frames") -> Dict[str, Any]:
    order = _ORDERS[kind]
    return {"key": key, "name": _NAMES[key], "state": "pending",
            "done": 0, "planned": max(int(planned), 0),
            "adaptive": bool(adaptive), "unit": unit, "note": "",
            "_kind": kind, "_ord": order.index(key), "_t0": 0.0, "_t1": 0.0,
            "_off": 0, "_base_done": 0, "_base_planned": 0,
            "_last_done": 0, "_last_planned": 0, "_full": 0}


def plan_em(*, eddy: bool, demag: bool, planned_final: int,
            planned_prepass: int = 0) -> List[Dict[str, Any]]:
    """The stages an EM transient is KNOWN to have before it starts.

    ``final`` (the reported window and its settling periods) and ``post`` always
    exist.  The warm-up exists for a coupled-eddy solve and is adaptive; the demag
    pre-pass exists when demag is on.  Gap probes and the calibration are not in
    the plan — whether they run depends on the geometry and the cache, so they
    appear (in order) the moment the solver reports them.
    """
    out: List[Dict[str, Any]] = []
    if eddy:
        out.append(_new("em", "warm_up", adaptive=True))
    if demag:
        out.append(_new("em", "demag_prepass", planned=planned_prepass))
    out.append(_new("em", "final", planned=planned_final))
    out.append(_new("em", "post", planned=0))
    return out


def plan_coupled(*, max_iter: int, pwm: bool, max_passes: int = 0
                 ) -> List[Dict[str, Any]]:
    """The coupled loop's stages: iterations (each an EM run + a thermal solve,
    so two steps), then the controller's PWM passes when a PWM source is on.
    Both are adaptive: the loop stops when it converges."""
    out = [_new("coupled", "loop", planned=2 * max(int(max_iter), 0),
                adaptive=True, unit="steps")]
    if pwm:
        out.append(_new("coupled", "pwm_final",
                        planned=2 * max(int(max_passes), 0),
                        adaptive=True, unit="steps"))
    return out


# ── reading the solver's stream ──────────────────────────────────────────────

def classify_em(phase: Optional[str]) -> Optional[str]:
    """Which EM stage a solver phase label names, or ``None`` if it names none."""
    p = str(phase or "").strip().lower()
    if not p:
        return None
    if p.startswith("gap probe"):
        return "gap_probe"          # BEFORE warm-up: a probe has its own warm-up
    if p.startswith("calibrating"):
        return "calibration"
    if p.startswith("eddy warm-up"):
        return "warm_up"
    if p.startswith("demag pre-pass"):
        return "demag_prepass"
    if p.startswith("post-processing"):
        return "post"
    if "harm-ref" in p or "six-phase" in p:
        return "extra"
    if p.startswith("fem-solve"):
        return "final"
    return None


_RE_ITER = re.compile(r"^\s*iteration\s+(\d+)\s*/\s*(\d+)", re.I)
_RE_PWM = re.compile(r"^\s*controller pwm pass\s+(\d+)\s*/\s*(\d+)", re.I)


def classify_coupled(phase: Optional[str]):
    """``(key, k, n, thermal_half)`` for a coupled phase label, or ``None``."""
    p = str(phase or "")
    for rx, key in ((_RE_ITER, "loop"), (_RE_PWM, "pwm_final")):
        m = rx.match(p)
        if m:
            return key, int(m.group(1)), int(m.group(2)), "thermal" in p.lower()
    return None


def _find(stages: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    for s in stages:
        if s["key"] == key:
            return s
    return None


def _close(st: Dict[str, Any], now: float) -> None:
    """A stage that has ended: freeze its clock and make its counts honest."""
    if st["state"] in ("done", "skipped"):
        return
    st["state"] = "done"
    st["_t1"] = now
    if st["adaptive"]:
        # What it actually took.  A warm-up that settled in 12 frames reports
        # 12 / 12 afterwards, not 12 / 40.
        st["done"] = max(st["done"], st["_full"])
        st["planned"] = st["done"]
    elif st["planned"] > 0:
        st["done"] = st["planned"]


def _enter(stages: List[Dict[str, Any]], kind: str, key: str, now: float
           ) -> Dict[str, Any]:
    """Make ``key`` the running stage; close what ran before it, skip what never
    ran.  Inserts the stage, in execution order, if the plan did not have it."""
    st = _find(stages, key)
    if st is None:
        st = _new(kind, key, adaptive=(key in ("warm_up", "gap_probe")))
        at = len(stages)
        for i, other in enumerate(stages):
            if other["_ord"] > st["_ord"]:
                at = i
                break
        stages.insert(at, st)
    if st["state"] == "running":
        return st
    first = st["state"] == "pending"
    for other in stages:
        if other is st or other["_ord"] >= st["_ord"]:
            continue
        if other["state"] == "running":
            _close(other, now)
        elif other["state"] == "pending":
            other["state"] = "skipped"
    st["state"] = "running"
    if first or not st["_t0"]:
        st["_t0"] = now
    st["_t1"] = 0.0
    st["_first"] = first
    return st


def observe(stages: Optional[List[Dict[str, Any]]], done: Optional[int],
            total: Optional[int], phase: Optional[str],
            composition: Optional[str] = None, *, kind: str = "em",
            now: Optional[float] = None) -> None:
    """Fold one ``progress(done, total, phase, composition)`` call into ``stages``.

    ``done``/``total`` of ``None`` is the solver's set-up checkpoint ("keep what
    you show"): it moves nothing, unless its phase names a stage.  A call with
    counts and no phase is the reported window — the solver says ``None`` to mean
    "back to the solve's own label".
    """
    if stages is None:
        return
    now = time.time() if now is None else float(now)
    if kind == "coupled":
        _observe_coupled(stages, phase, now)
        return
    key = classify_em(phase)
    if done is None and total is None:
        if key is not None:
            _enter(stages, "em", key, now)
        return
    if key is None:
        # Counts without a recognised label: the reported window, unless a
        # stage is already running and the label is merely one we do not know.
        run = next((s for s in stages if s["state"] == "running"), None)
        key = "final" if (not phase or run is None) else run["key"]
    st = _enter(stages, "em", key, now)
    _counts(stages, st, done, total, phase, composition, now)


def _counts(stages, st, done, total, phase, composition, now) -> None:
    key = st["key"]
    d = None if done is None else max(int(done), 0)
    t = None if total is None else max(int(total), 0)
    if key == "gap_probe":
        # Several probes run back to back and each restarts its own counter (and
        # runs its own warm-up inside): fold a finished probe into the base so
        # the stage reads as one monotonic count.
        label = str(phase or "").split(":")[0]
        if d is not None and (d < st["_last_done"]
                              or (st.get("_label") and label != st["_label"])):
            st["_base_done"] += st["_last_done"]
            st["_base_planned"] += max(st["_last_planned"], st["_last_done"])
        st["_label"] = label
        if d is not None:
            st["_last_done"] = d
        if t is not None:
            st["_last_planned"] = t
        st["done"] = st["_base_done"] + st["_last_done"]
        st["planned"] = st["_base_planned"] + max(st["_last_planned"],
                                                  st["_last_done"])
        return
    if key == "demag_prepass":
        # Spliced in behind the warm-up in ONE negative-frame counter: the
        # frames before this stage's first report belong to the warm-up.
        if st.pop("_first", False):
            st["_off"] = d or 0
            wu = _find(stages, "warm_up")
            if wu is not None and wu["state"] == "done":
                wu["done"] = wu["planned"] = max(st["_off"], 0)
        d = None if d is None else max(d - st["_off"], 0)
        t = None if t is None else max(t - st["_off"], 0)
    st.pop("_first", None)
    if d is not None:
        st["done"] = d
    if t is not None and (t > 0 or key != "final"):
        st["planned"] = t
    if st["planned"] < st["done"]:
        st["planned"] = st["done"]
    if composition and key == "final":
        st["note"] = str(composition)


def _observe_coupled(stages, phase, now) -> None:
    hit = classify_coupled(phase)
    if hit is None:
        return
    key, k, n, thermal = hit
    st = _enter(stages, "coupled", key, now)
    st.pop("_first", None)
    st["done"] = 2 * (k - 1) + (1 if thermal else 0)
    st["planned"] = max(st["planned"], 2 * max(n, k))
    if thermal:
        st["_full"] = 2 * k        # the pass is complete when its thermal half is


def finalize(stages: Optional[List[Dict[str, Any]]], *, ok: bool = True,
             now: Optional[float] = None) -> None:
    """The run is over: close what is running, skip what never started.

    ``ok=False`` (a stop or an error) marks the running stage ``stopped`` rather
    than ``done`` — a bar must not claim a stage finished that was cut short.
    """
    if not stages:
        return
    now = time.time() if now is None else float(now)
    for s in stages:
        if s["state"] == "running":
            if ok:
                _close(s, now)
            else:
                s["state"] = "stopped"
                s["_t1"] = now
        elif s["state"] == "pending":
            s["state"] = "skipped"


def public(stages: Optional[List[Dict[str, Any]]], *,
           now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The JSON the poll endpoints answer: the stage list without bookkeeping."""
    if not stages:
        return []
    now = time.time() if now is None else float(now)
    out: List[Dict[str, Any]] = []
    for s in list(stages):          # a callback thread may insert while we read
        t0 = float(s.get("_t0") or 0.0)
        t1 = float(s.get("_t1") or 0.0)
        el = ((t1 if t1 > 0 else now) - t0) if t0 > 0 else 0.0
        planned = int(s.get("planned") or 0)
        done = int(s.get("done") or 0)
        out.append({
            "key": s["key"], "name": s["name"], "state": s["state"],
            "done": done, "planned": planned,
            "adaptive": bool(s.get("adaptive")), "unit": s.get("unit", "frames"),
            "elapsed_s": round(max(el, 0.0), 1), "note": s.get("note", ""),
        })
    return out
