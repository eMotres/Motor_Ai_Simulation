"""The source interface and the registry.

To add a registry or API: subclass :class:`VerificationSource`, give it a ``name``, implement ``applies`` and ``run``, and call
:func:`register` (see ``sources/__init__.py`` for the defaults).  Nothing in the core flow changes.

Contract of a source:

* ``applies(country, data)`` - cheap, no I/O.  ``data`` is the case view (see :func:`motor_ai_sim.verification.service.case_view`).
* ``run(case, ctx)`` - returns a list of :class:`Evidence`.  It never decides a status and never raises on a network problem
  (return ``UNKNOWN``); a real exception is caught by :func:`run_all` and recorded as ``source.error``, which sends the case to a
  human.
* ``advisory = True`` (e.g. the phase-2 LLM research source) - its evidence is capped at "supporting" and can never confirm a
  company: an advisory source gathers cited evidence, it does not decide.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification.evidence import Evidence

log = logging.getLogger(__name__)


class Context:
    """What a source may use: the HTTP client, a per-case cache (so a re-run does not hit the registries again within
    :data:`config.CACHE_S`), the clock.  Tests build one with fakes."""

    def __init__(self, cache: Optional[dict] = None, ttl: float = 0.0, now: Callable[[], float] = time.time, http=None):
        self.cache = cache if cache is not None else {}
        self.ttl = ttl
        self.now = now
        self._http = http
        self._shared_local: dict = {}

    @property
    def http(self):
        return self._http or NET.get_http()

    def cached(self, key: str, fn: Callable[[], list]) -> list:
        """Evidence dicts of ``fn()`` reused while younger than ``ttl``; only answers that were not "unknown" are cached."""
        hit = self.cache.get(key)
        if hit and self.ttl > 0 and self.now() - hit["at"] < self.ttl:
            return [EV.from_dict(d) for d in hit["ev"]]
        ev = fn()
        if self.ttl > 0 and ev and all(e.result != EV.UNKNOWN for e in ev):
            self.cache[key] = {"at": self.now(), "ev": [e.to_dict() for e in ev]}
        return ev

    def shared(self, key: str, fn: Callable[[], object], cache_if: Optional[Callable[[object], bool]] = None) -> object:
        """Share fetched material between sources and source order without serializing it as evidence."""
        if key in self._shared_local:
            return self._shared_local[key]
        hit = self.cache.get("shared:" + key)
        if hit and self.ttl > 0 and self.now() - hit["at"] < self.ttl:
            self._shared_local[key] = hit["value"]
            return hit["value"]
        value = fn()
        self._shared_local[key] = value
        if self.ttl > 0 and value and (cache_if is None or cache_if(value)):
            self.cache["shared:" + key] = {"at": self.now(), "value": value}
        return value


class VerificationSource:
    name = "source"
    advisory = False

    def applies(self, country: str, data: dict) -> bool:
        return True

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        raise NotImplementedError


_REGISTRY: list[VerificationSource] = []
_LOCK = threading.Lock()


def register(source: VerificationSource, *, replace: bool = True) -> VerificationSource:
    """Add a source to the registry (by ``name``; a second source with the same name replaces the first)."""
    with _LOCK:
        for i, s in enumerate(_REGISTRY):
            if s.name == source.name:
                if replace:
                    _REGISTRY[i] = source
                return source
        _REGISTRY.append(source)
    return source


def unregister(name: str) -> None:
    with _LOCK:
        _REGISTRY[:] = [s for s in _REGISTRY if s.name != name]


def registered() -> list[VerificationSource]:
    with _LOCK:
        return list(_REGISTRY)


def _cap_advisory(ev: Evidence) -> Evidence:
    if ev.strength == EV.STRONG:
        ev.strength = EV.SUPPORTING
    ev.registry_grade = False
    if ev.kind == EV.COMPANY and ev.result == EV.PASS:
        ev.result = EV.UNKNOWN
        ev.summary = (ev.summary + " (advisory source: cannot confirm a company)").strip()
    return ev


def run_all(case: dict, ctx: Context, sources: Optional[list[VerificationSource]] = None) -> list[Evidence]:
    """Run every applicable source; an exception in one becomes a ``source.error`` evidence, the others still run."""
    out: list[Evidence] = []
    app = case.get("application") or {}
    country = str(app.get("country") or "").upper()
    for s in (sources if sources is not None else registered()):
        try:
            if not s.applies(country, case):
                continue
            rows = s.run(case, ctx) or []
            out.extend(_cap_advisory(e) if s.advisory else e for e in rows)
        except Exception as e:                                           # noqa: BLE001 - recorded, decided by a human
            log.exception("verification: source %s failed", s.name)
            out.append(Evidence(source=s.name, check=EV.CHECK_SOURCE_ERROR, kind=EV.INFO, result=EV.UNKNOWN,
                                summary="%s: %s" % (type(e).__name__, str(e)[:200])))
    return out
