"""Per-duty solver results — the small side store the full report is made of.

WHY THIS EXISTS (2026-09-09).  The user asked for a report that COMPARES every
duty of a configuration across every simulation: *"если в конфигурации несколько
режимов, их нужно сравнивать в таблицах по всем моделированиям"*.  Only the
electromagnetic side could answer that: a duty's own ``result``/``summary`` block
is saved into ``config/dies/<die>/<cfg>.yaml`` when the user presses Save in the
Simulation tab.  Everything else — the thermal map, the rotor stress, the
critical speeds, the coupled loop — is stored ONCE PER MACHINE, for whichever
duty happened to be solved last (``routes.thermal._LAST``,
``routes.mechanical._LAST``, ``routes.coupled._LAST``).  A report built from
those alone can only ever show one column, and the temptation to spread one
column across the others is exactly the failure the report exists to prevent.

So every completed thermal / mechanical / coupled solve also writes a COMPACT
summary of itself here, keyed by (die, configuration, duty) — the active duty
the catalog context names.  Compact means: the numbers a report table needs and
nothing else.  No meshes, no per-node temperatures, no per-element stress — the
heavy payloads stay in their own pickles, where /last already serves them, and a
file an engineer may want to read by hand stays readable.

WHY A SIDE FILE AND NOT THE YAML.  ``config/dies/<die>/<cfg>.yaml`` is the
user's catalogue and it changes ONLY on an explicit Save (the project rule; the
recovery incident of 2026-08-24 is why).  A thermal solve is not a Save, so it
may not touch that file — it writes ``config/.duty_results.json`` instead,
beside ``.family_context.json`` and the three ``.last_*`` stores, through the
same atomic tmp + ``os.replace`` those use.

NOTHING HERE MAY FAIL A SOLVE.  Every public function swallows its own errors
and returns a falsy answer: a six-minute coupled run that succeeded must not be
reported as failed because a JSON file was locked by a virus scanner.

SCHEMA (version 1)::

    {"version": 1,
     "updated_at": "<iso seconds>",
     "results": {
       "<die>": {"<configuration>": {"<duty>": {
           "<kind>": {"kind", "computed_at", "recorded_at",
                      "geometry_fingerprint", "point": {...}, ...payload}
       }}}}}

``<kind>`` is one of :data:`KINDS` — ``thermal``, ``rotor_stress``, ``modes``,
``critical_speeds``, ``coupled``, ``duty_cycle`` — plus ``em`` (a POINTER, not a
copy: the duty's own saved ``result``/``summary`` in the yaml is and stays the
electromagnetic source of truth, and duplicating it here would create a second
answer able to disagree with the catalogue).

``duty_cycle`` (2026-09-14) is the one kind that carries SERIES — the four nodes'
temperature through one cycle — and it is therefore the one kind with a sample
cap: 400 points per node, which keeps a 200-cycle S3 under 40 kB in a file an
engineer is meant to be able to open.

``pwm`` (2026-09-14) is the one kind that is a COMPARISON rather than an answer:
what the machine costs on a real two-level inverter against the sinusoid every
other record here assumes.  See :func:`compact_pwm`.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: What a duty may carry.  ``em`` is the pointer at the duty's own saved block.
#: ``duty_cycle`` (2026-09-14) is the transient answer: the lumped network this
#: duty's cycle was integrated on, the periodic state it reached and the limits
#: it was judged against.  It is the one kind that carries SERIES, so it is the
#: one kind with a sample cap — see :func:`compact_duty_cycle`.
#: ``pwm`` (2026-09-14) is the INVERTER's answer: what the machine costs when it
#: is fed by a real two-level bridge instead of the sinusoid every other number
#: in this store (and in the report) assumes.  It is a MEASUREMENT record, not a
#: solver route's tail — a PWM run is a separate, deliberate experiment at one
#: point, so it is filed through :func:`note_pwm` by whoever made it.
#: ``controller`` (2026-09-22) is the INVERTER's own answer for this duty: the
#: device, the coil->bridge map, the loss split, the junction temperatures and
#: the second efficiency (wall-to-shaft).  It is filed like the others so the
#: report can print a Controller section only where one exists, and it carries
#: NO field maps — the waveform it stores is one electrical period, decimated.
KINDS: Tuple[str, ...] = ("thermal", "rotor_stress", "modes",
                          "critical_speeds", "coupled", "em", "duty_cycle",
                          "pwm", "continuous_rating", "controller")

#: The mechanical kinds, in the order ``routes.mechanical._LAST`` names them.
MECH_KINDS: Tuple[str, ...] = ("rotor_stress", "modes", "critical_speeds")

VERSION = 1


# ---------------------------------------------------------------------------
# Where it lives
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """The folder the other sidecar stores sit in.

    Derived from ``DEFAULT_CONFIG_PATH`` exactly as ``routes.family._CTX_FILE``
    and ``routes.thermal._last_store_path`` derive theirs, so the test sandbox's
    redirect (``MOTOR_AI_SIM_CONFIG``) carries this store with it and a suite can
    never write beside the user's real machine.
    """
    try:
        # Stage 1: the caller's WORKSPACE, which with none set is
        # ``Path(DEFAULT_CONFIG_PATH).parent`` — the old expression exactly.
        from motor_ai_sim.workspace import root as _ws_root
        return Path(str(_ws_root()))
    except Exception:                                       # noqa: BLE001
        return Path(__file__).resolve().parents[2] / "config"


def store_path() -> Path:
    """The store this call WRITES — always the caller's own workspace."""
    return _config_dir() / ".duty_results.json"


# ---------------------------------------------------------------------------
# Reading THROUGH the layers (migration Stage 2)
# ---------------------------------------------------------------------------
# A die may be read out of the workspace, out of somebody's published work, or
# out of the shared catalog, and its ANSWERS have to come with it — a published
# duty whose thermal row stayed behind in the author's workspace is a duty the
# reader is told was never solved.  So reads fall through:
#
#   <ws>/.duty_results.json                     the caller's own
#   published/<ws_id>/<die>/.duty_results.json  the author's, per die
#   <shared>/.duty_results.json                 the vendor's
#
# first hit wins PER DUTY, and writes never fall through: they go to the
# workspace store, where this workspace's own answers belong.


def _read_store(p: Path) -> Dict[str, Any]:
    """The ``results`` block of any layer's store, or ``{}``."""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_results: %s is unreadable (%s) — treated as empty",
                    p, exc)
        return {}
    res = (d or {}).get("results") if isinstance(d, dict) else None
    return res if isinstance(res, dict) else {}


def _fallback_stores(die: str) -> List[Path]:
    """The published / shared stores that may answer for this die, DEEPEST first
    (shared, then published), so a shallower layer overwrites a deeper one."""
    out: List[Path] = []
    try:
        from motor_ai_sim import workspace as _ws
        if not _ws.layering():
            return out
        shared = Path(str(_ws.shared_root())) / ".duty_results.json"
        if shared.is_file() and shared != store_path():
            out.append(shared)
        src = _ws.source_die_dir(str(die))
        if src is not None and (src / ".duty_results.json").is_file():
            out.append(src / ".duty_results.json")
    except Exception:                                       # noqa: BLE001
        return []
    return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Reading and writing
# ---------------------------------------------------------------------------


def read_all() -> Dict[str, Any]:
    """The whole store, or an empty one.  A corrupt file reads as empty —
    losing a convenience is not worth 500-ing a report."""
    p = store_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": VERSION, "results": {}}
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_results: %s is unreadable (%s) — treated as empty",
                    p, exc)
        return {"version": VERSION, "results": {}}
    if not isinstance(d, dict) or not isinstance(d.get("results"), dict):
        return {"version": VERSION, "results": {}}
    return d


def _write_all(doc: Dict[str, Any]) -> bool:
    p = store_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        doc["version"] = VERSION
        doc["updated_at"] = _now()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, default=str)
        os.replace(tmp, p)
        return True
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_results: could not persist %s (%s)", p, exc)
        return False


# ---------------------------------------------------------------------------
# ONE POINT, SEVERAL EXCITATIONS (2026-09-14)
# ---------------------------------------------------------------------------
# A duty is an operating point, and the same point answers differently on a
# sinusoid and on a real two-level bridge — measured on the Ø200 L155 peak
# duty: +2 196 W at a 24 kHz carrier, 96 % of it in the stator, torque ripple
# 0.9 % → 25 %.  So a record that replaces the sine answer with the PWM one
# has DESTROYED the comparison the report exists to print, and re-making it
# costs an hour of FEM.
#
# Two blocks keep it instead, and both sit INSIDE the record they belong to so
# a reader of the file never has to correlate two entries:
#
#   ``reference_sine``  the SINE record this PWM record replaced, verbatim —
#                       temperatures, loss totals, efficiency, the coupled
#                       pair.  One level deep, never nested: a PWM record
#                       replacing a PWM record inherits the same reference
#                       rather than wrapping it again;
#   ``alt_carriers``    the same duty measured at ANOTHER carrier (48 kHz
#                       beside the duty's own 24 kHz).  Appended through
#                       :func:`record_alt_carrier`, which never touches the
#                       main record's own numbers — the duty's ``sim.fSwitch``
#                       stays the headline and the alternative is an extra
#                       column, so a report prints sine / 24 kHz / 48 kHz at
#                       one point without re-solving anything.
#
#: The kinds that describe an EXCITATION and therefore carry the two blocks.
DRIVEN_KINDS: Tuple[str, ...] = ("coupled", "thermal", "em")

