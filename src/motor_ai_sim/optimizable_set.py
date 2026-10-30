"""Which geometry parameters Sweep / Optimize / DOE may vary — the workspace's ``sweep_whitelist``.

The list lives in the workspace ``motor_config.yaml`` (key ``sweep_whitelist``) and is READ by the scan / descent / auto
optimiser start routes (``routes/optimization.py``), the screening DOE (``optimization/doe.py``) and the ``optimizable``
flag of ``GET /api/geometry/schema``.  Until this module the only way to change it was to edit the file on the server.

Meaning of the stored list (unchanged, now explicit in every answer):

* a NON-EMPTY list  -> exactly those parameters may vary (plus ``gamma_deg``, the load angle, which is not a geometry knob
  and is always allowed);
* an EMPTY or missing list -> ALL parameters may vary (``all_allowed`` is ``true``).

Writing touches ONE key.  The file is edited as text (the other keys, comments and line endings stay byte-for-byte), the
result is parsed again and compared key by key with the original before it replaces anything, the previous file is copied
to ``.config_backups/`` beside it first, and the swap itself is ``os.replace`` of a temp file in the same folder.  A run
that is already going keeps the list it started with: every consumer reads the list ONCE, when it starts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

log = logging.getLogger(__name__)

KEY = "sweep_whitelist"
#: The load angle is an operating-point knob, not geometry: the scan / descent guards let it through whatever the list says.
ALWAYS_ALLOWED: Tuple[str, ...] = ("gamma_deg",)
BACKUP_DIR = ".config_backups"
_KEEP_BACKUPS = 30
AUDIT_FILE = "optimizable_audit.jsonl"

_KEY_LINE = re.compile(r"^sweep_whitelist\s*:")


class OptimizableError(ValueError):
    """The request cannot be applied.  ``kind`` is a short stable code, ``names`` the offenders (if any)."""

    def __init__(self, kind: str, message: str, names: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.names = list(names or [])


# ─────────────────────────────────────────────────────────────────────────────
#  Reading
# ─────────────────────────────────────────────────────────────────────────────

def _is_string_param(meta: Any) -> bool:
    try:
        return str(dict(meta).get("type", "float")) == "string"
    except (TypeError, ValueError):
        return False


def schema_names(schema: Dict[str, Any]) -> List[str]:
    """Every parameter the Geometry table can show, in schema order."""
    return [str(n) for n in schema]


def universe(schema: Dict[str, Any]) -> List[str]:
    """The parameters that CAN vary: numeric schema entries (a word-valued knob is never a sweep variable)."""
    return [str(n) for n, m in schema.items() if not _is_string_param(m)]


def describe(stored: Optional[Iterable[Any]], schema: Dict[str, Any]) -> Dict[str, Any]:
    """The answer both routes give.

    ``optimizable``   the effective set, schema order (every numeric parameter when ``all_allowed``);
    ``stored``        what the file holds, as is (so a stale name nobody can toggle any more is still visible);
    ``all_allowed``   the file's list is empty/missing, i.e. nothing is restricted;
    ``always_allowed`` parameters the guards let through whatever the list says.
    """
    names = [str(n) for n in (stored or [])]
    all_allowed = not names
    uni = universe(schema)
    chosen = set(names)
    eff = list(uni) if all_allowed else [n for n in uni if n in chosen]
    return {
        "optimizable": eff,
        "stored": names,
        "all_allowed": all_allowed,
        "always_allowed": list(ALWAYS_ALLOWED),
        "known": schema_names(schema),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Computing the new list
# ─────────────────────────────────────────────────────────────────────────────

def _clean(names: Any, field: str) -> List[str]:
    if names is None:
        return []
    if isinstance(names, (str, bytes)) or not isinstance(names, (list, tuple)):
        raise OptimizableError("not_a_list", f"'{field}' must be a list of parameter names")
    out: List[str] = []
    for n in names:
        if not isinstance(n, str) or not n.strip():
            raise OptimizableError("bad_name", f"'{field}' holds a value that is not a parameter name", [repr(n)])
        n = n.strip()
        if n not in out:
            out.append(n)
    return out


def _check_known(names: Iterable[str], schema: Dict[str, Any]) -> None:
    known = set(schema_names(schema))
    bad = [n for n in names if n not in known]
    if bad:
        raise OptimizableError(
            "unknown_parameter",
            "not a geometry parameter of this machine: " + ", ".join(bad), bad)
    words = [n for n in names if _is_string_param(schema.get(n))]
    if words:
        raise OptimizableError(
            "not_numeric", "a word-valued parameter cannot be varied: " + ", ".join(words), words)


def compute(body: Dict[str, Any], stored: Optional[Iterable[Any]], schema: Dict[str, Any]) -> List[str]:
    """The list to store for ``body``.

    ``{"names": [...]}``                 replace the set; ``[]`` is the explicit "all allowed";
    ``{"enable": [...], "disable": [...]}``  change a few against what is effective now (race-free for two quick toggles).
    ``gamma_deg`` is accepted and ignored (it is always allowed).  An unknown or word-valued name is refused, and a delta
    that would leave NOTHING enabled is refused (an empty list would mean the opposite).
    """
    if not isinstance(body, dict):
        raise OptimizableError("bad_body", "the body must be a JSON object")
    has_names = "names" in body
    has_delta = "enable" in body or "disable" in body
    if has_names == has_delta:
        raise OptimizableError("bad_body", "send either 'names' or 'enable' / 'disable'")
    drop = set(ALWAYS_ALLOWED)
    if has_names:
        names = [n for n in _clean(body.get("names"), "names") if n not in drop]
        _check_known(names, schema)
        uni = universe(schema)
        # keep the schema's order: the file reads the same way the table does
        return [n for n in uni if n in set(names)] if names else []
    enable = [n for n in _clean(body.get("enable"), "enable") if n not in drop]
    disable = [n for n in _clean(body.get("disable"), "disable") if n not in drop]
    _check_known(enable + disable, schema)
    both = [n for n in enable if n in disable]
    if both:
        raise OptimizableError("conflict", "a parameter is both enabled and disabled: " + ", ".join(both), both)
    current = set(describe(stored, schema)["optimizable"])
    current |= set(enable)
    current -= set(disable)
    result = [n for n in universe(schema) if n in current]
    if not result:
        raise OptimizableError(
            "would_empty",
            "at least one parameter must stay optimizable (an empty list would mean every parameter is allowed)")
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Writing one key
# ─────────────────────────────────────────────────────────────────────────────

def _is_continuation(line: str) -> bool:
    """A line that still belongs to a top-level key's value: indented, or a ``- `` list item written at column 0."""
    return line[:1] in (" ", "\t") or line.startswith("- ") or line.rstrip("\r\n") == "-"


