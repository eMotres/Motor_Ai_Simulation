"""Seed NEW power-device cards from the image into the live shared catalogue.

WHY
===
The API image carries ``config/`` as a read-only SEED at ``<app>/config-seed``
(``deploy/Dockerfile.api``); the live cards are read from
``<SHARED_ROOT>/devices`` (:func:`motor_ai_sim.inverter.devices.devices_dir`).
The ``motres-seed`` one-shot builds that tree once, so a card added by a later
PR sat in the image only and the live app answered 422 ``unknown_device`` until
somebody copied it by hand (2026-11-03, 2MBI800XNE120-50).

THE RULES (owner 2026-11-03)
============================
* Copy only a card whose FILE NAME does not exist in the shared folder.
* NEVER overwrite or delete an existing shared card: owner edits and
  runtime-written cards (``write_card``) live there.  A same-named card that
  differs from the seed is left alone and logged as a warning for review.
* The new file takes the owner/mode of the existing shared cards (the process
  runs as ``motres`` uid 10001, so a copy made by it is already ``motres:motres``;
  under root the owner of a sibling card is applied).

The copy is race-safe and idempotent: the card is written to a temporary name
and hard-linked into place, which fails if the target exists, so two API colours
and the worker starting together cannot clobber each other or a card an admin
saved in between.  Only top-level ``*.yaml`` files are cards (the ``spice/``
subfolder holds table-building inputs, not cards).

CALLED FROM the API lifespan (``api.py``) once at boot, in every role, after
the startup checks.  It never raises: a failed seed is a log line, not a dead
server.  Importing the module has no side effects.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

__all__ = ["SeedReport", "seed_new_cards", "seed_new_device_cards", "seed_dir"]


@dataclass
class SeedReport:
    seeded: List[str] = field(default_factory=list)      # file names copied
    differs: List[str] = field(default_factory=list)     # same name, other content: left untouched
    errors: List[str] = field(default_factory=list)


def seed_dir() -> Path:
    """The image's ``config-seed/devices`` (``<app>/config-seed``, one level above ``src/``)."""
    return Path(__file__).resolve().parents[2] / "config-seed" / "devices"


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return True      # cannot compare -> do not claim a difference


def _match_siblings(new: Path, reference: Optional[Path]) -> None:
    """Give ``new`` the mode (and, under root, the owner) of an existing shared card."""
    if reference is None:
        return
    try:
        st = reference.stat()
        os.chmod(new, st.st_mode & 0o777)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(new, st.st_uid, st.st_gid)
    except OSError as exc:
        log.warning("seeded card %s: could not copy mode/owner from %s: %s", new.name, reference.name, exc)


def _place_without_overwrite(src: Path, dest: Path, reference: Optional[Path]) -> bool:
    """Put a copy of ``src`` at ``dest`` unless ``dest`` exists.  ``True`` = created."""
    fd, tmp_name = tempfile.mkstemp(prefix=".seed-", suffix=".tmp", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        _match_siblings(tmp, reference)
        try:
            os.link(tmp, dest)                     # atomic, refuses an existing target
            return True
        except FileExistsError:
            return False
        except OSError:                            # no hard links on this filesystem
            with open(dest, "xb") as out, open(tmp, "rb") as inp:
                shutil.copyfileobj(inp, out)
            _match_siblings(dest, reference)
            return True
    except FileExistsError:
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def seed_new_cards(seed: Path, shared: Path) -> SeedReport:
    """Copy every ``*.yaml`` of ``seed`` whose name is absent from ``shared``.

    Existing files are never modified; a same-named file with other content is
    reported in ``differs`` and logged as a warning.  Safe to call repeatedly.
    """
    rep = SeedReport()
    seed, shared = Path(seed), Path(shared)
    if not seed.is_dir():
        return rep
    if not shared.is_dir():
        log.info("device card seeding skipped: %s is not a directory", shared)
        return rep
    try:
        if seed.resolve() == shared.resolve():
            return rep
    except OSError:
        pass
    existing = sorted(p for p in shared.glob("*.yaml") if p.is_file())
    reference = existing[0] if existing else None
    for src in sorted(p for p in seed.glob("*.yaml") if p.is_file()):
        dest = shared / src.name
        try:
            if dest.exists() or dest.is_symlink():
                if not _same_bytes(src, dest):
                    rep.differs.append(src.name)
                    log.warning("device card %s in %s differs from the image seed %s; left untouched "
                                "(review by hand)", src.name, shared, seed)
                continue
            if _place_without_overwrite(src, dest, reference):
                rep.seeded.append(src.name)
                log.info("seeded device card %s", src.stem)
        except Exception as exc:                   # noqa: BLE001
            rep.errors.append(f"{src.name}: {exc}")
            log.warning("could not seed device card %s into %s: %s", src.name, shared, exc)
    return rep


def seed_new_device_cards() -> SeedReport:
    """Boot hook: seed the image's new cards into ``<SHARED_ROOT>/devices`` (only when that folder exists)."""
    try:
        from motor_ai_sim.workspace import shared_root
        return seed_new_cards(seed_dir(), Path(str(shared_root())) / "devices")
    except Exception as exc:                       # noqa: BLE001
        log.warning("device card seeding failed: %s", exc)
        return SeedReport(errors=[str(exc)])