#: How many alternative carriers one duty keeps.  Each is an hour of FEM, so a
#: duty with more than a handful does not exist; the cap is the same courtesy
#: every other list in this file gets.
ALT_CARRIERS_MAX = 4

_NESTING_KEYS = ("reference_sine", "alt_carriers")


def live_fingerprint_v2() -> Optional[str]:
    """THE SAME-MACHINE PRINT of the machine as it stands at this moment.

    Stamped on every record this module writes, beside the v1
    ``geometry_fingerprint`` it does not replace (see
    :func:`motor_ai_sim.simulation.geometry_2d.geometry_fingerprint_v2` for
    why there are two).  v1 keys the caches and every already-stored record;
    v2 answers "same motor?", which is the only question a report asks of it,
    and it does not move when a geometry is remeshed or a derived float comes
    back with different last bits.

    ``None`` — and then the record simply carries no v2 — whenever the live
    configuration cannot be read.  A missing print is never a mismatch.
    """
    try:
        from motor_ai_sim.config import get_config
        from motor_ai_sim.simulation.geometry_2d import (
            geometry_fingerprint_v2 as _fp2)
        cfg = get_config() or {}
        return _fp2(cfg.get("geometry") or {}, cfg.get("materials") or {},
                    cfg.get("winding") or {})
    except Exception as exc:                                 # noqa: BLE001
        log.debug("duty_results: no same-machine print (%s)", exc)
        return None


def _fp2_block() -> Dict[str, Any]:
    """``{"geometry_fingerprint_v2": …}`` or ``{}`` — spread into a record."""
    v = live_fingerprint_v2()
    return {"geometry_fingerprint_v2": v} if v else {}


def _entry_drive(entry: Any) -> str:
    """``"pwm"`` | ``"sine"`` — what excitation a stored record describes.

    An entry with no ``drive`` at all is every record written before the
    inverter existed, and those are sinusoidal: reading the absence as "sine"
    is a statement of fact, not a default.
    """
    d = str((entry or {}).get("drive") or "").strip().lower() \
        if isinstance(entry, dict) else ""
    return "pwm" if d in ("pwm", "pwm_voltage", "inverter") else "sine"


def _entry_carrier(entry: Any) -> Optional[float]:
    if not isinstance(entry, dict):
        return None
    return _f((entry.get("inverter") or {}).get("f_carrier_hz")
              if isinstance(entry.get("inverter"), dict) else None)


