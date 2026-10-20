"""Build the stored Configure answers AHEAD of any request (owner 2026-10-19: "it must already be ready").

``python -m motor_ai_sim.configure_warm`` walks the SHARED catalogue's public machines and, for every configuration
of each and every preset mode (plus the default load with no mode), builds the ``configure_load`` answer the page asks
for on a default load (25 °C air, the card's own housing diameter, the configuration's default propeller and drive)
and the configuration's context - into the answer store (``answer_store``), so the request only reads them.  An
answer whose inputs did not change since is a stamp check and nothing else.

The API starts it at boot as a SEPARATE, low-priority process (``start_background``): the S1 searches behind a
propeller machine's load cost seconds of CPU each, and in the API process they would hold the GIL against every
other request.  Inside the API only a short thread warms the in-memory caches of the catalogue references and the
family tree.  Nothing private is built here: only cards of the shared catalogue, as an anonymous caller sees them.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List

log = logging.getLogger(__name__)

ENV_SWITCH = "MOTRES_CONFIGURE_WARM"        # "0" = never; "1" = also off a layered install
DEFAULT_AMBIENT_C = 25.0                   # web/src/lib/configuratorPropeller.ts DEFAULT_AMBIENT_C


def _enabled() -> bool:
    v = os.environ.get(ENV_SWITCH, "").strip()
    if v == "0":
        return False
    if v == "1":
        return True
    try:
        from motor_ai_sim.workspace import layering
        return bool(layering())
    except Exception:                                           # noqa: BLE001
        return False


def _shared_cards() -> List[Dict[str, Any]]:
    from pathlib import Path
    from motor_ai_sim import fscache
    from motor_ai_sim.workspace import shared_root
    p = Path(str(shared_root())) / "motor_catalog.json"
    try:
        cat = fscache.read_json(p, copy_=False)
    except (OSError, ValueError):
        return []
    return [m for m in (cat.get("motors") or []) if isinstance(m, dict) and m.get("id")
            and (m.get("visibility") or "public") != "private"
            and ((m.get("passport") or {}).get("passport"))]


def warm_all(*, limit_s: float = 900.0) -> Dict[str, Any]:
    """Build every default load of every public machine; returns counts.  Never raises."""
    from fastapi import HTTPException
    from motor_ai_sim.configure_load import preset_modes
    from motor_ai_sim.routes import catalog as cat
    t0 = time.monotonic()
    done = failed = 0
    for card in _shared_cards():
        if time.monotonic() - t0 > limit_s:
            break
        mid = str(card["id"])
        try:
            ctx = cat.configure_context_answer(mid, None)
        except HTTPException:
            continue
        except Exception:                                       # noqa: BLE001
            log.warning("warm: context of %s failed", mid, exc_info=True)
            failed += 1
            continue
        geo = (card.get("passport") or {}).get("geo") or {}
        r = geo.get("statorOR_mm")
        housing = float(r) * 2 if isinstance(r, (int, float)) and r > 0 else None
        for pr in ctx.get("presets") or []:
            duties = [None] + [m.get("name") for m in preset_modes(pr)]
            for duty in duties:
                try:
                    cat.configure_load_answer(mid, None, config=str(pr.get("config")), duty=duty,
                                              ambient_c=DEFAULT_AMBIENT_C, propeller=None, housing_d_mm=housing,
                                              drive=None, pack_v_min=None, pack_v_max=None, series_rpm_max=40000.0,
                                              series_n=321)
                    done += 1
                except HTTPException:
                    continue
                except Exception:                               # noqa: BLE001
                    log.warning("warm: load %s %s %s failed", mid, pr.get("config"), duty, exc_info=True)
                    failed += 1
    out = {"built": done, "failed": failed, "seconds": round(time.monotonic() - t0, 1)}
    log.info("configure warm-up: %s", out)
    return out


def warm_propellers(*, limit_s: float = 1800.0) -> Dict[str, Any]:
    """The S1 answer of EVERY propeller a public configuration may drive (owner 2026-10-19: «почему так долго
    грузятся пропеллеры?» - the first pick of another propeller in the selector / the load chart's compare chips ran a
    5 s S1 search in the request), for the drives the page asks with (Sine, and the preset's own PWM variant when its
    S1 model carries it), at the default air and the preset's pack - exactly the request the page sends.  An answer
    whose inputs did not change is a stamp check.  S1 answers do not depend on the caller's workspace: one pass."""
    from fastapi import HTTPException
    from motor_ai_sim import configure_load as ld
    from motor_ai_sim import propeller as pp
    from motor_ai_sim import propeller_s1 as s1
    from motor_ai_sim.cooling_options import cooling_options
    from motor_ai_sim.routes import catalog as cat
    from motor_ai_sim.routes import propellers as R
    t0 = time.monotonic()
    done = failed = 0
    seen = set()
    try:
        catalog = pp.load_catalog()
    except Exception:                                           # noqa: BLE001
        catalog = {}
    for card in _shared_cards():
        try:
            ctx = cat.configure_context_answer(str(card["id"]), None)
        except Exception:                                       # noqa: BLE001
            continue
        die = str(((ctx.get("cooling") or {}) if isinstance(ctx.get("cooling"), dict) else {}).get("die") or "")
        if not die:
            continue
        for pr in ctx.get("presets") or []:
            cfg = str(pr.get("config") or "")
            try:
                model = s1.load_model(die, cfg)
            except Exception:                                   # noqa: BLE001 - no S1 model: no load chart
                continue
            drives = ["sine"] + [d for d in (pr.get("drive_variant"), pr.get("drive_variant_unmodelled"))
                                 if d and d in (model.get("drives") or {})]
            pack = ld.preset_pack(pr)
            props = [p for p in (cooling_options(die, cfg).get("propellers") or [])
                     if p in catalog and getattr(catalog[p], "selectable", False)]
            for pid in props:
                for d in dict.fromkeys(drives):
                    key = (pid, die, cfg, d, None if pack is None else (round(pack["min"], 6), round(pack["max"], 6)))
                    if key in seen:
                        continue
                    seen.add(key)
                    if time.monotonic() - t0 > limit_s:
                        break
                    try:
                        R.s1_answer(pid, die, cfg, ambient_c=DEFAULT_AMBIENT_C, drive=d,
                                    pack_v_min=pack["min"] if pack else None, pack_v_max=pack["max"] if pack else None)
                        done += 1
                    except HTTPException:
                        continue
                    except Exception:                           # noqa: BLE001
                        log.warning("warm: S1 %s %s %s %s failed", pid, die, cfg, d, exc_info=True)
                        failed += 1
    out = {"s1_answers": done, "failed": failed, "seconds": round(time.monotonic() - t0, 1)}
    log.info("propeller S1 warm-up: %s", out)
    return out


