"""Former per-temperature magnet names -> one 20 °C card per grade + a magnet temperature.

Owner 2026-10-30: «в каталоге магниты все с одинаковой температурой 20 °C, а в
моделировании просто вводить нужную температуру».  The library now holds ONE
20 °C card per grade (``N52UH``, ``F52SH`` ...) and the temperature is the
duty's ``sim.magnetTempC``.  This module rewrites a stored INPUT that still
names a former card (``N52UH_150C``) to the grade plus that temperature, so the
next solve of it is the solve it has always been:

* a configuration's ``materials.magnet`` and every duty's ``materials.magnet``
  become the grade;
* every duty whose magnet temperature is not already set gets the temperature
  its card stood for, in ``mesh['sim.magnetTempC']`` (the one place the web,
  the report and the passport read it) and in each stored excitation's
  ``runs.<drive>.settings`` — written as a string, like the panel writes it;
* a duty that ALREADY set one keeps it (it was solved at that temperature);
* the results the configuration carries are re-stamped onto the new build
  signature (``routes.family._build_sig`` hashes the materials map): the build
  did not change, only its description did — the precedent is
  ``family._restamp_results``.

RECORDS are left alone: a stored summary's ``demag.magnet_name`` /
``magnet_card`` says which card a past run was solved with, and the old name
still resolves to that card (``materials.LEGACY_MAGNET_CARDS``).

Pure functions on parsed documents: ``scripts/migrate_magnet_cards_20c.py``
walks a data tree with them.  Nothing here touches the disk.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim.materials import legacy_magnet

TEMP_KEY = "sim.magnetTempC"


def temp_text(t: float) -> str:
    """The panel's spelling of a magnet temperature: '150', '87.5'."""
    f = float(t)
    return str(int(f)) if f.is_integer() else ("%g" % f)


def _is_set(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _map_materials(mats: Any, where: str, notes: List[str]) -> Optional[Tuple[str, float]]:
    """Rewrite ``mats['magnet']`` in place when it is a former name; return its
    ``(grade, T)`` or None."""
    if not isinstance(mats, dict):
        return None
    name = mats.get("magnet")
    if not isinstance(name, str):
        return None
    hit = legacy_magnet(name)
    if hit is None:
        return None
    mats["magnet"] = hit[0]
    notes.append(f"{where}: magnet {name} -> {hit[0]} ({temp_text(hit[1])} °C)")
    return hit


def migrate_duty(duty: Dict[str, Any], cfg_hit: Optional[Tuple[str, float]],
                 where: str, notes: List[str]) -> bool:
    """One duty: its own magnet pick, then its magnet temperature.  In place;
    True when anything changed."""
    changed = False
    hit = _map_materials(duty.get("materials"), f"{where} materials", notes)
    changed |= hit is not None
    eff = hit or cfg_hit
    if eff is None:
        return changed
    t = temp_text(eff[1])
    mesh = duty.get("mesh")
    if isinstance(mesh, dict):
        if _is_set(mesh.get(TEMP_KEY)):
            notes.append(f"{where}: magnet temperature kept at {mesh[TEMP_KEY]} °C "
                         f"(already set; the former card stood for {t} °C)")
        else:
            mesh[TEMP_KEY] = t
            notes.append(f"{where}: magnet temperature set to {t} °C")
            changed = True
    else:
        # A duty saved without a settings snapshot: the snapshot is where every
        # reader looks, so it is created holding just this one key.
        duty["mesh"] = {TEMP_KEY: t}
        notes.append(f"{where}: no settings snapshot - created with the magnet "
                     f"temperature {t} °C only")
        changed = True
    for drive, run in (duty.get("runs") or {}).items():
        st = (run or {}).get("settings") if isinstance(run, dict) else None
        if isinstance(st, dict) and not _is_set(st.get(TEMP_KEY)):
            st[TEMP_KEY] = t
            notes.append(f"{where} runs.{drive}: magnet temperature set to {t} °C")
            changed = True
    return changed


def migrate_config_doc(cfg: Dict[str, Any], die_doc: Optional[Dict[str, Any]] = None,
                       where: str = "configuration") -> Tuple[Dict[str, Any], List[str]]:
    """A catalog configuration (``<die>/<cfg>.yaml``): a migrated COPY and what
    changed (empty list = nothing to do)."""
    out = copy.deepcopy(cfg)
    notes: List[str] = []
    cfg_hit = _map_materials(out.get("materials"), f"{where} materials", notes)
    for i, d in enumerate(out.get("duties") or []):
        if isinstance(d, dict):
            migrate_duty(d, cfg_hit, f"{where} duty '{d.get('name') or i}'", notes)
    if notes and cfg_hit is not None and die_doc is not None:
        try:
            from motor_ai_sim.routes.family import _build_sig, _restamp_results
            n = _restamp_results(out, _build_sig(die_doc, cfg), _build_sig(die_doc, out))
            if n:
                notes.append(f"{where}: {n} stored result(s) re-stamped onto the "
                             f"new build signature (same build, new name)")
        except Exception as exc:                            # noqa: BLE001
            notes.append(f"{where}: results NOT re-stamped ({exc}) - they will "
                         f"read as computed on an older build until re-run")
    return out, notes


def migrate_motor_payload(payload: Dict[str, Any],
                          where: str = "saved motor") -> Tuple[Dict[str, Any], List[str]]:
    """A "my motors" entry's family payload (``materials`` + one ``duty``)."""
    out = copy.deepcopy(payload)
    notes: List[str] = []
    cfg_hit = _map_materials(out.get("materials"), f"{where} materials", notes)
    duty = out.get("duty")
    if isinstance(duty, dict):
        migrate_duty(duty, cfg_hit, f"{where} duty", notes)
    return out, notes
