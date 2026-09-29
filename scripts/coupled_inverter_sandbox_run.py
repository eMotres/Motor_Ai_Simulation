# -*- coding: utf-8 -*-
"""The PWM coupled campaign, SOLVER-DIRECT and OUT OF THE USER'S WAY.

2026-09-15, the user: *"PWM поставь в фоне, чтобы не мешал работать"*.  He is in
the app on the L13 robot joint, through the API on port 8001, and the Ø200 L155
campaign has to keep running beside him without ever touching that machine.

So this file does NOT speak to the API.  It imports the route functions and
calls them in this process — `routes.coupled.run` is the same function the
endpoint calls, so the physics is bit-for-bit the endpoint's — and it runs them
against a SANDBOXED config directory.  Nothing under the real ``config/`` is
read-write until the very end, and then only the two files this duty owns.

THE SANDBOX, in one lever
-------------------------
Every store this project writes derives its path from
``motor_ai_sim.config.DEFAULT_CONFIG_PATH``'s parent — the transient and its
field snapshot, the thermal last-result and loss maps, the mechanical last, the
coupled last, the per-duty results, the family context, the mesh cache, the duty
fields.  And ``DEFAULT_CONFIG_PATH`` follows the ``MOTOR_AI_SIM_CONFIG``
environment variable, which exists precisely because a test suite once
overwrote the user's live machine (config.py, 2026-08-06).

So: a temp directory with its own ``motor_config.yaml``, ``MOTOR_AI_SIM_CONFIG``
pointed at it BEFORE the first import, and every one of those stores lands
there.  The explicit per-store redirects the smoke run used are kept on top as
belt-and-braces, and a startup guard ASSERTS that each resolved path is inside
the sandbox — a redirect that silently stopped working is exactly the failure
this whole file exists to prevent.

What is deliberately NOT done: ``simulation._BACKGROUND_RUN`` is left unset.  A
background solve throws its last-frame field away, and that field IS the loss
map the conduction solve is handed (`coupled._pwm_loss_map`) — the smoke run of
2026-09-14 established this.  The run therefore has to look like a live-machine
run; the sandbox, not a flag, is what keeps it out of the user's config.

THE MACHINE
-----------
Built into the sandbox config from the catalog yaml the way ▶ builds it into the
live one: die geometry + the configuration's overrides through the real
``PUT /api/geometry`` handler (so the derived fields are recomputed by the code
that owns them), then winding, materials, PART STATES and the duty's operating
point.  ``parts: {shaft: included}`` is written explicitly — it is the L155's
own accounting, and writing it rather than omitting it keeps ``_build_sig``
identical to the stored one, so no earlier result is falsely flagged "computed
on an older build".

THE SAVE
--------
At the end, and only then, a short critical section re-points
``routes.family._DIES_DIR`` and the duty-results store at the REAL config
directory, re-reads the configuration yaml from disk, and files the run through
the same two functions the HTTP save route uses — ``upsert_duty`` (the yaml
entry, non-primary under ``runs.pwm_voltage``) and ``record_duty_run`` (the
gzip sidecar) — then merges this duty's ``coupled`` / ``thermal`` / ``em``
records, with their ``drive: pwm`` and ``reference_sine`` blocks, into the real
``.duty_results.json`` — AND, since 2026-09-16, the three MECHANICAL records
(``rotor_stress``, ``modes`` with its carrier, ``critical_speeds`` with its
Campbell sweep) plus the four FIELD sidecars
(``runs/<cfg>/<duty-stem>/fields/{em,thermal,rotor_stress,modes}.npz``) that the
solve routes filed inside the sandbox.  Other duties are never touched, every
write is atomic, and the mass is asserted afterwards: 27.559 kg, the shaft
counted.

Those last two were missing until 2026-09-16, and the damage was visible in a
client report: the §6 table came from the PWM run while Fig. 11/13 and the
Campbell came from the sine solve of two days earlier.  A run whose save
predates the fix is repaired WITHOUT RE-SOLVING by ``--refile-from <sandbox>``;
``motor_ai_sim.duty_refile`` is the seam both paths share.

THE MACHINE IS AN ARGUMENT
--------------------------
``--die`` / ``--config`` default to the L155 motor campaign this file was
written for, and everything that used to be a constant now follows them: the
catalog yaml that is read, the run id, the save target, the DC LINK (the
configuration's own ``battery.v_nom`` — 750.4 V on the L155, 799.2 V on the
L180 generator), the mass asserted after the save (each duty's own
``mass_total_kg``) and the results file.  With the defaults the run is the one
it has always been.

usage:
    python coupled_pwm_bg.py                      # peak then rated, 3 passes each
    python coupled_pwm_bg.py --duty "rated 1x9 mm" [--max-iter 3]
    python coupled_pwm_bg.py --die "CIANO10 200 opt" --config "L180 gen" \
        --duties "rated 0.5x9 mm" "peak 0.5x9 mm" --max-iter 4 --wait-lock 28800
    python coupled_pwm_bg.py --config "L180 gen" --duties … --dry-run --no-save
    python coupled_pwm_bg.py --selftest --steps 24 --max-iter 1 --no-save
    python coupled_pwm_bg.py --refile-from "C:\\…\\Temp\\pwm_bg_xxxx" \
        --die "CIANO10 200 opt" --config "L180 gen" --duty "rated 0.5x9 mm"

options: --die --config --duty --duties --max-iter --steps --carrier --v-dc
         --dry-run/--dry --no-save --selftest --wait-lock [s] --refile-from
"""

import ctypes
import glob
import io
import json
import math
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
# PORTABLE (2026-09-29): repo root and the READ-ONLY config to seed the
# sandbox from are environment-selectable, so this runs on the Linux server.
ROOT = os.environ.get("INV_BG_ROOT") or os.path.dirname(HERE)
REAL_CONFIG = os.environ.get("INV_BG_REAL_CONFIG") or os.path.join(ROOT, "config")
SRC = os.environ.get("INV_BG_SRC") or os.path.join(ROOT, "src")
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                         # noqa: BLE001
    pass

LOG = open(os.path.join(HERE, "coupled_l180_cmp.log"), "a", encoding="utf-8")
RESULTS_PATH = os.path.join(HERE, "coupled_l180_cmp.json")
RESULTS = []


def log(*a):
    s = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


def dump():
    json.dump(RESULTS, io.open(RESULTS_PATH, "w", encoding="utf-8"),
              indent=1, default=str)


# ── BELOW_NORMAL, and PROVEN.  The user is working on the same CPU; a silent
# try/except once hid this failure and a night of runs came out at Normal
# priority (pwm_b5.py) ───────────────────────────────────────────────────────
if os.name != "nt":
    # POSIX: niceness >= 10 is PROVEN, not assumed (run under nice -n 19).
    if os.nice(0) < 10:
        raise SystemExit("REFUSING TO RUN: niceness %d < 10 - start under "
                         "nice -n 19 ionice -c3" % os.nice(0))
else:
    try:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.GetPriorityClass.argtypes = [ctypes.c_void_p]
        k32.GetPriorityClass.restype = ctypes.c_uint
        _h = k32.GetCurrentProcess()
        k32.SetPriorityClass(_h, 0x00004000)          # BELOW_NORMAL_PRIORITY_CLASS
        if k32.GetPriorityClass(_h) != 0x00004000:
            raise SystemExit("REFUSING TO RUN: priority is not BELOW_NORMAL")
    except SystemExit:
        raise
    except Exception as _pe:                                  # noqa: BLE001
        raise SystemExit("REFUSING TO RUN: could not set BELOW_NORMAL (%r)" % _pe)

_LOCK = os.path.join(HERE, ".inverter_bg.lock")
#: Seconds to WAIT for the lock instead of refusing outright.  A queued run — the
#: fixed campaign launched while the defective one is still finishing — must sit
#: on the doorstep rather than die on it, and must never run beside its
#: predecessor: one process, no parallelism, because the user is on this CPU.
_WAIT_LOCK = 0.0
if "--wait-lock" in sys.argv[1:]:
    _i = sys.argv.index("--wait-lock")
    _WAIT_LOCK = float(sys.argv[_i + 1]) if _i + 1 < len(sys.argv) else 6 * 3600
    del sys.argv[_i:_i + 2]


def _take_lock():
    _t0 = time.time()
    _said = False
    while True:
        try:
            fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            import atexit
            atexit.register(lambda: os.path.exists(_LOCK) and os.remove(_LOCK))
            return
        except FileExistsError:
            held = ""
            try:
                held = io.open(_LOCK, encoding="utf-8").read().strip()
            except Exception:                             # noqa: BLE001
                pass
            if time.time() - _t0 >= _WAIT_LOCK:
                raise SystemExit(
                    "REFUSING TO RUN: another run (pid %s) holds %s%s"
                    % (held or "?", _LOCK,
                       "" if _WAIT_LOCK <= 0 else
                       " — waited %.0f s" % (time.time() - _t0)))
            if not _said:
                log("waiting for pid %s to release %s (up to %.0f min) — one "
                    "process at a time: the user is on this CPU"
                    % (held or "?", _LOCK, _WAIT_LOCK / 60.0))
                _said = True
            time.sleep(20)


#: A DRY pass takes NO lock.  It solves nothing, writes nothing outside its own
#: temp directory and only reads the catalog — so making it queue behind a
#: four-hour solve would mean the cheapest check in this file could only be run
#: when it is least needed.  (Read early, beside --wait-lock: the real option
#: parsing happens further down, after the lock is already held.)
_DRY_EARLY = bool({"--dry", "--dry-run"} & set(sys.argv[1:]))
#: …and neither does a REFILE.  ``--refile-from <sandbox>`` solves nothing: it
#: reads a run that has already finished and files the artefacts its sandboxed
#: save could not (see the refile branch below).  It runs while another campaign
#: is solving — that is the normal case, since the thing being repaired is the
#: previous run — so it must not queue behind the lock, and it must not touch
#: the running process's sandbox.
_REFILE_EARLY = "--refile-from" in sys.argv[1:]
if _DRY_EARLY or _REFILE_EARLY:
    log("%s: no lock taken — this pass solves nothing"
        % ("DRY" if _DRY_EARLY else "REFILE"))
else:
    _take_lock()


# ── the campaign ────────────────────────────────────────────────────────────
#: THE MACHINE THIS RUN IS ABOUT.  The defaults are the L155 motor campaign this
#: file was written for; ``--die`` / ``--config`` move the whole runner to
#: another configuration of the catalog and EVERYTHING below follows them — the
#: yaml that is read, the run id, the save target, the DC link, the mass that is
#: asserted after the save and the results file.  With the defaults the run is
#: byte-for-byte the one this file has always made.
DIE_DEFAULT = "CIANO10 200 opt"
CFG_DEFAULT = "L155 motor"
DIE = DIE_DEFAULT
CFG = CFG_DEFAULT
#: The DC link.  Resolved from the CONFIGURATION's own ``battery.v_nom`` once the
#: catalog can be read (the battery is a property of the configuration, not of
#: the die: L155 motor carries 750.4 V, L180 gen carries 799.2 V), and this
#: value is only the fallback for a configuration that has no pack at all.
V_DC_FALLBACK = 750.4
V_DC = V_DC_FALLBACK
CARRIER = 24000.0
#: What the machine weighs with the whole of it on the scales.  NOT a constant
#: any more: read per duty from that duty's OWN stored summary
#: (``mass_total_kg``) — 27.559 kg for the L155 (26.676 active + 0.883 shaft),
#: 32.144 kg for the L180 — so the assertion after the save is the machine's own
#: number and not a number this file remembers about one campaign.
EXPECTED_MASS_TOTAL_KG = None
MASS_TOL_KG = 0.002
#: The parts that can carry an accounting state (`part_states.STATEFUL_PARTS`).
STATEFUL_PARTS = ("stator_core", "rotor_core", "magnet", "slot", "shaft",
                  "sleeve")
#: The Ø200 duties' measured boundary, read when a duty has no stored record.
#: Both the L155 motor and the L180 generator are characterised on it (water at
#: 60 °C, 10 l/min, bore air at 30 m/s) — it is the die's cooling, not one
#: configuration's — and it is only ever a fallback: `duty_thermal_settings`
#: reads the duty's own record first and both L180 duties have one.
CAMPAIGN_COOLING_FALLBACK = {"coolMode": "liquid", "ambientT": "30",
                             "fluid": "water", "tIn": "60", "flowLpm": "10",
                             "boreMode": "air", "boreAirSpeed": "30",
                             "shaftExtMm": "0"}