def _warm_memory() -> None:
    """In the API process: the references and the family tree of the process workspace (seconds once)."""
    time.sleep(5.0)
    try:
        from fastapi import Response
        from motor_ai_sim.routes import catalog as cat, family as fam
        from motor_ai_sim.workspace import process_workspace, use_workspace
        with use_workspace(process_workspace()):
            cat.get_references(authorization=None)
            fam.tree(Response(), authorization=None)
        from motor_ai_sim import propeller as _pp
        from motor_ai_sim.inverter import devices as _dev
        _pp.load_catalog()                                      # the propeller cards every S1 / series reads
        _dev.list_devices()                                     # the device cards the Drive block reads
    except Exception:                                           # noqa: BLE001
        log.info("warm: references / tree not warmed", exc_info=True)


def start_background() -> None:
    """At API boot: the store builder as a separate low-priority process, the memory warm-up as a short thread."""
    if not _enabled():
        return
    threading.Thread(target=_warm_memory, name="configure-warm-memory", daemon=True).start()
    try:
        kw: Dict[str, Any] = {"env": dict(os.environ), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "posix":
            kw["preexec_fn"] = lambda: os.nice(15)                # the API's requests come first
            kw["start_new_session"] = True
        subprocess.Popen([sys.executable, "-m", "motor_ai_sim.configure_warm"], **kw)
    except Exception:                                           # noqa: BLE001
        log.warning("configure warm-up process not started", exc_info=True)


def public_workspace():
    """A workspace with nothing of its own (an empty folder over the shared layer): what a signed-in account
    without its own copies sees - so the answers built here are the ones such an account's requests read."""
    import tempfile
    from pathlib import Path
    from motor_ai_sim.workspace import Workspace, shared_root
    root = Path(tempfile.mkdtemp(prefix="motres-warm-"))
    sr = Path(str(shared_root()))
    return Workspace(id="configure-warm", email="", root=root, shared_root=sr,
                     config_file=sr / "motor_config.yaml")


if __name__ == "__main__":                                      # pragma: no cover - the process the API starts
    logging.basicConfig(level=logging.INFO)
    from motor_ai_sim.workspace import process_workspace, use_workspace
    with use_workspace(public_workspace()):                     # what an account without own copies reads
        print(warm_all())
    with use_workspace(process_workspace()):                    # the admin's view
        print(warm_all())
    with use_workspace(public_workspace()):                     # every propeller of every public configuration
        print(warm_propellers())
