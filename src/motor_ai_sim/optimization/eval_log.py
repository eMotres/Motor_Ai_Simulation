"""Complete, append-only evaluation log for the optimizer / sweep (schema 2).

Two files per workspace receive evaluation rows:

* ``.opt_dataset.jsonl`` -- EVERY evaluation the optimizer paid for or refused:
  one JSON object per line, written by ``routes.optimization._log_eval``.
* ``.scan_cache.jsonl`` -- the sweep's persistent eval CACHE (``{"k", "v"}``
  lines).  Schema 2 adds an optional ``"m"`` block (provenance) to ok lines and
  failed lines ``{"k", "m"}`` WITHOUT ``"v"``.  A line without a healthy ``"v"``
  is never a cache hit (see :func:`servable_cache_record`).

This module is pure Python (no solver, no FastAPI) so it is unit-testable
without a solve.  Nothing here changes what the optimizer searches or computes;
it only describes it.

Row keys added in schema 2 (all optional for readers; a row without
``schema`` is a schema-1 row and is read as ``status: "ok"``)::

    schema, ts, campaign_id, campaign_kind, stage, status, error_class, error,
    elapsed_s, build{git_sha,version,built_at}, solve{...}, sampling_purpose,
    objective, objective_value, baseline_line{...}, constraints{...}, cfg_fp

``status``: ``ok`` solved and healthy; ``infeasible`` the design was refused
as such (geometry invalid, winding does not fit, guard/mesh-budget refusal);
``failed`` the evaluation did not produce a usable answer (exception, worker
crash, timeout, non-convergence, non-physical result).
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import json
import math
import os
import re
import secrets
import threading
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

SCHEMA_VERSION = 2

STATUS_OK = "ok"
STATUS_INFEASIBLE = "infeasible"
STATUS_FAILED = "failed"
STATUSES = (STATUS_OK, STATUS_INFEASIBLE, STATUS_FAILED)

#: Stage names (``stage`` key).  ``final_resolve`` is inferred from the eval's
#: sampling purpose; the others are set by the worker that knows its phase.
STAGE_FREE = "free_search"
STAGE_SEEDED = "seeded_refinement"
STAGE_FINAL = "final_resolve"
STAGE_SWEEP = "sweep"

_ERROR_MAX = 300


# ── campaign context ─────────────────────────────────────────────────────────
class Campaign:
    """Mutable, thread-shared state of ONE optimisation / sweep run.

    The object is stored in a ``ContextVar``; ``WorkspaceThreadPoolExecutor``
    copies the whole context per submit, so every pool worker of the run sees
    the SAME instance (a ``set_stage`` made by the run's thread is visible to
    its workers).
    """

    def __init__(self, kind: str, campaign_id: Optional[str] = None,
                 stage: Optional[str] = None, objective: Optional[str] = None):
        self.kind = str(kind or "")
        self.id = campaign_id or new_campaign_id(self.kind)
        self.stage = stage
        self.objective = objective
        self.baseline_line: Optional[Dict[str, Any]] = None
        self.limits: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {"id": self.id, "kind": self.kind, "stage": self.stage,
                    "objective": self.objective,
                    "baseline_line": (dict(self.baseline_line)
                                      if self.baseline_line else None),
                    "limits": dict(self.limits)}


_CAMPAIGN: "contextvars.ContextVar[Optional[Campaign]]" = contextvars.ContextVar(
    "motor_ai_sim_eval_campaign", default=None)


def new_campaign_id(kind: str = "") -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    k = re.sub(r"[^a-z0-9]+", "", str(kind or "run").lower()) or "run"
    return "%s-%s-%s" % (k, ts, secrets.token_hex(3))


def current() -> Optional[Campaign]:
    return _CAMPAIGN.get()


@contextlib.contextmanager
def campaign(kind: str, *, campaign_id: Optional[str] = None,
             stage: Optional[str] = None, objective: Optional[str] = None
             ) -> Iterator[Campaign]:
    """Open a campaign for the calling thread (and every pool thread that
    copies its context).  Nested: an inner ``campaign`` is a NEW campaign only
    when none is open -- a worker run inside another keeps the outer id."""
    outer = _CAMPAIGN.get()
    if outer is not None:
        yield outer
        return
    c = Campaign(kind, campaign_id, stage, objective)
    tok = _CAMPAIGN.set(c)
    try:
        yield c
    finally:
        try:
            _CAMPAIGN.reset(tok)
        except (ValueError, RuntimeError):   # reset from another context
            pass


def set_stage(stage: Optional[str]) -> None:
    c = _CAMPAIGN.get()
    if c is not None:
        with c._lock:
            c.stage = stage


def set_objective(name: Optional[str]) -> None:
    c = _CAMPAIGN.get()
    if c is not None:
        with c._lock:
            c.objective = None if name is None else str(name)


def set_baseline_line(bline: Optional[Dict[str, Any]]) -> None:
    """Record the baseline line the ``baseline_line`` objective measures
    against (the dict ``_make_bline`` builds)."""
    c = _CAMPAIGN.get()
    if c is not None and isinstance(bline, dict):
        with c._lock:
            c.baseline_line = {k: _num(v) for k, v in bline.items()
                               if isinstance(v, (int, float))}


def set_limits(**limits: Any) -> None:
    c = _CAMPAIGN.get()
    if c is not None:
        with c._lock:
            c.limits.update({k: _num(v) for k, v in limits.items()
                             if v is not None})


# ── small helpers ────────────────────────────────────────────────────────────
def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _num(v: Any) -> Any:
    """float for finite numbers, None for NaN/inf (strict-JSON safe)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return f if math.isfinite(f) else None


