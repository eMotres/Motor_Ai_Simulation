"""Limits on the paid machine translation of the production module (security review 2026-11-19, A08).

Three limits, all applied inside ``translate.translate_text`` (the one place every translating caller goes through - messages,
proposals, parts, revisions, BOM / package imports and retries), BEFORE the provider is called:

* one text is at most ``PRODUCTION_TRANSLATE_MAX_CHARS`` characters (default 8000);
* each account may have at most ``PRODUCTION_TRANSLATE_DAILY_CHARS_PER_ACCOUNT`` characters translated per UTC day (default 60000) and
  all accounts together ``PRODUCTION_TRANSLATE_DAILY_CHARS_GLOBAL`` (default 600000); the characters are RESERVED before the call
  (persistent, so a restart or a second worker does not reset them) and given back when the provider call fails;
* at most ``PRODUCTION_TRANSLATE_CONCURRENCY`` provider calls at once (default 4).

A refusal raises ``translate.TranslateError`` like any other translation failure: the text is stored with status ``pending`` and the
member can retry later - saving is never blocked.  Set a limit to 0 to switch that limit off.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

_LOCK = threading.Lock()
_SLOTS: Optional[threading.BoundedSemaphore] = None
_SLOTS_N = 0
SLOT_WAIT_S = 5.0


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int((os.environ.get(name) or "").strip() or default))
    except ValueError:
        return default


def max_chars() -> int:
    return _int_env("PRODUCTION_TRANSLATE_MAX_CHARS", 8000)


def per_account_daily() -> int:
    return _int_env("PRODUCTION_TRANSLATE_DAILY_CHARS_PER_ACCOUNT", 60000)


def global_daily() -> int:
    return _int_env("PRODUCTION_TRANSLATE_DAILY_CHARS_GLOBAL", 600000)


def concurrency() -> int:
    return _int_env("PRODUCTION_TRANSLATE_CONCURRENCY", 4)


def account_id() -> str:
    """The account the request runs for (the workspace caller), else ``system`` (scripts, imports run by the server)."""
    try:
        from motor_ai_sim import workspace as W
        who = W.caller() or {}
        return str(who.get("id") or "").strip().lower() or "system"
    except Exception:                                      # noqa: BLE001
        return "system"


def _file() -> Path:
    from motor_ai_sim.production import store
    return store.production_root() / "_translate_budget.json"


def _today(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(d, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp), str(p))


def reserve(chars: int, account: Optional[str] = None, now: Optional[float] = None) -> Optional[str]:
    """Reserve ``chars`` for ``account`` today.  ``None`` = reserved; otherwise the (English) reason it is refused."""
    acct = account or account_id()
    day = _today(now)
    with _LOCK:
        d = _load()
        today = d.get(day) if isinstance(d.get(day), dict) else {"accounts": {}, "total": 0}
        used_a = int((today.get("accounts") or {}).get(acct) or 0)
        used_t = int(today.get("total") or 0)
        cap_a, cap_t = per_account_daily(), global_daily()
        if cap_a and used_a + chars > cap_a:
            return "the daily translation allowance of this account is used up"
        if cap_t and used_t + chars > cap_t:
            return "the daily translation allowance of the server is used up"
        today.setdefault("accounts", {})[acct] = used_a + chars
        today["total"] = used_t + chars
        d = {day: today}                                   # older days are dropped: the file stays one day long
        try:
            _save(d)
        except OSError:
            pass                                           # an unwritable budget file must not stop a translation
    return None


def give_back(chars: int, account: Optional[str] = None, now: Optional[float] = None) -> None:
    """The provider call failed: the reserved characters are not spent."""
    acct = account or account_id()
    day = _today(now)
    with _LOCK:
        d = _load()
        today = d.get(day)
        if not isinstance(today, dict):
            return
        accounts = today.setdefault("accounts", {})
        accounts[acct] = max(0, int(accounts.get(acct) or 0) - chars)
        today["total"] = max(0, int(today.get("total") or 0) - chars)
        try:
            _save({day: today})
        except OSError:
            pass


def check_length(text: str) -> Optional[str]:
    cap = max_chars()
    if cap and len(text) > cap:
        return "the text is longer than %d characters" % cap
    return None


class Slot:
    """A bounded number of provider calls at once: ``with Slot(): ...``; waits a few seconds, then refuses."""

    def __enter__(self):
        global _SLOTS, _SLOTS_N
        n = concurrency()
        if not n:
            self._sem = None
            return self
        with _LOCK:
            if _SLOTS is None or _SLOTS_N != n:
                _SLOTS, _SLOTS_N = threading.BoundedSemaphore(n), n
            self._sem = _SLOTS
        if not self._sem.acquire(timeout=SLOT_WAIT_S):
            raise BusyError("too many translations are running, try again in a moment")
        return self

    def __exit__(self, *exc):
        if self._sem is not None:
            self._sem.release()
        return False


class BusyError(RuntimeError):
    pass
