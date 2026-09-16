"""FILE A SANDBOXED RUN'S ARTEFACTS UNDER THE DUTY IT BELONGS TO.

WHY THIS EXISTS (2026-09-16).  A long campaign cannot run through the API — the
user is working in the app on another machine — so it runs SOLVER-DIRECT against
a sandboxed ``config/`` (``MOTOR_AI_SIM_CONFIG`` pointed at a temp directory) and
files its answer into the real catalogue in one short critical section at the
end.  That end-of-run save wrote three things:

  * the duty's yaml entry under ``runs.pwm_voltage``  (``family.upsert_duty``),
  * the gzip waveform sidecar                         (``family.record_duty_run``),
  * the ``coupled`` / ``thermal`` / ``em`` rows        (``duty_results``),

and stopped there.  But a NORMAL run — the ▶ button on a live machine — leaves
more than that, because the solve routes themselves file it as they go:

  * ``config/dies/<die>/runs/<cfg>/<duty-stem>/fields/{em,thermal,rotor_stress,
    modes}.npz`` — ``duty_fields.save_active``, called from the tail of
    ``routes.simulation`` (em), ``routes.thermal`` (thermal) and
    ``routes.mechanical`` (rotor_stress, modes);
  * the MECHANICAL rows ``rotor_stress`` / ``modes`` / ``critical_speeds`` —
    ``duty_results.note_mechanical``, from the same tail.

In a sandboxed run every one of those lands INSIDE the sandbox (that is the
whole point of the sandbox) and was then thrown away with the temp directory.
The visible damage, measured on the L155/L180 client report of 2026-09-16: the
§6 table quoted the PWM run (175.2 / 219.0 °C) while Fig. 11 and Fig. 13 were
drawn from the sine solve of two days earlier (153.9 / 168.4 °C, colour bars to
match), the ring-mode table named no carrier, and the Campbell diagram printed
"sweep not stored".  Two different machines on facing pages.

WHAT THIS MODULE DOES.  It takes a FINISHED sandbox and files exactly that
missing set under (die, configuration, duty) in the real catalogue:

    refile(sandbox, die, cfg, duty)
        → the four field npz + the three mechanical rows (and, on request, the
          ``coupled`` / ``thermal`` / ``em`` / ``pwm`` rows too)

WHAT IT DELIBERATELY DOES NOT DO.  It never writes the configuration yaml, the
duty's ``runs`` pointer or the gzip sidecar: those are ``routes.family``'s, the
end-of-run save already wrote them, and a refile is a repair of what was lost,
not a second save.  It never touches another duty — every write is addressed by
(die, cfg, duty) and the results merge re-reads the store, edits one node and
replaces the file atomically.  And it solves nothing: a refile is minutes-old
arithmetic over files that already exist.

PROVENANCE IS PRESERVED, NOT RESTAMPED.  A field npz that the sandbox's own
``duty_fields`` already wrote is copied VERBATIM — it is the same code's output,
carrying the ``computed_at`` of the solve that made it — and only the file's
mtime is new (which is how a refile can be seen to have happened).  A kind the
sandbox never filed is re-packed from that sandbox's last-run store through the
same ``duty_fields`` packers, and is then marked ``repacked_from`` in its meta
so nobody has to guess later.

THE MASS IS STILL ASSERTED.  ``assert_mass`` repeats the end-of-run check
against the configuration on disk — the duty's own ``mass_total_kg``, the shaft
counted, no part marked "reference" in any stored run — so a refile cannot
quietly bless a machine that was saved mis-weighed.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: The maps a duty owns, in report order — ``duty_fields.KINDS`` verbatim.
FIELD_KINDS: Tuple[str, ...] = ("em", "thermal", "rotor_stress", "modes")

#: The rows the solve routes file and a sandboxed save never carried out.
MECH_RESULT_KINDS: Tuple[str, ...] = ("rotor_stress", "modes",
                                      "critical_speeds")

#: …and the rows the end-of-run save already merges itself.  Listed so a
#: ``--refile-from`` of a sandbox whose save never ran (or ran against an older
#: runner) can bring the whole set over in one pass.
RUN_RESULT_KINDS: Tuple[str, ...] = ("coupled", "thermal", "em", "pwm")

ALL_RESULT_KINDS: Tuple[str, ...] = RUN_RESULT_KINDS + MECH_RESULT_KINDS

#: Where each field kind is re-packed FROM when the sandbox filed no npz for it.
#: ``routes.thermal`` keys its last store by ``"field"``; ``routes.mechanical``
#: keys its own by the mechanical kind; the transient's field snapshot is a
#: single ``{"key": …, "entry": …}`` pair.
_REPACK_SOURCES: Dict[str, Tuple[str, Optional[str]]] = {
    "thermal": (".last_thermal.pkl", "field"),
    "rotor_stress": (".last_mechanical.pkl", "rotor_stress"),
    "modes": (".last_mechanical.pkl", "modes"),
    "em": (".last_transient_field.pkl", None),
}

MASS_TOL_KG = 0.002


class RefileError(Exception):
    """The refile cannot be made honestly — nothing was written."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _stem(duty: str) -> str:
    """The duty's folder name — ``duty_fields``', so the two agree by
    construction rather than by two copies of one hashing rule."""
    from motor_ai_sim import duty_fields as df
    return df._duty_stem(str(duty))