#: The L13 joint's, for the cheap isolation self-test only.
L13 = {"die": "CIANO28 85 20SW1200", "config": "L13",
       "duty": "peak 200\u0421 wire 120C NdFeB"}
L13_COOLING_FALLBACK = {"coolMode": "robotics", "ambientT": "40",
                        "emissivity": "0.9", "boreMode": "still",
                        "endFaces": "still", "endFaceSides": "2",
                        "mountG": "2", "mountT": "40", "shaftExtMm": "0"}

HEAVY = ("frames", "field", "demag_field", "demag_coef_per_tri")


class MassAbort(Exception):
    """The machine that was saved is not the one we characterised."""


class SaveContractChanged(Exception):
    """``routes.family``'s write routes no longer take what this file sends."""


def write_auth(fn):
    """The keyword and the object ``fn`` wants for its authorisation, READ OFF
    the function instead of remembered.

    The catalog's write routes take their caller identity as a FastAPI
    ``Depends`` parameter, and an in-process caller — which is what this whole
    file is — has to supply it by keyword.  So the parameter's NAME and the
    object's SHAPE are part of this file's contract with ``routes.family``, and
    both moved under the workspace migration (2026-09-15 21:33): ``_admin``,
    a ``{email, role}`` mapping, became ``_w``, a
    ``{user, is_admin, authorization}`` one.  The L155 campaign that was already
    running kept the old function in memory and was unaffected; a run launched
    afterwards would have solved for four hours and then died on a TypeError at
    the last step.  Hence: ask the function.
    """
    import inspect
    ps = inspect.signature(fn).parameters
    if "_w" in ps:
        return {"_w": {"user": {"uid": "claude-agent", "email": "claude-agent",
                                "tier": "admin"},
                       "is_admin": True, "authorization": None}}
    if "_admin" in ps:
        return {"_admin": {"email": "claude-agent", "role": "admin"}}
    raise SaveContractChanged(
        "routes.family.%s takes neither `_w` nor `_admin` (%s) — the save "
        "contract changed again and this runner must be taught the new one "
        "BEFORE it solves anything" % (fn.__name__, ", ".join(ps)))


# ── the command line ────────────────────────────────────────────────────────
_argv = list(sys.argv[1:])


def _opt(name, default=None, flag=False):
    if name not in _argv:
        return (False if flag else default)
    i = _argv.index(name)
    _argv.pop(i)
    if flag:
        return True
    return _argv.pop(i) if i < len(_argv) else default


def _opt_list(name):
    """Every value after ``name`` up to the next option — so both spellings of
    the campaign's sequence work: ``--duties "a;b"`` (the original) and
    ``--duties "a" "b"`` (what a shell command naturally writes)."""
    if name not in _argv:
        return None
    i = _argv.index(name)
    _argv.pop(i)
    out = []
    while i < len(_argv) and not str(_argv[i]).startswith("--"):
        out.append(_argv.pop(i))
    return out or None


#: THE CONTROLLER (Stage 2).  Defaults are the L155 motor stack of the Stage 1
#: table (docs/CONTROLLER_MODULE_2026-09-22.md §6): one three-phase bridge of
#: IMCQ120R004M2H, three devices per switch, 0.5 us dead time, the micro-channel
#: coldplate at 65 degC and 8 L/min.
DEVICE = _opt("--device", "IMCQ120R004M2H")
VGS_OFF = float(_opt("--vgs-off", 0.0))
RTH_JC = _opt("--rth-jc")
RTIM = _opt("--rtim")
PLATE_COOLANT = _opt("--plate-coolant", "water")
TOPOLOGY = _opt("--topology", "one_3ph")
N_PARALLEL = int(_opt("--n-parallel", 3))
DEAD_TIME_US = float(_opt("--dead-time-us", 0.5))
PLATE_T_IN_C = float(_opt("--plate-t-in", 65.0))
PLATE_FLOW_LPM = float(_opt("--plate-flow", 8.0))
#: The two EXTRA transients a coupled run makes by default — the 20 degC
#: catalogue pass and the modal/Campbell sweep.  At 400 steps per period they
#: are ~75 minutes that say nothing about the controller, and this duty already
#: carries both from its earlier campaigns.
NO_MECH = _opt("--no-mech", flag=True)
NO_COLD = _opt("--no-cold", flag=True)
SELFTEST = _opt("--selftest", flag=True)
#: REFILE MODE: the sandbox of a FINISHED run, whose field sidecars and
#: mechanical rows are to be filed under the duty it solved.  No solve, no lock,
#: no sandbox of its own — see the branch below.
REFILE_FROM = _opt("--refile-from")
#: Load the machine into the sandbox, prove the plumbing, solve NOTHING.  The
#: cheapest way to find a wrong attribute name — seconds instead of an hour.
#: ``--dry-run`` is the same switch under the name a caller is likely to type.
DRY = _opt("--dry", flag=True) or _opt("--dry-run", flag=True)
_opt("--no-save", flag=True)
#: ALWAYS.  A Stage-2 record is a new kind of answer about this duty and the
#: owner decides whether it replaces the stored one; an agent's background run
#: files nothing into the real catalogue.
NO_SAVE = True
ONE_DUTY = _opt("--duty")
#: Several duties, IN ORDER — the campaign's sequence made explicit.  After the
#: 10:03 failure the rated duty is the one that still owes an answer, so it goes
#: first.
DUTIES = _opt_list("--duties")
#: THE CONFIGURATION.  Everything this file does is scoped to these two names.
DIE = _opt("--die", DIE_DEFAULT)
CFG = _opt("--config", CFG_DEFAULT)
MAX_ITER = int(_opt("--max-iter", 1 if SELFTEST else 3))
STEPS = _opt("--steps")
STEPS = int(STEPS) if STEPS else None
#: The carrier, when it is not the duty's own ``sim.fSwitch`` (both the L155 and
#: the L180 duties state 24 kHz, so this is the same number either way).
CARRIER = float(_opt("--carrier", CARRIER))
#: …and the link, when the configuration's own pack is not what is wanted.
_V_DC_ARG = _opt("--v-dc")
V_DC_SOURCE = "the command line (--v-dc)" if _V_DC_ARG else ""
if _V_DC_ARG:
    V_DC = float(_V_DC_ARG)

# ── THE ARGUMENTS MUST HAVE SURVIVED THE SHELL ──────────────────────────────
# 2026-09-16 00:34.  A PowerShell `Start-Process -ArgumentList @(...)` joins the
# array with spaces and does NOT quote the elements that contain any, so
#     --die "CIANO10 200 opt" --config "L180 gen" --duties "rated 0.5x9 mm" …
# reached this process as fourteen bare words: DIE became "CIANO10", CFG became
# "L180", and the campaign went looking for a configuration nobody has ever
# built.  It failed six times in three seconds and cost nothing — but the SAME
# mangling with a die name that happens to exist would have solved for hours on
# the wrong machine, which is the one thing this file exists to prevent.
#
# Every option here is consumed with its value, so after parsing NOTHING may be
# left over.  A leftover word means the quoting was lost on the way in.
if _argv:
    raise SystemExit(
        "REFUSING TO RUN: %d unrecognised argument(s) left after parsing: %s.\n"
        "That is almost always LOST QUOTING — a name with spaces ('CIANO10 200 "
        "opt', 'L180 gen', 'rated 0.5x9 mm') that arrived as separate words.  "
        "This run would have been a different machine from the one intended.\n"
        "From PowerShell, pass ONE string and let the child do its own "
        "splitting:\n"
        "  Start-Process python -ArgumentList '-u \"<path>\\coupled_pwm_bg.py\" "
        "--die \"CIANO10 200 opt\" --config \"L180 gen\" --duties \"rated 0.5x9 "
        "mm\" \"peak 0.5x9 mm\" --max-iter 4'"
        % (len(_argv), ", ".join(repr(a) for a in _argv)))

#: Where this campaign's results land.  The default campaign keeps the file it
#: has always written; another configuration gets its own, so a second campaign
#: can never overwrite the first one's answers.
_SLUG = "".join(ch if ch.isalnum() else "_" for ch in CFG).strip("_").lower()
if (DIE, CFG) != (DIE_DEFAULT, CFG_DEFAULT):
    RESULTS_PATH = os.path.join(HERE, "coupled_inverter_bg_%s.json" % _SLUG)
#: The run-id prefix, which is what the log and the snapshot keys are read by.
TAG = "invbg" if (DIE, CFG) == (DIE_DEFAULT, CFG_DEFAULT) else "invbg-%s" % _SLUG


# ══════════════════════════════════════════════════════════════════════════
#  REFILE MODE — no solve, no sandbox, no lock
# ══════════════════════════════════════════════════════════════════════════
# 2026-09-16.  Everything below this branch builds a sandbox and points every
# store at it, which is exactly what a refile must NOT do: it files artefacts
# INTO the real catalogue and reads nothing but a sandbox that has already
# finished.  So it happens here — before `MOTOR_AI_SIM_CONFIG` is touched, with
# the imports resolving against the user's real config/ as any ordinary tool's
# would, and with the write guard never installed because there is nothing to
# guard against.
#
# It repairs what a sandboxed save of an earlier runner could not carry out of
# its temp directory: the four field npz and the three mechanical duty_results
# kinds.  The yaml entry, the `runs.pwm_voltage` pointer and the gzip sidecar
# are NOT rewritten — the run's own save already wrote them, and a repair that
# rewrote them would be a second save with none of the second save's evidence.

if REFILE_FROM:
    _names = ([x.strip() for v in DUTIES for x in str(v).split(";") if x.strip()]
              if DUTIES else [ONE_DUTY] if ONE_DUTY else [])
    if len(_names) != 1:
        raise SystemExit(
            "REFUSING TO REFILE: name exactly ONE duty — a sandbox holds ONE "
            "finished run's last stores, and filing it under two duties would "
            "put one run's maps over another's name.  "
            "Use: --refile-from \"<sandbox>\" --die \"%s\" --config \"%s\" "
            "--duty \"<duty>\"" % (DIE, CFG))
    _duty = _names[0]
    if not os.path.isdir(REFILE_FROM):
        raise SystemExit("REFUSING TO REFILE: no such sandbox: %s" % REFILE_FROM)
    sys.path.insert(0, SRC)
    from motor_ai_sim import duty_refile as _rf                # noqa: E402

    log("=" * 78)
    log("=== REFILE — %s / %s / %r <- %s" % (DIE, CFG, _duty, REFILE_FROM))
    # THE SANDBOX MUST BE THE ONE THAT SOLVED THIS DUTY.  A sandbox names the
    # duty it last had loaded in `.family_context.json`, and its fields folder
    # is keyed by the duty stem, so a mismatch is caught here rather than
    # discovered as a picture of the wrong machine in a client report.  A
    # sandbox that solved several duties in sequence (the campaign's normal
    # shape) keeps a fields folder per duty, and only the LAST one's last-run
    # pickles are still on disk — hence the two separate checks.
    _ctx = _rf.sandbox_context(REFILE_FROM)
    _fdir = _rf.sandbox_fields_dir(REFILE_FROM, DIE, CFG, _duty)
    _have = sorted(p.stem for p in _fdir.glob("*.npz")) if _fdir.is_dir() else []
    log("  sandbox context: %s" % (json.dumps(_ctx, ensure_ascii=False)
                                   if _ctx else "none"))
    log("  sandbox fields:  %s -> %s" % (_fdir, ", ".join(_have) or "NONE"))
    if not _have and not (_ctx and _ctx[1] == CFG and _ctx[2] == _duty):
        raise SystemExit(
            "REFUSING TO REFILE: %s holds no fields for %r and its last run was "
            "%r — there is nothing of this duty in it" % (REFILE_FROM, _duty,
                                                          (_ctx or [None] * 3)[2]))
    _store_before = None
    _p = os.path.join(REAL_CONFIG, ".duty_results.json")
    if os.path.isfile(_p):
        _st = os.stat(_p)
        _store_before = (_st.st_mtime_ns, _st.st_size)
    try:
        _rep = _rf.refile(REFILE_FROM, DIE, CFG, _duty,
                          config_dir=REAL_CONFIG,
                          result_kinds=_rf.MECH_RESULT_KINDS,
                          provenance={"refiled_from": str(REFILE_FROM),
                                      "refiled_at": time.strftime(
                                          "%Y-%m-%dT%H:%M:%S")})
    except _rf.RefileError as _e:
        raise SystemExit("REFUSING TO REFILE: %s" % _e)
    for _k, _v in sorted((_rep["fields"]["written"] or {}).items()):
        log("  field %-13s %-9s %8d B -> %s"
            % (_k, _v["how"], _v["bytes"], _v["path"]))
    if _rep["fields"]["missing"]:
        log("  !! NOT filed: %s" % ", ".join(_rep["fields"]["missing"]))
    log("  duty_results merged: %s%s"
        % (", ".join(_rep["results"]["merged"]) or "nothing",
           (" (absent in the sandbox: %s)"
            % ", ".join(_rep["results"]["absent"]))
           if _rep["results"]["absent"] else ""))
    log("  mass verified against the configuration on disk: %s kg, shaft "
        "counted" % _rep["mass_total_kg"])
    if _store_before is not None:
        _st = os.stat(_p)
        log("  .duty_results.json %s"
            % ("unchanged — nothing was merged"
               if (_st.st_mtime_ns, _st.st_size) == _store_before
               else "rewritten (%d B)" % _st.st_size))
    log("=== REFILED")
    RESULTS.append({"refile": _rep})
    dump()
    sys.exit(0 if not _rep["fields"]["missing"] else 1)


