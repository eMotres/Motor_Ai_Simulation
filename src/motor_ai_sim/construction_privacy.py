# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The FULL-CONSTRUCTION boundary on every output that carries bearing CAD data.

Re-audit R1 (Codex Astra, 2026-10-20): #340 added ``bearings.thermal_path`` to a
configuration — shaft segments, holder sections, contact and film areas and the
open fraction of the rotor face, all MEASURED ON THE CAD ASSEMBLY.  That is the
full construction, which only the NDA + verification gate may open
(``routes.construction.require_full_construction`` / ``model3d.can_download``),
yet the catalog tree, the editor context and the thermal payloads handed it to
any account that could see the configuration — and, with ``PUBLIC_EXHIBIT=1``,
to anonymous visitors.

This module is the ONE place that decides what of it leaves the server:

* :func:`public_bearings` — a configuration's ``bearings`` block reduced to its
  public part (the cards, seals, grease, lubrication, preload and bearing
  temperature): a WHITELIST, so any CAD key added later is withheld by default;
* :func:`may_see_full_construction` — the same gate as the full STEP, as a
  boolean (never raises);
* :func:`redact` — a payload (a tree row, a thermal map, a coupled record, a
  job result) with the CAD basis taken out: ``thermal_path`` anywhere, the
  bearing network's measured segments / areas / lengths, and the measured
  rotor-face open fraction with the air speeds that are it times the wash;
* :class:`ConstructionRedactionMiddleware` — the HTTP safety net: a JSON answer
  that carries any of it goes through :func:`redact` unless the caller passes
  the gate.  It costs a substring search on every other answer.

Derived RESULTS (conductances, resistances, temperatures, watts) stay: they are
what the thermal model computed, and an engineer is entitled to them.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional

#: What of a configuration's ``bearings`` block is public — the bearing card
#: names and how they run.  Everything else (``thermal_path`` today) is
#: construction data.
PUBLIC_BEARING_KEYS = frozenset({"A", "B", "lubrication", "preload_n",
                                 "temp_source", "temp_c"})
#: What of one end (``A`` / ``B``) is public.
PUBLIC_BEARING_END_KEYS = frozenset({"card", "seals", "grease"})

#: The CAD-measured keys this module knows by name — the MCP's geometry
#: denylist re-checks them on every agent answer.
CAD_KEYS = frozenset({"thermal_path", "shaft_segments_mm", "segments_mm",
                      "arm_segments_mm", "contact_core_mm2", "film_area_mm2",
                      "char_len_mm", "rotor_face_open_fraction",
                      "holder_open_fraction"})

WITHHELD = ("withheld: measured on the CAD assembly — the full construction "
            "needs the signed NDA and a verified account")

#: The bytes that mark a JSON answer as possibly carrying the CAD basis.  A
#: compact or an indented dump, either way.
_MARKERS = (b"thermal_path", b"rotor_face_open_fraction", b"cad_overrides",
            b"part_materials", b"construction_basis",
            # a forced rotor-face film (its per-path numbers invert the CAD
            # open fraction, re-audit #3 B2) in a record stored before the
            # construction marker existed
            b"in the wash: rotating disc",
            # a fitted transient network / its split in ANY format, marked or
            # not (re-audit #4): the machine decides, not a marker
            b"surface_G_W_per_K", b"raw_fits_W_per_K", b"link_kinds",
            b"generated_total_W")
_CAD_TRUE = re.compile(rb'"cad"\s*:\s*true')

#: The rotor-channel note that prints the measured open fraction.
_FACE_NOTE = re.compile(
    r"the air reaches the rotor face at [-+0-9.eE]+ m/s \(wash [-+0-9.eE]+ m/s "
    r"× holder open fraction [-+0-9.eE]+, CAD \(bearings\.thermal_path\."
    r"rotor_face_open_fraction\)\)")
_FACE_NOTE_SUB = ("the air reaches the rotor face through the holder's "
                  "measured windows (open fraction %s)" % WITHHELD)

#: The open rotor-face film's note (``cooling_models.rotor_end_faces_open``):
#: it prints the face air speed — the wash × the MEASURED open fraction — and
#: its Reynolds number (re-audit #2 R6).  Replaced WHOLE, with no numbers.
_END_FACE_NOTE = re.compile(
    r"\d+ × [-+0-9.eE]+ cm² of (?P<name>.+?) in the wash: rotating disc at "
    r".*?flat plate at [-+0-9.eE]+ m/s.*?(?:photographs\.|$)", re.S)
#: …and the rotor axial channels' note (``rotor_axial_channels``), same speed.
_CHANNEL_NOTE = re.compile(
    r"\d+ of \d+ rotor air region\(s\) are open axial channels; the air "
    r"reaches the rotor face at [-+0-9.eE]+ m/s;.*?(?:CALIBRATION\.|$)", re.S)
_END_FACE_NEUTRAL = ("{name} in the wash through the holder's measured windows: "
                     "a forced film from the solved model (the air speed and its "
                     "Reynolds number are " + WITHHELD + ")")
_CHANNEL_NEUTRAL = ("the rotor's open axial channels are ventilated through the "
                    "holder's measured windows (the air speed is " + WITHHELD + ")")
#: The phrases that say THIS cooling block's rotor face ran on the CAD fraction.
_FACE_CAD_TEXT = ("CAD (bearings.thermal_path.rotor_face_open_fraction)",
                  "holder's measured windows")


# ── the bearings block ──────────────────────────────────────────────────────

def public_bearings(block: Any) -> Any:
    """``block`` with only its public keys (a whitelist).  ``None`` / a
    non-dict is returned unchanged."""
    if not isinstance(block, Mapping):
        return block
    out: Dict[str, Any] = {}
    for k, v in block.items():
        if k not in PUBLIC_BEARING_KEYS:
            continue
        if k in ("A", "B") and isinstance(v, Mapping):
            v = {kk: vv for kk, vv in v.items() if kk in PUBLIC_BEARING_END_KEYS}
        out[k] = v
    return out