def sandbox_fields_dir(sandbox: Any, die: str, cfg: str, duty: str) -> Path:
    """Where the SANDBOX's own solve filed this duty's maps."""
    return (Path(str(sandbox)) / "dies" / str(die) / "runs" / str(cfg)
            / _stem(duty) / "fields")


def target_fields_dir(die: str, cfg: str, duty: str,
                      dies_dir: Optional[Any] = None) -> Path:
    """Where they belong in the real catalogue."""
    from motor_ai_sim import duty_fields as df
    return df.fields_dir(die, cfg, duty, root=dies_dir)


def sandbox_store(sandbox: Any) -> Path:
    return Path(str(sandbox)) / ".duty_results.json"


def sandbox_context(sandbox: Any) -> Optional[Tuple[str, str, str]]:
    """``(die, cfg, duty)`` the sandbox's LAST solve was filed under.

    The last-run pickles (``.last_thermal.pkl`` and friends) hold ONE answer per
    machine, so they describe this duty and no other — which is why a re-pack
    from them is refused for any other duty of the same sandbox.
    """
    try:
        d = json.loads((Path(str(sandbox)) / ".family_context.json")
                       .read_text(encoding="utf-8"))
        die, cfg, duty = d.get("die"), d.get("config"), d.get("duty")
        if die and cfg and duty:
            return str(die), str(cfg), str(duty)
    except Exception:                                       # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# The fields
# ---------------------------------------------------------------------------


