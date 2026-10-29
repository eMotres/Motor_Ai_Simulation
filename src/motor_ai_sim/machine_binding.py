"""A run belongs to the machine it STARTED on.

Incident 2026-10-29: a 16-point sweep of an L100 machine was running when the
owner loaded another motor (G2-L40).  The six points finished before the load
were fine; seven of the remaining ten were solved on the NEW machine and
appended to ``.opt_dataset.jsonl`` as ``ok`` beside the old machine's points,
and the sweep record (``machine`` block, ``scan_params.cfg_fp``) was stamped
with the machine loaded at the END.  The guard that existed compared the
fingerprint before and after ONE point, so every point that started after the
load saw "no change" and passed.

The rule this module carries: the identity of the machine is captured ONCE, when
the run starts, and every later step compares against that capture - never
against its own start.  A run whose machine changed stops dispatching, and a
point computed after the change is a failed point carrying the reason; it is
never written as ``ok`` anywhere.

Two things are compared:

* the catalogue context (die / configuration) of the caller's workspace -
  ``POST /api/family/activate`` rewrites it;
* the physics fingerprint of the live config (geometry, winding, materials,
  magnet, speed), which also moves when an UNSAVED edit is made in the editor.

Pure and cheap: a fingerprint is microseconds against a multi-minute FEM eval.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

__all__ = ["MachineBinding", "CURRENT", "current", "REASON_PREFIX"]

#: The wording ``optimization.eval_log.classify_result`` keys on ("config_changed").
REASON_PREFIX = "source config changed"

Fingerprint = Callable[[Tuple[str, ...]], str]

#: The binding of the run this thread / pool worker belongs to, or ``None``.
#: ``WorkspaceThreadPoolExecutor`` copies the whole context per submit, so the
#: eval workers of a campaign see the binding the campaign set.
CURRENT: "ContextVar[Optional[MachineBinding]]" = ContextVar(
    "motor_ai_sim_machine_binding", default=None)


def current() -> Optional["MachineBinding"]:
    return CURRENT.get()


def _key(keys: Iterable[str]) -> frozenset:
    return frozenset(str(k) for k in (keys or ()) if k != "gamma_deg")


def loaded_context() -> Dict[str, str]:
    """``{"die", "config"[, "duty"]}`` of the caller's workspace, or ``{}``."""
    try:
        from motor_ai_sim import run_target
        return dict(run_target.active())
    except Exception:                                   # noqa: BLE001
        return {}


class MachineBinding:
    """The machine a run was started on, and the test "is it still that one?".

    ``fingerprint(keys)`` returns the physics fingerprint with the geometry
    keys ``keys`` left out - the keys a candidate OVERRIDES, which the live
    config is allowed to disagree about.  ``what`` names the run in the reason
    ("FEM scan", "optimization", "transient").
    """

    def __init__(self, fingerprint: Optional[Fingerprint] = None, *,
                 what: str = "the run", context: Optional[Dict[str, str]] = None):
        self.what = str(what)
        self._fp: Optional[Fingerprint] = fingerprint
        self._lock = threading.Lock()
        self._fp0: Dict[frozenset, str] = {}
        self.context: Dict[str, str] = (dict(context) if context is not None
                                        else loaded_context())
        self.tripped: Optional[str] = None
        if self._fp is not None:
            self._fp0[frozenset()] = self._safe_fp(())

    # ── capture ──────────────────────────────────────────────────────────────
    def _safe_fp(self, keys: Iterable[str]) -> str:
        try:
            return str(self._fp(tuple(sorted(_key(keys)))))      # type: ignore[misc]
        except Exception:                               # noqa: BLE001 - cannot decide = no change
            return ""

    @property
    def fingerprint_full(self) -> str:
        """The fingerprint of the whole config, as it was when the run started."""
        return self._fp0.get(frozenset(), "")

    def prime(self, keysets: Iterable[Iterable[str]]) -> Optional[str]:
        """Capture the baselines for the override key sets the run will use.

        Call it as the run starts.  Returns the reason when the machine ALREADY
        differs from the capture made at the route (a run that waited in the
        queue), else ``None``."""
        if self._fp is None:
            return None
        why = self._context_changed()
        if why is None and self.fingerprint_full:
            now = self._safe_fp(())
            if now and now != self.fingerprint_full:
                why = self._reason("the configuration was edited")
        if why:
            return self._trip(why)
        with self._lock:
            for ks in keysets:
                k = _key(ks)
                if k not in self._fp0:
                    self._fp0[k] = self._safe_fp(k)
        return None

    def fingerprint_for(self, keys: Iterable[str]) -> str:
        """The start-of-run fingerprint for this override key set (captured on first use)."""
        k = _key(keys)
        with self._lock:
            if k not in self._fp0:
                self._fp0[k] = self._safe_fp(k)
            return self._fp0[k]

    def stamp(self) -> Dict[str, Any]:
        """What to write into the run's record: the machine it was started on."""
        out: Dict[str, Any] = {"fingerprint": self.fingerprint_full}
        out.update({k: v for k, v in self.context.items() if v})
        return out

    # ── test ─────────────────────────────────────────────────────────────────
    def _reason(self, detail: str) -> str:
        return "%s during %s (%s)" % (REASON_PREFIX, self.what, detail)

    def _context_changed(self) -> Optional[str]:
        if not (self.context.get("die") and self.context.get("config")):
            return None
        now = loaded_context()
        if not (now.get("die") and now.get("config")):
            return None                 # released, not replaced: the edit test decides
        if (now["die"].casefold() != self.context["die"].casefold()
                or now["config"].casefold() != self.context["config"].casefold()):
            return self._reason("another machine was loaded: %s / %s" %
                                (now["die"], now["config"]))
        return None

    def _trip(self, why: str) -> str:
        if self.tripped is None:
            self.tripped = why
        return self.tripped

    def poll(self) -> Optional[str]:
        """The cheap half of :meth:`changed`: only the catalogue context (die /
        configuration).  For a supervising loop that has no override key set."""
        if self.tripped:
            return self.tripped
        why = self._context_changed()
        return self._trip(why) if why else None

    def changed(self, keys: Iterable[str] = ()) -> Optional[str]:
        """The reason the machine is no longer the one the run started on, or ``None``.

        Sticky: once tripped, it stays tripped - a machine that was swapped and
        swapped back is still a run that has computed mixed states."""
        if self.tripped:
            return self.tripped
        why = self._context_changed()
        if why:
            return self._trip(why)
        if self._fp is not None:
            base = self.fingerprint_for(keys)
            if base:
                now = self._safe_fp(keys)
                if now and now != base:
                    return self._trip(self._reason("the configuration was edited"))
        return None
