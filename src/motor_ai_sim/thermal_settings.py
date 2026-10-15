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

import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

log = logging.getLogger(__name__)

__all__ = ["COOL_MODES", "BORE_MODES", "END_FACE_MODES", "HEAT_PATHS",
           "heat_path_of", "cooling_fields",
           "cooling_issue", "thermal_panel_settings",
           "coupled_iteration_settings", "motor_cooling_patch",
           "follow_loaded_motor", "settle_application_cooling"]

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
#: The robotics mode's ONE conduction choice (2026-09-26, owner: "let's
#: simplify") — replaces the mount W/K, mount °C, sink-or-link, link size and
#: link material fields.  See ``cooling_models.HEAT_PATHS``.
HEAT_PATHS = ("housing", "shaft", "both", "none")


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


def heat_path_of(s: Mapping[str, Any]) -> str:
    """The panel's Heat path, with OLD saved settings mapped to the nearest one.

    ``heatPath`` wins when it is one of the four.  A store saved before it
    existed (2026-09-26) carries the mount fields instead: any mount — a
    conductance > 0, or the one-day-old robot-link mode — was heat leaving the
    stator by conduction into the structure, which is ``housing``; a mount
    typed as 0 was a machine bolted to nothing, which is ``none``.  A store
    with neither is the panel's default, ``housing`` (the default it replaces
    was a 2 W/K mount).  Mirrors ``thermalStore``'s ``readHeatPath``.
    """
    hp = str(s.get("heatPath") or "").strip().lower()
    if hp in HEAT_PATHS:
        return hp
    if str(s.get("mountMode") or "").strip().lower() == "link":
        return "housing"
    if str(s.get("mountG") if s.get("mountG") is not None else "").strip():
        return "housing" if _num(s.get("mountG"), 0.0) > 0.0 else "none"
    return "housing"


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
    requested_cool = _mode(s.get("coolMode"), COOL_MODES, "air")
    source = str(s.get("airSpeedSource") or "manual").strip().lower()
    cool = "air" if source == "propeller" else requested_cool
    # Propeller-cooled machines have a solid shaft: a stale saved bore mode is
    # never used (the router forces it too and reports `bore_ignored`).
    bore = ("none" if source == "propeller"
            else _mode(s.get("boreMode"), BORE_MODES, "none"))
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
        source = str(s.get("airSpeedSource") or "manual").strip().lower()
        if source == "propeller":
            propeller_id = str(s.get("propellerId") or "").strip()
            out["air_speed_source"] = "propeller"
            if propeller_id:
                out["propeller_id"] = propeller_id
        else:
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
        # THE HEAT PATH (2026-09-26) replaces the five mount fields.  'none'
        # is the router's default and is not sent, so a no-conduction request
        # keys the cache on exactly the tuple it always did; the old mount
        # fields are READ (heat_path_of maps them) but never sent again.
        hp = heat_path_of(s)
        if hp != "none":
            out["heat_path"] = hp
    return out


def cooling_issue(s: Mapping[str, Any]) -> Optional[str]:
    """What is wrong with this cooling spec, as a sentence — or ``None``.

    The mirror of ``thermalStore.coolingIssue``.  The router refuses all four of
    these by name anyway; saying it HERE means the orchestrator can refuse before
    it spends an electromagnetic run on a thermal solve that cannot answer.
    """
    requested_cool = _mode(s.get("coolMode"), COOL_MODES, "air")
    source = str(s.get("airSpeedSource") or "manual").strip().lower()
    if source not in ("manual", "propeller"):
        return "air speed source must be manual or propeller"
    cool = "air" if source == "propeller" else requested_cool
    bore = ("none" if source == "propeller"
            else _mode(s.get("boreMode"), BORE_MODES, "none"))
    if cool == "liquid" and not _num(s.get("flowLpm"), 0.0) > 0:
        return "coolant flow must be greater than 0 L/min"
    if cool == "air" and str(s.get("airSpeedSource") or "manual").strip().lower() == "propeller" \
            and not str(s.get("propellerId") or "").strip():
        return "propeller slipstream cooling needs an assigned propeller ID"
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
    # The panel no longer offers a mount (2026-09-26 — the robotics heat path
    # replaced it), so a machine with no film and no bore has no door at all.
    if cool == "none" and bore == "none":
        return "no cooled surface — the heat has nowhere to leave"
    return None


