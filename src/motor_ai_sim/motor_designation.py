"""The standard motor designation, generated from the stored parameters (owner 2026-11-16, format confirmed the same day).

    <letters><poles> <stator OD>_<stack length> <turns><F|R><connection><Y|D> [Rev X]
    e.g.  CILN28 200_40 24F4SY      CIANO28 80_12 60R4SY      CIANDO56 400_90 15F4S2PY

Letters, in this order (the MOTRES price list, "Decoding of the motor name"):

====  ==========================================  =====================================================================
pos   meaning                                     where it comes from
====  ==========================================  =====================================================================
1     C concentrated / D distributed winding      parameters: slots per pole per phase q < 1 -> C, else D
2     I inrunner / O outrunner                    parameters: rotor outer radius vs stator inner radius
3     A air / L liquid / O oil cooling            the die name (no stored parameter holds the cooling class yet)
4     G grain-oriented / N non-oriented / C FeCo  parameters: the stator core material card
5     D double winding (two 3-phase sets)         parameters: winding phases = 6; omitted when single
6     O open construction ONLY                    the die name (no stored parameter yet); a closed motor has no letter
7     F ferrite magnets                           parameters: the magnet card; omitted for NdFeB
8     A aluminium coils                           parameters: the slot conductor; omitted for copper
====  ==========================================  =====================================================================

Then the pole count, the stator core OD and the stack length (mm), and the winding code: turns per coil
(``winding.turns_per_coil``, the electrical series turns), ``F`` flat (rectangular) or ``R`` round wire (a round wire is
modelled as a square conductor: equal width and height), the coil connection inside a phase (``4S``, ``2S2P``, ``4P``) and
``Y`` star / ``D`` delta.  ``Rev X`` is the product revision when the motor has one (``motor_revisions``).

The designation is NEVER typed and never stored as the motor's key: it is computed from the parameters at every read, so
an edit of turns, connection, wire form, star / delta or stack length changes it at once.  A letter that cannot be decided
is ``?`` and the designation is marked incomplete; nothing is guessed.  :func:`check` compares the stored die and
configuration NAMES with the parameters (read-only report; nothing is renamed).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

UNKNOWN = "?"

#: letters of the die name: winding, rotor, cooling, steel, [double], [open], [ferrite], [aluminium], poles
_DIE_NAME = re.compile(r"^(?P<w>[CD])(?P<r>[IO])(?P<c>[ALO])(?P<s>[GNC])(?P<d>D?)(?P<o>O?)(?P<f>F?)(?P<a>A?)(?P<poles>\d+)(?=\s|$)")
_DESIGNATION = re.compile(
    r"^(?P<w>[CD?])(?P<r>[IO?])(?P<c>[ALO?])(?P<s>[GNC?])(?P<d>D?)(?P<o>[O?]?)(?P<f>[F?]?)(?P<a>A?)(?P<poles>\d+)"
    r" (?P<od>\d+(?:\.\d+)?)_(?P<length>\d+(?:\.\d+)?)"
    r" (?P<turns>\d+)(?P<wire>[FR?])(?P<conn>\d+[SP](?:\d+P)?)(?P<sd>[YD?])"
    r"(?: Rev (?P<rev>[A-Z]+))?$")


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _mm(v: Optional[float]) -> str:
    if v is None:
        return UNKNOWN
    return str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:g}"


def geometry_of(die_doc: Mapping[str, Any], cfg_doc: Mapping[str, Any]) -> Dict[str, Any]:
    """The effective geometry: the die's geometry with the configuration's overrides on top."""
    g = dict((die_doc or {}).get("geometry") or {})
    g.update((cfg_doc or {}).get("geometry_overrides") or {})
    return g


def _library() -> Dict[str, Any]:
    try:
        from motor_ai_sim import materials
        return materials._load() or {}
    except Exception:                                       # noqa: BLE001 - no library: the letters stay unknown
        return {}


def _card(lib: Mapping[str, Any], category: str, name: Any) -> Optional[Mapping[str, Any]]:
    name = str(name or "")
    cards = lib.get(category) or {}
    if name in cards:
        return cards[name]
    base = re.sub(r"_\d+C$", "", name)                      # a magnet grade at a temperature: N52UH_150C -> N52UH
    return cards.get(base)


def steel_letter(lib: Mapping[str, Any], name: Any) -> str:
    card = _card(lib, "steel", name)
    if not card:
        return UNKNOWN
    text = f"{name} {card.get('description') or ''}".lower()
    if card.get("form") not in (None, "lamination"):
        return UNKNOWN                                      # SMC / solid steel: not in the code
    if any(k in text for k in ("cofe", "co-fe", "vacodur", "vacoflux", "hiperco", "cobalt")):
        return "C"
    if "non-oriented" in text or "ngo" in text:
        return "N"
    if "grain-oriented" in text or "grain oriented" in text:
        return "G"
    if "si-steel" in text or "electrical steel" in text:
        return "N"
    return UNKNOWN


def is_ferrite(lib: Mapping[str, Any], name: Any) -> Optional[bool]:
    card = _card(lib, "magnet", name)
    if not card:
        return None
    text = f"{name} {card.get('description') or ''} {card.get('family') or ''}".lower()
    return "ferrite" in text


def connection_code(conn: Any) -> Optional[str]:
    from motor_ai_sim.winding import parse_connection
    try:
        n_par, n_ser = parse_connection(str(conn or ""))
    except ValueError:
        return None
    if n_par == 1:
        return f"{n_ser}S"
    if n_ser == 1:
        return f"{n_par}P"
    return f"{n_ser}S{n_par}P"


def die_name_letters(die_name: str) -> Optional[Dict[str, str]]:
    m = _DIE_NAME.match(str(die_name or ""))
    return m.groupdict() if m else None


def describe(die_name: str, die_doc: Mapping[str, Any], cfg_doc: Mapping[str, Any], *, rev: Optional[str] = None,
             library: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The designation and every segment with its source; pure apart from the material library read."""
    lib = _library() if library is None else library
    g = geometry_of(die_doc, cfg_doc)
    w = dict((cfg_doc or {}).get("winding") or {})
    mats = dict((cfg_doc or {}).get("materials") or {})
    from_name = die_name_letters(die_name) or {}
    seg: Dict[str, Dict[str, Any]] = {}

    def put(key: str, value: str, source: str, **extra: Any) -> None:
        seg[key] = {"value": value, "source": source if value != UNKNOWN else "unknown", **extra}

    slots, poles = _num(g.get("num_slots")), _num(g.get("num_poles"))
    phases = int(_num(w.get("phases")) or 3)
    sets = 2 if phases == 6 else 1
    if slots and poles:
        q = slots / (poles * 3)
        put("winding", "C" if q < 1 else "D", "parameters", slots=int(slots), q=round(q, 4))
    else:
        put("winding", UNKNOWN, "unknown")
    r_rot, r_sta = _num(g.get("rotor_outer_radius")), _num(g.get("stator_inner_radius"))
    put("rotor", UNKNOWN if r_rot is None or r_sta is None else ("I" if r_rot < r_sta else "O"), "parameters")
    put("cooling", from_name.get("c") or UNKNOWN, "die_name")
    put("steel", steel_letter(lib, mats.get("stator_core")), "parameters", material=str(mats.get("stator_core") or ""))
    put("double", "D" if sets == 2 else "", "parameters", phases=phases)
    put("open", ("O" if from_name.get("o") else "") if from_name else UNKNOWN, "die_name")
    fer = is_ferrite(lib, mats.get("magnet"))
    put("ferrite", UNKNOWN if fer is None else ("F" if fer else ""), "parameters", material=str(mats.get("magnet") or ""))
    slot_mat = str(mats.get("slot") or "copper").lower()
    put("aluminium", "A" if slot_mat.startswith("alumin") else "", "parameters", material=slot_mat)
    put("poles", _mm(poles), "parameters")
    od = _num(g.get("stator_diameter"))
    if od is None and _num(g.get("stator_outer_radius")) is not None:
        od = 2 * float(g["stator_outer_radius"])
    put("od", _mm(od), "parameters")
    put("length", _mm(_num(g.get("motor_length"))), "parameters")
    try:
        from motor_ai_sim.winding import turns_per_coil
        turns = turns_per_coil(g)
    except Exception:                                       # noqa: BLE001 - an unreadable geometry: unknown turns
        turns = 0
    put("turns", str(turns) if turns > 0 else UNKNOWN, "parameters")
    ww, wh = _num(g.get("wire_width")), _num(g.get("wire_height"))
    put("wire", UNKNOWN if not ww or not wh else ("R" if abs(ww - wh) < 1e-9 else "F"), "parameters",
        width_mm=ww, height_mm=wh)
    put("connection", connection_code(w.get("connection")) or UNKNOWN, "parameters", stored=str(w.get("connection") or ""))
    sd = str(w.get("star_delta") or "star").lower()
    put("star_delta", {"star": "Y", "delta": "D"}.get(sd, UNKNOWN), "parameters")
    if rev:
        put("rev", str(rev), "motor_revisions")

    letters = "".join(seg[k]["value"] for k in ("winding", "rotor", "cooling", "steel", "double", "open", "ferrite", "aluminium"))
    family = f"{letters}{seg['poles']['value']} {seg['od']['value']}"
    code = f"{seg['turns']['value']}{seg['wire']['value']}{seg['connection']['value']}{seg['star_delta']['value']}"
    text = f"{family}_{seg['length']['value']} {code}" + (f" Rev {rev}" if rev else "")
    unknown = sorted(k for k, s in seg.items() if s["value"] == UNKNOWN)
    return {"designation": text, "complete": not unknown, "unknown": unknown, "family": family, "segments": seg,
            "die_name_parsed": bool(from_name)}


