"""systemd watchdog heartbeat — the Linux replacement for backend_watchdog.ps1.

WHY
===
``scripts/backend_watchdog.ps1`` exists because a crashed API and a *wedged* API
look identical from outside and need different answers.  ``Restart=always``
covers the crash.  It does not cover the hang: the process is alive, the port is
open, and nothing ever answers.  That is the case the PowerShell watchdog's
240 s ``HangTimeout`` was built for, and dropping it on the way to Linux would
be a quiet regression.

``systemd``'s ``WatchdogSec=`` is the same mechanism, better placed: the service
itself says "still answering" on a timer, and when it stops saying so systemd
kills and restarts it.  This module is the "says so" half.

THE PROMISE OF THIS FILE
------------------------
**With ``NOTIFY_SOCKET`` unset, nothing happens at all.**  :func:`start` reads
the variable, finds nothing, returns ``False`` and does not create a thread, a
socket or a timer.  That is every context but a systemd unit with
``Type=notify``: this workstation's scheduled task, a container (compose uses
``HEALTHCHECK``, not sd_notify), pytest, a CLI run, an optimizer eval
subprocess.  The switch is systemd's, and until systemd sets it this file is
dead weight by construction.

WHAT THE HEARTBEAT ACTUALLY MEASURES
------------------------------------
The **event loop**, and deliberately not the threadpool.

A ticking timer thread would prove only that the interpreter runs — exactly the
thing that is still true of a wedged server.  So each tick schedules a trivial
coroutine on the running loop and waits for it to come back; only a round trip
earns a ``WATCHDOG=1``.  That is the same question ``GET /api/health`` answers
(``api.py`` — an ``async def``, so it is served *on the loop*), asked from
inside, without a socket.

The threadpool is **not** probed, and that is the important half of the design.
Every FEM solve is a sync handler running in that pool, a Ø200 PWM transient
occupies one for 93 minutes, and ``QUEUE_WORKERS`` deliberately keeps several
busy at once.  A watchdog that demanded a free pool thread would kill the server
for doing its job — the textbook way to turn a liveness probe into an outage.
A saturated pool with a live loop is a healthy busy server; a dead loop is the
hang.

    [Unit]/[Service] contract (deploy/systemd/motres-api.service):
        Type=notify        WatchdogSec=300        Restart=always

    systemd sets NOTIFY_SOCKET and WATCHDOG_USEC; we ping at a THIRD of the
    interval, so two consecutive misses are needed before a restart — one slow
    tick under load must never be fatal.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from typing import Optional

log = logging.getLogger(__name__)

__all__ = ["notify", "enabled", "start", "stop"]

#: systemd's own default when WATCHDOG_USEC is absent but NOTIFY_SOCKET is not.
_DEFAULT_INTERVAL_S = 100.0

#: How long a loop round trip may take before this tick counts as a miss.  Well
#: under the interval: a miss must be *reported by silence*, not by a hang here.
_PROBE_TIMEOUT_S = 10.0

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_lock = threading.Lock()


def enabled() -> bool:
    """True only under a systemd unit that asked for notifications."""
    return bool(os.environ.get("NOTIFY_SOCKET", "").strip()) and hasattr(socket, "AF_UNIX")


def notify(state: str) -> bool:
    """Send one datagram to systemd.  ``False`` when there is nobody to tell.

    Never raises: a notification is an aside, and a failed aside must not take
    down the request that happened to trigger it.
    """
    addr = os.environ.get("NOTIFY_SOCKET", "").strip()
    if not addr or not hasattr(socket, "AF_UNIX"):
        return False
    # "@" is systemd's spelling of the abstract namespace, whose real first
    # byte is NUL.  Both forms appear in the wild.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        # SOCK_CLOEXEC is Linux-only; getattr keeps the module importable (and
        # testable) on the Windows box this was written on.
        kind = socket.SOCK_DGRAM | getattr(socket, "SOCK_CLOEXEC", 0)
        with socket.socket(socket.AF_UNIX, kind) as sock:
            sock.settimeout(2.0)
            sock.connect(addr)
            sock.sendall(state.encode("utf-8"))
        return True
    except OSError as exc:                                   # noqa: BLE001
        log.debug("sd_notify %r failed: %s", state, exc)
        return False


async def _probe() -> bool:
    """A no-op that only completes if the event loop is servicing callbacks."""
    await asyncio.sleep(0)
    return True


def _interval_s() -> float:
    raw = os.environ.get("WATCHDOG_USEC", "").strip()
    try:
        usec = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_S
    if usec <= 0:
        return _DEFAULT_INTERVAL_S
    # A THIRD, not a half: two misses in a row before systemd acts.
    return max(1.0, usec / 1e6 / 3.0)


def _beat(loop: asyncio.AbstractEventLoop, interval: float) -> None:
    while not _stop.wait(interval):
        try:
            fut = asyncio.run_coroutine_threadsafe(_probe(), loop)
            fut.result(timeout=_PROBE_TIMEOUT_S)
        except Exception as exc:                             # noqa: BLE001
            # Deliberately SILENT towards systemd: not sending is the signal.
            log.warning("watchdog: event loop did not answer in %.0fs (%s) — "
                        "withholding the heartbeat", _PROBE_TIMEOUT_S, exc)
            continue
        notify("WATCHDOG=1")


def start(loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
    """Announce readiness and begin the heartbeat.  No-op without systemd.

    Called from the API lifespan.  Returns whether anything was started, so the
    caller can log one line and otherwise not care.
    """
    if not enabled():
        return False
    with _lock:
        global _thread
        if _thread is not None and _thread.is_alive():
            return True
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                log.warning("watchdog: no running event loop — heartbeat off")
                return False
        interval = _interval_s()
        notify("READY=1")
        _stop.clear()
        _thread = threading.Thread(target=_beat, args=(loop, interval),
                                   name="sd-watchdog", daemon=True)
        _thread.start()
        log.info("watchdog: sd_notify heartbeat every %.0fs "
                 "(WatchdogSec/3), probing the event loop", interval)
        return True


def stop() -> None:
    """Stop the heartbeat and tell systemd we are going down on purpose."""
    with _lock:
        global _thread
        if _thread is None:
            return
        _stop.set()
        _thread.join(timeout=5.0)
        _thread = None
    notify("STOPPING=1")