def cooling_words(c: Mapping[str, Any]) -> str:
    """THE BOUNDARY, in a handful of words — ``""`` when there is nothing to say.

    Owner, 2026-09-18 (addendum): the limited state is the machine *"at a
    given power and given cooling"*, so the answer has to NAME the cooling it
    was computed with.  The sentence itself stays one line (the no-walls rule),
    so this is what its tooltip prints.

    Takes the mapped block :func:`cooling_fields` produces — the one the coupled
    loop actually solved with — never the panel's raw fields, so what is named
    is what was used.
    """
    if not isinstance(c, Mapping) or not c:
        return ""
    amb = _num(c.get("ambient_temp"), 40.0)
    mode = str(c.get("cooling_mode") or "air")
    bits = []
    if mode == "liquid":
        bits.append("%s jacket, %g L/min in at %g °C"
                    % (str(c.get("fluid") or "water"),
                       _num(c.get("flow_lpm"), 0.0),
                       _num(c.get("fluid_temp_in_c"), amb)))
    elif mode == "manual":
        bits.append("h = %g W/m²K at %g °C" % (_num(c.get("h_conv"), 0.0), amb))
    elif mode == "none":
        bits.append("no film on the housing")
    elif mode == "robotics":
        bits.append("robotics: still air + radiation (ε = %g) at %g °C"
                    % (_num(c.get("emissivity"), 0.9), amb))
        if str(c.get("end_faces") or "still") != "none":
            bits.append("end faces %s" % str(c.get("end_faces") or "still"))
    else:
        if str(c.get("air_speed_source") or "manual") == "propeller":
            bits.append("propeller %s slipstream at %g °C"
                        % (str(c.get("propeller_id") or "(missing ID)"), amb))
        else:
            v = _num(c.get("air_speed_mps"), 0.0)
            bits.append("still air at %g °C" % amb if v <= 0.0
                        else "air %g m/s at %g °C" % (v, amb))
    if _num(c.get("mount_g_w_per_k"), 0.0) > 0.0:
        bits.append("mount %g W/K at %g °C"
                    % (_num(c.get("mount_g_w_per_k"), 0.0),
                       _num(c.get("mount_temp_c"), amb)))
    hp = str(c.get("heat_path") or "none")
    if hp != "none":
        bits.append({"housing": "stator → housing (touch 70 °C)",
                     "shaft": "rotor → shaft → bearings → structure "
                              "(touch 70 °C)",
                     "both": "stator → housing + shaft → bearings "
                             "(touch 70 °C)"}.get(hp, hp))
    bore = str(c.get("bore_mode") or "none")
    if bore == "air":
        bits.append("bore air %g m/s" % _num(c.get("bore_air_speed_mps"), 0.0))
    elif bore == "liquid":
        bits.append("bore %s %g L/min" % (str(c.get("bore_fluid") or "water"),
                                          _num(c.get("bore_flow_lpm"), 0.0)))
    elif bore == "still":
        bits.append("still air down the bore")
    if str(c.get("frame") or "") == "open":
        bits.append("open frame")
    if _num(c.get("shaft_ext_length_mm"), 0.0) > 0.0:
        bits.append("%g mm of exposed shaft"
                    % _num(c.get("shaft_ext_length_mm"), 0.0))
    return ", ".join(bits)


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