def parse(designation: str) -> Optional[Dict[str, Any]]:
    """The fields of a designation (the decoder's input); None when it is not one."""
    m = _DESIGNATION.match(str(designation or "").strip())
    if not m:
        return None
    d = m.groupdict()
    return {"winding": d["w"], "rotor": d["r"], "cooling": d["c"], "steel": d["s"], "double": d["d"], "open": d["o"],
            "ferrite": d["f"], "aluminium": d["a"], "poles": d["poles"], "od": d["od"], "length": d["length"],
            "turns": d["turns"], "wire": d["wire"], "connection": d["conn"], "star_delta": d["sd"], "rev": d["rev"] or ""}


def fields(described: Mapping[str, Any]) -> Dict[str, str]:
    """The same fields as :func:`parse`, from a :func:`describe` answer (round trip: parse(text) == fields(describe))."""
    seg = described["segments"]
    keys = ("winding", "rotor", "cooling", "steel", "double", "open", "ferrite", "aluminium", "poles", "od", "length",
            "turns", "wire", "connection", "star_delta")
    out = {k: seg[k]["value"] for k in keys}
    out["rev"] = seg.get("rev", {}).get("value", "")
    return out


_TURNS_TAG = re.compile(r"(?:^|-)(\d+)t(?:$|-)", re.IGNORECASE)
_LENGTH_TAG = re.compile(r"(?:^|-)L(\d+(?:\.\d+)?)(?:$|-)")