# ══════════════════════════════════════════════════════════════════════════
#  THE SANDBOX — everything below this line happens in a temp config dir
# ══════════════════════════════════════════════════════════════════════════

def _real_state():
    """Every file under the REAL config/ that a solve could write, with its
    mtime and size.  The isolation proof is this dict, before and after."""
    out = {}
    pats = ("config/*.pkl", "config/.*", "config/motor_config.yaml",
            "config/*.json")
    for pat in pats:
        for p in glob.glob(pat):
            if os.path.isfile(p):
                try:
                    st = os.stat(p)
                    out[os.path.normpath(p)] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
    return out


TMP = tempfile.mkdtemp(prefix="inv_bg_")
log("=" * 78)
log("=== CONTROLLER (STAGE 2) COUPLED BACKGROUND RUN — %s / %s — sandbox %s" % (DIE, CFG, TMP))
log("pid %d, priority BELOW_NORMAL, lock %s" % (os.getpid(), _LOCK))

# The sandbox's own config file, and the catalog it needs to read.  The `runs`
# folders (9 MB of gzip sidecars) are NOT copied — nothing here reads them.
shutil.copy2(os.path.join(REAL_CONFIG, "motor_config.yaml"),
             os.path.join(TMP, "motor_config.yaml"))
for _dd in os.listdir(os.path.join(REAL_CONFIG, "dies")):
    _src = os.path.join(REAL_CONFIG, "dies", _dd)
    if not os.path.isdir(_src):
        continue
    _dst = os.path.join(TMP, "dies", _dd)
    os.makedirs(_dst, exist_ok=True)
    for _f in os.listdir(_src):
        if _f.endswith(".yaml") and os.path.isfile(os.path.join(_src, _f)):
            shutil.copy2(os.path.join(_src, _f), os.path.join(_dst, _f))
# …and the per-duty results, SEEDED from the real store: `duty_results.record`
# builds a PWM record's `reference_sine` out of the SINE record it replaces, so
# a sandbox that starts empty would silently drop the comparison the whole
# campaign exists to print.
if os.path.exists(os.path.join(REAL_CONFIG, ".duty_results.json")):
    shutil.copy2(os.path.join(REAL_CONFIG, ".duty_results.json"),
                 os.path.join(TMP, ".duty_results.json"))

#: THE LEVER.  Set before the first motor_ai_sim import — `DEFAULT_CONFIG_PATH`
#: is computed at import time of `motor_ai_sim.config`.
os.environ["MOTOR_AI_SIM_CONFIG"] = os.path.join(TMP, "motor_config.yaml")
sys.path.insert(0, SRC)

BEFORE = _real_state()

import yaml                                                   # noqa: E402
from motor_ai_sim import duty_refile                          # noqa: E402
from motor_ai_sim import duty_results as dr                   # noqa: E402
from motor_ai_sim import part_states as ps                    # noqa: E402
from motor_ai_sim.config import clear_config_cache            # noqa: E402
from motor_ai_sim.routes import coupled as cp                 # noqa: E402
from motor_ai_sim.routes import family as fam                 # noqa: E402
from motor_ai_sim.routes import geometry as geo_route         # noqa: E402
from motor_ai_sim.routes import mechanical as mech            # noqa: E402
from motor_ai_sim.routes import simulation as sim             # noqa: E402
from motor_ai_sim.routes import thermal as th                 # noqa: E402
from motor_ai_sim.simulation.fem_solver_2d import _NO_WARM_CACHE_CTX  # noqa: E402

#: The real dies directory, kept from before the redirect — the final save
#: needs it, and nothing else does.
REAL_DIES = os.path.join(REAL_CONFIG, "dies")

#: THE DC LINK, from the machine's own pack.  `routes.coupled._pack_nominal_v`
#: reads exactly this (`battery.v_nom`) when a request names no bus, and the
#: battery lives on the CONFIGURATION, not on the die — the CIANO10 200 opt die
#: file carries none at all, while L155 motor states 750.4 V and L180 gen states
#: 799.2 V.  Reading it here rather than hard-coding one campaign's number is
#: the difference between a generator that runs and one that is refused: at
#: 750.4 V the L180 peak duty needs m = 1.215, past the 1.15 linear limit.
#: …and THE CONFIGURATION ITSELF EXISTS.  Asked once, here, so a name that
#: never reached this process intact (see the argument check above) is one
#: refusal that names the die and lists what the catalog actually holds —
#: instead of the same FileNotFoundError traceback once per duty.
_CFG_YAML = os.path.join(REAL_DIES, DIE, CFG + ".yaml")
if not os.path.isfile(_CFG_YAML):
    _have = []
    try:
        _have = sorted(f[:-5] for f in os.listdir(os.path.join(REAL_DIES, DIE))
                       if f.endswith(".yaml") and f != "die.yaml")
    except OSError:
        _have = sorted(d for d in os.listdir(REAL_DIES)
                       if os.path.isdir(os.path.join(REAL_DIES, d)))
        raise SystemExit("REFUSING TO RUN: there is no die %r — the catalog "
                         "holds: %s" % (DIE, ", ".join(repr(x) for x in _have)))
    raise SystemExit("REFUSING TO RUN: %r has no configuration %r — it holds: "
                     "%s" % (DIE, CFG, ", ".join(repr(x) for x in _have)))

#: The die's pole count, for the carriers-per-period arithmetic the compensated
#: modulation ceiling needs BEFORE the machine is loaded.  The die file owns the
#: cross-section; a configuration may only override the free keys (stack, wire,
#: turns), never the poles.
try:
    _POLES = int(((yaml.safe_load(io.open(
        os.path.join(REAL_DIES, DIE, "die.yaml"), encoding="utf-8")) or {}
    ).get("geometry") or {}).get("num_poles") or 0)
except Exception:                                         # noqa: BLE001
    _POLES = 0

if not _V_DC_ARG:
    try:
        _cfg_doc = yaml.safe_load(io.open(_CFG_YAML, encoding="utf-8")) or {}
        _v_nom = ((_cfg_doc.get("battery") or {}) or {}).get("v_nom")
        if _v_nom:
            V_DC = float(_v_nom)
            V_DC_SOURCE = "%s's own battery v_nom" % CFG
        else:
            V_DC_SOURCE = ("the fallback — %s carries no battery block" % CFG)
    except Exception as _be:                              # noqa: BLE001
        V_DC_SOURCE = "the fallback (%r)" % (_be,)

# ── belt-and-braces: the explicit per-store redirects, on top of the lever ──
sim._transient_store_path = lambda: os.path.join(TMP, ".last_transient.json")
sim._transient_field_store_path = lambda: os.path.join(
    TMP, ".last_transient_field.pkl")
sim._append_run_journal = lambda *a, **k: None
sim._bench_compute = lambda *a, **k: None
th._last_store_path = lambda: os.path.join(TMP, ".last_thermal.pkl")
th._loss_maps_path = lambda: os.path.join(TMP, ".thermal_loss_maps.pkl")
th._LOSS_MAPS_LOADED = True
th._LOSS_MAPS.clear()
mech._last_store_path = lambda: os.path.join(TMP, ".last_mechanical.pkl")
mech._LAST_LOADED = True
cp._last_store_path = lambda: os.path.join(TMP, ".last_coupled.json")
cp._LAST_LOADED = True
dr.store_path = lambda: __import__("pathlib").Path(TMP) / ".duty_results.json"
# A geometry PUT starts a thread that pre-meshes the new machine five seconds
# later.  It writes only into the sandbox's mesh cache, but it is CPU the user
# is using right now, for a mesh the solve builds anyway.
geo_route._warm_live_mesh = lambda *a, **k: None


# ══════════════════════════════════════════════════════════════════════════
#  THE POSITIVE PROOF: this process CANNOT write under the real config/
# ══════════════════════════════════════════════════════════════════════════
# A differential proof — "config/ has the same mtimes afterwards" — is not
# available today: the user is working in the app while this runs, and HE
# changes those files, legitimately (measured 2026-09-15 08:17-08:21:
# motor_config.yaml, sweep_config.json and .sessions.json all moved while this
# runner was solving in its sandbox).  A guard that cannot tell his writes from
# ours would cry wolf for six hours.
#
# So the guard sits on THIS PROCESS's writes instead.  Every write whose target
# lands under the real config/ is REDIRECTED to the same relative path inside
# the sandbox, and recorded.  Redirect rather than refuse, deliberately: a
# refusal raises out of the middle of an hour-long FEM solve over a cache file
# nobody reads, while a redirect loses nothing and still proves the point —
# "N writes were attempted, N were redirected, 0 landed under config/".
#
# The final save switches the guard into pass-through for exactly two places:
# this die's catalog folder and .duty_results.json.

_REAL_ROOT = os.path.normcase(os.path.abspath(REAL_CONFIG))
_SANDBOX_MIRROR = os.path.join(TMP, "_config_mirror")
_ALLOW = {"on": False, "prefixes": ()}
#: (how, path) for every write this process aimed at the real config/.
WRITES_REDIRECTED = []
WRITES_PASSED = []
_WRITE_MODES = set("wax+")


def _under_real(p):
    try:
        a = os.path.normcase(os.path.abspath(str(p)))
    except Exception:                                     # noqa: BLE001
        return None
    if a == _REAL_ROOT or a.startswith(_REAL_ROOT + os.sep):
        return a
    return None


def _redirect(path, how):
    """The path this write should actually use.  Unchanged when it is not aimed
    at the real config/, or when the critical section has allowed it."""
    a = _under_real(path)
    if a is None:
        return path
    if _ALLOW["on"] and any(a.startswith(x) for x in _ALLOW["prefixes"]):
        WRITES_PASSED.append((how, str(path)))
        return path
    rel = os.path.relpath(a, _REAL_ROOT)
    dst = os.path.join(_SANDBOX_MIRROR, rel)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    except Exception:                                     # noqa: BLE001
        pass
    WRITES_REDIRECTED.append((how, str(path)))
    return dst


def _mirror_read(path):
    """A read of the real config/ prefers this process's own redirected copy.

    Without this the sandbox would be incoherent for any cache that writes then
    reads back (`.daxis_cache.json` is the live example): the write would land
    in the mirror and the read would return the user's file.  Nothing is broken
    by that — a cache miss costs time, not correctness — but a store that lies
    about what this process just wrote is the kind of thing that wastes an
    afternoon, so the mirror wins when it exists.
    """
    a = _under_real(path)
    if a is None:
        return path
    dst = os.path.join(_SANDBOX_MIRROR, os.path.relpath(a, _REAL_ROOT))
    return dst if os.path.exists(dst) else path