def replace_key_text(text: str, names: List[str]) -> str:
    """``text`` with the ``sweep_whitelist`` value replaced by ``names`` and NOTHING else changed."""
    nl = "\r\n" if "\r\n" in text else "\n"
    block = yaml.safe_dump({KEY: list(names)}, default_flow_style=False, allow_unicode=True, sort_keys=False)
    block = block.replace("\n", nl)
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if _KEY_LINE.match(ln)), None)
    if start is None:
        if text and not text.endswith(("\n", "\r")):
            text += nl
        return text + block
    end = start + 1
    last = start
    while end < len(lines):
        ln = lines[end]
        if ln.strip() == "":
            end += 1
            continue
        if _is_continuation(ln):
            last = end
            end += 1
            continue
        break
    return "".join(lines[:start]) + block + "".join(lines[last + 1:])


def _verify(old_text: str, new_text: str, names: List[str]) -> None:
    """Parse both and prove only ``sweep_whitelist`` differs.  Raises before anything is written."""
    old = yaml.safe_load(old_text)
    new = yaml.safe_load(new_text)
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise OptimizableError("config_unreadable", "the workspace config is not a YAML mapping")
    rest_old = {k: v for k, v in old.items() if k != KEY}
    rest_new = {k: v for k, v in new.items() if k != KEY}
    if rest_old != rest_new:
        raise OptimizableError("write_refused", "editing the list would have changed another key; nothing was written")
    got = new.get(KEY) or []
    if list(got) != list(names):
        raise OptimizableError("write_refused", "the list did not read back as written; nothing was written")


def _backup(path: Path) -> Optional[Path]:
    try:
        d = path.parent / BACKUP_DIR
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        dest = d / f"{path.stem}_{stamp}{path.suffix}"
        shutil.copy2(path, dest)
        olds = sorted(d.glob(f"{path.stem}_*{path.suffix}"))[:-_KEEP_BACKUPS]
        for old in olds:
            try:
                old.unlink()
            except OSError:
                pass
        return dest
    except OSError as exc:
        raise OptimizableError("backup_failed", f"could not back the config up first ({exc}); nothing was written")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        last: Optional[OSError] = None
        for _ in range(8):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:          # Windows: a reader has the file open for a moment
                last = exc
                time.sleep(0.05)
        raise last or OSError("replace failed")
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_stored(path: Path) -> List[str]:
    """The stored list straight from the file (never the cached config: the answer must be what a restart would see)."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get(KEY) if isinstance(data, dict) else None
    return [str(n) for n in (raw or [])]


def write(path: Path, names: List[str]) -> Dict[str, Any]:
    """Replace the list in ``path`` (atomic, backed up, verified).  Returns ``{"before": [...], "after": [...], "backup": str}``."""
    path = Path(path)
    with open(path, "r", encoding="utf-8", newline="") as f:
        old_text = f.read()
    before = read_stored(path)
    if set(before) == set(names):                       # same set (order is cosmetic): nothing to write, nothing to keep
        return {"before": before, "after": before, "backup": None, "unchanged": True}
    new_text = replace_key_text(old_text, names)
    _verify(old_text, new_text, names)
    if new_text == old_text:
        return {"before": before, "after": list(names), "backup": None, "unchanged": True}
    backup = _backup(path)
    _atomic_write(path, new_text)
    return {"before": before, "after": list(names), "backup": str(backup) if backup else None, "unchanged": False}


# ─────────────────────────────────────────────────────────────────────────────
#  Audit
# ─────────────────────────────────────────────────────────────────────────────

def audit(actor: str, config_file: Path, before: List[str], after: List[str], client: str = "") -> None:
    """One JSONL line per change, beside the geometry audit.  Never raises (an audit is not a reason to refuse a save)."""
    try:
        from motor_ai_sim.audit import audit_path
        path = audit_path().parent / AUDIT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        b, a = set(before), set(after)
        line = {
            "t": datetime.now().astimezone().isoformat(timespec="seconds"),
            "actor": actor,
            "config": str(config_file),
            "pid": os.getpid(),
            "added": sorted(a - b),
            "removed": sorted(b - a),
            "all_allowed_after": not after,
            "count_after": len(after),
            "client": client,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:                                   # noqa: BLE001
        log.debug("optimizable audit not written", exc_info=True)