def check(die_name: str, cfg_name: str, described: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Where the stored NAMES say something else than the parameters (read-only; nothing is renamed)."""
    seg = described["segments"]
    out: List[Dict[str, str]] = []
    parsed = die_name_letters(die_name)
    if not parsed:
        out.append({"code": "die_name_not_standard", "name": die_name})
    else:
        for key, letter in (("winding", "w"), ("rotor", "r"), ("steel", "s")):
            if seg[key]["value"] not in (UNKNOWN, parsed[letter]):
                out.append({"code": f"die_name_{key}", "name": die_name, "parameters": seg[key]["value"]})
        if parsed["poles"] != seg["poles"]["value"]:
            out.append({"code": "die_name_poles", "name": die_name, "parameters": seg["poles"]["value"]})
        m = re.match(r"^\S+\s+(\d+)(?:_\d+)?(?=\s|$)", die_name)
        if m and m.group(1) != seg["od"]["value"]:
            out.append({"code": "die_name_od", "name": die_name, "parameters": seg["od"]["value"]})
    t = _TURNS_TAG.search(cfg_name or "")
    if t and t.group(1) != seg["turns"]["value"]:
        out.append({"code": "config_name_turns", "name": cfg_name, "parameters": seg["turns"]["value"]})
    ln = _LENGTH_TAG.search(cfg_name or "")
    if ln and _mm(_num(ln.group(1))) != seg["length"]["value"]:
        out.append({"code": "config_name_length", "name": cfg_name, "parameters": seg["length"]["value"]})
    return out


def _stored(die: str, cfg: str) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[str]]:
    import yaml
    from motor_ai_sim.routes import family
    die_doc = yaml.safe_load(family._die_file(die).read_text(encoding="utf-8")) or {}
    cfg_doc = yaml.safe_load(family._cfg_file(die, cfg).read_text(encoding="utf-8")) or {}
    rev = None
    try:
        from motor_ai_sim import motor_revisions as MR
        hit = MR.find(die, cfg)
        rev = hit[1].get("rev") if hit else None
    except Exception:                                       # noqa: BLE001 - no revision store: no Rev suffix
        rev = None
    return die_doc, cfg_doc, rev


def stamp(die: str, cfg: str, *, geometry: Optional[Mapping[str, Any]] = None, winding: Optional[Mapping[str, Any]] = None,
          materials: Optional[Mapping[str, Any]] = None, library: Optional[Mapping[str, Any]] = None,
          stored: Optional[Tuple[Dict[str, Any], Dict[str, Any], Optional[str]]] = None) -> Dict[str, Any]:
    """What a stored RESULT records at compute time (owner 2026-11-17): the designation of the parameters actually
    solved, so it never takes the name of the motor as it is later.  ``geometry`` (the effective geometry of the solve,
    e.g. a payload or trial geometry), ``winding`` and ``materials`` replace the stored ones where given; the rest is read
    from the stored die / configuration NOW, or taken from ``stored`` (a ``_stored(die, cfg)`` read made once by a caller
    that stamps many candidates).  ``{"designation", "complete"}``; ``{}`` when nothing can be read.  Never
    raises: the caller is the tail of a solve."""
    try:
        try:
            die_doc, cfg_doc, rev = stored if stored is not None else _stored(die, cfg)
        except Exception:                                   # noqa: BLE001 - a payload-only machine has no files
            die_doc, cfg_doc, rev = {}, {}, None
            if geometry is None:
                return {}
        cfg_doc = dict(cfg_doc)
        if geometry is not None:
            cfg_doc["geometry_overrides"] = {**(cfg_doc.get("geometry_overrides") or {}), **dict(geometry)}
        if winding is not None:
            cfg_doc["winding"] = {**(cfg_doc.get("winding") or {}), **dict(winding)}
        if materials is not None:
            cfg_doc["materials"] = {**(cfg_doc.get("materials") or {}), **dict(materials)}
        out = describe(die, die_doc, cfg_doc, rev=rev, library=library)
        return {"designation": out["designation"], "complete": out["complete"]}
    except Exception:                                       # noqa: BLE001
        return {}


def stamp_from_config(die: str, config: Mapping[str, Any]) -> Dict[str, Any]:
    """The stamp for the LIVE motor config a solve ran on (``motor_ai_sim.config.get_config()``: full geometry,
    materials, winding - the same source the record's same-machine print is taken from).  ``{}`` on any failure."""
    try:
        cfg_doc = {"geometry_overrides": dict(config.get("geometry") or {}), "winding": dict(config.get("winding") or {}),
                   "materials": dict(config.get("materials") or {})}
        if not cfg_doc["geometry_overrides"]:
            return {}
        out = describe(die, {}, cfg_doc)
        return {"designation": out["designation"], "complete": out["complete"]}
    except Exception:                                       # noqa: BLE001
        return {}


def compare(result_designation: Optional[str], current_designation: Optional[str]) -> Optional[Dict[str, str]]:
    """The mark of a result computed for another motor: ``{"computed_for", "now"}``, or None when they agree or the
    result carries no stamp (unknown is not "different").  The ``Rev`` suffix alone does not make a difference."""
    if not result_designation or not current_designation:
        return None
    strip = lambda s: re.sub(r" Rev [A-Z]+$", "", str(s).strip())     # noqa: E731
    if strip(result_designation) == strip(current_designation):
        return None
    return {"computed_for": str(result_designation), "now": str(current_designation)}


def for_motor(die: str, cfg: str) -> Dict[str, Any]:
    """The designation of a stored motor, read now from its files (never cached, never stored)."""
    die_doc, cfg_doc, rev = _stored(die, cfg)
    out = describe(die, die_doc, cfg_doc, rev=rev)
    out["checks"] = check(die, cfg, out)
    return out