def _atomic_install(src: Path, dst: Path) -> int:
    """Copy ``src`` over ``dst`` atomically, with a FRESH mtime.

    ``shutil.copy2`` is deliberately not used: the refile's own mtime is the one
    externally visible sign that a duty's maps were repaired, and preserving the
    sandbox's would erase it.  The bytes are the sandbox's, untouched.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
    try:
        shutil.copyfile(str(src), str(tmp))
        os.replace(str(tmp), str(dst))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return int(dst.stat().st_size)


def _repack_payload(sandbox: Any, kind: str) -> Optional[Dict[str, Any]]:
    """The store entry ``duty_fields.pack_<kind>`` wants, out of the sandbox."""
    src = _REPACK_SOURCES.get(kind)
    if src is None:
        return None
    fname, key = src
    p = Path(str(sandbox)) / fname
    if not p.is_file():
        return None
    try:
        with open(p, "rb") as fh:
            blob = pickle.load(fh)
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_refile: %s is unreadable (%s)", p, exc)
        return None
    if kind == "em":
        entry = blob.get("entry") if isinstance(blob, dict) else None
        return entry if isinstance(entry, dict) else None
    node = blob.get(key) if isinstance(blob, dict) else None
    if not isinstance(node, dict):
        return None
    res = node.get("result")
    if not isinstance(res, dict):
        return None
    out = dict(res)
    # ``pack_thermal`` / ``pack_rotor_stress`` / ``pack_modes`` read
    # ``computed_at`` off the result they are handed; the last store keeps it
    # one level up, beside ``params``.
    out.setdefault("computed_at", node.get("computed_at"))
    return out


def refile_fields(sandbox: Any, die: str, cfg: str, duty: str, *,
                  dies_dir: Optional[Any] = None,
                  kinds: Sequence[str] = FIELD_KINDS,
                  allow_repack: bool = True) -> Dict[str, Any]:
    """Install this duty's four maps into the real catalogue.

    Returns ``{kind: {"how", "bytes", "path"}}`` for what landed, plus a
    ``"missing"`` list for what the sandbox never had.  Never raises: a lost map
    is a figure the report will say it has not got, and that is a far better
    outcome than a refile that aborts half-way.
    """
    from motor_ai_sim import duty_fields as df
    src_dir = sandbox_fields_dir(sandbox, die, cfg, duty)
    dst_dir = target_fields_dir(die, cfg, duty, dies_dir)
    ctx = sandbox_context(sandbox)
    same_duty = ctx is not None and ctx[2] == str(duty) and ctx[1] == str(cfg)
    out: Dict[str, Any] = {"dir": str(dst_dir), "written": {}, "missing": []}
    for kind in kinds:
        src = src_dir / f"{kind}.npz"
        dst = dst_dir / f"{kind}.npz"
        if src.is_file():
            try:
                n = _atomic_install(src, dst)
                out["written"][kind] = {"how": "copied", "bytes": n,
                                        "path": str(dst), "from": str(src)}
                continue
            except Exception as exc:                        # noqa: BLE001
                log.warning("duty_refile: could not install %s (%s)", src, exc)
        if not (allow_repack and same_duty):
            out["missing"].append(kind)
            continue
        payload = _repack_payload(sandbox, kind)
        if not payload:
            out["missing"].append(kind)
            continue
        written = df.save(die, cfg, duty, kind, payload, root=dies_dir,
                          extra_meta={"repacked_from": str(sandbox),
                                      "repacked_at": _now()})
        if written:
            out["written"][kind] = {"how": "repacked",
                                    "bytes": int(Path(written).stat().st_size),
                                    "path": written,
                                    "from": str(Path(str(sandbox))
                                                / _REPACK_SOURCES[kind][0])}
        else:
            out["missing"].append(kind)
    return out


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_refile: %s is unreadable (%s)", p, exc)
        return {}
    return d if isinstance(d, dict) else {}


def _write_store(p: Path, doc: Dict[str, Any]) -> None:
    """``duty_results._write_all``'s semantics, against an EXPLICIT path.

    Explicit rather than the module's own ``store_path()``: this runs inside a
    process whose configuration directory is a sandbox, and re-pointing a global
    for the length of a write is a race with anything else in the process.  Same
    stamps, same ``ensure_ascii=False``, same atomic ``tmp`` + ``os.replace``.
    """
    from motor_ai_sim import duty_results as dr
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    doc["version"] = dr.VERSION
    doc["updated_at"] = _now()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, default=str)
    os.replace(str(tmp), str(p))


def refile_results(sandbox: Any, die: str, cfg: str, duty: str, *,
                   store: Any, kinds: Sequence[str] = MECH_RESULT_KINDS,
                   provenance: Optional[Dict[str, Any]] = None
                   ) -> Dict[str, Any]:
    """Merge the sandbox's compact rows for ONE duty into the real store.

    The rows are copied as the sandbox wrote them — ``note_mechanical`` already
    put them through ``duty_results.compact_mechanical``, so the compaction, the
    ``computed_at`` and the ``geometry_fingerprint`` are the solve's own and not
    this module's reconstruction of them.  ``modes`` therefore arrives with its
    ``f_switch_hz`` and ``critical_speeds`` with its ``campbell`` sweep, which is
    the entire reason the mechanical half had to be refiled at all.

    Everything outside ``results[die][cfg][duty]`` is left exactly as it was.

    ``provenance`` adds keys to each row it writes — ``{"refiled_from": …}`` for
    a repair made long after the run.  Nothing is stamped by default: the row a
    run's OWN end-of-save carries over is not a repair, and a key the report has
    never seen is not worth inventing twice.
    """
    src = _read_json(sandbox_store(sandbox))
    mine = ((((src.get("results") or {}).get(str(die)) or {})
             .get(str(cfg)) or {}).get(str(duty)) or {})
    have = [k for k in kinds if isinstance(mine.get(k), dict)]
    absent = [k for k in kinds if k not in have]
    if not have:
        return {"merged": [], "absent": absent, "store": str(store)}
    p = Path(str(store))
    doc = _read_json(p)
    if not isinstance(doc.get("results"), dict):
        doc = {"version": 1, "results": {}}
    node = (doc["results"].setdefault(str(die), {})
            .setdefault(str(cfg), {}).setdefault(str(duty), {}))
    for k in have:
        row = dict(mine[k])
        row["kind"] = k
        row.setdefault("recorded_at", _now())
        if provenance:
            row.update({str(a): b for a, b in provenance.items()})
        node[k] = row
    _write_store(p, doc)
    return {"merged": have, "absent": absent, "store": str(p)}


# ---------------------------------------------------------------------------
# The mass, one last time
# ---------------------------------------------------------------------------


def _mass_faults(summary: Any, where: str, expected: float) -> List[str]:
    """``coupled_pwm_bg._mass_faults`` verbatim — the campaign's own check.

    A refile writes maps and rows, not masses, so this can only ever FAIL on a
    machine that was already saved wrong; catching that here is how a repair
    stays a repair.
    """
    out: List[str] = []
    if not isinstance(summary, dict) or not summary:
        return out
    m = summary.get("mass_total_kg")
    if m is None:
        if summary.get("mass_components") or summary.get("mass_active_kg"):
            out.append("%s: a mass block with no mass_total_kg" % where)
    elif abs(float(m) - expected) > MASS_TOL_KG:
        out.append("%s: mass_total_kg = %s kg, not %s kg" % (where, m, expected))
    st = summary.get("part_states") or {}
    if str(st.get("shaft") or "included").lower() != "included":
        out.append("%s: part_states says shaft = %r" % (where, st.get("shaft")))
    for row in (summary.get("mass_components") or []):
        if isinstance(row, dict) and row.get("state") and (
                "shaft" in str(row.get("name") or "").lower()):
            out.append("%s: the shaft row carries state=%r (%r)"
                       % (where, row.get("state"), row.get("name")))
    return out


def assert_mass(die: str, cfg: str, duty: str,
                dies_dir: Optional[Any] = None) -> float:
    """The machine on the scales, re-read from the configuration on disk.

    Returns the duty's ``mass_total_kg``; raises :class:`RefileError` when the
    configuration says the shaft is not counted, when the duty is missing, or
    when any stored run of it disagrees with the duty's own number.
    """
    import yaml
    from motor_ai_sim import duty_fields as df
    base = Path(str(dies_dir)) if dies_dir else df._dies_dir()
    p = base / str(die) / (str(cfg) + ".yaml")
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:                                # noqa: BLE001
        raise RefileError("cannot read %s (%s)" % (p, exc))
    entry = next((x for x in (doc.get("duties") or [])
                  if isinstance(x, dict) and x.get("name") == str(duty)), None)
    if entry is None:
        raise RefileError("duty %r is not in %s" % (duty, p))
    expected = (entry.get("summary") or {}).get("mass_total_kg")
    if expected is None:
        raise RefileError("%r carries no mass_total_kg — there is nothing to "
                          "weigh this refile against" % (duty,))
    expected = float(expected)
    faults: List[str] = []
    shaft = str((doc.get("parts") or {}).get("shaft") or "included").lower()
    if shaft != "included":
        faults.append("the configuration's parts: block says shaft = %r" % shaft)
    faults += _mass_faults(entry.get("summary"), "the duty's stored summary",
                           expected)
    for k, run in sorted((entry.get("runs") or {}).items()):
        faults += _mass_faults((run or {}).get("summary"),
                               "the stored %r run's summary" % k, expected)
    if faults:
        raise RefileError("%r is mis-weighed on disk: %s"
                          % (duty, "; ".join(faults)))
    return expected


# ---------------------------------------------------------------------------
# The whole thing
# ---------------------------------------------------------------------------


def refile(sandbox: Any, die: str, cfg: str, duty: str, *,
           config_dir: Optional[Any] = None,
           dies_dir: Optional[Any] = None,
           field_kinds: Sequence[str] = FIELD_KINDS,
           result_kinds: Sequence[str] = MECH_RESULT_KINDS,
           check_mass: bool = True,
           allow_repack: bool = True,
           provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """File one finished sandbox's fields and rows under one duty.

    ``config_dir`` is the real configuration directory (``<root>/config``);
    ``dies_dir`` defaults to ``<config_dir>/dies``.  With neither given the
    caller's own workspace answers, which is what a test wants and what a
    process that is NOT sandboxed already has.
    """
    if config_dir is not None and dies_dir is None:
        dies_dir = Path(str(config_dir)) / "dies"
    sb = Path(str(sandbox))
    if not sb.is_dir():
        raise RefileError("no such sandbox: %s" % sb)
    mass = assert_mass(die, cfg, duty, dies_dir) if check_mass else None
    fields = refile_fields(sandbox, die, cfg, duty, dies_dir=dies_dir,
                           kinds=field_kinds, allow_repack=allow_repack)
    if config_dir is not None:
        store = Path(str(config_dir)) / ".duty_results.json"
    else:
        from motor_ai_sim import duty_results as dr
        store = dr.store_path()
    rows = refile_results(sandbox, die, cfg, duty, store=store,
                          kinds=result_kinds, provenance=provenance)
    out = {"die": str(die), "config": str(cfg), "duty": str(duty),
           "sandbox": str(sb), "mass_total_kg": mass,
           "fields": fields, "results": rows}
    log.info("duty_refile: %s/%s/%s <- %s: fields %s%s; rows %s",
             die, cfg, duty, sb,
             ", ".join("%s (%s)" % (k, v["how"])
                       for k, v in sorted(fields["written"].items())) or "none",
             (" — missing " + ", ".join(fields["missing"])
              if fields["missing"] else ""),
             ", ".join(rows["merged"]) or "none")
    return out


def artefact_set(die: str, cfg: str, duty: str, *,
                 config_dir: Optional[Any] = None,
                 dies_dir: Optional[Any] = None) -> Dict[str, List[str]]:
    """WHAT a duty has on disk, as the two lists a refile is judged by.

    ``{"fields": [kind, …], "results": [kind, …]}`` — used by the test that
    proves a refiled duty carries the same artefacts as one saved the normal
    way, and handy from a shell when checking a repair by hand.
    """
    if config_dir is not None and dies_dir is None:
        dies_dir = Path(str(config_dir)) / "dies"
    fdir = target_fields_dir(die, cfg, duty, dies_dir)
    fields = [k for k in FIELD_KINDS if (fdir / f"{k}.npz").is_file()]
    if config_dir is not None:
        store = Path(str(config_dir)) / ".duty_results.json"
    else:
        from motor_ai_sim import duty_results as dr
        store = dr.store_path()
    node = ((((_read_json(store).get("results") or {}).get(str(die)) or {})
             .get(str(cfg)) or {}).get(str(duty)) or {})
    results = [k for k in ALL_RESULT_KINDS if isinstance(node.get(k), dict)]
    return {"fields": fields, "results": results}