def clean(obj: Any) -> Any:
    """Recursively make ``obj`` strict-JSON safe (non-finite floats -> None,
    numpy scalars/arrays -> python)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "tolist"):
        return clean(obj.tolist())
    if hasattr(obj, "item"):
        return clean(obj.item())
    return str(obj)


_BUILD: Optional[Dict[str, Any]] = None


def build_fingerprint() -> Dict[str, Any]:
    """Build identity the app already exposes (``APP_GIT_SHA`` / ``APP_BUILT_AT``
    env, ``VERSION`` file -- the same fields ``/api/version`` reports)."""
    global _BUILD
    if _BUILD is None:
        version = None
        try:
            from pathlib import Path
            version = (Path(__file__).resolve().parents[3] / "VERSION"
                       ).read_text(encoding="utf-8").strip() or None
        except Exception:                  # noqa: BLE001
            pass
        _BUILD = {"git_sha": os.environ.get("APP_GIT_SHA") or "unknown",
                  "version": version,
                  "built_at": os.environ.get("APP_BUILT_AT")}
    return dict(_BUILD)


# ── error classification ─────────────────────────────────────────────────────
_PATH_RES = (
    re.compile(r"[A-Za-z]:[\\/][^\s'\"<>|]*"),                       # C:\Users\x\..
    re.compile(r"(?<![\w.])/(?:home|Users|srv|root|opt|tmp|var|mnt|app|data)"
               r"(?:/[^\s'\"<>|:]*)?"),                              # /srv/..
)


def sanitize_error(err: Any, limit: int = _ERROR_MAX) -> str:
    """One short line: no stack, no filesystem paths, bounded length."""
    s = str(err or "").strip()
    if not s:
        return ""
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if lines and lines[0].startswith("Traceback"):
        lines = lines[-1:]               # the exception line only
    s = " ".join(lines[:2]) if lines else ""
    for rx in _PATH_RES:
        s = rx.sub("<path>", s)
    return s[:limit]


def classify_error(err: Any, error_class: Optional[str] = None
                   ) -> Tuple[str, str]:
    """``(status, error_class)`` of a failed eval from its message.

    infeasible = the DESIGN was refused (geometry / winding / guard / mesh
    budget).  failed = the evaluation itself did not yield an answer."""
    e = str(err or "").lower()
    if any(t in e for t in ("geometry violation", "not buildable",
                            "infeasible winding", "does not fit",
                            "geometry rejected", "geometry invalid")):
        return STATUS_INFEASIBLE, "geometry_invalid"
    if "mesh budget" in e:
        return STATUS_INFEASIBLE, "mesh_budget"
    if "refused" in e and ("guard" in e or "gate" in e):
        return STATUS_INFEASIBLE, "guard_refused"
    if "unconverged" in e or "not converge" in e or "picard" in e:
        return STATUS_FAILED, "non_convergence"
    if e.strip() == "timeout" or "timed out" in e or e.startswith("timeout"):
        return STATUS_FAILED, "timeout"
    if "non-physical result" in e:
        return STATUS_FAILED, "non_physical"
    if "worker exited with code" in e:
        return STATUS_FAILED, "worker_crash"
    if "source config changed" in e:
        return STATUS_FAILED, "config_changed"
    return STATUS_FAILED, (re.sub(r"[^A-Za-z0-9_]", "", error_class or "")
                           or "exception")


# ── objective / constraints ──────────────────────────────────────────────────
def baseline_distance(res: Dict[str, Any], bline: Dict[str, Any]
                      ) -> Optional[float]:
    """Signed perpendicular distance of (T/mass, eff) ABOVE the baseline line --
    the exact expression ``_descent_cost`` uses for ``F`` (kept in sync by a
    test)."""
    try:
        td = float(res.get("torque_per_mass_Nm_kg", 0.0) or 0.0)
        eff = float(res.get("efficiency", 0.0) or 0.0)
        score = bline["w_td"] * (td - bline["td_a"]) \
            + bline["w_eff"] * (eff - bline["eff_a"])
        return _num(score / (bline.get("norm", 1.0) or 1.0))
    except Exception:                      # noqa: BLE001
        return None


def constraint_verdicts(res: Dict[str, Any], limits: Optional[Dict[str, Any]]
                        ) -> Dict[str, Any]:
    """Verdicts of what the optimizer already knows per eval: ripple gate,
    over-voltage, nonlinear convergence, eddy settle, steady state, demag.
    Mechanics (safety factors) and thermal are NOT computed inside an eval
    (they belong to the final validation), so they are absent, never guessed."""
    limits = limits or {}
    out: Dict[str, Any] = {}
    verdicts = []
    rip = res.get("T_ripple_pct")
    if rip is not None:
        out["ripple_pct"] = _num(rip)
        lim = limits.get("ripple_max_pct")
        if lim is not None and out["ripple_pct"] is not None:
            out["ripple_max_pct"] = lim
            out["ripple_ok"] = bool(out["ripple_pct"] <= lim)
            verdicts.append(out["ripple_ok"])
    vp = res.get("V_peak")
    if vp is not None:
        out["v_peak_v"] = _num(vp)
        lim = limits.get("v_peak_limit_v")
        if lim is not None and lim < 1e8 and out["v_peak_v"] is not None:
            out["v_peak_limit_v"] = lim
            out["v_peak_ok"] = bool(out["v_peak_v"] <= lim)
            verdicts.append(out["v_peak_ok"])
    for k in ("nonlinear_converged", "eddy_settled", "steady_state",
              "demag_settled", "qualified"):
        if isinstance(res.get(k), bool):
            out[k] = res[k]
    if "demag_warning" in res:
        w = res.get("demag_warning")
        out["demag_warning"] = None if not w else sanitize_error(w, 120)
        out["demag_ok"] = not bool(w)
        verdicts.append(out["demag_ok"])
    if out.get("nonlinear_converged") is False:
        verdicts.append(False)
    out["feasible"] = (all(verdicts) if verdicts else None)
    return out


# ── row construction ─────────────────────────────────────────────────────────
def _stage_for(camp: Optional[Dict[str, Any]], sampling_purpose: Optional[str]
               ) -> Optional[str]:
    if sampling_purpose in ("standard", "cogging_quality"):
        return STAGE_FINAL
    return (camp or {}).get("stage")


def build_meta(*, status: str, camp: Optional[Dict[str, Any]],
               sampling_purpose: Optional[str] = None,
               elapsed_s: Optional[float] = None,
               error: Any = None, error_class: Optional[str] = None,
               res: Optional[Dict[str, Any]] = None,
               solve: Optional[Dict[str, Any]] = None,
               pre_solve: bool = False) -> Dict[str, Any]:
    """The schema-2 provenance block shared by both files."""
    camp = camp or {}
    meta: Dict[str, Any] = {
        "schema": SCHEMA_VERSION, "ts": utc_now_iso(),
        "campaign_id": camp.get("id"), "campaign_kind": camp.get("kind"),
        "stage": _stage_for(camp, sampling_purpose),
        "status": status,
        "elapsed_s": (None if elapsed_s is None else round(float(elapsed_s), 3)),
        "build": build_fingerprint(),
        "sampling_purpose": sampling_purpose,
    }
    if solve:
        meta["solve"] = solve
    if pre_solve:
        meta["pre_solve"] = True        # refused before a FEM was started
    obj = camp.get("objective")
    meta["objective"] = obj
    bl = camp.get("baseline_line")
    if status == STATUS_OK and isinstance(res, dict):
        if obj == "baseline_line" and bl:
            meta["objective_value"] = baseline_distance(res, bl)
            meta["baseline_line"] = bl
        else:
            meta["objective_value"] = None
        meta["constraints"] = constraint_verdicts(res, camp.get("limits"))
    else:
        meta["objective_value"] = None
        if status != STATUS_OK:
            msg = sanitize_error(error)
            meta["error"] = msg
            meta["error_class"] = error_class or classify_error(error)[1]
    return clean(meta)


def classify_result(result: Optional[Dict[str, Any]],
                    error_class: Optional[str] = None
                    ) -> Tuple[str, Optional[str]]:
    """``(status, error_class)`` for an ``_subprocess_eval`` payload.
    ``error_class`` (an exception class name) is the fallback class when the
    message matches no known pattern."""
    if isinstance(result, dict) and result.get("ok"):
        return STATUS_OK, None
    r = result if isinstance(result, dict) else {}
    return classify_error(r.get("error"), error_class or r.get("error_class"))


# ── append-only, atomic per line ─────────────────────────────────────────────
_append_lock = threading.Lock()
_nl_checked: set = set()


def _json_default(o: Any) -> Any:
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    try:
        return float(o)
    except Exception:                      # noqa: BLE001
        return str(o)


def append_line(path: str, rec: Dict[str, Any],
                lock: Optional[Any] = None, allow_nan: bool = False) -> None:
    """Append ONE json line with a single ``write`` on an ``O_APPEND``
    descriptor.  Thread-safe in-process (``lock``, else a module lock); across
    processes the O_APPEND single write keeps lines whole.  A file whose last
    line was torn by a crash gets a newline first so the new row is not
    glued to it.

    ``allow_nan=False`` (default, for LOG rows, which are passed through
    :func:`clean` first) refuses NaN/inf so every row is strict JSON.  The scan
    CACHE line carries the solver's result dict verbatim and must keep storing
    exactly what it always did, so ``_store_eval`` passes ``allow_nan=True``."""
    data = (json.dumps(rec, default=_json_default, allow_nan=allow_nan,
                       separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    with (lock if lock is not None else _append_lock):
        if path not in _nl_checked:
            _nl_checked.add(path)
            try:
                with open(path, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    if fh.tell() > 0:
                        fh.seek(-1, os.SEEK_END)
                        if fh.read(1) != b"\n":
                            data = b"\n" + data
            except OSError:
                pass
        fd = os.open(path, flags, 0o644)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
        finally:
            os.close(fd)


# ── readers ──────────────────────────────────────────────────────────────────
def row_status(rec: Dict[str, Any]) -> str:
    """Status of a dataset row; schema-1 rows (no key) are ``ok``."""
    s = rec.get("status") if isinstance(rec, dict) else None
    return s if s in STATUSES else STATUS_OK


def is_ok_row(rec: Dict[str, Any]) -> bool:
    return row_status(rec) == STATUS_OK


def servable_cache_record(rec: Any) -> bool:
    """True only for a scan-cache line that may be served as a cache HIT:
    it has a value dict and is not marked failed / infeasible."""
    if not isinstance(rec, dict) or "k" not in rec:
        return False
    if not isinstance(rec.get("v"), dict):
        return False
    m = rec.get("m")
    if isinstance(m, dict) and m.get("status") in (STATUS_FAILED,
                                                   STATUS_INFEASIBLE):
        return False
    return True


def iter_rows(path: str) -> Iterable[Dict[str, Any]]:
    """Tolerant JSONL reader (skips torn / blank lines)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:              # noqa: BLE001
                continue
            if isinstance(rec, dict):
                yield rec