def propeller_cooling_context_issue(
        settings: Mapping[str, Any], authorization: Optional[str] = None, *,
        expected_die: Optional[str] = None,
        expected_config: Optional[str] = None) -> Optional[str]:
    """Refuse an unbound propeller source before any solver uses it.

    The Thermal settings record is shared per account, while assigned props are
    scoped to one loaded reference/configuration. This guard ties source + ID to
    the authenticated account's current last-motor selection, active family
    context, and the server's actual cooling allowlist. Pure
    :func:`cooling_fields` intentionally remains a mapper; request entry points
    call this before mapping.
    """
    if str(settings.get("airSpeedSource") or "manual").strip().lower() != "propeller":
        return None
    supplied = str(settings.get("propellerContextKey") or "").strip()
    propeller_id = str(settings.get("propellerId") or "").strip()
    if not supplied or not propeller_id:
        return ("propeller cooling needs a current motor selection and an assigned "
                "propeller; reload the motor and choose its propeller")
    try:
        from motor_ai_sim.routes.account import get_last_motor
        current = get_last_motor(authorization=authorization) or {}
        selection = current.get("selection")
        email = str(current.get("user") or "").strip().lower()
    except Exception:  # noqa: BLE001 — context lookup must fail closed
        selection, email = None, ""
    if not email or not isinstance(selection, Mapping):
        return ("sign in and load the motor again before using propeller cooling")
    die = str(selection.get("die") or "")
    config = str(selection.get("config") or "")
    ref_id = str(selection.get("ref_id") or "")
    expected_key = "%s|%s|%s|%s" % (email, die, config, ref_id)
    # A motor whose application has no propeller (a robot, a liquid-cooled
    # aviation/marine motor) can never use this source, whatever key came with
    # it: say what to do instead of the generic "different motor" text.
    blocked = _application_blocks_propeller(
        str(expected_die or die), str(expected_config or config), authorization)
    if blocked is not None:
        return _application_message(str(expected_die or die),
                                    str(expected_config or config), blocked)
    if supplied != expected_key:
        return ("propeller selection belongs to a different motor or account "
                "(chosen for %s, loaded: %s %s); reload the current motor and "
                "choose its assigned propeller"
                % (_key_motor(supplied) or "an unknown motor", die, config))
    if expected_die and die != str(expected_die):
        return "propeller selection does not match this duty's die; load that motor first"
    if expected_config and config != str(expected_config):
        return "propeller selection does not match this duty's configuration; load that motor first"
    try:
        from motor_ai_sim.routes.family import _read_ctx
        active_context = _read_ctx()
    except Exception:  # noqa: BLE001 — released/unreadable context must fail closed
        active_context = None
    if not isinstance(active_context, Mapping) or not active_context.get("die"):
        return ("the active motor-family context is unavailable or released; "
                "load the motor family again before using propeller cooling")
    active_die = str(active_context.get("die") or "")
    active_config = str(active_context.get("config") or "")
    if active_die != die or active_config != config:
        return ("propeller selection does not match the active motor family; "
                "reload the current motor and choose its assigned propeller")
    if expected_die and active_die != str(expected_die):
        return "propeller selection does not match this duty's die; load that motor first"
    if expected_config and active_config != str(expected_config):
        return "propeller selection does not match this duty's configuration; load that motor first"
    try:
        from motor_ai_sim.routes.propellers import get_cooling_options
        allowed = get_cooling_options(die=die, config=config,
                                      authorization=authorization) or {}
        ids = allowed.get("propellers") or []
        details = allowed.get("propeller_details") or []
        summary = next((p for p in details
                        if isinstance(p, Mapping) and str(p.get("id") or "") == propeller_id), None)
        if ("propeller_air" not in (allowed.get("cooling_options") or [])
                or propeller_id not in ids or not summary
                or summary.get("selectable") is not True):
            return ("the chosen propeller is not usable for this motor/configuration; "
                    "choose an assigned propeller in Thermal")
    except Exception:  # noqa: BLE001 — missing allowlist is a refusal, not a guess
        return "the assigned propeller list is unavailable; reload Thermal and try again"
    return None


# ── cooling follows the loaded motor's application (2026-10-15) ──────────────
#
# The Thermal settings are ONE record per account, but the cooling they hold
# belongs to a motor: a propeller source bound to the CIANO14 40 drone motor is
# meaningless on a robot motor.  Before this, loading another motor left the old
# source in the record and the next coupled Run was refused with "propeller
# selection belongs to a different motor".  Now (a) loading a different motor
# moves the cooling fields to that motor's application, and (b) a Run that still
# finds a propeller source on a motor whose application has no propeller is
# switched to that application's cooling instead of refused.

#: The record's fields the application decides; everything else the user typed
#: (ambient, heat path, emissivity, flow ...) is left alone.
FOLLOW_KEYS = ("coolMode", "airSpeedSource", "propellerId", "propellerContextKey")
_PROPELLER_AIR = "propeller_air"


def context_key(email: str, die: str, config: str, ref_id: Optional[str]) -> str:
    """The identity a propeller choice is bound to (mirrors the web's
    ``propellerContextKey``)."""
    return "%s|%s|%s|%s" % (str(email).strip().lower(), die, config, ref_id or "")


def _key_motor(key: str) -> str:
    parts = str(key or "").split("|")
    return "%s %s" % (parts[1], parts[2]) if len(parts) >= 3 and parts[1] else ""


def _cooling_options_for(die: str, config: str,
                         authorization: Optional[str]) -> Optional[Mapping[str, Any]]:
    try:
        from motor_ai_sim.routes.propellers import get_cooling_options
        out = get_cooling_options(die=die, config=config, authorization=authorization)
        return out if isinstance(out, Mapping) else None
    except Exception:  # noqa: BLE001 — a reader; the guard fails closed on its own
        return None


def _application_blocks_propeller(die: str, config: str,
                                  authorization: Optional[str]) -> Optional[Mapping[str, Any]]:
    """The cooling options of ``die``/``config`` when its application list
    names applications and none of them is ``propeller``; otherwise ``None``."""
    if not (die and config):
        return None
    options = _cooling_options_for(die, config, authorization)
    apps = list((options or {}).get("applications") or [])
    if options is not None and apps and "propeller" not in apps:
        return options
    return None