def bearings_for(block: Any, full_construction: bool) -> Any:
    """The block as THIS caller may see it."""
    return block if full_construction else public_bearings(block)


def _is_config_bearings(v: Any) -> bool:
    """Does ``v`` look like a configuration's ``bearings`` block (and not a
    list / result block that happens to sit under the same key)?"""
    if not isinstance(v, Mapping):
        return False
    if "thermal_path" in v:
        return True
    ends = [v.get(e) for e in ("A", "B")]
    return (any(isinstance(e, Mapping) and "card" in e for e in ends)
            and any(k in v for k in ("lubrication", "temp_source", "preload_n",
                                     "temp_c")))


# ── the gate ────────────────────────────────────────────────────────────────

def may_see_full_construction(authorization: Optional[str] = None,
                              identity: Optional[Mapping[str, Any]] = None) -> bool:
    """The gate of the full STEP / the part tree / ``cad_params``
    (``model3d.can_download``: the admin; an account with an administrator's
    NDA flag; an online NDA signer once VERIFIED), as a boolean.

    ``identity`` wins; else the ``Authorization`` header; else the identity the
    middleware resolved for this request (``workspace.caller``), which is what
    an in-process caller with no header (the MCP) is acting as.  Fails CLOSED.
    """
    try:
        from motor_ai_sim import model3d as _m3
        who: Optional[Mapping[str, Any]] = identity
        if who is None and authorization:
            from motor_ai_sim.auth import caller_identity
            who = caller_identity(authorization)
        if who is None:
            from motor_ai_sim import workspace as _ws
            who = _ws.caller()
        if who is None:
            from motor_ai_sim.auth import caller_identity
            who = caller_identity(None)
        who = dict(who or {})
        rec: Dict[str, Any] = {}
        if not who.get("is_admin") and who.get("id"):
            from motor_ai_sim import users as _U
            rec = _U.get_user(who["id"]) or {}
        return bool(_m3.can_download(who, rec))
    except Exception:  # noqa: BLE001 — never open the gate on an error
        return False


# ── a payload ───────────────────────────────────────────────────────────────

def _redact_bearing_node(bn: Dict[str, Any]) -> Dict[str, Any]:
    """The bearing network's report without the measured geometry."""
    out = dict(bn)
    ends = []
    for e in bn.get("ends") or []:
        if not isinstance(e, Mapping):
            ends.append(e)
            continue
        e = dict(e)
        rows = []
        for r in e.get("resistances") or []:
            if not isinstance(r, Mapping):
                rows.append(r)
                continue
            r = {k: v for k, v in r.items() if k != "segments_mm"}
            path = str(r.get("path") or "")
            if path.startswith("shaft") or path.startswith("holder"):
                r["source"] = WITHHELD
            rows.append(r)
        if "resistances" in e:
            e["resistances"] = rows
        if isinstance(e.get("film"), Mapping):
            f = e["film"]
            e["film"] = {k: f[k] for k in ("mode", "regime", "G_W_per_K",
                                           "air_speed_mps") if k in f}
            e["film"]["source"] = WITHHELD
        ends.append(e)
    if "ends" in bn:
        out["ends"] = ends
    out["construction"] = WITHHELD
    return out


#: What of a CAD-derived FILM may leave without the gate (follow-up to re-audit
#: #2 R6): the conductance and the watts it carries, the temperatures it sits
#: between, and the plain facts of the face — a WHITELIST.  Every other field
#: (h_conv / h_total / h_disc / h_plate, Re / Nu, the regime, the air speed,
#: the mass flow, the areas and lengths the coefficient is per) is returned as
#: ``None``, because from any one of them and the public correlation the face
#: or channel air speed — the wash × the MEASURED open fraction — inverts.
FILM_PUBLIC_KEYS = frozenset({
    "mode", "film_kind", "name", "orientation", "n_faces", "emissivity",
    "G_W_per_K", "heat_removed_W", "convection_W", "radiation_W", "h_rad",
    "t_wall_c", "t_film_c", "t_sink_c", "t_mean_c", "rpm", "calibration", "note",
})
CHANNEL_PUBLIC_KEYS = frozenset({"open", "why", "G_W_per_K", "count",
                                 "magnet_wall_share", "heat_removed_W"})


def _whitelist(blk: Mapping[str, Any], keep: frozenset) -> Dict[str, Any]:
    """Same keys (the shape the web reads), ``None`` outside ``keep``."""
    return {k: (v if k in keep else None) for k, v in blk.items()}


