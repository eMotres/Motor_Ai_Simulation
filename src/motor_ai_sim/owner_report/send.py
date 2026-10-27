"""The ONLY module of the owner report that reaches SMTP, plus the once-a-day marker and lock.

* **Recipients are locked to the owner.**  ``recipients()`` starts from ``OWNER_REPORT_TO`` (default: the admin address the NDA
  copies already go to, else the address of the visitor-request notices) and KEEPS only an address that is an administrator of
  this installation (``ADMIN_EMAILS``, an enabled admin account, the notice / NDA admin address).  An address of anybody else is
  dropped with a warning; if nothing is left the default owner address is used.  ``send_report`` takes no recipient argument.
* **Once a day.**  ``<shared>/owner_report/state.json`` keeps ``last_sent_date``; ``run.run_daily`` skips a date that was sent and
  holds ``run.lock`` (created exclusively, stale after 30 min) while it works, so two processes, a restart or the timer firing
  again after a failure never send a second copy.
"""
from __future__ import annotations

import logging
import os
import time
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Callable, Optional

from motor_ai_sim import json_store

log = logging.getLogger(__name__)

ENV_TO = "OWNER_REPORT_TO"
LOCK_STALE_S = 1800.0


# -- recipients ----------------------------------------------------------------------------------------------------------------------

def _split(raw: str) -> list:
    return [a.strip().lower() for a in str(raw or "").replace(";", ",").split(",") if a.strip()]


def default_owner() -> str:
    from motor_ai_sim import nda as N
    from motor_ai_sim import notify
    return (N.admin_address() or notify.DEFAULT_NOTIFY_TO).strip().lower()


def allowed_addresses() -> set:
    """The addresses that may ever receive the report: administrators of this installation."""
    out = {default_owner()}
    try:
        from motor_ai_sim import auth as A
        out |= {e.lower() for e in A._ADMIN_EMAILS}
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from motor_ai_sim import notify
        out |= {a.lower() for a in notify.recipients()}
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from motor_ai_sim import users as U
        for email, rec in U._load_soft().items():
            if rec.get("role") == "admin" and not rec.get("disabled"):
                out.add(str(email).lower())
    except Exception:                                        # noqa: BLE001
        pass
    return {a for a in out if a}


def recipients() -> list:
    configured = _split(os.environ.get(ENV_TO, "")) or [default_owner()]
    allowed = allowed_addresses()
    ok = [a for a in configured if a in allowed]
    for a in configured:
        if a not in allowed:
            log.warning("owner_report: %s is not an administrator address - not used (OWNER_REPORT_TO is for the owner only)", a)
    return ok or [default_owner()]


# -- state and lock ------------------------------------------------------------------------------------------------------------------

def root() -> Path:
    env = (os.environ.get("MOTRES_OWNER_REPORT_ROOT") or "").strip()
    if env:
        return Path(env)
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "owner_report"


def state_file() -> Path:
    return root() / "state.json"


def get_state() -> dict:
    d = json_store.read_json(state_file(), {})
    return d if isinstance(d, dict) else {}


def set_state(**fields) -> dict:
    root().mkdir(parents=True, exist_ok=True)

    def mutate(doc):
        doc.update(fields)

    return json_store.mutate_json(state_file(), mutate, default={})


class RunLock:
    """An exclusive lock file for one daily run (works on Windows and Linux; a crashed holder expires after 30 minutes)."""

    def __init__(self, now: Optional[float] = None):
        self.path = root() / "run.lock"
        self.now = time.time() if now is None else now
        self.held = False

    def __enter__(self) -> "RunLock":
        root().mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                return self
            with os.fdopen(fd, "w") as fh:
                fh.write(f"{os.getpid()} {self.now}\n")
            self.held = True
            return self
        return self

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


# -- the message -----------------------------------------------------------------------------------------------------------------------

def build_message(report, to: list) -> EmailMessage:
    from motor_ai_sim import auth_email as E
    sender = E.mail_from() or "noreply@aerostator.com"
    msg = EmailMessage(policy=policy.default.clone(max_line_length=998))
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Subject"] = report.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(sender.split("@")[-1].strip("> ") or "aerostator.com"))
    msg["X-AeroStator-Internal"] = "owner-report"
    msg.set_content(report.text)
    msg.add_alternative(report.html, subtype="html")          # text + HTML only: no images, no attachments
    return msg


def send_report(report, *, deliver: Optional[Callable[[EmailMessage], None]] = None) -> dict:
    """Mail the report to the owner (``recipients()``) and nobody else.  Returns ``{sent, to, error}``; never raises."""
    from motor_ai_sim import auth_email as E
    to = recipients()
    if deliver is None and not E.smtp_configured():
        return {"sent": False, "to": to, "error": "smtp_not_configured"}
    try:
        msg = build_message(report, to)
        if set(_split(msg["To"])) - allowed_addresses():     # belt and braces: the header is the last word
            return {"sent": False, "to": to, "error": "recipient_not_allowed"}
        (deliver or E.deliver)(msg)
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: send failed (%s: %s)", type(exc).__name__, str(exc)[:160])
        return {"sent": False, "to": to, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return {"sent": True, "to": to, "error": ""}