def _application_cooling_mode(options: Mapping[str, Any]) -> Optional[str]:
    """The outer cooling mode the application implies, ``None`` = no opinion."""
    app = options.get("application")
    if app == "robot":
        return "robotics"
    if app in ("aviation", "marine"):
        return "liquid"
    return None


def _application_message(die: str, config: str, options: Mapping[str, Any],
                         switched: bool = False) -> str:
    apps = ", ".join(options.get("applications") or []) or "not a propeller application"
    mode = {"robotics": "Robotics", "liquid": "Liquid"}.get(
        _application_cooling_mode(options) or "", "manual air")
    todo = ("Thermal cooling was switched to %s for this run" if switched
            else "set Thermal cooling to %s") % mode
    return ("%s %s is a %s motor (application: %s), so propeller cooling does not "
            "apply; %s" % (die, config, (options.get("application") or "non-propeller"),
                           apps, todo))


def _selectable_propellers(options: Mapping[str, Any]) -> List[str]:
    ids = [str(i) for i in (options.get("propellers") or [])]
    ok = {str(p.get("id")) for p in (options.get("propeller_details") or [])
          if isinstance(p, Mapping) and p.get("selectable") is True}
    return [i for i in ids if i in ok]


def _liquid_needs_flow(patch: Dict[str, str], settings: Mapping[str, Any]) -> None:
    """A jacket with no coolant flow cannot be solved: give it the panel's own
    default (8 L/min) when the record has none."""
    if patch.get("coolMode") == "liquid" and not _num(settings.get("flowLpm"), 0.0) > 0:
        patch["flowLpm"] = "8"


def motor_cooling_patch(options: Mapping[str, Any], settings: Mapping[str, Any],
                        key: str, *, motor_changed: bool) -> Dict[str, str]:
    """The Thermal fields that must change for the motor behind ``options``.

    Pure.  ``options`` is the ``/api/propellers/cooling-options`` answer for the
    loaded motor, ``key`` its :func:`context_key`.  Returns only the
    :data:`FOLLOW_KEYS` whose value differs from ``settings`` (``{}`` = leave
    the record alone):

    * propeller motor: propeller source with this motor's default propeller and
      the fresh key, unless the record is already bound to this motor with an
      allowed propeller, or the motor is unchanged and the source is manual
      (a deliberate choice on the SAME motor is kept);
    * robot motor: robotics cooling, manual source, propeller fields cleared;
    * aviation/marine: liquid cooling, manual source, propeller fields cleared;
    * no opinion (application not set): a leftover propeller source is dropped,
      the cooling mode is untouched.

    ``motor_changed`` False (the same motor loaded again) only repairs a record
    that contradicts the motor; it never rewrites a consistent one.
    """
    apps = list(options.get("applications") or [])
    primary = options.get("application")
    allowed = _selectable_propellers(options)
    propeller_ok = (_PROPELLER_AIR in (options.get("cooling_options") or [])
                    and bool(allowed)
                    and (not apps or "propeller" in apps))
    source = str(settings.get("airSpeedSource") or "manual").strip().lower()
    pid = str(settings.get("propellerId") or "").strip()
    ctx = str(settings.get("propellerContextKey") or "").strip()
    bound = source == "propeller" and ctx == key and pid in allowed

    want: Dict[str, str]
    if propeller_ok and primary in (None, "propeller"):
        if bound or (source != "propeller" and not motor_changed):
            return {}                    # same motor: manual air is the user's choice
        default = str(options.get("default_propeller") or "")
        pick = default if default in allowed else allowed[0]
        want = {"coolMode": "air", "airSpeedSource": "propeller",
                "propellerId": pick, "propellerContextKey": key}
    else:
        if bound:
            return {}                    # a propeller deliberately kept on a mixed motor
        if source != "propeller" and not motor_changed:
            return {}
        want = {"airSpeedSource": "manual", "propellerId": "", "propellerContextKey": ""}
        mode = _application_cooling_mode(options)
        if mode:
            want["coolMode"] = mode
            _liquid_needs_flow(want, settings)
    return {k: v for k, v in want.items()
            if str(settings.get(k) if settings.get(k) is not None else "") != v}