def _redact_rotor_channels(rc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(rc)
    if "note" in out:
        out["note"] = _CHANNEL_NEUTRAL
    out["holder_open_fraction"] = None
    out["holder_open_fraction_source"] = "CAD (%s)" % WITHHELD
    for k in ("face_speed_mps",):
        if k in out:
            out[k] = None
    chans = []
    for c in rc.get("channels") or []:
        if isinstance(c, Mapping):
            c = _whitelist(c, CHANNEL_PUBLIC_KEYS)
        chans.append(c)
    if "channels" in rc:
        out["channels"] = chans
    return out


def _redact_end_faces(ef: Any) -> Any:
    """The rotor-face films in the wash run at the wash × the MEASURED open
    fraction: their air speed (and the Reynolds numbers of it) is the
    fraction, so it goes too."""
    def _one(blk: Any) -> Any:
        if not isinstance(blk, Mapping):
            return blk
        if str(blk.get("film_kind") or "") != "forced":
            return blk
        out = _whitelist(blk, FILM_PUBLIC_KEYS)
        if "note" in out:
            out["note"] = _END_FACE_NEUTRAL.format(
                name=str(blk.get("name") or "rotor end face"))
        return out
    if isinstance(ef, Mapping):
        return {k: _one(v) for k, v in ef.items()}
    if isinstance(ef, list):
        return [_one(v) for v in ef]
    return ef


# -- THE PROTECTED CONSTRUCTION SCHEMA (re-audit #3 B1), defined ONCE --------
#
# Every block of a die / configuration document that the full-construction
# routes (``routes.construction``: cad_params GET / PUT / import / rebuild,
# part_tree; ``routes.family`` part_materials GET / PUT) read or write behind
# ``require_full_construction``:
#
#   * ``cad_overrides``  - the CAD-only parameter overrides (``cad_params.
#     OVERRIDES_KEY`` / ``cad.store.CAD_OVERRIDES_KEY``): the dimensions;
#   * ``part_materials`` - die.yaml's per-part materials of the full parts list;
#   * ``bearings.thermal_path`` - the bearing / holder basis measured on the
#     assembly (and, fail closed, every ``bearings`` key off the public
#     whitelist ``PUBLIC_BEARING_KEYS``);
#   * any OTHER ``cad_*`` key of a die / configuration document (fail closed:
#     a construction key added tomorrow is protected until it is whitelisted
#     here); ``cad_variant`` (full | frameless, what the catalog row says) is
#     the one public one.
PROTECTED_KEYS = frozenset({"thermal_path", "cad_overrides", "part_materials"})
DOC_PUBLIC_CAD_KEYS = frozenset({"cad_variant"})
#: The keys that make a mapping a die / configuration DOCUMENT.
_DOC_KEYS = ("geometry_overrides", "geometry", "duties", "winding")


def _is_catalog_doc(d: Mapping[str, Any]) -> bool:
    return any(k in d for k in _DOC_KEYS)


def protected_doc_keys(d: Mapping[str, Any]) -> list:
    """The construction keys present in a die / configuration document."""
    out = [k for k in d if k in PROTECTED_KEYS]
    if _is_catalog_doc(d):
        out += [k for k in d if str(k).startswith("cad_")
                and k not in DOC_PUBLIC_CAD_KEYS and k not in out]
    b = d.get("bearings")
    if isinstance(b, Mapping) and set(b) - PUBLIC_BEARING_KEYS:
        out.append("bearings")
    return out


# -- CAD-DERIVED COOLING PATHS (re-audit #3 B2) ------------------------------
#
# Hiding a film's coefficient is not enough: the public mesh gives the face
# area, so a path's G - or its watts and the two temperatures - gives h back,
# then the air speed, then the measured open fraction.  For a caller without
# the gate a cooling result whose rotor-side axial paths ran on a CAD-measured
# input returns NO per-path detail for those paths (rotor end faces in the
# wash, rotor channels, the bearing / holder network), and its heat budget only
# the motor-level aggregates: the losses, the heat to air, the residual.  The
# part temperatures stay.  One line says where the detail went.
NDA_DETAIL = "detailed cooling paths with NDA"
#: The marker a derived record (a fitted network, a heat-path model, a stored
#: compact map) carries when its cooling was CAD-derived.
CONSTRUCTION_BASIS = "CAD-measured construction input"
#: What of a heat budget stays: generation, totals and the solve's closure.
BUDGET_PUBLIC_KEYS = frozenset({
    "losses_W", "losses_total_W", "losses_at_bearing_nodes_W", "em_loss_total_W",
    "mech_loss_total_W", "mech_loss_in_map_W", "mech_loss_at_nodes_W",
    "bearing_friction_W", "bearing_friction_in_map_W",
    "bearing_friction_at_nodes_W", "bearing_friction_not_modelled_W",
    "windage_W", "windage_not_modelled_W", "residual_W", "residual_pct",
    "frame", "symmetry_mult", "passes", "n_slip_ties", "n_islands_rotor",
    "n_islands_stator", "note", "heat_to_air_W", "detail"})
#: Paths of the fitted transient network that ride on the CAD basis.
CAD_NETWORK_G = frozenset({"r_bearings", "s_holders", "r_s_holders",
                           "r_channels", "m_channels"})
CAD_SURFACES = frozenset({"rotor_ends", "magnet_ends"})
#: Lines of a time-averaged split (``thermal_duty_cycle.average_split``) that
#: are, or isolate by subtraction, a CAD-derived path.
CAD_SPLIT_KEYS = frozenset({"rotor_end_faces_W", "magnet_end_faces_W",
                            "bearings_W", "stator_holders_W", "rotor_channels_W",
                            "magnet_channels_W", "rotor_side_W", "stator_side_W",
                            "rotor_pct", "stator_pct", "gap_W"})


#: The end faces that sit in the wash through the holder's windows.
CAD_FACES = ("rotor", "magnet")


def _forced_faces(ef: Any) -> bool:
    """A rotor / magnet face on a forced film (a full payload says
    ``film_kind``; a compact stored copy keeps only ``mode``)."""
    if isinstance(ef, Mapping):
        blocks = [ef.get(k) for k in CAD_FACES] + list(ef.values())
    elif isinstance(ef, list):
        blocks = ef
    else:
        blocks = []
    return any(isinstance(b, Mapping) and "forced" in (str(b.get("film_kind") or ""),
                                                       str(b.get("mode") or ""))
               for b in blocks)


def cooling_is_cad_sensitive(c: Any) -> bool:
    """Did this cooling result's rotor-side axial paths run on a CAD input?
    Fails closed on a stored record that cannot say (no source key, no bearing
    network) but has a forced rotor-face film."""
    if not isinstance(c, Mapping):
        return False
    if c.get("construction_basis"):
        return True
    bn = c.get("bearing_node")
    if isinstance(bn, Mapping) and bn.get("cad"):
        return True
    if str(c.get("holder_open_fraction_source") or "").startswith("CAD"):
        return True
    rc = c.get("rotor_channels")
    if isinstance(rc, Mapping):
        if str(rc.get("holder_open_fraction_source") or "").startswith("CAD"):
            return True
        _txt = json.dumps([rc.get("notes"), rc.get("note")], ensure_ascii=False)
        if any(t in _txt for t in _FACE_CAD_TEXT):
            return True
    if ("holder_open_fraction_source" not in c and "bearing_node" not in c
            and _forced_faces(c.get("end_faces"))):
        return True
    return False


def config_cooling_is_cad(die: str, cfg: str, cfg_doc: Optional[Mapping[str, Any]] = None) -> bool:
    """Does anything this configuration's report would print rest on the CAD
    basis?  Its own document (a protected key), any stored per-duty thermal /
    coupled map of it, or the workspace's last thermal / coupled result.
    Fails CLOSED: an unreadable store answers yes."""
    try:
        if isinstance(cfg_doc, Mapping) and protected_doc_keys(cfg_doc):
            return True
        from motor_ai_sim import duty_results as _dr
        for rec in (_dr.get(die, cfg) or {}).values():
            if not isinstance(rec, Mapping):
                continue
            for kind in ("thermal", "coupled"):
                blk = rec.get(kind)
                if isinstance(blk, Mapping) and _any_cad_cooling(blk):
                    return True
        from motor_ai_sim.routes import thermal as _th
        _th._load_last()
        for k in ("field", "coupled"):
            e = _th._LAST.get(k) or {}
            if _any_cad_cooling(e.get("result")):
                return True
        return False
    except Exception:  # noqa: BLE001
        return True


def _any_cad_cooling(obj: Any, depth: int = 0) -> bool:
    """A CAD-sensitive ``cooling`` block anywhere inside ``obj`` (a few levels)."""
    if depth > 6:
        return False
    if isinstance(obj, Mapping):
        c = obj.get("cooling")
        if isinstance(c, Mapping) and cooling_is_cad_sensitive(c):
            return True
        return any(_any_cad_cooling(v, depth + 1) for k, v in obj.items()
                   if isinstance(v, (Mapping, list)) and k not in (
                       "T_node", "triangles", "vertices", "domain_per_tri"))
    if isinstance(obj, list):
        return any(_any_cad_cooling(v, depth + 1) for v in obj[:50]
                   if isinstance(v, (Mapping, list)))
    return False


def _bearing_node_summary(bn: Mapping[str, Any]) -> Dict[str, Any]:
    """The bearing temperatures (a part temperature) and nothing per path."""
    ends = []
    for e in bn.get("ends") or []:
        if not isinstance(e, Mapping):
            continue
        ts = [e.get(k) for k in ("inner_ring_c", "balls_c", "outer_ring_c")
              if isinstance(e.get(k), (int, float))]
        ends.append({"end": e.get("end"), "card": e.get("card"),
                     "bearing_max_c": (max(ts) if ts else None)})
    return {"mode": bn.get("mode"), "cad": bool(bn.get("cad")),
            "t_bearing_max_c": bn.get("t_bearing_max_c"),
            "advisory": bn.get("advisory"), "ends": ends,
            "notes": [NDA_DETAIL], "detail": NDA_DETAIL}


def _aggregate_budget(hb: Mapping[str, Any]) -> Dict[str, Any]:
    out = {k: (v if k in BUDGET_PUBLIC_KEYS else None) for k, v in hb.items()}
    tot = hb.get("losses_total_W", hb.get("losses_W"))
    res = hb.get("residual_W")
    if isinstance(tot, (int, float)):
        out["heat_to_air_W"] = round(float(tot) - float(res or 0.0), 2)
    out["detail"] = NDA_DETAIL
    return out


def _withhold_face(b: Any, force: bool = False) -> Any:
    if isinstance(b, Mapping) and (force or "forced" in (
            str(b.get("film_kind") or ""), str(b.get("mode") or ""))):
        return {"mode": b.get("mode"), "film_kind": b.get("film_kind") or "forced",
                "name": b.get("name"), "note": NDA_DETAIL, "detail": NDA_DETAIL}
    return b


def _withhold_cad_paths(c: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(c)
    bn = out.get("bearing_node")
    if isinstance(bn, Mapping) and bn.get("mode") not in (None, "off"):
        out["bearing_node"] = _bearing_node_summary(bn)
    rc = out.get("rotor_channels")
    if isinstance(rc, Mapping):
        rc2 = {"mode": rc.get("mode"), "detail": NDA_DETAIL, "notes": [NDA_DETAIL]}
        if "note" in rc:
            rc2["note"] = NDA_DETAIL
        out["rotor_channels"] = rc2
    ef = out.get("end_faces")
    if isinstance(ef, Mapping):
        # the rotor / magnet faces always (a compact copy cannot say which film
        # they were on), any other forced film, and the block's own totals —
        # a total minus the public faces is the rotor face again
        out["end_faces"] = {k: (_withhold_face(v, force=k in CAD_FACES)
                                if isinstance(v, Mapping) else
                                (None if k in ("heat_removed_W", "G_W_per_K") else v))
                            for k, v in ef.items()}
    elif isinstance(ef, list):
        out["end_faces"] = [_withhold_face(v) for v in ef]
    if isinstance(out.get("heat_budget"), Mapping):
        out["heat_budget"] = _aggregate_budget(out["heat_budget"])
    out["holder_open_fraction_source"] = "CAD (%s)" % WITHHELD
    out["construction_basis"] = CONSTRUCTION_BASIS
    out["cooling_detail"] = NDA_DETAIL
    return out


def _redact_network(n: Mapping[str, Any]) -> Dict[str, Any]:
    """A fitted transient network (``Network.as_record``) of a CAD-derived map."""
    out = dict(n)
    if isinstance(n.get("G_W_per_K"), Mapping):
        out["G_W_per_K"] = {k: (None if (k in CAD_NETWORK_G or k in CAD_SURFACES) else v)
                            for k, v in n["G_W_per_K"].items()}
    if isinstance(n.get("areas_m2"), Mapping):
        out["areas_m2"] = {k: (None if k in CAD_SURFACES else v)
                           for k, v in n["areas_m2"].items()}
    if isinstance(n.get("links"), Mapping):
        links = {}
        for k, v in n["links"].items():
            if k in ("bearing_node", "r_channels", "m_channels") or k in CAD_NETWORK_G:
                v = {"kind": (v or {}).get("kind"), "nodes": (v or {}).get("nodes"),
                     "G_W_per_K": None, "basis": NDA_DETAIL}
            links[k] = v
        out["links"] = links
    cal = n.get("calibration")
    if isinstance(cal, Mapping):
        cal = dict(cal)
        for k in ("bearings_W", "rotor_channels_W", "rotor_channels_magnet_W"):
            if k in cal:
                cal[k] = None
        for k in ("surface_G_W_per_K", "surface_t_fit_c", "surface_film_kind"):
            if isinstance(cal.get(k), Mapping):
                cal[k] = {kk: (None if kk in CAD_SURFACES else vv)
                          for kk, vv in cal[k].items()}
        out["calibration"] = cal
    return out


def _redact_split(sp: Mapping[str, Any]) -> Dict[str, Any]:
    def _cad(k: str) -> bool:
        k = str(k)
        return (k in CAD_SPLIT_KEYS or "holder" in k
                or ("channel" in k and k != "slot_channels_W"))
    out = {k: (None if _cad(k) else v) for k, v in sp.items()}
    out["detail"] = NDA_DETAIL
    return out


def _redact_heat_path_model(m: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(m)
    sinks = []
    for s_ in m.get("sinks") or []:
        if isinstance(s_, Mapping) and s_.get("id") in ("end_face_rotor", "end_face_magnet"):
            s_ = {k: (v if k in ("id", "group", "label", "short", "mode", "active",
                                 "placement") else None) for k, v in s_.items()}
            s_["note"] = NDA_DETAIL
            s_["detail"] = []
        sinks.append(s_)
    out["sinks"] = sinks
    if isinstance(m.get("totals"), Mapping):
        out["totals"] = {k: (None if k in ("stator_side_W", "rotor_side_W",
                                           "stator_side_pct", "rotor_side_pct")
                             else v) for k, v in m["totals"].items()}
    out["detail"] = NDA_DETAIL
    return out


def _redact_cooling(c: Dict[str, Any]) -> Dict[str, Any]:
    if cooling_is_cad_sensitive(c):
        return _withhold_cad_paths(c)
    out = dict(c)
    bn = out.get("bearing_node")
    if isinstance(bn, Mapping) and bn.get("cad"):
        out["bearing_node"] = _redact_bearing_node(dict(bn))
    rc = out.get("rotor_channels")
    face_cad = str(out.get("holder_open_fraction_source") or "").startswith("CAD")
    if isinstance(rc, Mapping):
        # a payload stored before the cooling-level source key existed still
        # says it in the channels' own notes (re-audit #2: stored payloads)
        _txt = json.dumps(rc.get("notes") or [], ensure_ascii=False)
        if any(t in _txt for t in _FACE_CAD_TEXT):
            face_cad = True
    if isinstance(rc, Mapping) and (face_cad or str(
            rc.get("holder_open_fraction_source") or "").startswith("CAD")):
        out["rotor_channels"] = _redact_rotor_channels(dict(rc))
        face_cad = True
    if face_cad:
        out["holder_open_fraction_source"] = "CAD (%s)" % WITHHELD
        if "end_faces" in out:
            out["end_faces"] = _redact_end_faces(out["end_faces"])
    return out


# -- FITTED NETWORKS ON A CAD-COOLED MACHINE (re-audit #4) -------------------
#
# A fitted transient network is a set of conductances FITTED to the steady
# map: every one of them is a watt over a kelvin of that map, and an internal
# link (magnet -> rotor: P_mag minus what the magnet faces took) gives the
# withheld path back by subtraction.  Field-by-field redaction cannot close
# that.  So for a caller without the gate NO fitted network of a CAD-cooled
# machine leaves the server - fresh, stored, exported or cached alike - and its
# split and the per-node loss series go with it: what stays is the part
# temperatures over the cycle and the motor-level totals.
#
# "CAD-cooled" is decided from the MACHINE (its die / configuration documents
# in any layer carry a CAD thermal basis), or from the record's own text naming
# CAD.  A record whose machine cannot be determined is treated as CAD-cooled.
NDA_NETWORK = "detailed cooling network with NDA"
#: Text in a record that says its cooling ran on the CAD basis.
_CAD_RECORD_TEXT = ("thermal_path", "rotor_face_open_fraction",
                    "holder's measured windows", "measured on the assembly",
                    "CAD: ", CONSTRUCTION_BASIS)
_NODE_KEYS = ("winding", "stator", "rotor", "magnet")


def _doc_has_cad_cooling(doc: Any) -> bool:
    if not isinstance(doc, Mapping):
        return False
    b = doc.get("bearings")
    if isinstance(b, Mapping) and set(b) - PUBLIC_BEARING_KEYS:
        return True
    try:
        txt = json.dumps(doc, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return True
    return "thermal_path" in txt or "rotor_face_open_fraction" in txt


def machine_is_cad_cooled(die: Optional[str], cfg: Optional[str]) -> bool:
    """Does this machine's cooling rest on a CAD-measured input?  Read from
    the machine's own documents in EVERY layer (a workspace copy of an
    ungated caller has been scrubbed, the shared original has not).  ``True``
    when the machine cannot be found or read (fail closed)."""
    if not die or not cfg:
        return True
    try:
        from pathlib import Path
        import yaml
        from motor_ai_sim import workspace as _W
        name = str(die)
        try:
            name = _W.split_published_label(name)[0] or name
        except Exception:  # noqa: BLE001
            pass
        dirs = []
        try:
            from motor_ai_sim.routes import family as _fam
            dirs.append(Path(str(_fam._dies_dir())))
        except Exception:  # noqa: BLE001
            pass
        try:
            dirs += [Path(str(l.dies_dir)) for l in _W.layers()]
        except Exception:  # noqa: BLE001
            pass
        found = False
        for dd in dirs:
            for fn in (f"{cfg}.yaml", "die.yaml"):
                f = dd / name / fn
                if f.is_file():
                    found = found or fn != "die.yaml"
                    if _doc_has_cad_cooling(yaml.safe_load(f.read_text("utf-8"))):
                        return True
        return not found
    except Exception:  # noqa: BLE001
        return True


def _record_names_cad(d: Mapping[str, Any]) -> bool:
    try:
        txt = json.dumps(d, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return True
    return any(t in txt for t in _CAD_RECORD_TEXT)


def _is_network(d: Mapping[str, Any]) -> bool:
    return isinstance(d.get("G_W_per_K"), Mapping) and any(
        k in d for k in ("links", "calibration", "link_kinds", "areas_m2",
                         "C_J_per_K", "h_sources"))


def _is_split(d: Mapping[str, Any]) -> bool:
    return "generated_total_W" in d and any(
        k in d for k in ("rotor_side_W", "stator_side_W", "housing_W", "generated_W"))


def _is_cycle(d: Mapping[str, Any]) -> bool:
    return isinstance(d.get("P_W"), Mapping) and ("T_c" in d or "t_s" in d)


def _withheld_network(d: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"withheld": True, "detail": NDA_NETWORK}
    for k in ("calibration_duty", "C_total_J_per_K", "active_nodes"):
        if k in d:
            out[k] = d[k]
    return out


def _withheld_split(d: Mapping[str, Any]) -> Dict[str, Any]:
    gen = d.get("generated_total_W")
    clo = d.get("closure_W")
    out: Dict[str, Any] = {"generated_total_W": gen, "detail": NDA_NETWORK,
                           "basis": d.get("basis"),
                           "closure_pct": d.get("closure_pct")}
    if isinstance(gen, (int, float)):
        out["heat_to_air_W"] = round(float(gen) - float(clo or 0.0), 3)
    return out


def _total_series(pw: Mapping[str, Any]) -> Dict[str, Any]:
    series = [v for v in pw.values() if isinstance(v, list)]
    if not series:
        return {}
    n = min(len(v) for v in series)
    return {"total": [round(sum(float(v[i] or 0.0) for v in series), 3) for i in range(n)]}


class _Ctx:
    __slots__ = ("machine", "memo", "cad_text")

    def __init__(self, machine=None, memo=None, cad_text=False):
        self.machine = machine
        self.memo = {} if memo is None else memo
        self.cad_text = cad_text

    def at(self, machine, record: Any = None) -> "_Ctx":
        """The context of a RECORD of ``machine``: its own text naming CAD
        marks everything inside it."""
        return _Ctx(machine, self.memo,
                    self.cad_text or (record is not None and _record_names_cad(record)))

    def cad(self, record: Mapping[str, Any]) -> bool:
        if self.cad_text or _record_names_cad(record):
            return True
        if self.machine is None:
            return True                                # cannot tell: fail closed
        if self.machine not in self.memo:
            self.memo[self.machine] = machine_is_cad_cooled(*self.machine)
        return self.memo[self.machine]


def _machine_of(d: Mapping[str, Any]):
    die = d.get("die")
    cfg = d.get("configuration", d.get("config"))
    if isinstance(die, str) and isinstance(cfg, str) and die and cfg:
        return (die, cfg)
    return None


def _is_duty_store(v: Any) -> bool:
    """``{die: {cfg: {duty: {kind: record}}}}`` - the duty-results ledger."""
    if not isinstance(v, Mapping) or not v:
        return False
    return all(isinstance(x, Mapping) and all(isinstance(y, Mapping) for y in x.values())
               for x in v.values())


def redact(obj: Any) -> Any:
    """``obj`` without the CAD basis (a new object; ``obj`` is not touched)."""
    return _redact(obj, _Ctx())


def _redact(obj: Any, ctx: "_Ctx") -> Any:
    if isinstance(obj, Mapping):
        m = _machine_of(obj)
        if m is not None:
            ctx = ctx.at(m, obj)
        d: Dict[str, Any] = {}
        _doc = _is_catalog_doc(obj)
        for k, v in obj.items():
            if k in PROTECTED_KEYS:
                continue
            if (_doc and str(k).startswith("cad_")
                    and k not in DOC_PUBLIC_CAD_KEYS):
                continue                      # B1: fail closed on the schema
            if k == "bearings" and _is_config_bearings(v):
                d[k] = public_bearings(v)
                continue
            if k == "results" and _is_duty_store(v):
                # the ledger: the machine of every row is its own key path
                d[k] = {die: {cfg: _redact(rows, ctx.at((str(die), str(cfg)), rows))
                              for cfg, rows in byc.items()}
                        for die, byc in v.items()}
                continue
            d[k] = _redact(v, ctx)
        if (isinstance(d.get("bearing_node"), Mapping)
                or isinstance(d.get("rotor_channels"), Mapping)
                or "holder_open_fraction_source" in d
                or "construction_basis" in d
                or ("heat_budget" in d and "end_faces" in d)):
            d = _redact_cooling(d)
        if _is_network(obj) and ctx.cad(obj):
            return _withheld_network(obj)
        if _is_split(obj) and ctx.cad(obj):
            return _withheld_split(obj)
        if _is_cycle(d) and ctx.cad(obj):
            d["P_W"] = _total_series(d["P_W"])
            d["detail"] = NDA_NETWORK
        if (isinstance(d.get("losses_W"), Mapping)
                and set(d["losses_W"]) & set(_NODE_KEYS)
                and "total_W" in d and ctx.cad(obj)):
            d["losses_W"] = None                       # the per-node split goes
        if "sinks" in d and "schema_version" in d and d.get("construction_basis"):
            d = _redact_heat_path_model(d)
        return d
    if isinstance(obj, list):
        return [_redact(v, ctx) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v, ctx) for v in obj)
    if isinstance(obj, str):
        if "holder open fraction" in obj:
            obj = _FACE_NOTE.sub(_FACE_NOTE_SUB, obj)
        if "flat plate at" in obj and "in the wash: rotating disc" in obj:
            obj = _END_FACE_NOTE.sub(lambda m: _END_FACE_NEUTRAL.format(
                name=m.group("name")), obj)
        if "open axial channels; the air reaches the rotor face at" in obj:
            obj = _CHANNEL_NOTE.sub(_CHANNEL_NEUTRAL, obj)
        return obj
    return obj


def needs_redaction(body: bytes) -> bool:
    """Might this JSON body carry the CAD basis?  A substring search."""
    if not body:
        return False
    return any(m in body for m in _MARKERS) or bool(_CAD_TRUE.search(body))


def redact_for(obj: Any, *, authorization: Optional[str] = None,
               identity: Optional[Mapping[str, Any]] = None) -> Any:
    """``obj`` as this caller may see it."""
    if may_see_full_construction(authorization, identity):
        return obj
    return redact(obj)


# ── the HTTP safety net ─────────────────────────────────────────────────────

class ConstructionRedactionMiddleware:
    """Pure ASGI: every JSON answer under ``/api/`` that carries the CAD basis
    is redacted unless the caller passes the full-construction gate.

    Only ``application/json`` bodies are buffered (they are built whole in
    memory anyway); a stream, a file or an encoded body passes untouched.  The
    gate is evaluated only when the body carries a marker, so an ordinary
    answer pays one substring search.
    """

    def __init__(self, app, prefixes: Iterable[str] = ("/api/",)):
        self.app = app
        self.prefixes = tuple(prefixes)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not str(scope.get("path") or "").startswith(self.prefixes):
            await self.app(scope, receive, send)
            return
        auth_hdr: Optional[str] = None
        for k, v in scope.get("headers") or ():
            if k == b"authorization":
                auth_hdr = v.decode("latin-1")
                break
        state: Dict[str, Any] = {"start": None, "buffer": [], "json": False}

        async def _send(message):
            t = message.get("type")
            if t == "http.response.start":
                headers = list(message.get("headers") or ())
                ctype = b""
                enc = False
                for k, v in headers:
                    lk = k.lower()
                    if lk == b"content-type":
                        ctype = v.lower()
                    elif lk == b"content-encoding":
                        enc = True
                state["json"] = ctype.startswith(b"application/json") and not enc
                if not state["json"]:
                    await send(message)
                    return
                state["start"] = message
                return
            if t == "http.response.body" and state["json"]:
                state["buffer"].append(message.get("body") or b"")
                if message.get("more_body"):
                    return
                body = b"".join(state["buffer"])
                start = state["start"]
                if needs_redaction(body) and not may_see_full_construction(auth_hdr):
                    try:
                        body = json.dumps(redact(json.loads(body)),
                                          ensure_ascii=False,
                                          separators=(",", ":")).encode("utf-8")
                    except Exception:  # noqa: BLE001 — fail CLOSED
                        body = json.dumps({"detail": {
                            "code": "nda_required",
                            "message": "This answer carries construction data; "
                                       "it needs the signed NDA and a verified "
                                       "account."}}).encode("utf-8")
                        start = dict(start, status=403)
                    start = dict(start)
                    start["headers"] = [(k, v) for k, v in start.get("headers") or ()
                                        if k.lower() != b"content-length"] + [
                        (b"content-length", str(len(body)).encode("latin-1"))]
                await send(start)
                await send({"type": "http.response.body", "body": body,
                            "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, _send)


def install(app) -> None:
    app.add_middleware(ConstructionRedactionMiddleware)


# -- documents at rest: copies, exports, the one-off cleanup (re-audit #2 R5) --
#
# The JSON middleware guards ANSWERS.  A workspace copy (layered copy-on-write)
# and the account ZIP export are not answers: they are files, and a file that
# holds the CAD basis is the basis.  So the same redaction runs on the
# documents themselves: when they are written into a workspace for a caller
# without the gate, when they are packed into such an owner's export, and
# (once, by hand) over every copy written before this rule existed.

#: The document kinds that can carry the basis (a configuration yaml, a stored
#: thermal / coupled payload, a JSON-lines log).  ``.gz`` wraps any of them.
DOC_SUFFIXES = (".yaml", ".yml", ".json", ".jsonl")


def subject_may_see_full_construction(email: Optional[str]) -> bool:
    """The gate for the OWNER of some data (an export's subject, a workspace's
    owner) rather than for the current request's caller.  Fails closed."""
    try:
        ident = str(email or "").strip().lower()
        if not ident:
            return False
        from motor_ai_sim import auth as _auth
        from motor_ai_sim import users as _U
        rec = _U.get_user(ident) or {}
        admins = getattr(_auth, "_ADMIN_EMAILS", None) or set()
        is_admin = (ident in {str(a).strip().lower() for a in admins}
                    or str(rec.get("role") or "") == "admin")
        return may_see_full_construction(identity={"id": ident,
                                                   "is_admin": is_admin})
    except Exception:  # noqa: BLE001
        return False


def caller_may_keep_construction() -> bool:
    """For a WRITE seam with no header in hand (``_save_yaml``,
    copy-on-write): the identity the middleware resolved for this request."""
    return may_see_full_construction(None)


def scrub_bytes(name: str, data: bytes) -> Optional[bytes]:
    """``data`` (the file ``name``) with the CAD basis removed, or ``None``
    when there is nothing to remove.  Raises ``ValueError`` when the file
    carries a marker but cannot be parsed: the caller then withholds it rather
    than ship it unread."""
    import gzip
    low = name.lower()
    if low.endswith(".gz"):
        try:
            inner = gzip.decompress(data)
        except OSError as exc:
            raise ValueError("unreadable gzip: %s" % exc)
        out = scrub_bytes(name[:-3], inner)
        return None if out is None else gzip.compress(out)
    if not low.endswith(DOC_SUFFIXES):
        return None
    if not needs_redaction(data) and not (
            low.endswith((".yaml", ".yml")) and b"cad_" in data):
        return None
    try:
        text = data.decode("utf-8")
        if low.endswith((".yaml", ".yml")):
            import yaml
            doc = yaml.safe_load(text)
            return yaml.safe_dump(redact(doc), sort_keys=False,
                                  allow_unicode=True).encode("utf-8")
        if low.endswith(".jsonl"):
            lines = [json.dumps(redact(json.loads(ln)), ensure_ascii=False)
                     if ln.strip() else ln for ln in text.splitlines()]
            return ("\n".join(lines)
                    + ("\n" if text.endswith("\n") else "")).encode("utf-8")
        return json.dumps(redact(json.loads(text)), ensure_ascii=False,
                          indent=1).encode("utf-8")
    except Exception as exc:  # noqa: BLE001 -- yaml / json / unicode errors
        raise ValueError("%s: unparseable (%s)" % (name, exc))


def scrub_file(path, *, dry_run: bool = False) -> str:
    """Scrub one file in place (atomic replace): ``"clean"``, ``"scrubbed"``
    (``"would-scrub"`` on a dry run) or ``"error: ..."``."""
    import os
    from pathlib import Path
    p = Path(path)
    try:
        data = p.read_bytes()
        new = scrub_bytes(p.name, data)
    except (OSError, ValueError) as exc:
        return "error: %s" % exc
    if new is None or new == data:
        return "clean"
    if dry_run:
        return "would-scrub"
    tmp = p.with_name(p.name + ".scrub.tmp")
    tmp.write_bytes(new)
    os.replace(str(tmp), str(p))
    return "scrubbed"


def _is_doc(fn: str) -> bool:
    low = fn.lower()
    return low.endswith(DOC_SUFFIXES) or (
        low.endswith(".gz") and low[:-3].endswith(DOC_SUFFIXES))


def scrub_tree(root, *, dry_run: bool = False) -> Dict[str, Any]:
    """Every document under ``root`` (symlinks not followed)."""
    import os
    from pathlib import Path
    out: Dict[str, Any] = {"root": str(root), "files": 0, "scrubbed": [],
                           "errors": []}
    r = Path(root)
    if not r.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(r, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if fp.is_symlink() or not _is_doc(fn):
                continue
            out["files"] += 1
            st = scrub_file(fp, dry_run=dry_run)
            if st in ("scrubbed", "would-scrub"):
                out["scrubbed"].append(str(fp))
            elif st.startswith("error"):
                out["errors"].append({"file": str(fp), "error": st})
    return out


def scrub_workspaces(*, dry_run: bool = True) -> Dict[str, Any]:
    """THE ONE-OFF CLEANUP (re-audit #2 R5c): scrub the CAD basis out of every
    per-account workspace and published namespace whose OWNER does not pass the
    full-construction gate.  An owner who passes it keeps their copies; a
    workspace whose owner cannot be identified is scrubbed (fails closed).
    Shared originals are never touched.  ``dry_run`` (the default) only lists."""
    from motor_ai_sim import users as _U
    from motor_ai_sim import workspace as _W
    report: Dict[str, Any] = {"dry_run": bool(dry_run), "workspaces": [],
                              "published": [], "kept_for_gated_owner": []}
    root = _W.workspaces_root()
    if root is None:
        report.update(note="WORKSPACES_ROOT is not set: a single-user install "
                           "has no per-account copies", files_scrubbed=0, errors=0)
        return report
    owners: Dict[str, str] = {}
    for u in _U.list_users():
        email = str((u or {}).get("email") or "").strip().lower()
        if email:
            owners[_W.workspace_id(email)] = email
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        email = owners.get(d.name, "")
        if email and subject_may_see_full_construction(email):
            report["kept_for_gated_owner"].append(d.name)
            continue
        report["workspaces"].append(dict(scrub_tree(d, dry_run=dry_run),
                                         workspace=d.name, owner_known=bool(email)))
    try:
        pub = _W.published_root()
    except Exception:  # noqa: BLE001
        pub = None
    if pub is not None and pub.is_dir():
        for ns in sorted(p for p in pub.iterdir() if p.is_dir() and not p.is_symlink()):
            email = (_W._owner_email(ns) or owners.get(ns.name, "")).strip().lower()
            if email and subject_may_see_full_construction(email):
                report["kept_for_gated_owner"].append("published/" + ns.name)
                continue
            report["published"].append(dict(scrub_tree(ns, dry_run=dry_run),
                                             namespace=ns.name,
                                             owner_known=bool(email)))
    rows = report["workspaces"] + report["published"]
    report["files_scrubbed"] = sum(len(r["scrubbed"]) for r in rows)
    report["errors"] = sum(len(r["errors"]) for r in rows)
    return report


def _main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.construction_privacy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sw = sub.add_parser("scrub-workspaces",
                        help="remove the CAD bearing basis from the workspace / "
                             "published copies of owners without the NDA gate")
    sw.add_argument("--apply", action="store_true",
                    help="write the changes (default: a dry run that only lists)")
    a = ap.parse_args(argv)
    rep = scrub_workspaces(dry_run=not a.apply)
    json.dump(rep, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if rep.get("errors") else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
