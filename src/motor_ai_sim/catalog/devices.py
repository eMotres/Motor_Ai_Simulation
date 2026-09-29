"""Adapter: ``config/devices/<PART>.yaml`` <-> the common card envelope.

The device cards already carry their provenance as DATA (a ``source`` line on
every block, a ``basis: table | figure`` on every point, ``null`` plus a note
where the datasheet prints nothing), so this adapter DERIVES the envelope's
``sources`` / ``prov`` from them instead of duplicating them in the file.  The
only thing stored for the envelope is the card's ``catalog:`` block (status,
revision).  Reads go through :mod:`motor_ai_sim.inverter.devices` — same
folder, same shared-copy precedence, same mtime cache as the loss model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim.catalog.envelope import make_envelope
from motor_ai_sim.inverter import devices as _dev

ENVELOPE_KEY = "catalog"

#: Blocks whose ``source`` line becomes a per-block provenance entry.
_BLOCKS = ("ratings", "gate", "r_ds_on", "capacitance", "switching",
           "third_quadrant", "thermal", "package_size_mm", "module",
           "footprint")

UNITS = {"ratings.v_dss_V": "V", "ratings.t_j_max_c": "degC",
         "r_ds_on": "mOhm", "switching": "uJ (energies), ns (times)",
         "capacitance": "pF", "gate": "V, nC, ohm", "thermal": "K/W",
         "package_size_mm": "mm", "weight_g": "g"}


def _walk_basis(node: Any, acc: Dict[str, Any]) -> None:
    """Count table vs figure points under ``node``; keep the worst tolerance."""
    if isinstance(node, dict):
        b = node.get("basis")
        if b in ("table", "figure"):
            acc[b] = acc.get(b, 0) + 1
            tol = node.get("tolerance_pct")
            if b == "figure" and isinstance(tol, (int, float)):
                acc["tol"] = max(acc.get("tol", 0), float(tol))
        for v in node.values():
            _walk_basis(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk_basis(v, acc)


def _nulls(node: Any, path: str, out: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if v is None:
                out.append(f"{path}{k}")
            elif isinstance(v, dict):
                _nulls(v, f"{path}{k}.", out)


def _sources(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    ds = {"id": "datasheet", "doc": f"{body.get('manufacturer') or ''} "
                                    f"{body.get('part')} datasheet".strip(),
          "url": body.get("datasheet_url"), "file": body.get("datasheet_file"),
          "rev": body.get("datasheet_revision")}
    out = [ds]
    if body.get("application_note_url"):
        out.append({"id": "app_note", "doc": "application note",
                    "url": body.get("application_note_url"),
                    "rev": body.get("application_note_revision")})
    st = body.get("switching_table") or {}
    if isinstance(st, dict) and st.get("basis"):
        out.append({"id": "spice", "doc": "vendor SPICE model (switching_table)",
                    "rev": str(st.get("basis"))})
    price = body.get("price")
    if isinstance(price, dict) and price.get("source"):
        out.append({"id": "quote", "doc": str(price.get("source")),
                    "read": price.get("dated")})
    return out


def _prov(body: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    prov: Dict[str, Dict[str, Any]] = {}
    for blk in _BLOCKS:
        b = body.get(blk)
        if not isinstance(b, dict):
            continue
        acc: Dict[str, Any] = {}
        _walk_basis(b, acc)
        src_line = str(b.get("source") or b.get("outline_source") or "").strip()
        note = src_line
        if acc.get("figure"):
            note += (f" · {acc['figure']} point(s) read off figures"
                     + (f" (±{acc['tol']:g} %)" if acc.get("tol") else ""))
        entry = {"type": "datasheet", "src": "datasheet", "note": note.strip(" ·")}
        if acc.get("figure"):
            entry["figure_points"] = acc["figure"]
        if acc.get("table"):
            entry["table_points"] = acc["table"]
        prov[blk] = entry
    sw = body.get("switching") or {}
    if isinstance(sw, dict) and not (isinstance(sw.get("curves"), list) and sw.get("curves")):
        prov["switching"] = {
            "type": "derived", "src": "datasheet",
            "note": "no E_on/E_off table published — energies estimated by the "
                    "times-and-charges overlap model from the datasheet's "
                    "times and gate charges"}
    st = body.get("switching_table")
    if isinstance(st, dict) and st.get("sets"):
        prov["switching_table"] = {"type": "derived", "src": "spice",
                                   "note": "generated from the vendor SPICE model "
                                           "(scripts/spice_build_tables.py)"}
    price = body.get("price")
    if isinstance(price, dict) and price.get("amount") is not None:
        prov["price"] = {"type": "estimate",
                         "src": "quote" if price.get("source") else None,
                         "note": "a quotation, not a datasheet value"}
    nulls: List[str] = []
    for blk in ("ratings", "gate", "capacitance", "thermal"):
        _nulls(body.get(blk), f"{blk}.", nulls)
    for f in nulls:
        prov.setdefault(f, {"type": "datasheet", "src": "datasheet",
                            "note": "not published (null)"})
    return prov


def _validation(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    v = (body.get("switching_table") or {}).get("validation")
    if not isinstance(v, dict):
        return []
    return [{"what": "SPICE switching energies vs datasheet",
             "status": v.get("status"), "delta_pct": v.get("worst_pct"),
             "limit_pct": v.get("limit_pct"), "line": v.get("line"),
             "doc": v.get("source")}]


def _split(doc: Dict[str, Any]):
    body = dict(doc or {})
    meta = body.pop(ENVELOPE_KEY, None) or {}
    return body, meta


def envelope_from_doc(doc: Dict[str, Any], *, file: Optional[str] = None
                      ) -> Dict[str, Any]:
    body, meta = _split(doc)
    return make_envelope(
        id=str(body.get("part")), kind="device", body=body,
        manufacturer=body.get("manufacturer"),
        part_number=str(body.get("part")),
        description=" · ".join(str(x) for x in (body.get("family"),
                                                body.get("package_common_name")
                                                or body.get("package")) if x),
        status=meta.get("status") or "active",
        sources=_sources(body), units=UNITS,
        prov={**_prov(body), **(meta.get("prov") or {})},
        validation=_validation(body) + list(meta.get("validation") or []),
        revision=meta.get("revision"), file=file)


def _file_of(p: Path) -> str:
    return f"config/devices/{p.name}"


def device_envelopes() -> List[Dict[str, Any]]:
    d = _dev.devices_dir()
    out: List[Dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.yaml")):
        try:
            out.append(envelope_from_doc(_dev._read(p), file=_file_of(p)))
        except Exception as exc:                          # noqa: BLE001
            out.append({"id": p.stem, "kind": "device", "error": str(exc)})
    return out


def device_envelope(part: str) -> Optional[Dict[str, Any]]:
    p = _dev.devices_dir() / f"{str(part).strip()}.yaml"
    if not p.is_file():
        return None
    return envelope_from_doc(_dev._read(p), file=_file_of(p))


def to_device_card(env: Dict[str, Any]) -> "_dev.DeviceCard":
    """Envelope -> the loss model's card.  The body is the card minus its
    ``catalog`` block, which the model never read anyway."""
    return _dev.DeviceCard(env["body"])


def device_row(env: Dict[str, Any]) -> Dict[str, Any]:
    b = env["body"]
    try:
        r = to_device_card(env).row()
    except Exception:                                     # noqa: BLE001
        r = {}
    return {"type": b.get("technology"),
            "package": b.get("package_common_name") or b.get("package"),
            "v_dss_V": r.get("v_dss_V"), "i_d_100c_A": r.get("i_d_100c_A"),
            "r_ds_on_25c_mohm": r.get("r_ds_on_25c_mohm"),
            "r_ds_on_175c_mohm": r.get("r_ds_on_175c_mohm"),
            "t_j_max_c": r.get("t_j_max_c"),
            "r_th_jc_max_k_w": r.get("r_th_jc_max_k_w"),
            "weight_g": r.get("weight_g"),
            "footprint_group": (r.get("footprint") or {}).get("compatibility_group")}