def _flat(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One record without its own nesting blocks — what may be stored INSIDE
    another record."""
    return {k: v for k, v in (entry or {}).items() if k not in _NESTING_KEYS}


def _carry_excitation_blocks(kind: str, entry: Dict[str, Any],
                             prev: Any) -> Dict[str, Any]:
    """Keep what this new record would otherwise have thrown away.

    A SINE record replaces everything — it is the primary answer and a sine
    result that carried a "reference sine" of itself would be a joke.  A PWM
    record keeps the sine it displaced (or, when it displaces another PWM
    record, the reference that one already carried), and carries forward every
    alternative carrier that is not its own.
    """
    if kind not in DRIVEN_KINDS or not isinstance(prev, dict):
        return entry
    mine = _entry_carrier(entry)
    alts = [a for a in (prev.get("alt_carriers") or [])
            if isinstance(a, dict)
            and (mine is None or _entry_carrier(a) != mine)]
    if alts:
        entry["alt_carriers"] = alts[:ALT_CARRIERS_MAX]
    if _entry_drive(entry) != "pwm":
        return entry
    if entry.get("reference_sine") is None:
        if _entry_drive(prev) == "sine":
            entry["reference_sine"] = _flat(prev)
        elif isinstance(prev.get("reference_sine"), dict):
            entry["reference_sine"] = dict(prev["reference_sine"])
    return entry


def record(die: str, cfg: str, duty: str, kind: str,
           entry: Dict[str, Any]) -> bool:
    """Park one compact result under (die, configuration, duty).

    Returns True when it landed on disk.  NEVER raises: the caller is the tail
    of a solve route, and a solve that produced a real answer is a success even
    when the bookkeeping beside it failed.

    A run marked ``record: false`` (``run_recording``) lands NOWHERE: it is a
    solve made on some other duty's behalf, and this is the one choke point
    every ``note_*`` seam goes through, so one guard here covers them all.
    """
    try:
        from motor_ai_sim import run_recording as _rr
        if _rr.suppressed():
            return False
        if not (die and cfg and duty) or kind not in KINDS:
            return False
        doc = read_all()
        res = doc.setdefault("results", {})
        node = (res.setdefault(str(die), {})
                   .setdefault(str(cfg), {})
                   .setdefault(str(duty), {}))
        e = dict(entry or {})
        e["kind"] = kind
        e.setdefault("recorded_at", _now())
        e = _carry_excitation_blocks(kind, e, node.get(kind))
        node[kind] = e
        return _write_all(doc)
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_results: could not record %s for %s/%s/%s (%s)",
                    kind, die, cfg, duty, exc)
        return False


def record_alt_carrier(die: str, cfg: str, duty: str, kind: str,
                       entry: Dict[str, Any]) -> bool:
    """Park one ALTERNATIVE CARRIER's answer beside the duty's main record.

    Never replaces it: the duty's own ``sim.fSwitch`` is the machine's design
    carrier and stays the headline column, while this is "the same point at
    another carrier" — the 48 kHz run beside the 24 kHz one.  Keyed on the
    carrier frequency, so re-measuring one replaces that one alone.

    ``False`` when there is no main record yet: an alternative to nothing is a
    number with no meaning, and writing it would look like the duty's answer.
    """
    try:
        from motor_ai_sim import run_recording as _rr
        if _rr.suppressed():
            return False
        if not (die and cfg and duty) or kind not in DRIVEN_KINDS:
            return False
        f = _entry_carrier(entry)
        if f is None:
            log.warning("duty_results: an alternative carrier needs "
                        "inverter.f_carrier_hz — not recorded")
            return False
        doc = read_all()
        node = (((doc.get("results") or {}).get(str(die)) or {})
                .get(str(cfg)) or {}).get(str(duty))
        main = (node or {}).get(kind)
        if not isinstance(node, dict) or not isinstance(main, dict):
            return False
        e = _flat(dict(entry or {}))
        e["kind"] = kind
        e.setdefault("recorded_at", _now())
        alts = [a for a in (main.get("alt_carriers") or [])
                if isinstance(a, dict) and _entry_carrier(a) != f]
        alts.append(e)
        main["alt_carriers"] = alts[-ALT_CARRIERS_MAX:]
        return _write_all(doc)
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_results: could not record the %s Hz carrier for "
                    "%s/%s/%s (%s)", f if 'f' in dir() else "?",
                    die, cfg, duty, exc)
        return False


def _node(results: Dict[str, Any], die: str, cfg: str) -> Dict[str, Any]:
    """This configuration's rows, matched by the name the caller used AND by the
    plain die name — a published die is addressed as ``"<die> · by <name>"``
    while its own store, written by its author, knows it as ``<die>``."""
    for key in (str(die), _plain_die(die)):
        node = (results.get(key) or {}).get(str(cfg))
        if isinstance(node, dict) and node:
            return node
    return {}


def _plain_die(die: str) -> str:
    try:
        from motor_ai_sim import workspace as _ws
        return _ws.split_published_label(str(die))[0]
    except Exception:                                       # noqa: BLE001
        return str(die)


def get(die: str, cfg: str) -> Dict[str, Dict[str, Any]]:
    """``{duty: {kind: entry}}`` for one configuration.  ``{}`` when unknown.

    Reads THROUGH the layers (see above): the shared and published stores are
    merged in first and the workspace's own rows land on top, so re-solving a
    published duty in your own workspace shows YOUR answer while the ones you
    have not re-solved still show the author's.
    """
    try:
        out: Dict[str, Dict[str, Any]] = {}
        for p in _fallback_stores(die):
            out.update(_node(_read_store(p), die, cfg))
        out.update(_node(read_all().get("results") or {}, die, cfg))
        return out
    except Exception:                                       # noqa: BLE001
        return {}


def store_paths(die: str) -> List[Path]:
    """Every store a read for this die falls THROUGH, in merge order.

    Deepest first (shared, then the publishing author's, then this workspace's),
    which is exactly the order :func:`get` and :func:`index` apply them in — the
    shallowest layer lands on top.  Exposed because a caller that MEMOISES
    anything derived from this store has to be able to ask what its memo depends
    on, and stat-ing the paths is the cheapest honest answer (see
    :func:`store_signature`).
    """
    out = list(_fallback_stores(die))
    out.append(store_path())
    return out


def store_signature(dies: Any) -> tuple:
    """``((path, mtime_ns, size), …)`` for every store that could answer for
    ``dies`` — what a memo over this store is valid for.

    WHY THIS EXISTS (2026-09-17).  ``routes.family._tree_signature`` covers the
    catalog's YAML files, because until now the tree was built from nothing else.
    A coupled run writes ``.duty_results.json`` and touches no yaml, so a tree
    that carries anything from this store would keep serving the answer from
    before the run — a duty row saying a machine is fine hours after the loop
    said it is not.  One stat per store (one file on a single-layer install)
    closes that, and an unreadable store returns ``()``, which every caller here
    treats as "never serve from the memo".
    """
    seen: Dict[str, None] = {}
    sig: List[tuple] = []
    try:
        for die in (dies or ()):
            for p in store_paths(str(die)):
                key = str(p)
                if key in seen:
                    continue
                seen[key] = None
                try:
                    st = p.stat()
                except FileNotFoundError:
                    # An absent store is a FACT about this workspace, and it has
                    # to be part of the signature: a memo taken before the first
                    # coupled run of a fresh workspace must fall the moment that
                    # run creates the file.
                    sig.append((key, -1, -1))
                    continue
                sig.append((key, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    return tuple(sig)


def _die_configs(results: Dict[str, Any], die: str) -> Dict[str, Any]:
    """One layer's rows for a die: ``{configuration: {duty: {kind: entry}}}``.

    The same two-name match :func:`_node` makes, applied to the whole die at
    once: a published die is addressed as ``"<die> · by <name>"`` while its own
    store, written by its author, knows it as ``<die>``.  The caller's own
    spelling wins, so the two are visited least-specific first.
    """
    out: Dict[str, Any] = {}
    for key in (_plain_die(die), str(die)):
        node = results.get(key)
        if not isinstance(node, dict):
            continue
        for cfg, duties in node.items():
            if isinstance(duties, dict) and duties:
                out[str(cfg)] = duties
    return out


def index(die: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Every stored answer this die has, ``{configuration: {duty: {kind: …}}}``.

    THE POINT IS THE READ COUNT.  :func:`get` answers for ONE configuration and
    re-reads every layer's whole JSON to do it, which is the right shape for a
    report of one machine and the wrong one for the catalog tree: a die with
    twelve configurations would parse the same file twelve times, on a request
    the Motors tab makes on every render.  This reads each layer ONCE and hands
    back the whole die.

    Merged exactly the way :func:`get` merges — deepest layer first, the
    shallowest on top, first hit wins PER DUTY — so a caller cannot see one
    answer here and a different one there.  ``{}`` on anything unreadable: the
    catalog degrades to the row it drew before this store existed.
    """
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    try:
        for p in store_paths(die):
            for cfg, duties in _die_configs(_read_store(p), die).items():
                node = out.setdefault(cfg, {})
                for duty, kinds in duties.items():
                    if isinstance(kinds, dict):
                        node[str(duty)] = kinds
    except Exception:                                       # noqa: BLE001
        return {}
    return out


def rename(die: str, cfg: str, old: str, new: str) -> bool:
    """Carry a duty's results to its NEW name, keeping every row.

    A duty is addressed here by its NAME, so a rename that left the rows
    behind hid every thermal, mechanical, coupled and duty-cycle answer the
    duty owns — the report then says "not solved for this duty" over results
    that are still in the store (the L180 gen rename of 2026-09-16).  Dropping
    them instead, which :func:`forget`'s docstring once offered a rename, is
    the same loss with the evidence deleted.
    """
    if str(old) == str(new):
        return False
    try:
        doc = read_all()
        results = doc.get("results") or {}
        for key in (str(die), _plain_die(die)):
            node = (results.get(key) or {}).get(str(cfg))
            if isinstance(node, dict) and str(old) in node:
                node[str(new)] = node.pop(str(old))
                return _write_all(doc)
        return False
    except Exception:                                       # noqa: BLE001
        return False


def forget(die: str, cfg: str, duty: Optional[str] = None) -> bool:
    """Drop a duty's results (or a whole configuration's) — used when the
    catalogue DELETES the duty they describe.  A rename carries them instead
    (:func:`rename`)."""
    try:
        doc = read_all()
        cfgs = (doc.get("results") or {}).get(str(die)) or {}
        node = cfgs.get(str(cfg))
        if not isinstance(node, dict):
            return False
        if duty is None:
            cfgs.pop(str(cfg), None)
        elif str(duty) in node:
            node.pop(str(duty), None)
        else:
            return False
        return _write_all(doc)
    except Exception:                                       # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Which duty is loaded right now
# ---------------------------------------------------------------------------


def active_context() -> Optional[Tuple[str, str, str]]:
    """``(die, configuration, duty)`` of the machine the editor has loaded.

    Read straight off ``config/.family_context.json`` rather than through
    ``routes.family`` — this module is imported from the tail of three solve
    routes and must not drag the catalog router (and its FastAPI surface) in
    with it.  ``None`` when nothing is loaded or no duty is named: a solve of a
    machine that is not a catalogued duty has nowhere to be filed, and inventing
    a place for it is how one duty's answer ends up under another's name.
    """
    try:
        d = json.loads((_config_dir() / ".family_context.json")
                       .read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return None
        die, cfg, duty = d.get("die"), d.get("config"), d.get("duty")
        if die and cfg and duty:
            return str(die), str(cfg), str(duty)
    except Exception:                                       # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Compaction — what a report table actually needs
# ---------------------------------------------------------------------------
# Deliberately explicit key lists rather than "everything but the heavy arrays":
# a solver that grows a new 40 MB payload tomorrow must not silently grow this
# file too.


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return None if (f != f or f in (float("inf"), float("-inf"))) else f
    except (TypeError, ValueError):
        return None


def _pick(src: Any, keys) -> Dict[str, Any]:
    if not isinstance(src, dict):
        return {}
    return {k: src[k] for k in keys if src.get(k) is not None}


_COOLING_KEYS = ("mode", "h_conv", "t_sink_c", "t_in_c", "t_out_c", "flow_lpm",
                 "air_speed_mps", "fluid", "heat_removed_W", "area_m2",
                 # THE STILL-AIR FILM (2026-09-14, cooling_mode='robotics'):
                 # its Robin coefficient is h_total, and the half of it that is
                 # RADIATION is the half the emissivity buys.  Without these
                 # the comparison table's boundary cell had an h and no way to
                 # say whether it was one film or two, and the report printed a
                 # still-air boundary as "fixed h" — the very bug class this
                 # whitelist is commented for further down.
                 "h_rad", "h_total", "t_wall_c", "t_film_c", "emissivity",
                 "convection_W", "radiation_W", "regime")

#: One axial end face's block, as ``compact_thermal`` keeps it.  The per-face
#: geometry (area, orientation, Ra/Nu) is not copied: the report asks these
#: blocks for watts and for the film that produced them.
_END_FACE_KEYS = ("mode", "area_m2", "h_conv", "h_rad", "h_total", "n_faces",
                  "G_W_per_K", "t_sink_c", "t_mean_c", "heat_removed_W",
                  "emissivity")


def _end_faces(block: Any) -> Dict[str, Any]:
    """``cooling.end_faces`` trimmed to what a report row asks it: the totals,
    and one small block per face.  ``{}`` on a record written before the axial
    paths existed — an absent key, not a blank row."""
    if not isinstance(block, dict) or not block:
        return {}
    out = _pick(block, ("mode", "sides", "emissivity", "heat_removed_W",
                        "G_W_per_K", "k_end"))
    for node in ("winding", "stator", "rotor", "magnet"):
        sub = _pick(block.get(node), _END_FACE_KEYS)
        if sub:
            out[node] = sub
    return out


def compact_thermal(result: Dict[str, Any], params: Dict[str, Any],
                    fp: Optional[str], computed_at: Optional[str] = None
                    ) -> Dict[str, Any]:
    """The temperature answer as a report row: per-part temperatures, the
    boundary conditions in numbers, and the heat budget that closes them.

    The per-node temperature field is NOT copied — it is what makes the pickle
    tens of megabytes, and a comparison table has no use for it.  The map in the
    report still comes from ``routes.thermal._LAST``, for the one duty that
    holds it.
    """
    res = result if isinstance(result, dict) else {}
    inner = res.get("field") if isinstance(res.get("field"), dict) else res
    comps: Dict[str, Any] = {}
    for k, v in dict(res.get("components") or inner.get("components") or {}).items():
        if isinstance(v, dict):
            comps[str(k)] = {"avg": _f(v.get("avg")), "max": _f(v.get("max")),
                             "min": _f(v.get("min"))}
    cooling = dict(res.get("cooling") or inner.get("cooling") or {})
    # WHICH EXCITATION's watts this map was solved on.  The loss map arrives
    # with its own provenance (`loss_source`), and on a coupled PWM run that
    # block carries the inverter — so the thermal column can say "these
    # temperatures are the 24 kHz ones" without the reader having to find the
    # coupled record.  Absent = the sinusoid, which is every map solved before
    # the PWM loop existed.
    _ls = res.get("loss_source") or inner.get("loss_source") or {}
    _ls = _ls if isinstance(_ls, dict) else {}
    _snap = res.get("transient_snapshot") or inner.get("transient_snapshot") or {}
    _snap = _snap if isinstance(_snap, dict) else {}
    out = {
        "computed_at": computed_at,
        "geometry_fingerprint": fp,
        **_fp2_block(),
        "drive": str(_ls.get("drive") or "sine"),
        **({"inverter": dict(_ls["inverter"])}
           if isinstance(_ls.get("inverter"), dict) else {}),
        # WHICH STATE these components are (2026-09-21).  "steady" is a solved
        # map; "limited" is the machine at the instant a part reached its limit,
        # translated off the steady map it was solved from — and then the
        # `cooling` block below is the STEADY one and says so
        # (`cooling.from_state`).  A record that does not say which state it is
        # cannot be read back at all: the L13 peak's mixed one carried a 183.5 °C
        # winding beside a 297.3 °C housing wall and refits to nothing.
        "state": str(res.get("state") or inner.get("state") or "steady"),
        # …and the node means of the map the cooling block DOES belong to, so a
        # limited record is refittable read-only.
        **({"calibration_components_c": dict(_snap["calibration_components_c"])}
           if isinstance(_snap.get("calibration_components_c"), dict) else {}),
        **({"transient_snapshot": {k: v for k, v in _snap.items()
                                   if k != "calibration_components_c"}}
           if _snap else {}),
        "components": comps,
        "T_max": _f(res.get("T_max", inner.get("T_max"))),
        "T_min": _f(res.get("T_min", inner.get("T_min"))),
        "P_loss_total_W": _f(res.get("P_loss_total_W",
                                     inner.get("P_loss_total_W"))),
        "P_cu_W": _f(res.get("P_cu_W", inner.get("P_cu_W"))),
        "P_fe_W": _f(res.get("P_fe_W", inner.get("P_fe_W"))),
        "cooling": {
            # WHICH STATE the block below belongs to (2026-09-21).  On a LIMITED
            # record it is the steady map's, never the instant the components
            # describe — `rescale_map_to_nodes` stamps it and this carries it
            # through, so nobody fits a network to two states at once again.
            **({"from_state": str(cooling["from_state"]),
                "state_note": str(cooling.get("state_note") or "")}
               if cooling.get("from_state") else {}),
            "outer": _pick(cooling.get("outer"), _COOLING_KEYS),
            "inner": _pick(cooling.get("inner"), _COOLING_KEYS),
            "shaft_ends": _pick(cooling.get("shaft_ends"),
                                _COOLING_KEYS + ("length_each_side_mm",)),
            # The OPEN FRAME's two extra paths (2026-09-09): a machine with no
            # housing loses heat off the end windings and down the slot ducts,
            # and a housed one reports `mode: housed` here rather than nothing —
            # "there is a housing" is an answer, and a comparison column that
            # simply omitted the row could not tell the two apart.
            "frame": _pick(cooling.get("frame"),
                           _COOLING_KEYS + ("open_air_speed_mps",)),
            "end_windings": _pick(cooling.get("end_windings"), _COOLING_KEYS),
            "slot_channels": _pick(cooling.get("slot_channels"), _COOLING_KEYS),
            # THE ROBOT JOINT's two paths (2026-09-14).  On a machine in still
            # air the housing hands the room a few watts and the BOLTS take the
            # rest, so a comparison table that dropped `mount` compared the
            # wrong three per cent of the cooling; `end_faces` is the same
            # story for the end turns, which stand proud of the core.  Both are
            # kept whole-shaped (a block per face) rather than as one number,
            # because "which face" is the design question.
            "mount": _pick(cooling.get("mount"),
                           ("mode", "G_W_per_K", "t_sink_c", "t_sink_source",
                            "t_housing_mean_c", "heat_removed_W", "attached_to",
                            "note")),
            "end_faces": _end_faces(cooling.get("end_faces")),
            # THE ROBOTICS HEAT PATH (2026-09-26): the one conduction choice
            # and the housing / structure body it lands on, with its touch
            # limit — what `part_limits` judges and `cooling_from_duty_thermal`
            # reads the choice back from.
            "heat_path": _pick(cooling.get("heat_path"),
                               ("option", "body", "rides_node", "t_body_c",
                                "touch_limit_c", "over_touch_K",
                                "binds_touch_limit", "diameter_mm",
                                "length_mm", "area_m2", "G_film_W_per_K",
                                "heat_to_room_W", "contact", "bearings",
                                "note")),
            "gap": _pick(cooling.get("gap"), ("k_eff", "Ta", "Nu", "mode")),
            # THE WHOLE BUDGET, not five legacy names.  `P_in_W` / `P_out_W`
            # are not keys this route has produced for a long time, so the
            # stored block was all but empty and every per-duty budget row in
            # the report came back blank.  Found 2026-09-10 while the user was
            # generating a two-duty report: `rotor_heat_split` — the rotor's
            # own balance, out through the gap against in through the bore —
            # was being dropped here on its way to the very table it was
            # written for.
            "heat_budget": _pick(cooling.get("heat_budget"),
                                 ("losses_W", "housing_W", "bore_W", "gap_W",
                                  "shaft_ends_W", "end_windings_W",
                                  "slot_channels_W", "rotor_W",
                                  "residual_W", "residual_pct",
                                  "em_loss_total_W", "mech_loss_total_W",
                                  "mech_loss_in_map_W", "symmetry_mult",
                                  "rotor_heat_split",
                                  # …and the 2026-09-14 additions, for exactly
                                  # the same reason `rotor_heat_split` is here:
                                  # a key this list does not name is a row the
                                  # comparison table prints blank.  The mount's
                                  # watts, the two halves of the housing film,
                                  # the end faces and the STATOR's own balance —
                                  # the user's headline question (which side of
                                  # the machine does the heat leave from?) is
                                  # unanswerable without the last of them.
                                  "mount_W", "housing_convection_W",
                                  "housing_radiation_W", "end_faces_W",
                                  "stator_heat_split", "bearings_W",
                                  # legacy names, kept so an old record still
                                  # reads back the way it was written
                                  "P_in_W", "P_out_W", "P_gap_W", "closed")),
            "mech_losses": _pick(cooling.get("mech_losses"),
                                 ("P_mech_total_W", "P_mech_into_map_W")),
        },
        # The operating point the map was solved AT — without it a column
        # cannot be checked against the duty it claims to describe.
        "point": _pick(params, ("rpm", "I_phase_rms", "current_arms", "gamma_deg",
                                "coil_temp_c", "magnet_temp_c", "cooling_mode",
                                "ambient_temp", "h_conv", "coolant_temp",
                                "flow_lpm", "inner_mode", "inner_air_speed",
                                # the robotics mode's own inputs (2026-09-14):
                                # without them the column says "still air" and
                                # not at which ε, against which structure, or
                                # with how many ends open
                                "emissivity", "mount_g_w_per_k", "mount_temp_c",
                                "heat_path", "end_faces", "end_face_sides")),
    }
    return out


#: The ceiling on a stored series, per node.  400 points is what a chart of one
#: cycle can show and a great deal more than a reader can count; a 200-cycle S3
#: solved at 60 samples a segment produces 24 000, which is 1.5 MB of JSON in a
#: file an engineer is meant to be able to open.
DUTY_CYCLE_MAX_SAMPLES = 400

#: What survives compaction, block by block.  Explicit, for the reason at the
#: top of this section: a solver that grows a new payload tomorrow must not grow
#: this file too.
_DC_SPEC_KEYS = ("kind", "cycle_s", "ed_pct", "ed_given", "t_on_s", "rest_duty",
                 "calibration_duty", "t_start_c", "n_cycles_max", "duration_s",
                 "note", "segments")
_DC_SEGMENT_KEYS = ("duty", "t_s", "rpm", "coil_ref_c", "total_W", "losses_W",
                    "note")
_DC_NETWORK_KEYS = ("G_W_per_K", "areas_m2", "C_J_per_K", "cp_sources",
                    "t_ambient_c", "t_mount_c", "emissivity", "d_housing_m",
                    "hot_spot_offset_K", "merged", "h_sources", "calibration",
                    # WHICH of the internal links was fitted to the map, which
                    # carries the interface's own formula and which is a merge
                    # (2026-09-15).  A gap conductance a report prints has to
                    # say where it came from, and "merged" is a missing
                    # conductance rather than a small one.  ``merged`` above
                    # stays for the records written before this existed.
                    "links", "link_kinds", "active_nodes",
                    "calibration_duty", "notes")
_DC_CYCLE_SCALARS = ("converged", "n_cycles", "residual_K", "closure_pct",
                     "winding_hot_peak_c", "winding_hot_mean_c", "note",
                     "n_solved", "n_samples")
_DC_CYCLE_NODE_BLOCKS = ("peak_c", "min_c", "mean_c", "start_state_c")
_DC_LIMIT_KEYS = ("winding_limit_c", "winding_limit_note", "magnet_limit_c",
                  "magnet_limit_note", "limits_c", "s2_time_to_limit_s",
                  "s2_limiting_part", "s2_note", "s2_horizon_s",
                  "ed_allowable_pct", "ed_requested_pct", "ed_limiting_part",
                  "ed_note", "ed_curve", "basis",
                  # THE FOUND REGIME (2026-09-15).  The duty cycle no longer
                  # grades a ratio somebody typed: it finds the one the machine
                  # holds, over a span of cycle lengths, and says how hot it is
                  # there and how long a single pull lasts from cold, from the
                  # rated state and out of the settled cycle.  A report that
                  # printed "allowable 21.6 %" without `ed_found` could not tell
                  # a found regime from a graded one, so the flag is stored with
                  # the number.
                  "ed_vs_cycle", "ed_cycle_s", "ed_found", "ed_found_note",
                  "at_allowable", "limiting_part",
                  "s2_from_rated_s", "s2_from_rated_part",
                  "s2_from_rated_start_c", "s2_from_rated_note",
                  "s2_from_cycle_mean_s", "s2_from_cycle_mean_part",
                  "s2_from_cycle_mean_start_c", "s2_from_cycle_mean_note")
_DC_SPLIT_KEYS = ("basis", "generated_W", "generated_total_W", "stator_side_W",
                  "rotor_side_W", "stator_pct", "rotor_pct", "housing_W",
                  "mount_W", "winding_end_faces_W", "stator_end_faces_W",
                  "rotor_end_faces_W", "magnet_end_faces_W", "bore_W",
                  "shaft_ends_W", "bearings_W", "gap_W", "winding_to_core_W",
                  "closure_W",
                  "closure_pct", "note")
_DC_POINT_KEYS = ("rpm", "I_phase_rms", "current_arms", "gamma_deg",
                  "coil_temp_c", "magnet_temp_c", "cooling_mode",
                  "ambient_temp", "bore_mode", "end_faces", "end_face_sides",
                  "emissivity", "mount_g_w_per_k", "mount_temp_c", "heat_path",
                  "h_conv",
                  "calibration_duty", "calibration_source",
                  "n_steps_per_period", "n_periods", "mesh_size_mm",
                  "min_size_mm", "n_sectors")


def _decimate_series(xs: Any, cap: int) -> Any:
    """Every ``stride``-th sample of a list, the last one always kept.

    The same rule ``thermal_duty_cycle.decimate`` applies to the live trace, run
    again here because this function must be able to compact a record that came
    from anywhere — an older route, a re-read of the store, a test.
    """
    if not isinstance(xs, (list, tuple)):
        return xs
    n = len(xs)
    cap = max(int(cap), 2)
    if n <= cap:
        return list(xs)
    stride = int(-(-n // cap))                       # ceil
    idx = list(range(0, n, stride))
    if idx[-1] != n - 1:
        if len(idx) >= cap:
            idx[-1] = n - 1                          # REPLACE, never append:
        else:                                        # the cap is a ceiling
            idx.append(n - 1)
    return [xs[i] for i in idx]


def compact_duty_cycle(record: Dict[str, Any], params: Dict[str, Any],
                       fp: Optional[str], computed_at: Optional[str] = None,
                       *, max_samples: int = DUTY_CYCLE_MAX_SAMPLES
                       ) -> Dict[str, Any]:
    """The duty-cycle answer as a report section: the spec, the network it was
    integrated on, the periodic cycle (series included) and the limits.

    The SERIES are what makes this kind different from every other one in this
    store, and they are what a duty-cycle page is: the temperature of four nodes
    through one cycle, the hot spot above them and the loss that drove it.  They
    are kept — decimated to ``max_samples`` points per node, which is under
    40 kB for the 200-cycle S3 this was written for — because a chart rebuilt
    from a peak and a mean is not the answer, it is a sketch of it.

    Everything else is a whitelist, block by block: no ``trace`` object, no
    numpy, no second copy of the steady map the network was fitted to.
    """
    rec = record if isinstance(record, dict) else {}
    out: Dict[str, Any] = {
        "computed_at": computed_at or rec.get("computed_at"),
        "geometry_fingerprint": fp or rec.get("geometry_fingerprint"),
        **_fp2_block(),
        "duty": rec.get("duty"),
        "spec": _pick(rec.get("spec"), _DC_SPEC_KEYS),
        "network": _pick(rec.get("network"), _DC_NETWORK_KEYS),
        "split": _pick(rec.get("split"), _DC_SPLIT_KEYS),
        "limits": _pick(rec.get("limits"), _DC_LIMIT_KEYS),
        # The operating point the network was CALIBRATED at, plus the boundary
        # conditions — without them a stored cycle cannot be checked against the
        # machine it claims to describe (same rule as `compact_thermal.point`).
        "point": _pick(rec.get("point") or params, _DC_POINT_KEYS),
        "elapsed_s": _f(rec.get("elapsed_s")),
        "solve_time_s": _f(rec.get("solve_time_s")),
    }
    # `duty` is spelled out rather than left to `_pick`, which drops a None: on a
    # segment `null` is not a missing value, it is the machine standing UNPOWERED
    # — a real segment of a real cycle — and a reader that saw no key at all
    # could not tell it from a segment whose name was lost.
    segs = [dict(_pick(s, _DC_SEGMENT_KEYS), duty=s.get("duty"))
            for s in ((out["spec"] or {}).get("segments") or [])
            if isinstance(s, dict)]
    if segs:
        out["spec"] = dict(out["spec"], segments=segs)

    cyc = rec.get("cycle") if isinstance(rec.get("cycle"), dict) else {}
    cycle: Dict[str, Any] = _pick(cyc, _DC_CYCLE_SCALARS)
    if isinstance(cyc.get("converged"), bool):
        cycle["converged"] = bool(cyc["converged"])
    for k in _DC_CYCLE_NODE_BLOCKS:
        blk = cyc.get(k)
        if isinstance(blk, dict):
            cycle[k] = {str(n): _f(v) for n, v in blk.items()}
    cycle["t_s"] = _decimate_series(cyc.get("t_s"), max_samples)
    for k in ("T_c", "P_W"):
        blk = cyc.get(k)
        if isinstance(blk, dict):
            cycle[k] = {str(n): _decimate_series(v, max_samples)
                        for n, v in blk.items()}
    if isinstance(cyc.get("winding_hot_c"), list):
        cycle["winding_hot_c"] = _decimate_series(cyc["winding_hot_c"],
                                                  max_samples)
    cycle["n_samples"] = len(cycle.get("t_s") or [])
    out["cycle"] = cycle
    return out


#: How many speed points of a Campbell sweep a stored record keeps.  The solver
#: itself runs 41 (``rotordynamics.N_CAMPBELL_POINTS``), so this is a ceiling
#: nothing hits today and a guard against a future sweep that runs finer.
CAMPBELL_MAX_POINTS = 200


def compact_campbell(cam: Any, cap: int = CAMPBELL_MAX_POINTS
                     ) -> Optional[Dict[str, Any]]:
    """The Campbell sweep, small enough to live in a stored record.

    THE PICTURE, not just its crossings (2026-09-14).  ``critical_speeds`` alone
    says WHERE a forward branch met the 1x line; the report's Campbell diagram
    draws the branches themselves, and a record that kept only the crossings
    published an empty pair of axes under a caption promising curves — the L155
    report, page 29.  Kept in the solver's own shape: ``rpm`` is the speed axis
    and ``forward`` / ``backward`` are indexed BY SPEED POINT, one per-mode
    frequency vector each, so a branch is a column.

    ``None`` when there is no sweep to keep, which is what tells the report to
    drop the figure rather than draw nothing.
    """
    if not isinstance(cam, dict):
        return None
    rpm = cam.get("rpm")
    if not isinstance(rpm, (list, tuple)) or not rpm:
        return None
    fwd = cam.get("forward") if isinstance(cam.get("forward"), (list, tuple)) else []
    bwd = cam.get("backward") if isinstance(cam.get("backward"), (list, tuple)) else []
    if not fwd and not bwd:
        return None
    n = len(rpm)
    idx = _decimate_series(list(range(n)), cap)

    def _branch(b) -> List[Any]:
        out: List[Any] = []
        for i in idx:
            row = b[i] if i < len(b) else None
            out.append([_f(x) for x in row]
                       if isinstance(row, (list, tuple)) else [])
        return out

    out: Dict[str, Any] = {"rpm": [_f(rpm[i]) for i in idx],
                           "forward": _branch(fwd) if fwd else [],
                           "backward": _branch(bwd) if bwd else [],
                           "n_points": len(idx)}
    if len(idx) < n:
        out["decimated_from"] = n
    return out


def compact_mechanical(kind: str, result: Dict[str, Any],
                       params: Dict[str, Any], fp: Optional[str],
                       computed_at: Optional[str] = None) -> Dict[str, Any]:
    """One mechanical answer as a report row.

    ``rotor_stress`` keeps the per-part stress rows, the safety factors, the
    contact verdicts, the seating record and the fit — everything the warnings
    engine checks — but not the per-element von Mises field.  ``modes`` and
    ``critical_speeds`` keep their frequency lists, which are a handful of
    numbers each.
    """
    res = result if isinstance(result, dict) else {}
    base = {"computed_at": computed_at, "geometry_fingerprint": fp,
            **_fp2_block(),
            "point": _pick(params, ("rpm", "torque_nm", "loads", "cases",
                                    "overspeed", "mesh_size_mm", "order"))}
    if kind == "rotor_stress":
        case_name = res.get("primary_case") or next(iter(res.get("cases") or {}), None)
        case = (res.get("cases") or {}).get(case_name) or {}
        parts: Dict[str, Any] = {}
        for name, p in dict(case.get("parts") or {}).items():
            if isinstance(p, dict):
                parts[str(name)] = _pick(p, (
                    "material", "von_mises_p995_mpa", "von_mises_max_mpa",
                    "principal_max_p995_mpa", "hoop_max_mpa",
                    "principal_max_mpa", "radial_min_mpa",
                    # The AVERAGED / unaveraged pair and the stress the safety
                    # factor was divided into, so a restored duty can still say
                    # which convention its numbers are on (2026-09-10).
                    "von_mises_max_unaveraged_mpa",
                    "principal_max_unaveraged_mpa",
                    "hoop_max_unaveraged_mpa", "stress_convention",
                    "governing_stress_mpa",
                    "strength_mpa", "strength_kind", "safety_factor",
                    "mass_kg"))
        ifaces: Dict[str, Any] = {}
        for label, i in dict(case.get("interfaces") or {}).items():
            if isinstance(i, dict):
                ifaces[str(label)] = _pick(i, ("type", "open_fraction",
                                               "lift_off", "p_max_mpa"))
        contact = case.get("contact") or {}
        base.update({
            "case": case_name,
            "rpm": _f(case.get("rpm", res.get("rpm"))),
            "overspeed_factor": _f(res.get("overspeed_factor")),
            "sf_min": _f(case.get("sf_min")),
            "sf_min_part": case.get("sf_min_part"),
            "sf_min_p05": _f(case.get("sf_min_p05")),
            "sf_min_unaveraged": _f(case.get("sf_min_unaveraged")),
            "parts": parts,
            "interfaces": ifaces,
            "rotor_od_growth_um": _f(case.get("rotor_od_growth_um")),
            # The same surface as a named block: max / mean / least radial
            # travel of the outermost rotating surface, and which part it is.
            "od_growth": _pick(case.get("od_growth"),
                               ("max_um", "mean_um", "min_um", "r_mm",
                                "part", "n_nodes")),
            # What is left of the air gap once the rotor has grown into it.
            "air_gap": _pick(case.get("air_gap"),
                             ("clearance_um", "closed_um", "remaining_um",
                              "closed_pct", "bore_r_mm", "rotor_r_mm")),
            "max_displacement_um": _f(case.get("max_displacement_um")),
            "interference_mm": _f(res.get("interference_mm")),
            "interference_effective_mm": _f(res.get("interference_effective_mm")),
            "lift_off_rpm": {str(k): _f(v) for k, v in
                             dict(res.get("lift_off_rpm") or {}).items()},
            "magnet_retention": _pick(case.get("magnet_retention"),
                                      ("verdict", "share", "seated")),
            "torque_path": _pick(case.get("torque_path"),
                                 ("verdict", "worst_pair", "held")),
            "torque_balance": _f(case.get("torque_balance")),
            "contact": {
                "converged": bool(contact.get("converged")) if contact else None,
                "iterations": contact.get("iterations"),
                "residual_um": _f(contact.get("residual_um")),
                "unretained_parts": list(contact.get("unretained_parts") or []),
                # WHAT WAS SEATED, not the whole travel record: a magnet that had
                # to travel to find its pocket wall is a different machine from
                # one that was already touching, and the report says which.
                "seated": [{"part": s.get("part"),
                            "travel_rel_um": _f(s.get("travel_rel_um")),
                            "landed_on": s.get("landed_on")}
                           for s in (contact.get("seated") or [])
                           if isinstance(s, dict)],
            },
            # RULE 2026-09-09: temperature enters mechanics ONLY as a band's fit
            # change, so the temperatures a stress solve ran with are part of
            # its identity and travel with it.
            "part_temps_c": {str(k): _f(v) for k, v in
                             dict((res.get("thermal") or {}).get("part_temps_c")
                                  or {}).items()},
            "ref_temp_c": _f((res.get("thermal") or {}).get("ref_temp_c")),
            "mesh": _pick(res.get("mesh"), ("n_triangles", "element_order",
                                            "mesh_size_mm")),
        })
        # THE LIMIT SPEED (owner 2026-09-21: "нужно искать ещё максимальную
        # скорость вращения ... она будет, когда достигает SF = 1"), riding on
        # the same rotor-stress record — present only when the **Limit speed**
        # button (not this table build) produced it, never computed here.
        _ls = res.get("limit_speed")
        if isinstance(_ls, dict):
            base["limit_speed"] = {
                "rpm_sf1": _f(_ls.get("rpm_sf1")),
                "reached": (bool(_ls.get("reached"))
                           if _ls.get("reached") is not None else None),
                "limiting_part": _ls.get("limiting_part"),
                "sf_at_rpm0": _f(_ls.get("sf_at_rpm0")),
                "target_sf": _f(_ls.get("target_sf")),
                "analysed_rpm": _f(_ls.get("analysed_rpm")),
                "loads": _ls.get("loads"),
                "torque_nm": _f(_ls.get("torque_nm")),
                "n_solves": _ls.get("n_solves"),
                "omega2_extrapolation_rpm": _f(_ls.get("omega2_extrapolation_rpm")),
                "note": _ls.get("note"),
                # v2 search (2026-09-25): when NOT reached, how far it looked
                # and which guard stopped it — the report row says so.
                "searched_to_rpm": _f(_ls.get("searched_to_rpm")),
                "sf_at_searched_to": _f(_ls.get("sf_at_searched_to")),
                "cap_reason": _ls.get("cap_reason"),
                "stopped_by": _ls.get("stopped_by"),
            }
    elif kind == "critical_speeds":
        base.update({
            "rated_rpm": _f(res.get("rated_rpm")),
            "verdict": res.get("verdict"),
            "critical_speeds": [
                _pick(c, ("mode", "whirl", "rpm", "margin_vs_rated_pct",
                          "excited_by_unbalance"))
                for c in (res.get("critical_speeds") or []) if isinstance(c, dict)],
            # …and the SWEEP the crossings came out of, so the report's Campbell
            # diagram can be drawn from a duty's own record and not only from
            # the machine-level store (A4, 2026-09-14).
            "campbell": compact_campbell(res.get("campbell")),
            "rpm_plot_max": _f(res.get("rpm_plot_max")),
            # Which carrier the excitation table beside the branches was built
            # on — the duty's own, since 2026-09-14.
            "f_switch_hz": _f(res.get("f_switch_hz")),
        })
    else:                                   # modes
        # ``modes`` is a list of one dict per mode; only its frequency and its
        # excitation flag mean anything in a comparison column — the mode SHAPES
        # are per-node vectors and stay in the mechanical pickle.
        mm = [m for m in (res.get("modes") or []) if isinstance(m, dict)][:12]
        base.update({
            "modes": [_pick(m, ("f_hz", "kind", "name", "excited_by",
                                "margin_pct", "flag")) for m in mm],
            "frequencies_hz": [_f(m.get("f_hz")) for m in mm],
            "n_modes": len(res.get("modes") or []) or res.get("n_modes"),
            # THE CARRIER the "nearest excitation" column was built on (A1,
            # 2026-09-14): without it a table naming "PWM carrier (48,000 Hz)"
            # could not be checked against the duty's own `sim.fSwitch`, and one
            # duty's ring modes were published against another machine's
            # inverter.  `None` = this run had no PWM line.
            "f_switch_hz": _f(res.get("f_switch_hz")),
        })
    return base


#: A loss block, in the four classes a report row compares plus the total.
_PWM_LOSS_KEYS = ("copper_W", "iron_W", "solid_W", "magnets_W", "total_W")

#: …and the same four as an ADDED loss, which is what the carrier costs.
_PWM_DELTA_KEYS = ("copper", "iron", "solid", "magnets", "total")

#: The scalars one measured carrier keeps.  ``label`` and ``note`` are the two
#: strings; everything else is a number, and the two booleans below are read
#: separately because ``_f`` drops a bool on purpose.
_PWM_CASE_NUM_KEYS = ("f_carrier_hz", "v_bus_V", "m", "dc_residual_A",
                      "T_em_Nm", "eta_em", "eta_shaft_est", "ripple_pct",
                      "thd_i_pct")

#: How many carriers one duty's record keeps.  A PWM run is an hour of FEM, so
#: a duty with more than a handful of them does not exist; the cap is here for
#: the same reason every other cap in this file is.
PWM_MAX_CASES = 8

#: …and how many free-text notes.  Each is trimmed, so the whole record stays
#: the "under 10 kB an engineer can open" this store promises.
PWM_MAX_NOTES = 12
PWM_NOTE_CHARS = 400


def _pwm_losses(src: Any) -> Dict[str, Any]:
    d = src if isinstance(src, dict) else {}
    return {k: _f(d.get(k)) for k in _PWM_LOSS_KEYS if _f(d.get(k)) is not None}


def compact_pwm(record: Dict[str, Any],
                computed_at: Optional[str] = None) -> Dict[str, Any]:
    """One duty's PWM measurement as a report section.

    THE SHAPE IS A COMPARISON, not a run.  A PWM answer only means anything
    beside the sinusoid it is measured against, and only when the two were
    solved at the SAME time step, mesh and temperatures — the resolution-matched
    baseline (PWM study 2026-09-13 §1.1: the step count alone is worth 101 W on
    this machine, 1.3 % of the loss, which is half of what a carrier costs).  So
    the record carries the baseline it was measured against, then one block per
    carrier with its own already-differenced ``delta_W``.

    WHAT IS DELIBERATELY NOT HERE: waveforms, spectra, the DC-link current
    series, the modulator's pulse train.  They are what makes a PWM run payload
    a hundred kilobytes, and a comparison table has no use for them; the run's
    own JSON keeps them.

    ``dc_converged`` is the flag that says whether the run's torque ripple and
    ripple current may be QUOTED at all — a PWM solve that ends with a DC offset
    in the phase current reports a ripple that belongs to the offset, not to the
    machine (the 2026-09-02 settle artefact, measured again at 24 and 48 kHz on
    this machine).  Losses survive that; ripple does not, and a record that did
    not carry the distinction would publish the wrong half.
    """
    rec = record if isinstance(record, dict) else {}
    base = rec.get("baseline") if isinstance(rec.get("baseline"), dict) else {}
    out: Dict[str, Any] = {
        "computed_at": computed_at or rec.get("computed_at"),
        "source": (str(rec.get("source"))[:600]
                   if rec.get("source") is not None else None),
        "baseline": {
            "n_steps": _f(base.get("n_steps")),
            "T_em_Nm": _f(base.get("T_em_Nm")),
            "losses": _pwm_losses(base.get("losses")),
            "eta_em": _f(base.get("eta_em")),
            "ripple_pct": _f(base.get("ripple_pct")),
        },
        "cases": [],
        "notes": [str(n)[:PWM_NOTE_CHARS]
                  for n in (rec.get("notes") or [])][:PWM_MAX_NOTES],
    }
    for c in (rec.get("cases") or [])[:PWM_MAX_CASES]:
        if not isinstance(c, dict):
            continue
        case: Dict[str, Any] = {"label": (str(c.get("label"))[:80]
                                          if c.get("label") is not None else None)}
        for k in _PWM_CASE_NUM_KEYS:
            case[k] = _f(c.get(k))
        # The two verdicts are booleans on purpose: "unknown" is not "no", and a
        # reader of the stored file must be able to tell them apart.
        for k in ("dc_converged", "post_fix"):
            case[k] = bool(c.get(k)) if c.get(k) is not None else None
        case["losses"] = _pwm_losses(c.get("losses"))
        dw = c.get("delta_W") if isinstance(c.get("delta_W"), dict) else {}
        case["delta_W"] = {k: _f(dw.get(k)) for k in _PWM_DELTA_KEYS
                           if _f(dw.get(k)) is not None}
        case["note"] = (str(c.get("note"))[:PWM_NOTE_CHARS]
                        if c.get("note") is not None else None)
        out["cases"].append(case)
    return out


def compact_coupled(out: Dict[str, Any]) -> Dict[str, Any]:
    """The coupled loop's verdict: how many electromagnetic runs it took, where
    the temperatures settled, what the bearings cost and what came out of the
    shaft.  ``history`` is dropped — one row per iteration is a chart, not a
    comparison column."""
    o = out if isinstance(out, dict) else {}
    c = o.get("coupling") or {}
    mech = c.get("mechanical") if isinstance(c.get("mechanical"), dict) else {}
    return {
        "computed_at": o.get("computed_at"),
        "geometry_fingerprint": o.get("geometry_fingerprint"),
        **_fp2_block(),
        # WHICH EXCITATION these temperatures were reached on, and — when it is
        # the inverter — the bridge that reached them.  A record with no
        # `drive` predates the PWM loop and is a sinusoid (see `_entry_drive`).
        "drive": c.get("drive") or "sine",
        **({"inverter": dict(c["inverter"])}
           if isinstance(c.get("inverter"), dict) else {}),
        # The last electromagnetic run's own numbers — torque, the four loss
        # classes, the total and η.  Without them a `reference_sine` block
        # could say what the sinusoid's temperatures were but not what its
        # watts were, which is the one row a sine → PWM table is about.
        **({"em": {k: _f(v) if not isinstance(v, str) else v
                   for k, v in c["em"].items()}}
           if isinstance(c.get("em"), dict) and c["em"] else {}),
        # THE REGIME the loop found, when this duty has a cycle (2026-09-16).
        # Kept whole — it is two dozen scalars and two small temperature blocks,
        # the curves live in the `duty_cycle` record the same run files — because
        # a coupled answer on an impulse duty IS a duty ratio, and a column that
        # carried the temperatures without the ratio they belong to would be a
        # winding at 131 °C with nothing to say when.
        **({"duty_cycle": dict(c["duty_cycle"])}
           if isinstance(c.get("duty_cycle"), dict) and c["duty_cycle"] else {}),
        # HOW LONG IT MAY RUN, when the point is over a limit (2026-09-17).
        # Kept whole — it is a handful of scalars, a per-part list and two start
        # blocks — because the report's "Time to the limit" row and the §8 notes
        # are read off the DUTY's record and not off the run, and a temperature
        # over its class with no time beside it is the question the owner asked
        # this feature for.  Absent, never null, on a point inside every limit:
        # a machine that is not over anything has no time to a limit.
        **({"time_to_limit": dict(c["time_to_limit"])}
           if isinstance(c.get("time_to_limit"), dict) and c["time_to_limit"]
           else {}),
        # ── WHICH QUESTION THIS DUTY WAS ASKED, AND WHAT CAME BACK ──────────
        # Owner 2026-09-18: the loop now answers one of two questions — the
        # steady state, or the machine AT the first limit it reaches — and it is
        # a CHOICE.  Both travel: `solve_to` is what was asked, `mode` is what
        # this record is.  A record written before they existed carries neither,
        # and every reader must take that as `steady`: that is what it was.
        #
        # The `limited` block is kept WHOLE (a dozen scalars, two small
        # temperature dicts and the cooling the answer is conditional on)
        # because the report's §8, its coupled table and the catalog chip all
        # read the DUTY's record and not the run, and a table of temperatures
        # at a limit with nothing saying they are at a limit is worse than no
        # table at all.
        **({"solve_to": c["solve_to"]} if c.get("solve_to") else {}),
        **({"mode": c["mode"]} if c.get("mode") else {}),
        **({"limited": dict(c["limited"])}
           if isinstance(c.get("limited"), dict) and c["limited"] else {}),
        # ── THE CATALOGUE CONSTANTS (owner 2026-09-18) ──────────────────────
        # *«для каждого отчёта делать прогон на холодную 20 °C, чтобы находить
        # все коэффициенты KV, Kt, Km, Km/mass»*.  Kept WHOLE — three small
        # dicts of scalars — because §4's catalogue subsection, the datasheet
        # and any comparison between machines read the DUTY's record and not
        # the run, and a constant that does not make this crossing reaches no
        # page.  Absent, never null, on a record whose cold pass was switched
        # off or refused: "not solved" is an answer, an extrapolated Kt is not.
        **({"constants_20c": dict(c["constants_20c"])}
           if isinstance(c.get("constants_20c"), dict) and c["constants_20c"]
           else {}),
        # THE CONTINUOUS (S1) RATING (owner 2026-09-21): the largest current
        # this machine may hold for ever at THIS duty's own saved cooling, from
        # a ``solve_to: continuous`` run.  Kept WHOLE — the same shape
        # ``coupled_continuous_rating.rate`` returns for one condition — because
        # the report's rating row and the catalog chip read the DUTY's record,
        # not the run.  Absent, never null, on every record that did not ask
        # for it.
        **({"continuous_rating": dict(c["continuous_rating"])}
           if isinstance(c.get("continuous_rating"), dict)
           and c["continuous_rating"] else {}),
        # ── THE CONTROLLER AND THE SINE BESIDE IT (2026-09-25) ──────────────
        # A drive=inverter record's device block (Stage 2) and the same point
        # on an ideal sinusoid at the same temperatures.  Both were dropped
        # here, so the report's Controller section — which reads the DUTY's
        # record — could never print the coupled answer or the comparison the
        # owner asked for.  Kept whole: a few dozen scalars and small tables;
        # the waveform arrays were already stripped by the route.
        **({"controller": dict(c["controller"])}
           if isinstance(c.get("controller"), dict) and c["controller"]
           else {}),
        **({"sine_comparison": dict(c["sine_comparison"])}
           if isinstance(c.get("sine_comparison"), dict)
           and c["sine_comparison"] else {}),
        "iterations": c.get("iterations"),
        "em_runs": c.get("em_runs"),
        "converged": bool(c.get("converged")) if c else None,
        "runaway": bool(c.get("runaway")) if c else None,
        "coil_temp_c": _f(c.get("coil_temp_c")),
        "magnet_temp_c": _f(c.get("magnet_temp_c")),
        "magnet_temp_max_c": _f(c.get("magnet_temp_max_c")),
        "bearing_temp_c": _f(c.get("bearing_temp_c")),
        "bearing_temp_source": c.get("bearing_temp_source"),
        "P_bearings_W": _f(c.get("P_bearings_W")),
        "P_windage_W": _f(c.get("P_windage_W")),
        "P_mech_extra_W": _f(c.get("P_mech_extra_W")),
        "P_loss_total_incl_mech_W": _f(c.get("P_loss_total_incl_mech_W")),
        "efficiency_shaft": _f(c.get("efficiency_shaft")),
        "tol_K": _f(c.get("tol_K")),
        "residual_coil_K": _f(c.get("residual_coil_K")),
        "residual_magnet_K": _f(c.get("residual_magnet_K")),
        # The bearing seat the loop converges on since 2026-09-13 evening.
        "tol_bearing_K": _f(c.get("tol_bearing_K")),
        "residual_bearing_K": _f(c.get("residual_bearing_K")),
        "warning": c.get("warning"),
        # …AND ITS MACHINE-READABLE HALF (2026-09-16).  The route writes both
        # and only the prose was kept, so every reader of this record had to
        # recognise the loop's state by matching words in a sentence written
        # for an operator ("out of INVERTER", "electromagnetic run 4 refused").
        # One of them got it wrong on the L180 gen 'rated' duty and billed a
        # clamped-but-solved pass as a refused one.
        **({"warning_code": c.get("warning_code")}
           if c.get("warning_code") else {}),
        # `modes` rides along (2026-09-14): the report's ring-mode-vs-carrier
        # warning is judged on `mechanical.modes.tightest`, which the coupled
        # run already computes and which costs a dozen numbers to keep.
        # `critical_speeds` rides along for the same reason and carries the
        # Campbell sweep the diagram is drawn from (A4, same day).
        "mechanical": _pick(mech, ("verdict", "sf_min", "sf_min_part",
                                   "rpm", "refused", "reason", "modes",
                                   "critical_speeds")),
        "elapsed_s": _f(o.get("elapsed_s")),
    }


# ---------------------------------------------------------------------------
# The seam the solve routes call
# ---------------------------------------------------------------------------


def note_thermal(result: Dict[str, Any], params: Dict[str, Any],
                 fp: Optional[str], computed_at: Optional[str] = None) -> bool:
    ctx = active_context()
    if ctx is None:
        return False
    return record(*ctx, "thermal",
                  compact_thermal(result, params, fp, computed_at))


def note_duty_cycle(record_: Dict[str, Any], params: Dict[str, Any],
                    fp: Optional[str], computed_at: Optional[str] = None,
                    *, die: Optional[str] = None, cfg: Optional[str] = None,
                    duty: Optional[str] = None) -> bool:
    """File a duty-cycle answer under the duty it describes.

    ``die`` / ``cfg`` / ``duty`` override the active context, and that is the
    normal case here: the duty-cycle route is TOLD which duty it is solving (the
    request may name one, and a cycle names its own duties), so it does not have
    to hope the editor still has that one open.  With nothing given it falls back
    to :func:`active_context` like every other ``note_*``.
    """
    if die and cfg and duty:
        ctx: Optional[Tuple[str, str, str]] = (str(die), str(cfg), str(duty))
    else:
        ctx = active_context()
        if ctx is not None and duty:
            ctx = (ctx[0], ctx[1], str(duty))
    if ctx is None:
        return False
    return record(*ctx, "duty_cycle",
                  compact_duty_cycle(record_, params, fp, computed_at))


def note_mechanical(kind: str, result: Dict[str, Any], params: Dict[str, Any],
                    fp: Optional[str], computed_at: Optional[str] = None) -> bool:
    if kind not in MECH_KINDS:
        return False
    ctx = active_context()
    if ctx is None:
        return False
    return record(*ctx, kind,
                  compact_mechanical(kind, result, params, fp, computed_at))


def note_coupled(out: Dict[str, Any], *, alt_carrier: bool = False) -> bool:
    """File a coupled answer under the duty the editor has loaded.

    ``alt_carrier`` files it as an ALTERNATIVE CARRIER instead — beside the
    duty's main record, never over it.  That is what a 48 kHz sweep of a duty
    whose own ``sim.fSwitch`` is 24 kHz is: an extra column for the report, not
    a new answer for the machine.
    """
    ctx = active_context()
    if ctx is None:
        return False
    e = compact_coupled(out)
    if alt_carrier:
        return record_alt_carrier(*ctx, "coupled", e)
    return record(*ctx, "coupled", e)


def note_pwm(record_: Dict[str, Any], computed_at: Optional[str] = None,
             *, die: Optional[str] = None, cfg: Optional[str] = None,
             duty: Optional[str] = None) -> bool:
    """File a PWM measurement under the duty it was measured at.

    ``die`` / ``cfg`` / ``duty`` override the active context, and unlike the
    other ``note_*`` seams that is the NORMAL case here: a PWM study is a
    deliberate off-line experiment (solver-direct, hours of FEM, often on a
    machine the editor no longer has open), so the caller names the duty it
    belongs to rather than hoping the context still points at it.
    """
    if die and cfg and duty:
        ctx: Optional[Tuple[str, str, str]] = (str(die), str(cfg), str(duty))
    else:
        ctx = active_context()
        if ctx is not None and duty:
            ctx = (ctx[0], ctx[1], str(duty))
    if ctx is None:
        return False
    return record(*ctx, "pwm", compact_pwm(record_, computed_at))


def compact_controller(out: Dict[str, Any]) -> Dict[str, Any]:
    """What the report and the datasheet need from a controller solve.

    Explicit keys, like every other ``compact_*`` here: the solve's own payload
    carries a full electrical period of every coil's voltage and current, and a
    duty store that grew those arrays would be megabytes per machine.  The
    waveform is dropped; the numbers, the map and the assumptions stay.
    """
    topo = out.get("topology") or {}
    return {
        "computed_at": out.get("computed_at") or _now(),
        "device": out.get("device"),
        "device_row": _pick(out.get("device_row") or {},
                            ("part", "manufacturer", "package",
                             "package_common_name", "v_dss_V", "i_d_100c_A",
                             "t_j_max_c", "r_ds_on_25c_mohm",
                             "r_ds_on_175c_mohm", "r_th_jc_k_w",
                             "r_th_jc_max_k_w", "datasheet_url",
                             "datasheet_revision")),
        "topology": {k: topo.get(k) for k in
                     ("preset", "preset_label", "star_delta", "n_bridges",
                      "n_switches", "n_devices", "mapping", "notes")},
        "bridges": [{k: b.get(k) for k in
                     ("id", "kind", "label", "connection", "coils",
                      "devices_parallel", "n_switches", "modulation",
                      "modulation_index", "p_loss_W", "legs")}
                    for b in (out.get("bridges") or [])],
        # The datasheet limit table travels with the record: a stored answer
        # that says 96 % efficiency and does not say whether the part is
        # inside its ratings is half an answer.
        "feasible": out.get("feasible"),
        "limits_verdict": out.get("limits_verdict"),
        "limits": [dict(r) for r in (out.get("limits") or [])],
        "losses": dict(out.get("losses") or {}),
        "thermal": dict(out.get("thermal") or {}),
        "dc_link": dict(out.get("dc_link") or {}),
        "efficiency": dict(out.get("efficiency") or {}),
        "point": dict(out.get("point") or {}),
        "settings": dict(out.get("settings") or {}),
        "set_split": out.get("set_split"),
        "provenance": dict(out.get("provenance") or {}),
        "warnings": list(out.get("warnings") or []),
        "violations": list(out.get("violations") or []),
        # The full assumption text lives HERE, in the record, and in the docs
        # note — never on the tab (owner 2026-09-22: «не пиши это всё, никто
        # это не читает»).  The tab shows one line and a tooltip.
        "assumptions": list(out.get("model_notes") or []),
        "schematic_svg": out.get("schematic_svg"),
        "elapsed_s": _f(out.get("elapsed_s")),
    }


def note_controller(out: Dict[str, Any], die: Optional[str] = None,
                    cfg: Optional[str] = None,
                    duty: Optional[str] = None) -> bool:
    """File a controller solve under the duty it was solved for.

    Like ``note_pwm``, the caller may NAME the duty: the Controller tab solves
    from a stored record and the editor's context is not necessarily pointing
    at it.
    """
    if die and cfg and duty:
        ctx: Optional[Tuple[str, str, str]] = (str(die), str(cfg), str(duty))
    else:
        ctx = active_context()
    if ctx is None:
        return False
    return record(*ctx, "controller", compact_controller(out))


def note_em_pointer(die: str, cfg: str, duty: str,
                    saved_at: Optional[str] = None,
                    build_sig: Optional[str] = None) -> bool:
    """Record WHERE the electromagnetic column comes from — never a copy of it.

    The duty's own ``result``/``summary`` in ``config/dies/<die>/<cfg>.yaml`` is
    the electromagnetic source of truth; this entry only stamps that it exists
    and when it was saved, so the report's Sources page can date the column the
    same way it dates the thermal and mechanical ones.
    """
    return record(die, cfg, duty, "em",
                  {"source": "duty.result/summary in the configuration yaml",
                   "saved_at": saved_at, "build_sig": build_sig})


def kinds_present(entry: Dict[str, Any]) -> List[str]:
    """Which simulations this duty has an answer for, in report order.

    ``pwm`` sits next to ``em`` because it IS the electromagnetic column, asked
    of a real two-level bridge instead of a sinusoid.  It was left out when the
    kind was added (2026-09-14: "one new section and no change anywhere else"),
    which left a display gap — the store accepted a PWM record, ``KINDS`` listed
    it, and the one view of "what has this duty been solved for"
    (``GET /api/family/duty_results``) never mentioned it.
    """
    return [k for k in ("em", "pwm", "thermal", "duty_cycle", "coupled",
                        "controller", "rotor_stress", "critical_speeds",
                        "modes")
            if isinstance((entry or {}).get(k), dict)]
