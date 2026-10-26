"""File storage of one configuration's tests: ``<shared>/motor_tests/<die>/<config>/``.

* ``tests.json``  ``{"settings": {"tests_visible_to_customer": false}, "tests": [record, ...]}``
* ``audit.jsonl`` append-only: every add / edit / delete / settings change (who, when, what, the snapshot)

A sibling of the production store (``<shared>/production``) with the same folder naming, so a die rename moves both
(``die_rename.step_motor_tests``).  Documents are NOT stored here: a test names production documents by id.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict

from motor_ai_sim import json_store
from motor_ai_sim.production.store import _safe_slug

TESTS_FILE = "tests.json"
AUDIT_FILE = "audit.jsonl"


def root() -> Path:
    env = (os.environ.get("MOTRES_MOTOR_TESTS_ROOT") or "").strip()
    if env:
        return Path(env)
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "motor_tests"


def die_dir(die: str) -> Path:
    r = root().resolve()
    p = (r / _safe_slug(die)).resolve()
    if r != p and r not in p.parents:
        raise ValueError("motor tests path outside the root")
    return p


def path_for(die: str, config: str) -> Path:
    r = root().resolve()
    p = (r / _safe_slug(die) / _safe_slug(config)).resolve()
    if r != p and r not in p.parents:
        raise ValueError("motor tests path outside the root")
    return p


def _default() -> Dict[str, Any]:
    return {"die": "", "config": "", "settings": {"tests_visible_to_customer": False}, "tests": []}


def load(die: str, config: str) -> Dict[str, Any]:
    doc = json_store.read_json(path_for(die, config) / TESTS_FILE, default=_default())
    if not isinstance(doc, dict):
        doc = _default()
    doc.setdefault("settings", {"tests_visible_to_customer": False})
    if not isinstance(doc.get("tests"), list):
        doc["tests"] = []
    doc["die"], doc["config"] = die, config
    return doc


def exists(die: str, config: str) -> bool:
    return (path_for(die, config) / TESTS_FILE).is_file()


def mutate(die: str, config: str, fn: Callable[[Dict[str, Any]], Any]) -> Any:
    """Apply ``fn`` to the latest document under the file lock and write it; returns what ``fn`` returned."""
    p = path_for(die, config) / TESTS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {}

    def _m(doc):
        if not isinstance(doc, dict):
            doc = {}
        if not doc:
            doc.update(_default())
        doc.setdefault("settings", {"tests_visible_to_customer": False})
        if not isinstance(doc.get("tests"), list):
            doc["tests"] = []
        doc["die"], doc["config"] = die, config
        result["value"] = fn(doc)
        return doc
    json_store.mutate_json(p, _m, default=_default())
    return result.get("value")


def append_audit(die: str, config: str, entry: Dict[str, Any]) -> None:
    p = path_for(die, config) / AUDIT_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with json_store.lock_for(p):
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line)


def read_audit(die: str, config: str) -> list:
    p = path_for(die, config) / AUDIT_FILE
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