def install_write_guard():
    import builtins
    import pathlib
    _open = builtins.open

    def open_guard(file, mode="r", *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            if set(str(mode)) & _WRITE_MODES:
                file = _redirect(file, "open(%s)" % mode)
            else:
                file = _mirror_read(file)
        return _open(file, mode, *a, **k)

    builtins.open = open_guard
    io.open = open_guard                  # io.open IS builtins.open — patch both

    for name in ("replace", "rename", "remove", "unlink"):
        _f = getattr(os, name)

        def mk(_f=_f, _n=name):
            if _n in ("remove", "unlink"):
                return lambda p, *a, **k: _f(_redirect(p, _n), *a, **k)
            return lambda src, dst, *a, **k: _f(src, _redirect(dst, _n), *a, **k)

        setattr(os, name, mk())

    _mkdir, _makedirs = os.mkdir, os.makedirs
    os.mkdir = lambda p, *a, **k: _mkdir(_redirect(p, "mkdir"), *a, **k)
    os.makedirs = lambda p, *a, **k: _makedirs(_redirect(p, "makedirs"), *a, **k)

    _P = pathlib.Path
    _wt, _wb, _po = _P.write_text, _P.write_bytes, _P.open
    _pu, _pm, _pr, _pn = _P.unlink, _P.mkdir, _P.replace, _P.rename
    _P.write_text = lambda s, *a, **k: _wt(_P(_redirect(s, "Path.write_text")), *a, **k)
    _P.write_bytes = lambda s, *a, **k: _wb(_P(_redirect(s, "Path.write_bytes")), *a, **k)
    _P.unlink = lambda s, *a, **k: _pu(_P(_redirect(s, "Path.unlink")), *a, **k)
    _P.mkdir = lambda s, *a, **k: _pm(_P(_redirect(s, "Path.mkdir")), *a, **k)
    _P.replace = lambda s, t, *a, **k: _pr(s, _redirect(t, "Path.replace"), *a, **k)
    _P.rename = lambda s, t, *a, **k: _pn(s, _redirect(t, "Path.rename"), *a, **k)

    def path_open(s, mode="r", *a, **k):
        if set(str(mode)) & _WRITE_MODES:
            s = _P(_redirect(s, "Path.open(%s)" % mode))
        return _po(s, mode, *a, **k)

    _P.open = path_open
    log("  write guard: every write aimed at %s is redirected into %s"
        % (REAL_CONFIG, _SANDBOX_MIRROR))


def guard_physics():
    """WHICH coupled.py this process is about to solve with, and whether it
    carries the regulator's compensated ceiling.

    The loop reaches its solvers by importing the project's own route modules
    from ``src/``, so the physics in this run is whatever the working tree held
    at the moment of import — and the tree moves under it (2026-09-15 21:33:
    ``family.upsert_duty``'s signature changed while a campaign was in flight;
    2026-09-16: commit 972cf83 taught the regulator the modulator-compensated
    ceiling, which is the difference between a clamped fourth pass and a lost
    one).  Naming the file and the feature in the log costs nothing and makes
    every answer traceable to the code that produced it.
    """
    has = hasattr(cp, "_modulator_gain")
    log("  physics from %s — regulator ceiling: %s"
        % (os.path.dirname(os.path.abspath(cp.__file__)),
           "MODULATOR-COMPENSATED (_modulator_gain present)" if has else
           "uncompensated — this tree predates the compensated clamp, a "
           "regulator step may be refused by the modulation gate instead of "
           "being capped"))
    return has


def guard_sandbox():
    """EVERY writer resolves inside the sandbox, or this process stops.

    A redirect that quietly stopped working is the one failure mode that would
    put a Ø200 answer into the machine the user has on screen — so it is
    asserted, loudly, before anything solves, rather than trusted.
    """
    probes = {
        "config dir": str(dr._config_dir()),
        "motor_config.yaml": str(__import__(
            "motor_ai_sim.config", fromlist=["x"]).DEFAULT_CONFIG_PATH),
        "transient": sim._transient_store_path(),
        "transient field": sim._transient_field_store_path(),
        "thermal last": th._last_store_path(),
        "thermal loss maps": th._loss_maps_path(),
        "mechanical last": mech._last_store_path(),
        "coupled last": cp._last_store_path(),
        "duty results": str(dr.store_path()),
        "family context": str(fam._CTX_FILE),
        "dies dir": str(fam._DIES_DIR),
        "mesh cache": str(geo_route._MESH_DISK_DIR),
    }
    root = os.path.normcase(os.path.abspath(TMP))
    bad = {k: v for k, v in probes.items()
           if not os.path.normcase(os.path.abspath(v)).startswith(root)}
    for k, v in sorted(probes.items()):
        log("  sandbox %-20s -> %s%s" % (k, v, "   *** OUTSIDE ***"
                                         if k in bad else ""))
    if bad:
        raise SystemExit("REFUSING TO RUN: %d store(s) resolve outside the "
                         "sandbox: %s" % (len(bad), json.dumps(bad)))
    log("  sandbox guard: all %d stores are inside %s" % (len(probes), TMP))


# ── LIVE VISIBILITY, for a run that takes hours ────────────────────────────
# The runner prints only when a duty FINISHES, and a Stage-2 run at full
# resolution is a couple of hours.  The coupled route already says everything
# worth knowing at INFO — which pass it is on, what current it drew, where the
# regulator is aiming next — so that one logger is turned up rather than the
# root (which would drown the file in solver chatter).
import logging as _lg
for _n in ("motor_ai_sim.routes.coupled", "motor_ai_sim.inverter"):
    _l = _lg.getLogger(_n)
    _l.setLevel(_lg.INFO)
    if not _l.handlers:
        _h = _lg.StreamHandler(sys.stdout)
        _h.setFormatter(_lg.Formatter("%(asctime)s  [%(name)s] %(message)s",
                                      "%Y-%m-%d %H:%M:%S"))
        _l.addHandler(_h)
    _l.propagate = False

guard_physics()
guard_sandbox()
install_write_guard()


# ══════════════════════════════════════════════════════════════════════════
#  LOADING A MACHINE INTO THE SANDBOX — what ▶ does, to the temp config
# ══════════════════════════════════════════════════════════════════════════

def _tmp_cfg():
    return yaml.safe_load(io.open(os.environ["MOTOR_AI_SIM_CONFIG"],
                                  encoding="utf-8")) or {}


def _write_tmp_cfg(c):
    p = os.environ["MOTOR_AI_SIM_CONFIG"]
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(c, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, p)
    clear_config_cache()


class GeometryMismatch(Exception):
    """The machine the solver holds is not the machine on disk."""


#: The keys that DEFINE which machine is being solved.  A mismatch on any of
#: them means two different motors are in play at once, which is the 10:03
#: failure exactly.
_IDENTITY_KEYS = ("num_poles", "num_slots", "num_seg", "num_poles_per_segment",
                  "num_slots_per_segment", "stator_diameter", "motor_length",
                  "air_gap", "magnet_height")


def assert_geometry_live(want, duty_doc_):
    """THE SOLVER'S OWN GEOMETRY IS THE ONE WE ASKED FOR — checked, not assumed.

    `fem_solver_2d` reads `p.num_poles` off the process-wide
    `services.geometry_service` singleton and derives the electrical frequency
    from it (`f_elec = rpm * (num_poles // 2) / 60`, fem_solver_2d.py:2996).
    On 2026-09-15 that singleton was a different motor from the one in the
    config file for a whole 93-minute run, and the only outward sign was one
    `log.warning` about a stale `frequency` that the solver said it was
    ignoring.  A wrong f_elec mis-sizes the PWM settle window and the
    period-mean DC anchors, and the run dies at the DC gate after an hour and a
    half — or, worse, does not, and reports a machine nobody built.

    So the identity is compared key by key, and the derived fundamental with
    it, before a single frame is solved.
    """
    from motor_ai_sim.services.geometry_service import get_current_geometry
    live = get_current_geometry().to_dict()
    bad = []
    for k in _IDENTITY_KEYS:
        w, l = want.get(k), live.get(k)
        if w is None or l is None:
            continue
        try:
            if abs(float(w) - float(l)) > 1e-6:
                bad.append("%s: catalog %s vs singleton %s" % (k, w, l))
        except (TypeError, ValueError):
            if w != l:
                bad.append("%s: catalog %r vs singleton %r" % (k, w, l))
    # …AND the params the transient itself builds.  Checking the singleton alone
    # was not enough (2026-09-15, the second failure): the singleton was right
    # and `simulation.geometry_2d.params_from_config` — the call every run
    # without a `geo_override` makes — read a DIFFERENT file, because its
    # `cfg_path` default was a module constant bound at import and did not
    # follow MOTOR_AI_SIM_CONFIG.  It is the pole count from THAT object that
    # sets f_elec, so it is that object which has to be asserted on.
    try:
        from motor_ai_sim.simulation.geometry_2d import params_from_config
        pp = params_from_config()
        for k in ("num_poles", "num_slots"):
            w, l = want.get(k), getattr(pp, k, None)
            if w is not None and l is not None and int(w) != int(l):
                bad.append("%s: catalog %s vs the solver's own params %s"
                           % (k, w, l))
        _p_poles = int(getattr(pp, "num_poles", 0) or 0)
    except GeometryMismatch:
        raise
    except Exception as e:                                # noqa: BLE001
        bad.append("could not build the solver's own params (%r)" % (e,))
        _p_poles = 0
    rpm = float(duty_doc_.get("rpm") or 0.0)
    f_want = rpm * (int(want.get("num_poles") or 0) // 2) / 60.0
    f_live = rpm * (int(live.get("num_poles") or 0) // 2) / 60.0
    f_solv = rpm * (_p_poles // 2) / 60.0
    if abs(f_want - f_live) > 1e-6 or abs(f_want - f_solv) > 1e-6:
        bad.append("f_elec at %.0f rpm: catalog %.2f Hz, singleton %.2f Hz, "
                   "solver params %.2f Hz" % (rpm, f_want, f_live, f_solv))
    if bad:
        raise GeometryMismatch(
            "the solver holds a different machine from the one on disk — %s"
            % "; ".join(bad))
    log("  geometry verified: the solver's own machine is %s slots / %s poles, "
        "Ø%s mm, L %s mm — f_elec %.2f Hz at %.0f rpm (singleton and "
        "params_from_config agree)"
        % (live.get("num_slots"), live.get("num_poles"),
           live.get("stator_diameter"), live.get("motor_length"), f_live, rpm))


def load_machine(die, cfg, duty):
    """The configuration and its duty, into the sandbox config.

    The catalog's own payload builder answers what to load (`family.payload`,
    the exact dict the frontend's ▶ applies), and the geometry goes in through
    the real `PUT /api/geometry` handler so the derived fields are recomputed by
    the code that owns them rather than by a copy of it here.  Winding,
    materials, PART STATES and the operating point are plain blocks and are
    written straight into the sandbox yaml.
    """
    p = fam.payload(die, cfg, duty=duty, authorization=None)
    g = dict(p.get("geometry") or {})
    sim_blk = p.get("sim") or {}
    d = p.get("duty") or {}
    mesh = d.get("mesh") or {}

    # 1. THE GEOMETRY, into the sandbox yaml — every key the payload names,
    #    verbatim.  The die carries its own derived fields (num_poles, the
    #    radii, the pitches) and they are the catalog's answer.
    #
    #    THIS USED TO GO THROUGH `routes.geometry.update_geometry` and that is
    #    what cost the 10:03 run (2026-09-15).  `GeometryUpdateModel` is an
    #    `extra="allow"` model with ZERO declared fields, so
    #    `k in GeometryUpdateModel.model_fields` matched nothing and the handler
    #    was called with an EMPTY dict: `update_current_geometry()` then had no
    #    kwargs and left the process-wide geometry singleton exactly as it was —
    #    the L13 the sandbox yaml was seeded from.  The yaml said 12s/10p and
    #    `services.geometry_service._current_geometry` still said 28 poles, so
    #    `fem_solver_2d` derived f_elec = 20000 × 14/60 = 4666.67 Hz for a
    #    machine whose real fundamental at that speed is 1666.67 Hz.  Every
    #    settle window and every period-mean DC anchor was then sized on an
    #    electrical period 2.8× too short, and the run ended with -175.11 A of
    #    DC in phase A that "12 period-mean DC anchors" could not remove.
    #
    #    So: write the yaml, then REBUILD the singleton from it.
    c = _tmp_cfg()
    c.setdefault("geometry", {}).update(g)

    # 3. winding / materials / PART STATES / the point.
    w = dict(p.get("winding") or {})
    if w:
        c["winding"] = w
    mats = dict(c.get("materials") or {})
    mats.update({k: v for k, v in (p.get("materials") or {}).items() if v})
    c["materials"] = mats
    # THE PART STATES OF THE CONFIGURATION BEING LOADED.  Written explicitly,
    # including an explicit `included`: `_build_sig` hashes the block when it is
    # present, so writing {shaft: included} — which is what the L155 stores —
    # keeps the signature identical and no earlier result is falsely flagged
    # "computed on an older build".  (`part_states.resolve` drops `included`, so
    # the shaft is counted in every mass.)
    pstates = {k: str(v).strip().lower()
               for k, v in (p.get("parts") or {}).items() if k in STATEFUL_PARTS}
    if pstates:
        c["parts"] = pstates
    else:
        c.pop("parts", None)
    s = c.setdefault("simulation", {})
    s.update({
        "max_current": float(d.get("current_arms") or sim_blk.get("current_a") or 0),
        "current_a": float(d.get("current_arms") or sim_blk.get("current_a") or 0),
        "rpm": float(d.get("rpm") or sim_blk.get("rpm") or 0),
        "phase_offset_deg": float(d.get("gamma_deg") or 0.0),
        "gamma_deg": float(d.get("gamma_deg") or 0.0),
        "mode": str(d.get("mode") or "motor"),
        "connection": str(sim_blk.get("connection")
                          or mesh.get("sim.connection") or "2P"),
        "star_delta": str(sim_blk.get("star_delta")
                          or d.get("star_delta") or "delta"),
        # The carrier is a DUTY setting (`sim.fSwitch`) — it is where the loop's
        # `_effective_f_switch` and the modal excitation table read it from, and
        # leaving the previous machine's 48 kHz here is what once put a Ø85
        # carrier into a Ø200 ring-mode table.
        "f_switch": float(mesh.get("sim.fSwitch") or CARRIER),
        # rpm is the master and the frequency is DERIVED (fem_solver_2d:2986);
        # the seeded copy carried the L13's 233.33 Hz, which made every run log
        # a "config frequency inconsistent with rpm" warning.  The warning was
        # not the bug — the solver does re-derive — but leaving a wrong number
        # in the file next to the right one is how the next reader gets it
        # wrong, so it is written correctly here.
        "frequency": round(float(d.get("rpm") or 0.0)
                           * (int(g.get("num_poles") or 0) // 2) / 60.0, 3),
    })
    if sim_blk.get("end_winding_factor") is not None:
        s["end_winding_factor"] = float(sim_blk["end_winding_factor"])
    elif mesh.get("sim.endWinding"):
        s["end_winding_factor"] = float(mesh["sim.endWinding"])
    if sim_blk.get("daxis_deg") is not None:
        s["daxis_deg"] = sim_blk["daxis_deg"]
    _write_tmp_cfg(c)

    # 3b. THE SINGLETON, rebuilt from the yaml that was just written.
    #     `reset_geometry()` clears the config cache and re-reads — it is the
    #     only call that makes `services.geometry_service._current_geometry`
    #     describe the machine on disk.  Without it the process keeps whatever
    #     machine it first touched; see the note in step 1.
    from motor_ai_sim.services.geometry_service import (
        get_current_geometry as _gcg, reset_geometry as _reset_geo)
    _reset_geo()
    assert_geometry_live(g, d)

    # 4. the catalog context, so `_duty_summary` finds this duty's own V1 seed
    #    and `duty_results` files the records under the right three names.
    io.open(os.path.join(TMP, ".family_context.json"), "w",
            encoding="utf-8").write(json.dumps(
                {"die": die, "config": cfg, "duty": duty}))

    # 5. and the assertions.  The GENERAL one first: the machine now in the
    #    sandbox carries exactly the accounting its configuration states —
    #    which is the invariant the live ▶ does not enforce and the reason the
    #    L13 joint's `shaft: reference` rode into the L155 on 2026-09-14.
    live = ps.config_part_states()
    eff = ps.resolve()
    if live != pstates:
        raise MassAbort(
            "%s/%s states parts %s but the sandbox machine resolves to %s"
            % (die, cfg, json.dumps(pstates, sort_keys=True),
               json.dumps(live, sort_keys=True)))
    # …and the one the L155 campaign turns on.  It is NOT general: the L13
    # robot joint is a frameless build whose shaft is the customer's, and
    # `reference` is the right answer there.
    if (die, cfg) == (DIE, CFG) and str(eff.get("shaft") or "included") != "included":
        raise MassAbort("the sandbox %s/%s resolves its shaft to %r — the "
                        "L155 counts its shaft" % (die, cfg, eff.get("shaft")))
    gg = (_tmp_cfg().get("geometry") or {})
    log("  loaded %s / %s / %r into the sandbox: stator %s mm, L %s mm, "
        "parts %s (effective %s), %s, %.1f A, %.0f rpm, γ=%.1f°"
        % (die, cfg, duty, gg.get("stator_diameter"), gg.get("motor_length"),
           json.dumps(live, sort_keys=True), json.dumps(eff, sort_keys=True)
           or "{} — the shaft counted",
           s.get("star_delta"), s.get("max_current"), s.get("rpm"),
           s.get("gamma_deg")))
    return p, d


# ══════════════════════════════════════════════════════════════════════════
#  THE BOUNDARY CONDITIONS COME FROM THE DUTY — never from a panel
# ══════════════════════════════════════════════════════════════════════════

def duty_thermal_settings(die, cfg, duty, fallback, max_iter):
    """The Thermal-panel block for one duty, read BACK from its stored record.

    THE SINE RECORD WINS: when the duty's current thermal record is itself a PWM
    one (this campaign's), its `reference_sine` holds the boundary the duty was
    characterised on.  Returns ``(settings, source, mode)``.
    """
    s = dict(fallback)
    s["maxIter"] = str(int(max_iter))
    src = "the fallback (no stored thermal record)"
    try:
        e = (dr.get(die, cfg) or {}).get(duty) or {}
        rec = e.get("thermal") or {}
        ref = rec.get("reference_sine") or {}
        if str(rec.get("drive") or "sine").lower() == "pwm" and ref:
            rec, which = ref, "reference_sine (the duty's SINE boundary)"
        else:
            which = "the duty's thermal record"
        pt, cool = rec.get("point") or {}, rec.get("cooling") or {}
        if not pt and not cool:
            return s, src, str(s["coolMode"]).lower()
        outer, inner = cool.get("outer") or {}, cool.get("inner") or {}
        shaft, frame = cool.get("shaft_ends") or {}, cool.get("frame") or {}
        for k, v in (("coolMode", pt.get("cooling_mode")),
                     ("ambientT", pt.get("ambient_temp")),
                     ("hConv", pt.get("h_conv")),
                     ("airSpeed", outer.get("air_speed_mps")),
                     ("fluid", outer.get("fluid")),
                     ("tIn", outer.get("t_in_c")),
                     ("flowLpm", outer.get("flow_lpm")),
                     ("boreMode", inner.get("mode")),
                     ("boreAirSpeed", inner.get("air_speed_mps")),
                     ("boreFluid", inner.get("fluid")),
                     ("boreTIn", inner.get("t_in_c")),
                     ("boreFlowLpm", inner.get("flow_lpm")),
                     ("shaftExtMm", shaft.get("length_each_side_mm")),
                     ("frame", frame.get("mode")),
                     ("openAirSpeed", frame.get("open_air_speed_mps")),
                     ("emissivity", pt.get("emissivity")),
                     ("endFaces", pt.get("end_faces")),
                     ("endFaceSides", pt.get("end_face_sides")),
                     ("mountG", pt.get("mount_g_w_per_k")),
                     ("mountT", pt.get("mount_temp_c"))):
            if v is not None:
                s[k] = str(v)
        if str(s.get("boreMode") or "none").lower() != "air":
            s.pop("boreAirSpeed", None)
        if str(s.get("coolMode") or "").lower() != "liquid":
            for k in ("fluid", "tIn", "flowLpm"):
                s.pop(k, None)
        s["maxIter"] = str(int(max_iter))
        src = "%s (%s)" % (which, rec.get("computed_at"))
    except Exception as e:                                # noqa: BLE001
        log("  could not read %r's thermal record (%r) — using the fallback"
            % (duty, e))
    return s, src, str(s.get("coolMode") or "").lower()


def assert_cooling(out, want_mode, where):
    """The thermal half must have solved the boundary we asked for — the whole
    defect of 2026-09-14 was a perfectly converged loop on somebody else's
    cooling, and it was invisible in the coupled answer."""
    got = str((((out.get("thermal") or {}).get("cooling") or {})
               .get("outer") or {}).get("mode") or "").lower()
    if got != str(want_mode).lower():
        raise RuntimeError(
            "%s: the thermal half solved cooling_mode=%r but this duty is "
            "characterised on %r — refusing to keep it" % (where, got, want_mode))
    return got


# ══════════════════════════════════════════════════════════════════════════
#  THE RUN
# ══════════════════════════════════════════════════════════════════════════

def winding_current(d):
    """The duty's current IN THE WINDING.  In DELTA the catalogued
    ``current_arms`` is the LINE current and the branch carries it over √3."""
    i_line = float(d["current_arms"])
    return (i_line / math.sqrt(3.0)
            if str(d.get("star_delta") or "delta").lower().startswith("d")
            else i_line)


class InverterImpossible(Exception):
    """The fundamental this duty needs cannot be built on this DC link."""


def assert_modulation(d, v_dc):
    """THE BRIDGE CAN ACTUALLY PRODUCE THIS POINT — checked before it is solved.

    ``routes.simulation`` refuses m > 1.15 with a 422 in milliseconds, which is
    the right place for it; this is the same gate read out loud one step
    earlier, so a campaign that cannot run says so in the log by name instead of
    arriving as a traceback three hours later — and so the DRY pass, which
    solves nothing, still answers the question the campaign is about.

    m = 2·V₁/V_dc, and in DELTA the drive is asked for the BRANCH fundamental,
    which IS the line voltage, so the honest index carries the √3
    (`pwm.modulation_index`; a gate fed the branch volts straight reads 1.53
    where the bridge sits at 0.88).

    THE L180 GENERATOR IS EXACTLY THIS QUESTION.  Its peak duty needs 789.75 V
    of branch fundamental: m = 1.215 on the L155's 750.4 V pack (refused) and
    1.141 on its own 799.2 V nominal (0.8 % of headroom left).
    """
    from motor_ai_sim.simulation.pwm import (MAX_MODULATION_INDEX as _MAXM,
                                             modulation_index as _mod)
    s = d.get("summary") or {}
    v1 = float(s.get("V1_seed_peak_V") or 0.0)
    sd = str(d.get("star_delta") or "delta")
    if not (v1 > 0.0):
        raise InverterImpossible(
            "%r carries no V1_seed_peak_V — run it on the current drive first"
            % (d.get("name"),))
    m = _mod(v1, float(v_dc), star_delta=sd)
    v1_max = 0.5 * _MAXM * float(v_dc) * (math.sqrt(3.0)
                                          if str(sd).lower().startswith("d")
                                          else 1.0)
    # THE CEILING THE REGULATOR WILL ACTUALLY BE HELD TO, which is NOT the one
    # above.  The modulator does not apply the reference it is handed, so the
    # route clamps to the fundamental it can be made to APPLY
    # (`coupled._modulator_gain` × the headroom margin) — 747 V, not 796 V, at
    # 14 carriers on this link.  Printing the linear number alone is what made
    # a step to 761.7 V look like it had 4 % of room when it had none
    # (2026-09-16, this duty's lost pass 4).
    nc, gain = None, 1.0
    if _POLES and float(d.get("rpm") or 0) > 0:
        f_el = float(d["rpm"]) * (int(_POLES) // 2) / 60.0
        nc = max(1, int(round(float(CARRIER) / max(f_el, 1e-9))))
        try:
            gain = float(cp._modulator_gain(
                nc, round(float((d.get("summary") or {})
                                .get("V1_seed_delta_deg") or 0.0), 3)))
            v1_max = v1_max * gain * (1.0 - float(
                getattr(cp, "_MODULATION_HEADROOM", 0.005)))
        except Exception as _ge:                          # noqa: BLE001
            log("  (could not compute the compensated ceiling: %r — the linear "
                "one is quoted below)" % (_ge,))
    if m > _MAXM:
        raise InverterImpossible(
            "%r needs V1 = %.2f V peak (%s branch) on a %.1f V link, i.e. "
            "m = %.4f — past the %.2f linear-modulation limit.  The bridge "
            "would be in overmodulation (pulse dropping / six-step), which is a "
            "different machine: raise the link above %.0f V, or take the point "
            "below %.1f V.  REFUSING to solve it rather than saturating "
            "silently." % (d.get("name"), v1, sd, float(v_dc), m, _MAXM,
                           math.ceil(float(v_dc) * m / _MAXM), v1_max))
    log("  modulation: V1 %.2f V peak (%s branch) on a %.1f V link -> m = %.4f "
        "of %.2f — %.2f %% of headroom to the %.2f V ceiling%s"
        % (v1, sd, float(v_dc), m, _MAXM, 100.0 * (v1_max / v1 - 1.0), v1_max,
           "" if nc is None else
           " (the MODULATOR-COMPENSATED one: %d carriers per electrical "
           "period, gain %.4f, so the linear %.2f V is not reachable)"
           % (nc, gain, 0.5 * _MAXM * float(v_dc)
              * (math.sqrt(3.0) if str(sd).lower().startswith("d") else 1.0))))
    if v1 > v1_max:
        log("  NOTE: the seed itself is above the compensated ceiling — the "
            "first pass will be refused by the modulation gate; the run stands "
            "or falls on that refusal, by name")
    return m, v1_max


def body_for(d, f_carrier, max_iter, settings, run_id, steps=None,
             mechanical=True, record_as="main"):
    mesh = d.get("mesh") or {}
    s = d.get("summary") or {}
    c = s.get("coupling") or {}
    inv = {"f_carrier_hz": float(f_carrier), "v_dc_V": V_DC,
           "schedule": "mixed", "record_as": record_as,
           "v_phase_peak_V": float(s["V1_seed_peak_V"]),
           "v_delta_deg": float(s["V1_seed_delta_deg"]),
           # STAY AT THE DUTY'S POINT: without this the bridge holds a fixed
           # fundamental and the machine drifts wherever its settling magnets
           # take it (442.8 -> 500.8 A on the first attempt).
           "target_I_phase_rms_A": round(winding_current(d), 4),
           "i_tol_pct": 1.0,
           # No in-run sinusoidal reference: a second full transient per
           # iteration for a number nothing here reads.
           "harm_ref": False}
    if steps:
        inv["n_steps_per_period"] = int(steps)
    f = lambda x, dv=float("nan"): (float(x) if x is not None else dv)
    return {
        "restore": False, "n_periods": 1,
        "n_steps_per_period": int(mesh.get("sim.stepsPP") or 36),
        "gamma_deg": float(d["gamma_deg"]),
        "I_phase_rms": float(d["current_arms"]),
        "mesh_size_mm": float(mesh.get("mesh.meshSize") or 4.0),
        "min_size_mm": float(mesh.get("mesh.minSize") or 0.3),
        "outer_air_factor": float(mesh.get("mesh.outerAir") or 1.2),
        "motion_band": True, "band_thickness_mm": 0.4,
        "gap_layers": float(mesh.get("mesh.gapLayers") or 1),
        "n_sectors": int(mesh.get("mesh.nSectors") or 2),
        "stator_fillet_mm": 0, "sliding_band": True,
        "rotor_eddy": True, "eddy": True, "field_snapshot": True,
        "demag": bool(mesh.get("sim.demag", True)),
        "torque_filter": False, "pole_copy": bool(mesh.get("mesh.poleCopy", False)),
        "structured_gap": bool(mesh.get("mesh.structuredGap", True)),
        "airgap_macro": False, "element_order": 2,
        "iron_template": bool(mesh.get("mesh.ironTemplate", True)),
        "geo_mesh": bool(mesh.get("mesh.geoMesh", True)),
        "rpm": float(d["rpm"]),
        "coil_temp_c": f(c.get("coil_temp_c") or mesh.get("sim.coilTemp") or 120.0),
        "magnet_temp_c": f(c.get("magnet_temp_c") or mesh.get("sim.magnetTempC") or 150.0),
        "end_winding_factor": float(mesh.get("sim.endWinding") or 1.355),
        "mode": str(d.get("mode") or "motor"),
        "connection": str(mesh.get("sim.connection") or "2P"),
        "star_delta": str(d.get("star_delta") or "delta"),
        "component_mesh": json.dumps(mesh.get("mesh.componentMesh")
                                     or {"coil_rel": 0.5}),
        "include_frames": False, "n_frames": 0, "run_id": str(run_id),
        "mechanical": bool(mechanical), "max_iter": int(max_iter),
        "drive": "inverter", "inverter": inv,
        # The 20 degC catalogue constants are a MEASUREMENT OF THE MACHINE, not
        # of its power stage, and they cost a whole extra transient at this
        # resolution.  Off when asked; the duty's own stored constants stand.
        **({"cold_constants": False} if NO_COLD else {}),
        # STAGE 2 — the CONTROLLER's own bridge.  The carrier, the link and the
        # fundamental stay in `inverter` (identical to the ideal-PWM run); this
        # block is the power stage that applies them.
        "controller": {
            "device": DEVICE,
            "topology": TOPOLOGY,
            "devices_parallel": N_PARALLEL,
            "dead_time_us": DEAD_TIME_US,
            "v_gs_on_V": 18.0, "v_gs_off_V": VGS_OFF,
            "pwm_modulation": "svpwm",
            **({"r_th_jc_k_w": float(RTH_JC)} if RTH_JC else {}),
            **({"r_tim_k_w": float(RTIM)} if RTIM else {}),
            "cooling": {"coolant": PLATE_COOLANT,
                        "flow_lpm": PLATE_FLOW_LPM,
                        "t_in_c": PLATE_T_IN_C},
        },
        "thermal_settings": dict(settings or {}),
    }


def coupled_pwm(d, f_carrier, max_iter, settings, want_mode, steps=None,
                mechanical=True, tag="pwmbg"):
    t0 = time.time()
    run_id = "%s-%s-%.0f-%s" % (tag, str(d["name"]).replace(" ", "_"),
                                f_carrier, time.strftime("%H%M%S"))
    body = body_for(d, f_carrier, max_iter, settings, run_id, steps=steps,
                    mechanical=mechanical)
    log("  RUN_ID %s   (in-process; stop it by killing pid %d)"
        % (run_id, os.getpid()))
    log("  REQUEST %s" % json.dumps(
        {"drive": body["drive"], "inverter": body["inverter"],
         "controller": body.get("controller"),
         "max_iter": body["max_iter"], "mechanical": body["mechanical"],
         "rpm": body["rpm"], "gamma_deg": body["gamma_deg"],
         "star_delta": body["star_delta"],
         "thermal_settings": body["thermal_settings"]}, ensure_ascii=False))
    # The warm cache is PROCESS-WIDE state; a background solve must not publish
    # a seed into it (2026-09-07: a full-ring eddy field went in and ten sweep
    # points died on it).
    tok = _NO_WARM_CACHE_CTX.set(True)
    try:
        out = cp.run(dict(body))
    finally:
        _NO_WARM_CACHE_CTX.reset(tok)
    got = assert_cooling(out, want_mode, "%r @ %.0f Hz" % (d["name"], f_carrier))
    log("  cooling verified: the map came back on %r, as the duty states" % got)
    c = out.get("coupling") or {}
    tr = out.get("transient") or {}
    s = tr.get("summary") or {}
    inv = c.get("inverter") or {}
    m = c.get("mechanical") or {}
    log("  POINT: aimed at %s A in the winding, landed %s A (%s %%) -> %s"
        % (inv.get("target_I_phase_rms_A"), inv.get("I_phase_rms_solved_A"),
           inv.get("point_error_pct"),
           "ON POINT" if inv.get("on_point") else "OFF POINT"))
    ctl = c.get("controller") or {}
    if ctl:
        _th = ctl.get("thermal") or {}
        _ls = ctl.get("losses") or {}
        _ef = ctl.get("efficiency") or {}
        log("  CONTROLLER %s x%d, %s: T_j=%s degC (limit %s, margin %s K) | "
            "cond=%s W 3q=%s W sw=%s W total=%s W | eta_inv=%s "
            "eta_wall_to_shaft=%s | limits=%s | T_j residual %s K over %d pass(es)"
            % (ctl.get("device"),
               (ctl.get("settings_resolved") or {}).get("devices_parallel", 0),
               (ctl.get("settings_resolved") or {}).get("topology"),
               _th.get("t_j_max_c"), _th.get("t_j_limit_c"), _th.get("margin_K"),
               _ls.get("conduction_W"), _ls.get("third_quadrant_W"),
               _ls.get("switching_W"), _ls.get("total_W"),
               _ef.get("inverter"), _ef.get("wall_to_shaft"),
               ctl.get("limits_verdict"), ctl.get("t_j_residual_K"),
               len(ctl.get("passes") or [])))
        log("  EXCITATION %s" % json.dumps(ctl.get("excitation") or {},
                                           ensure_ascii=False, default=str)[:900])
        for _p in ctl.get("passes") or []:
            log("    ctl pass %s: T_j %s degC (d %s K) | P_inv %s W | eta_inv %s "
                "| R_ds %s mohm | %s"
                % (_p.get("iter"), _p.get("t_j_c"), _p.get("d_t_j_K"),
                   _p.get("p_inverter_W"), _p.get("eta_inverter"),
                   _p.get("r_ds_on_mohm_device"), _p.get("limits_verdict")))
        for _w in (ctl.get("warnings") or [])[:8]:
            log("    ctl warning: %s" % str(_w)[:300])
    log("  COUPLED pwm %.0f Hz -> it=%s conv=%s | coil=%s mag=%s brg=%s | "
        "T2d=%s Nm ripple=%s%% | Cu=%s Fe=%s solid=%s total=%s eta=%s | "
        "m=%s eq_star=%s dc=%s A THD_I=%s%% | sf_min=%s | %.0f s"
        % (f_carrier, c.get("iterations"), c.get("converged"),
           c.get("coil_temp_c"), c.get("magnet_temp_c"), c.get("bearing_temp_c"),
           s.get("T_em_avg_Nm"), s.get("T_ripple_pct"), s.get("P_stranded_W"),
           s.get("P_core_W"), s.get("P_solid_W"), s.get("P_loss_total_W"),
           s.get("efficiency"), inv.get("m"), inv.get("equivalent_star"),
           inv.get("dc_residual_A"), inv.get("thd_i_pct"), m.get("sf_min"),
           time.time() - t0))
    for h in c.get("history") or []:
        log("    pass %s: coil %s->%s mag %s->%s brg %s->%s | T_j %s degC | "
            "V1 %s V -> "
            "I_solved %s A (next V1 %s) | ripple %s %% dc %s A | P_loss %s W"
            % (h.get("iter"), h.get("T_coil_in"), h.get("T_coil_out"),
               h.get("T_magnet_in"), h.get("T_magnet_out"),
               h.get("bearing_temp_c"), h.get("T_bearing_out"),
               h.get("T_junction_c"),
               h.get("v_phase_peak_V"), h.get("I_phase_rms_solved_A"),
               h.get("v_phase_peak_next_V"), h.get("T_ripple_pct"),
               h.get("pwm_dc_residual_A"), h.get("P_loss_W")))
    if c.get("warning"):
        log("  warning: %s" % str(c.get("warning"))[:500])
    return out, s, c, inv


# ══════════════════════════════════════════════════════════════════════════
#  THE SAVE — a short critical section against the REAL config
# ══════════════════════════════════════════════════════════════════════════

def _mass_faults(summary, where):
    out = []
    if not isinstance(summary, dict) or not summary:
        return out
    if EXPECTED_MASS_TOTAL_KG is None:
        raise MassAbort("%s: no expected mass was read for this duty" % where)
    m = summary.get("mass_total_kg")
    if m is None:
        if summary.get("mass_components") or summary.get("mass_active_kg"):
            out.append("%s: a mass block with no mass_total_kg" % where)
    elif abs(float(m) - EXPECTED_MASS_TOTAL_KG) > MASS_TOL_KG:
        out.append("%s: mass_total_kg = %s kg, not %s kg"
                   % (where, m, EXPECTED_MASS_TOTAL_KG))
    st = summary.get("part_states") or {}
    if str(st.get("shaft") or "included").lower() != "included":
        out.append("%s: part_states says shaft = %r" % (where, st.get("shaft")))
    for row in (summary.get("mass_components") or []):
        if isinstance(row, dict) and row.get("state") and (
                "shaft" in str(row.get("name") or "").lower()):
            out.append("%s: the shaft row carries state=%r (%r)"
                       % (where, row.get("state"), row.get("name")))
    return out


def _backfill_reference_em(die, cfg, duty, d):
    """Give this duty's `reference_sine` its WATTS, from the duty's own saved
    sine summary in the configuration yaml.  The sine coupled records predate
    the coupling block's `em` face, so a sine → PWM table would otherwise have
    temperatures but no losses — the one row the campaign is about."""
    _EM_FACE = ("T_em_avg_Nm", "T_ripple_pct", "P_stranded_W", "P_core_W",
                "P_solid_W", "P_mag_W", "P_shaft_W", "P_sleeve_W",
                "P_loss_total_W", "efficiency", "I1_phase_rms_A", "THD_I_pct",
                "THD_LL_pct", "V_line_peak_V", "n_steps_per_period")
    try:
        doc = dr.read_all()
        node = (((doc.get("results") or {}).get(die) or {}).get(cfg) or {}
                ).get(duty)
        if not isinstance(node, dict):
            return
        s = d.get("summary") or {}
        face = {k: s[k] for k in _EM_FACE if s.get(k) is not None}
        if not face:
            return
        moved = []
        for kind in ("coupled", "thermal"):
            ref = ((node.get(kind) or {}) or {}).get("reference_sine")
            if isinstance(ref, dict) and not ref.get("em"):
                ref["em"] = dict(face)
                ref["em_source"] = ("the duty's saved sine summary in the "
                                    "configuration yaml")
                moved.append(kind)
        if moved and dr._write_all(doc):
            log("  reference_sine.em backfilled (%s): Cu %s W, Fe %s W, "
                "total %s W" % (", ".join(moved), face.get("P_stranded_W"),
                                face.get("P_core_W"),
                                face.get("P_loss_total_W")))
    except Exception as e:                                # noqa: BLE001
        log("  reference_sine.em backfill skipped (%r)" % (e,))


def save_to_real_catalog(die, cfg, duty, d, out, s, c):
    """THE CRITICAL SECTION.  Short, atomic, and this duty only.

    `routes.family` is re-pointed at the real dies directory and the duty-results
    store at the real config directory for the length of this call and no
    longer.  The configuration yaml is re-read from disk INSIDE the section —
    the user may have saved something of his own in the hours this run took —
    and both writes go through the functions the HTTP save route uses, which
    are atomic (`_save_yaml` → `_replace_with_retry`, `_write_all` → os.replace).

    `drive: "pwm_voltage"` is what makes this a non-primary save: the duty's
    sine snapshot — the story the catalog row shows and the settings the next ▶
    restores — is untouched, and this lands under `runs.pwm_voltage`.  That was
    the 2026-09-02 lesson: a PWM save that overwrote the primary made every
    later Run a twelve-minute PWM solve.
    """
    tr = out.get("transient") or {}
    inv = c.get("inverter") or {}
    mesh = dict(d.get("mesh") or {})
    mesh.update({
        "sim.current": float(d["current_arms"]), "sim.rpm": float(d["rpm"]),
        "sim.gamma": float(d["gamma_deg"]),
        "sim.starDelta": str(d.get("star_delta") or "delta"),
        "sim.opMode": str(d.get("mode") or "motor"),
        "sim.coilTemp": c.get("coil_temp_c"),
        "sim.magnetTempC": str(c.get("magnet_temp_c") or ""),
        "sim.coupled": True, "sim.drive": "pwm_voltage",
        "sim.fSwitch": inv.get("f_carrier_hz"),
        "sim.vBus": inv.get("v_dc_V"),
        "sim.stepsPP": inv.get("steps_per_period"),
        "sim.vPhasePeak": inv.get("v_phase_peak_V"),
        "sim.vDeltaDeg": inv.get("v_delta_deg"),
        "sim.coupledAdopted": "%s|%s|%s|%s" % (
            c.get("coil_temp_c"), c.get("magnet_temp_c") or "",
            c.get("iterations") or "", tr.get("computed_at") or "")})
    mats = (_tmp_cfg().get("materials") or {})
    sig = "|".join("%s=%s" % (k, mats[k]) for k in sorted(mats))

    # ── the per-duty records this run produced, lifted out of the sandbox ────
    _backfill_reference_em(die, cfg, duty, d)
    sand = dr.read_all()
    mine = (((sand.get("results") or {}).get(die) or {}).get(cfg) or {}
            ).get(duty) or {}

    # ── in ──────────────────────────────────────────────────────────────────
    _dies_was, _cfgdir_was = fam._DIES_DIR, dr._config_dir
    _store_was = dr.store_path
    from pathlib import Path as _P
    t0 = time.time()
    #: The ONLY two places this process is ever allowed to write for real: this
    #: die's catalog folder (the configuration yaml, its history snapshot and
    #: the gzip run sidecar) and the per-duty results store.  Named as prefixes
    #: so a stray write anywhere else under config/ is still redirected.
    _allow_prefixes = tuple(os.path.normcase(os.path.abspath(p)) for p in (
        os.path.join(REAL_DIES, die),
        os.path.join(REAL_CONFIG, ".duty_results.json")))
    try:
        _ALLOW["prefixes"] = _allow_prefixes
        _ALLOW["on"] = True
        fam._DIES_DIR = _P(REAL_DIES)
        dr._config_dir = lambda: _P(REAL_CONFIG)
        dr.store_path = lambda: _P(REAL_CONFIG) / ".duty_results.json"

        # 1. the yaml entry (non-primary) — re-read from disk by upsert_duty
        #    itself, which is why nothing is cached across this boundary.
        spec = fam.DutySpec(
            name=duty, mode=str(d.get("mode") or "motor"),
            current_arms=float(d["current_arms"]), rpm=float(d["rpm"]),
            gamma_deg=float(d["gamma_deg"]), from_current=True,
            mesh=mesh, summary=s, drive="pwm_voltage",
            assignment_sig=sig,
            star_delta=str(d.get("star_delta") or "delta"))
        r = fam.upsert_duty(fam.DutyCreate(die=die, config=cfg, duty=spec),
                            **write_auth(fam.upsert_duty))
        cfg_name = r.get("config") or cfg
        # 2. the waveforms, in the gzip sidecar beside the die.
        payload = {k: v for k, v in tr.items() if k not in HEAVY}
        rr = fam.record_duty_run(
            fam.DutyRunSave(die=die, config=cfg_name, duty=duty,
                            drive="pwm_voltage", settings=mesh, summary=s,
                            assignment_sig=sig, payload=payload),
            **write_auth(fam.record_duty_run))
        log("  SAVED %r/%r drive=%s primary=%s -> %s"
            % (cfg_name, duty, r.get("drive"), r.get("primary"),
               rr.get("payload_file")))

        # 3. the per-duty records (coupled / thermal / em, with their
        #    `drive: pwm` and `reference_sine`) merged into the REAL store —
        #    re-read fresh, THIS duty only, atomically.
        #
        #    …AND THE MECHANICAL ONES (2026-09-16).  `rotor_stress`, `modes`
        #    and `critical_speeds` are written by `routes.mechanical`'s own tail
        #    (`duty_results.note_mechanical`) during the coupled run — into the
        #    SANDBOX, like everything else — and this list used to name
        #    "mechanical", which is not a kind at all.  So four PWM duties ended
        #    up with a PWM thermal row beside ring modes from the sine solve of
        #    two days before: the report's Campbell said "sweep not stored" and
        #    its mode table named no carrier, because the 2026-09-14 records
        #    that carry `campbell` and `f_switch_hz` were the sandbox's and died
        #    with it.
        real = dr.read_all()
        node = (real.setdefault("results", {}).setdefault(die, {})
                .setdefault(cfg_name, {}))
        moved = []
        for kind in ("coupled", "thermal", "em", "pwm",
                     "rotor_stress", "modes", "critical_speeds"):
            if isinstance(mine.get(kind), dict):
                node.setdefault(duty, {})[kind] = mine[kind]
                moved.append(kind)
        if not dr._write_all(real):
            raise MassAbort("could not write %s" % dr.store_path())
        log("  duty_results merged into %s: %r <- %s"
            % (dr.store_path(), duty, ", ".join(moved) or "nothing"))

        # 4. …and the FIELD SIDECARS, which no amount of merging can reach: the
        #    four npz `duty_fields.save_active` wrote from the solve routes'
        #    tails into `<sandbox>/dies/<die>/runs/<cfg>/<stem>/fields/`.  They
        #    are the maps the report draws (Fig. 11's temperature, Fig. 13's
        #    losses, the stress map, the mode gallery); without them the report
        #    falls back to whatever the machine-level pickles hold, which after
        #    a sandboxed campaign is the PREVIOUS solve — 153.9 °C under a
        #    caption belonging to a 175.2 °C run.  Copied verbatim (they are
        #    this very `duty_fields`' output, carrying the solve's own
        #    `computed_at`), atomically, into this die's folder — which the
        #    write guard is already allowing for the length of this section.
        _fields = duty_refile.refile_fields(
            TMP, die, cfg_name, duty, dies_dir=REAL_DIES)
        for _k, _v in sorted((_fields["written"] or {}).items()):
            log("  field %-13s %-9s %8d B -> %s"
                % (_k, _v["how"], _v["bytes"], _v["path"]))
        if _fields["missing"]:
            log("  !! the run filed no %s field — the report will say so "
                "rather than draw the previous solve's"
                % ", ".join(_fields["missing"]))
    finally:
        _ALLOW["on"] = False
        _ALLOW["prefixes"] = ()
        fam._DIES_DIR, dr._config_dir, dr.store_path = (
            _dies_was, _cfgdir_was, _store_was)
        log("  critical section held the real catalog for %.1f s — %d real "
            "write(s): %s" % (time.time() - t0, len(WRITES_PASSED),
                              ", ".join(sorted({os.path.basename(p)
                                                for _h, p in WRITES_PASSED}))))

    # ── and the verdict, re-read from disk, outside the section ─────────────
    faults = list(_mass_faults(s, "the run's own summary"))
    doc = yaml.safe_load(io.open(os.path.join(REAL_DIES, die, cfg_name + ".yaml"),
                                 encoding="utf-8")) or {}
    shaft = str((doc.get("parts") or {}).get("shaft") or "included").lower()
    if shaft != "included":
        faults.append("the configuration's parts: block now says shaft = %r "
                      "(routes/family.py:1683 adopted a live map)" % shaft)
    entry = next((x for x in (doc.get("duties") or [])
                  if isinstance(x, dict) and x.get("name") == duty), None)
    if entry is None:
        faults.append("duty %r is not in %r after the save" % (duty, cfg_name))
    else:
        faults += _mass_faults(entry.get("summary"), "the duty's stored summary")
        for k, run in sorted((entry.get("runs") or {}).items()):
            faults += _mass_faults((run or {}).get("summary"),
                                   "the stored %r run's summary" % k)
    if faults:
        raise MassAbort("the save of %r left the machine mis-weighed: %s"
                        % (duty, "; ".join(faults)))
    log("  mass verified after the save: %s kg total, shaft counted, no part "
        "marked 'reference' anywhere in %r/%r"
        % (EXPECTED_MASS_TOTAL_KG, cfg_name, duty))
    return cfg_name


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def duty_doc(die, cfg, name):
    doc = yaml.safe_load(io.open(os.path.join(REAL_DIES, die, cfg + ".yaml"),
                                 encoding="utf-8")) or {}
    d = next((x for x in (doc.get("duties") or []) if x.get("name") == name), None)
    if d is None:
        raise SystemExit("duty %r is not in %s/%s — known: %s"
                         % (name, die, cfg,
                            ", ".join(repr(x.get("name"))
                                      for x in (doc.get("duties") or []))))
    return d


def one_duty(die, cfg, name, fallback, max_iter, steps=None, mechanical=True,
             save=True, tag=None):
    global EXPECTED_MASS_TOTAL_KG
    tag = tag or TAG
    log("-" * 78)
    d = duty_doc(die, cfg, name)
    log("--- %r @ %.0f Hz — %s, %s, %.1f A, %.0f rpm, γ=%.1f°, max_iter %d%s"
        % (name, CARRIER, d.get("mode"), d.get("star_delta"),
           float(d.get("current_arms") or 0), float(d.get("rpm") or 0),
           float(d.get("gamma_deg") or 0), max_iter,
           ", %d steps/period" % steps if steps else ""))
    # THE MASS THIS DUTY'S OWN ANSWER CARRIES, which the save is asserted
    # against.  Read here, from the duty the campaign names, so the assertion is
    # the machine's number and not one configuration's constant.
    EXPECTED_MASS_TOTAL_KG = ((d.get("summary") or {}).get("mass_total_kg")
                              if isinstance(d.get("summary"), dict) else None)
    if EXPECTED_MASS_TOTAL_KG is None and save and not NO_SAVE:
        raise MassAbort("%r carries no mass_total_kg in its stored summary — "
                        "there is nothing to weigh the save against" % (name,))
    if EXPECTED_MASS_TOTAL_KG is not None:
        EXPECTED_MASS_TOTAL_KG = float(EXPECTED_MASS_TOTAL_KG)
        log("  the machine on the scales: %s kg total (%s kg active), from this "
            "duty's own stored summary"
            % (EXPECTED_MASS_TOTAL_KG,
               (d.get("summary") or {}).get("mass_active_kg")))
    # …and the inverter, before anything is meshed.
    assert_modulation(d, V_DC)
    load_machine(die, cfg, name)
    settings, csrc, want_mode = duty_thermal_settings(
        die, cfg, name, fallback, max_iter)
    log("  cooling from %s -> %s (the map must come back on %r)"
        % (csrc, json.dumps(settings, ensure_ascii=False), want_mode))
    if DRY:
        rid = "dry-%s" % str(name).replace(" ", "_")
        b = body_for(d, CARRIER, max_iter, settings, rid, steps=steps,
                     mechanical=mechanical)
        log("  DRY REQUEST %s" % json.dumps(
            {"drive": b["drive"], "inverter": b["inverter"],
             "max_iter": b["max_iter"], "mechanical": b["mechanical"],
             "rpm": b["rpm"], "gamma_deg": b["gamma_deg"],
             "mode": b["mode"], "I_phase_rms": b["I_phase_rms"],
             "star_delta": b["star_delta"], "n_sectors": b["n_sectors"],
             "mesh_size_mm": b["mesh_size_mm"],
             "thermal_settings": b["thermal_settings"]}, ensure_ascii=False))
        log("  DRY: the point — %.2f A at the terminals, %.2f A in the %s "
            "branch (what `target_I_phase_rms_A` aims at and what "
            "`I_phase_rms_solved_A` reports back), V1 seed %.4f V ∠%.3f° on a "
            "%.1f V link (%s)"
            % (float(d["current_arms"]), winding_current(d),
               str(d.get("star_delta") or "delta"),
               b["inverter"]["v_phase_peak_V"], b["inverter"]["v_delta_deg"],
               V_DC, V_DC_SOURCE))
        # …and the save path's models, built but never called: a wrong field
        # name here is an hour of FEM thrown away when it surfaces at the end.
        fam.DutySpec(name=name, mode=str(d.get("mode") or "motor"),
                     current_arms=float(d["current_arms"]), rpm=float(d["rpm"]),
                     gamma_deg=float(d["gamma_deg"]), from_current=True,
                     mesh=dict(d.get("mesh") or {}), summary={},
                     drive="pwm_voltage", assignment_sig="x",
                     star_delta=str(d.get("star_delta") or "delta"))
        fam.DutyRunSave(die=die, config=cfg, duty=name, drive="pwm_voltage",
                        settings={}, summary={}, assignment_sig="x", payload={})
        log("  DRY: DutySpec and DutyRunSave accept every field the save sends")
        # …and THE SAVE ITSELF, against the SANDBOX copy of the catalog.
        # `upsert_duty` compares the machine now loaded with the configuration
        # on disk, and on a LOCKED configuration — which L180 gen is — any
        # difference is a REFUSAL rather than an adoption.  A build that does
        # not match would therefore throw four hours of FEM away at the very
        # last step, so it is asked here, where the answer costs a second and
        # nothing outside the sandbox can be touched (`fam._DIES_DIR` still
        # points inside it, and the write guard redirects anything that is not).
        try:
            _r = fam.upsert_duty(
                fam.DutyCreate(die=die, config=cfg, duty=fam.DutySpec(
                    name=name, mode=str(d.get("mode") or "motor"),
                    current_arms=float(d["current_arms"]), rpm=float(d["rpm"]),
                    gamma_deg=float(d["gamma_deg"]), from_current=True,
                    mesh=dict(d.get("mesh") or {}),
                    summary=dict(d.get("summary") or {}), drive="pwm_voltage",
                    assignment_sig="dry",
                    star_delta=str(d.get("star_delta") or "delta"))),
                **write_auth(fam.upsert_duty))
            _rr = fam.record_duty_run(
                fam.DutyRunSave(die=die, config=_r.get("config") or cfg,
                                duty=name, drive="pwm_voltage",
                                settings=dict(d.get("mesh") or {}),
                                summary=dict(d.get("summary") or {}),
                                assignment_sig="dry",
                                payload={"computed_at": "dry"}),
                **write_auth(fam.record_duty_run))
            log("  DRY: …and the sidecar too -> %s" % _rr.get("payload_file"))
            log("  DRY: the SANDBOX save is accepted — locked=%s, drive=%s, "
                "primary=%s (the real one will be the same call against the "
                "real catalog)"
                % (bool((yaml.safe_load(io.open(os.path.join(
                    REAL_DIES, die, cfg + ".yaml"), encoding="utf-8")) or {}
                ).get("locked")), _r.get("drive"), _r.get("primary")))
        except Exception as _se:                          # noqa: BLE001
            log("  DRY: !! THE SAVE WOULD BE REFUSED: %r" % (_se,))
            raise
        RESULTS.append({"duty": name, "dry": True})
        dump()
        return {"dry": True}
    out, s, c, inv = coupled_pwm(d, CARRIER, max_iter, settings, want_mode,
                                 steps=steps, mechanical=mechanical, tag=tag)
    row = {"duty": name, "die": die, "config": cfg, "f_carrier_hz": CARRIER,
           "iterations": c.get("iterations"), "converged": c.get("converged"),
           "coil_temp_c": c.get("coil_temp_c"),
           "magnet_temp_c": c.get("magnet_temp_c"),
           "magnet_temp_max_c": c.get("magnet_temp_max_c"),
           "bearing_temp_c": c.get("bearing_temp_c"),
           "efficiency_shaft": c.get("efficiency_shaft"),
           "em": c.get("em"), "inverter": inv,
           "controller": c.get("controller"),
           "cooling_source": csrc, "cooling_mode": want_mode,
           "on_point": inv.get("on_point"),
           "point_error_pct": inv.get("point_error_pct"),
           "ripple_quotable": inv.get("ripple_quotable"),
           "mass_total_kg": (s or {}).get("mass_total_kg"),
           "sf_min": (c.get("mechanical") or {}).get("sf_min"),
           "warning": c.get("warning")}
    if save and not NO_SAVE:
        row["saved_to"] = save_to_real_catalog(die, cfg, name, d, out, s, c)
        row["mass_verified_kg"] = EXPECTED_MASS_TOTAL_KG
    else:
        log("  NOT saved (--no-save): nothing outside the sandbox was written")
    RESULTS.append(row)
    dump()
    return row


rc = 0
try:
    if SELFTEST:
        # THE ISOLATION PROOF, on the cheapest case there is: the L13 joint the
        # user has on screen, 24 steps, one pass, no mechanics, no save.  If a
        # run of HIS machine cannot move a byte under config/, neither can a
        # four-hour Ø200 one.
        _st = STEPS or 24
        # THE CARRIER IS DELIBERATELY LOW.  The route refuses fewer than 4 FEM
        # samples per switching period, and 24 steps against a real 24 kHz
        # carrier is 0.2 — so the self-test uses 4 carriers per electrical
        # period (the smoke run's choice, 2026-09-14), which gives 6 samples
        # per carrier in 24 steps.  Far too coarse to MEASURE a carrier's watts
        # with, and exactly right for proving the plumbing in minutes: the
        # question here is isolation, not resolution.
        _d13 = duty_doc(L13["die"], L13["config"], L13["duty"])
        _geo = yaml.safe_load(io.open(
            os.path.join(REAL_DIES, L13["die"], "die.yaml"),
            encoding="utf-8")).get("geometry") or {}
        _fel = float(_d13["rpm"]) * (float(_geo.get("num_poles") or 28) / 2.0) / 60.0
        CARRIER = round(4.0 * _fel, 3)
        log("=== SELF-TEST: %s / %s / %r, %s steps, max_iter %d, carrier "
            "%.1f Hz (4 per electrical period at f_el %.1f Hz), no save"
            % (L13["die"], L13["config"], L13["duty"], _st, MAX_ITER,
               CARRIER, _fel))
        one_duty(L13["die"], L13["config"], L13["duty"],
                 L13_COOLING_FALLBACK, MAX_ITER, steps=_st,
                 mechanical=False, save=False, tag="pwmbg-selftest")
    else:
        # Both spellings of the sequence: one ';'-separated value, or several
        # values after --duties.  The DEFAULT names belong to the default
        # campaign only — another configuration must say which duties it means,
        # because a duty name that is not in its yaml is a typo, not a default.
        names = ([x.strip() for v in DUTIES for x in str(v).split(";")
                  if x.strip()] if DUTIES
                 else [ONE_DUTY] if ONE_DUTY
                 else ["peak 1x9 mm", "rated 1x9 mm"]
                 if (DIE, CFG) == (DIE_DEFAULT, CFG_DEFAULT) else None)
        if not names:
            raise SystemExit(
                "REFUSING TO RUN: %s / %s is not the default campaign, so name "
                "the duties: --duties \"<rated>\" \"<peak>\"" % (DIE, CFG))
        log("=== CAMPAIGN: %s / %s — %s, %.0f Hz, %.1f V (%s), %d passes each"
            % (DIE, CFG, ", ".join(repr(n) for n in names), CARRIER, V_DC,
               V_DC_SOURCE or "the campaign default", MAX_ITER))
        for n in names:
            try:
                one_duty(DIE, CFG, n, CAMPAIGN_COOLING_FALLBACK, MAX_ITER,
                         steps=STEPS, mechanical=not NO_MECH, save=True)
            except (MassAbort, GeometryMismatch):
                raise
            except Exception as e:                        # noqa: BLE001
                log("!! %r FAILED: %s" % (n, e))
                log(traceback.format_exc())
                RESULTS.append({"duty": n, "error": str(e)})
                dump()
                rc = 1
    log("=== DONE")
except GeometryMismatch as e:
    log("=== ABORTED (wrong machine): %s" % e)
    RESULTS.append({"aborted": str(e), "reason": "geometry_mismatch"})
    dump()
    rc = 8
except MassAbort as e:
    log("=== ABORTED (part accounting): %s" % e)
    RESULTS.append({"aborted": str(e), "reason": "part_state_mass"})
    dump()
    rc = 9
except SystemExit:
    raise
except Exception as e:                                    # noqa: BLE001
    log("=== FAILED: %s" % e)
    log(traceback.format_exc())
    RESULTS.append({"failed": str(e)})
    dump()
    rc = 2
finally:
    # ── THE PROOF ───────────────────────────────────────────────────────────
    # 1. THE POSITIVE one, which the user's own editing cannot confuse: every
    #    write this process aimed at the real config/, and where it went.
    log("-" * 78)
    red = sorted({p for _h, p in WRITES_REDIRECTED})
    log("write guard: %d write(s) aimed at config/ were REDIRECTED into the "
        "sandbox%s" % (len(WRITES_REDIRECTED),
                       (": " + ", ".join(os.path.relpath(p, REAL_CONFIG)
                                         for p in red)) if red else
                       " — the solve never even tried"))
    if WRITES_PASSED:
        log("write guard: %d write(s) were ALLOWED through, all inside the "
            "critical section: %s"
            % (len(WRITES_PASSED),
               ", ".join(sorted({os.path.relpath(p, REAL_CONFIG)
                                 for _h, p in WRITES_PASSED}))))
    else:
        log("write guard: NOTHING was written under config/ by this process")
    # 2. …and the differential one, for what it is worth.  The user is working
    #    in the app on the same files, so a change here is information, not a
    #    verdict — it is reported, never asserted on.
    after = _real_state()
    moved = {k: {"before": BEFORE.get(k), "after": v}
             for k, v in after.items() if BEFORE.get(k) != v}
    gone = [k for k in BEFORE if k not in after]
    if moved or gone:
        log("config/ mtimes that moved meanwhile (this process wrote none of "
            "them unless listed as ALLOWED above — the user is in the app): %s"
            % ", ".join(sorted(os.path.basename(k) for k in moved) +
                        ["%s (gone)" % os.path.basename(k) for k in gone]))
    else:
        log("config/ differential: nothing moved at all — %d files, identical "
            "mtime and size before and after" % len(after))
    RESULTS.append({"isolation": {
        "files_watched": len(after), "changed": moved, "disappeared": gone,
        "writes_redirected": WRITES_REDIRECTED,
        "writes_allowed_through": WRITES_PASSED, "sandbox": TMP}})
    dump()
    log("=== results in %s | sandbox kept at %s" % (RESULTS_PATH, TMP))
sys.exit(rc)
