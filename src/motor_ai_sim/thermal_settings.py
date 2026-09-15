"""The Thermal tab's remembered fields, as a thermal REQUEST.

WHY THIS EXISTS
---------------
The coupled orchestrator (``routes.coupled``) runs the Thermal solve itself, from
the Electromagnetic tab, and it must use *the boundary conditions the user set on
the Thermal tab* — not a default invented here.  Those fields live server-side in
``routes.panel_settings`` (``config/.panel_settings.json`` → ``thermal`` → the
caller's e-mail → ``settings``), stored exactly as the panel holds them: STRINGS,
because that is what a text field contains.

The browser already has this translation — ``web/src/stores/thermalStore.ts``
``coolingFields()`` — and this module is its mirror, field for field, including
the rule that makes it more than a rename:

    A PARAMETER THAT THE CHOSEN MODE DOES NOT USE IS NOT SENT.

An ``air_speed_mps`` beside a water jacket is a value the solver keys its cache
on and never reads, so the same machine under the same cooling can end up with
two cache entries and two answers to "was this already solved".  The panel drops
them; so does this.

Deliberately a plain module and not a method on the router: it is pure
(dict → dict), so ``tests/test_coupled.py`` pins it against the real
``config/.panel_settings.json`` without a browser, a session or a solve.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

__all__ = ["COOL_MODES", "BORE_MODES", "END_FACE_MODES", "cooling_fields",
           "cooling_issue", "thermal_panel_settings",
           "coupled_iteration_settings"]

#: The outer stator surface's boundary conditions (routes.thermal).
#: ``robotics`` (2026-09-14) is ONE mode and not four fields, by the user's
#: decision: choosing it means a still-air housing with an emissivity, an OPEN
#: bore in still air, the exposed AXIAL end faces of the coils / cores / magnets,
#: and a bolted MOUNT conductance — a joint that is bolted to an arm and sits in
#: a room.  Scattered as independent switches, a half-configured machine would
#: look exactly like a converged answer.
COOL_MODES = ("air", "liquid", "manual", "none", "robotics")
#: The rotor bore's four.  No ``manual``: a bore h nobody can quote is a number,
#: not a cooling system (the panel says the same).  ``still`` is the open,
#: unventilated bore of the robotics mode and is refused by name outside it — it
#: is evaluated with that mode's emissivity, which no other mode sends.
BORE_MODES = ("none", "air", "liquid", "still")
#: The AXIAL end faces, robotics only: ``still`` (the default — the end turns
#: stand proud of the core on both sides and the core / magnet end faces are
#: uncovered) or ``none`` (both ends buried against a gearbox and the arm).
END_FACE_MODES = ("still", "none")
#: How the machine is BUILT (2026-09-09).  ``open`` — no housing, the tooth
#: blocks between two end plates, the end turns and the axial slot channels in
#: the airflow — is the 40 mm CIANO14; ``housed`` is every other machine and the
#: model this project has always solved.
FRAME_MODES = ("housed", "open")


def _num(v: Any, default: float) -> float:
    """The panel's own ``num()``: a blank / unparsable field is its default.

    Not a refusal, because these values come from a store the user filled through
    a text box — an empty ``flowLpm`` means "not typed yet", and the request that
    results is refused BY NAME downstream (``coolingIssue`` in the panel,
    ``_validate_field_params`` in the router), which is where a missing pump
    should be reported from.
    """
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return float(default)
    return f if f == f and abs(f) != float("inf") else float(default)


def _mode(v: Any, allowed, default: str) -> str:
    s = str(v or "").strip().lower()
    return s if s in allowed else default


def cooling_fields(s: Mapping[str, Any]) -> Dict[str, Any]:
    """The COOLING half of a ``/api/thermal/field`` request, from the panel's
    stored fields.

    Mirrors ``thermalStore.coolingFields`` one-for-one; the defaults are the
    panel's own (``thermalStore``'s initial state), so a user who has never
    opened the Thermal tab gets the same still-air 40 °C machine the tab would
    show them.

    Returns only the keys that this cooling mode actually uses — see the module
    docstring.  ``ambient_temp`` is always present: it is the film the outer
    surface works against AND the temperature of the air blown through the bore,
    because it is the same air.
    """
    ambient = _num(s.get("ambientT"), 40.0)
    cool = _mode(s.get("coolMode"), COOL_MODES, "air")
    bore = _mode(s.get("boreMode"), BORE_MODES, "none")
    liquid = cool == "liquid"
    bore_liquid = bore == "liquid"
    # The exposed shaft is OFF at 0 mm, and off means neither shaft field is
    # sent — `shaft_ext_sides` beside a length of zero is exactly the unused
    # cache-splitting parameter the rule above is about.  The DIAMETER is never
    # sent at all: it is derived from the geometry, and a second place to type a
    # shaft diameter is a second place for it to disagree with the CAD.
    shaft_mm = max(0.0, _num(s.get("shaftExtMm"), 0.0))
    # THE FRAME (2026-09-09) follows the same rule as everything else here: a
    # HOUSED request must be byte-identical to the request this mapper produced
    # before the field existed, so neither key is sent unless the machine is
    # open.  `openAirSpeed` = 0 is legal and meaningful (the solver then takes
    # the housing's own air speed), which is why it is sent WITH the frame
    # rather than gated on being > 0 the way `shaftExtMm` is.
    frame = _mode(s.get("frame"), FRAME_MODES, "housed")

    out: Dict[str, Any] = {"cooling_mode": cool, "ambient_temp": ambient,
                           "bore_mode": bore}
    if cool == "manual":
        out["h_conv"] = _num(s.get("hConv"), 50.0)
    if cool == "air":
        out["air_speed_mps"] = _num(s.get("airSpeed"), 0.0)
    if liquid:
        out["fluid"] = str(s.get("fluid") or "water")
        # The inlet defaults to the AMBIENT rather than to a number of its own:
        # a coolant loop nobody has configured sits at room temperature.
        out["fluid_temp_in_c"] = _num(s.get("tIn"), ambient)
        out["flow_lpm"] = _num(s.get("flowLpm"), 8.0)
    if bore == "air":
        out["bore_air_speed_mps"] = _num(s.get("boreAirSpeed"), 0.0)
    if bore_liquid:
        out["bore_fluid"] = str(s.get("boreFluid") or "water")
        out["bore_fluid_temp_in_c"] = _num(s.get("boreTIn"), ambient)
        out["bore_flow_lpm"] = _num(s.get("boreFlowLpm"), 4.0)
    if shaft_mm > 0.0:
        out["shaft_ext_length_mm"] = shaft_mm
        out["shaft_ext_sides"] = 1 if _num(s.get("shaftExtSides"), 2.0) == 1 else 2
    if frame == "open":
        out["frame"] = "open"
        out["open_air_speed_mps"] = max(0.0, _num(s.get("openAirSpeed"), 0.0))
    # ── THE ROBOTICS MODE's own fields (2026-09-14) ─────────────────────────
    # Same rule as everything else here: they are sent ONLY by the mode that
    # reads them, so a liquid-jacket request keys the cache on exactly the tuple
    # it always did.  `emissivity` decides more than half of what leaves a small
    # housing in still air, and the two END-FACE flags decide four lumped
    # conductances, so all three ride with the mode; `endFaceSides` does not ride
    # alone, because a side count beside `end_faces: none` is exactly the unused
    # cache-splitting parameter the module docstring is about.
    if cool == "robotics":
        out["emissivity"] = min(max(_num(s.get("emissivity"), 0.9), 0.0), 1.0)
        ef = str(s.get("endFaces") or "still").strip().lower()
        out["end_faces"] = ef if ef in END_FACE_MODES else "still"
        if out["end_faces"] != "none":
            out["end_face_sides"] = (
                1 if _num(s.get("endFaceSides"), 2.0) == 1 else 2)
        # THE MOUNT is the path this machine's temperature actually hangs on,
        # and it is sent when there IS one — a zero conductance is the machine
        # bolted to nothing, which is what the request without the field already
        # means (the router still reports it, as `mount.mode: off` with a note,
        # so "nobody has typed the mount conductance yet" stays visible).  Same
        # gate the exposed shaft length has had since 2026-09-07.  The mount
        # TEMPERATURE rides with it only when it was typed: blank means the
        # ambient, and sending the ambient in its place would make a default look
        # like a number somebody chose.
        #
        # ASYMMETRY, ON PURPOSE: the ROUTER reads `mount_g_w_per_k` in every
        # cooling mode (a jacketed machine is bolted to something too, and the
        # cache key carries it whenever it is non-zero), but the PANEL only
        # offers the field with the robotics mode — so that is the only mode
        # this mapper can send it from.  Nothing is lost: a direct caller can
        # still ask for a mount beside a jacket.
        mount_g = max(0.0, _num(s.get("mountG"), 0.0))
        if mount_g > 0.0:
            out["mount_g_w_per_k"] = mount_g
            if str(s.get("mountT") or "").strip():
                out["mount_temp_c"] = _num(s.get("mountT"), ambient)
    return out


def cooling_issue(s: Mapping[str, Any]) -> Optional[str]:
    """What is wrong with this cooling spec, as a sentence — or ``None``.

    The mirror of ``thermalStore.coolingIssue``.  The router refuses all four of
    these by name anyway; saying it HERE means the orchestrator can refuse before
    it spends an electromagnetic run on a thermal solve that cannot answer.
    """
    cool = _mode(s.get("coolMode"), COOL_MODES, "air")
    bore = _mode(s.get("boreMode"), BORE_MODES, "none")
    mount_g = _num(s.get("mountG"), 0.0)
    if cool == "liquid" and not _num(s.get("flowLpm"), 0.0) > 0:
        return "coolant flow must be greater than 0 L/min"
    if cool == "manual" and not _num(s.get("hConv"), 0.0) > 0:
        return "h must be greater than 0 W/m²K"
    if bore == "liquid" and not _num(s.get("boreFlowLpm"), 0.0) > 0:
        return "bore coolant flow must be greater than 0 L/min"
    if bore == "still" and cool != "robotics":
        return ("a still (unventilated) bore belongs to the robotics mode — it "
                "radiates out of the two ends at the machine's emissivity, and "
                "that input only exists there")
    if cool == "robotics":
        eps = _num(s.get("emissivity"), 0.9)
        if not 0.0 <= eps <= 1.0:
            return "emissivity must be between 0 and 1"
    if mount_g < 0.0:
        return "the mount conductance cannot be negative"
    # WIDENED 2026-09-14: a machine bolted to a cold arm IS cooled, even with
    # every film switched off — the mount is a conductance to a held temperature
    # and the steady problem has a solution.  What has nowhere to send its heat
    # is the machine with no door at all.
    if cool == "none" and bore == "none" and not mount_g > 0:
        return ("no cooled surface and no mount conductance — the heat has "
                "nowhere to leave")
    return None


def coupled_iteration_settings(s: Mapping[str, Any]) -> Dict[str, Any]:
    """The one ITERATION field the Thermal panel owns: ``maxIter``.

    Kept apart from the cooling because it is not a boundary condition — the
    orchestrator uses it only as the default for its own ``max_iter`` when the
    request does not state one.
    """
    n = int(round(_num(s.get("maxIter"), 6.0)))
    return {"max_iter": max(1, min(40, n))}


def thermal_panel_settings(authorization: Optional[str] = None) -> Dict[str, Any]:
    """The caller's remembered Thermal-tab fields, or ``{}``.

    Goes through ``routes.panel_settings.get_panel_settings`` rather than reading
    the JSON here, so the per-user keying (and the "shared" bucket when auth is
    not enforced) has exactly one implementation.  A store that cannot be read is
    an EMPTY store, never a 500: the panel's own defaults are then used, which is
    the same machine the Thermal tab would show.
    """
    try:
        from motor_ai_sim.routes.panel_settings import get_panel_settings
        out = get_panel_settings(panel="thermal", authorization=authorization)
        s = (out or {}).get("settings")
        return dict(s) if isinstance(s, dict) else {}
    except Exception:  # noqa: BLE001 — a missing store is not a failed run
        return {}