def _save_thermal_patch(email: str, patch: Mapping[str, str]) -> Dict[str, Any]:
    """Merge ``patch`` into the account's stored Thermal fields; return them."""
    import time
    from motor_ai_sim.routes import panel_settings as ps
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"
    with ps._LOCK:
        d = ps._load()
        cur = (((d.get("thermal") or {}).get(email) or {}).get("settings")) or {}
        new = {**cur, **patch}
        d.setdefault("thermal", {})[email] = {"settings": new, "updated_at": stamp}
        ps._save(d)
    return new


def _stored_thermal(email: str) -> Dict[str, Any]:
    from motor_ai_sim.routes import panel_settings as ps
    with ps._LOCK:
        d = ps._load()
    return dict((((d.get("thermal") or {}).get(email) or {}).get("settings")) or {})


def _same_motor(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return all((a.get(k) or None) == (b.get(k) or None) for k in ("die", "config", "ref_id"))


def follow_loaded_motor(email: str, selection: Mapping[str, Any],
                        previous: Optional[Mapping[str, Any]],
                        authorization: Optional[str] = None) -> Dict[str, Any]:
    """Make the account's Thermal fields follow the motor it just loaded.

    Called by ``PUT /api/me/last_motor`` after the selection is remembered.
    ``previous`` is the selection that was remembered before (``None`` = none
    yet).  The cooling is moved to the motor's application only when the motor
    CHANGED (die, configuration or catalogue reference); loading the same motor
    again repairs a contradictory record but keeps the user's manual edits.

    Never raises: a Thermal record that cannot be adjusted must not fail the
    motor load.  Returns ``{"changed", "application", "settings",
    "allowed_propellers"}`` where ``settings`` is the effective
    :data:`FOLLOW_KEYS` after the call.
    """
    out: Dict[str, Any] = {"changed": False, "application": None,
                           "settings": {}, "allowed_propellers": []}
    try:
        email = str(email or "").strip().lower()
        die, config = str(selection.get("die") or ""), str(selection.get("config") or "")
        options = _cooling_options_for(die, config, authorization)
        if not email or not die or not config or options is None:
            return out
        changed = isinstance(previous, Mapping) and not _same_motor(previous, selection)
        key = context_key(email, die, config, selection.get("ref_id"))
        cur = _stored_thermal(email)
        patch = motor_cooling_patch(options, cur, key, motor_changed=bool(changed))
        new = _save_thermal_patch(email, patch) if patch else cur
        out.update(changed=bool(patch), application=options.get("application"),
                   settings={k: str(new.get(k) or "") for k in FOLLOW_KEYS},
                   allowed_propellers=_selectable_propellers(options))
    except Exception:  # noqa: BLE001
        log.warning("thermal cooling could not follow the loaded motor", exc_info=True)
    return out


def settle_application_cooling(
        settings: Mapping[str, Any], authorization: Optional[str] = None, *,
        expected_die: Optional[str] = None,
        expected_config: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[str]]:
    """Run-time safety net: a propeller source on a motor that has no propeller.

    Returns ``(settings, note)``.  When ``settings`` carry the propeller source
    and the motor (``expected_*``, else the account's loaded motor) belongs to an
    application without a propeller, the returned settings use that
    application's cooling instead (robot: Robotics; aviation/marine: Liquid),
    the account's stored record is corrected the same way, and ``note`` says
    what was switched.  Anything else comes back unchanged with ``note`` None.
    A propeller source on a propeller motor is NOT touched here - the context
    guard refuses a stale or forged one.
    """
    if str(settings.get("airSpeedSource") or "manual").strip().lower() != "propeller":
        return dict(settings), None
    die, config, email = str(expected_die or ""), str(expected_config or ""), ""
    try:
        from motor_ai_sim.routes.account import get_last_motor
        cur = get_last_motor(authorization=authorization) or {}
        email = str(cur.get("user") or "").strip().lower()
        sel = cur.get("selection")
        if isinstance(sel, Mapping):
            die = die or str(sel.get("die") or "")
            config = config or str(sel.get("config") or "")
    except Exception:  # noqa: BLE001
        pass
    options = _application_blocks_propeller(die, config, authorization)
    if options is None:
        return dict(settings), None
    patch = {"airSpeedSource": "manual", "propellerId": "", "propellerContextKey": ""}
    mode = _application_cooling_mode(options)
    if mode:
        patch["coolMode"] = mode
        _liquid_needs_flow(patch, settings)
    if email:
        try:
            _save_thermal_patch(email, patch)
        except Exception:  # noqa: BLE001 — the run still proceeds with the switched copy
            log.warning("could not store the corrected Thermal cooling", exc_info=True)
    note = _application_message(die, config, options, switched=True)
    log.info("thermal: %s", note)
    return {**settings, **patch}, note
