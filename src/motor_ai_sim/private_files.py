"""Owner-only file modes for everything that holds personal data.

Audit 2026-09-29 finding #9: the identity directory was 0700 but the files in
it (users.json, .sessions.json, mcp_audit.jsonl, newsletter.json,
agent_keys.json) were 0644, logs were 0644 in a 0755 directory, workspaces
0755.  One uid runs the API, so the exposure is "any other local account", but
a mode is cheap and a leak is not.

Two layers, both here:

* :func:`apply_process_umask` sets ``umask 077`` once for the API process, so
  every file the process creates from then on — including ones written by
  libraries we do not control (the rotating log handler, sqlite) — is born
  0600 / 0700.
* :func:`chmod_private` / :func:`ensure_private_dir` / :func:`open_private`
  set the mode EXPLICITLY where an identity/log/workspace file is created, so
  the guarantee does not depend on who imported what first (a CLI, a test, a
  worker process that never imported ``api``).

Every helper is a no-op-on-failure: a chmod that the filesystem refuses
(Windows, a bind mount owned by another uid) must never turn into a failed
sign-in.  On Windows ``os.chmod`` only toggles the read-only bit, so these
calls are harmless there.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO, Union

log = logging.getLogger(__name__)

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
PROCESS_UMASK = 0o077

PathLike = Union[str, os.PathLike]


def apply_process_umask() -> int:
    """``umask 077`` for this process.  Returns the previous mask.

    Skipped when ``MOTOR_AI_KEEP_UMASK=1`` (an operator who shares a results
    folder with a group on purpose).
    """
    if os.environ.get("MOTOR_AI_KEEP_UMASK", "").strip() in ("1", "true", "yes"):
        return -1
    try:
        return os.umask(PROCESS_UMASK)
    except Exception:                                       # noqa: BLE001
        return -1


def chmod_private(path: PathLike) -> None:
    """0600 for a file, 0700 for a directory.  Never raises."""
    try:
        p = Path(path)
        if p.is_symlink():
            return                      # never chmod through a link
        os.chmod(p, PRIVATE_DIR_MODE if p.is_dir() else PRIVATE_FILE_MODE)
    except Exception:                                       # noqa: BLE001
        pass


def ensure_private_dir(path: PathLike) -> Path:
    """``mkdir -p`` with mode 0700 on the leaf, then enforce it (mkdir's mode is
    filtered by the umask and ignored for a directory that already exists)."""
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    except Exception:                                       # noqa: BLE001
        pass
    chmod_private(p)
    return p


def open_private(path: PathLike, mode: str = "a", encoding: str = "utf-8") -> IO:
    """``open`` that creates the file 0600 (O_CREAT with an explicit mode).

    Text modes only (``a``, ``w``); the file mode is re-applied after open so a
    file that already existed with 0644 is tightened on the next write.
    """
    p = Path(path)
    flags = os.O_WRONLY | os.O_CREAT
    if "a" in mode:
        flags |= os.O_APPEND
    elif "w" in mode:
        flags |= os.O_TRUNC
    else:
        raise ValueError(f"open_private supports 'a' and 'w', not {mode!r}")
    if hasattr(os, "O_BINARY"):         # Windows: the io layer translates newlines once
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(p), flags, PRIVATE_FILE_MODE)
    chmod_private(p)
    return os.fdopen(fd, mode, encoding=encoding)


def write_private_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text``, the new file being 0600."""
    with open_private(path, "w", encoding=encoding) as f:
        f.write(text)
