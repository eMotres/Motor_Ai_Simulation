"""Stable ids and explicit links beside the existing stores (unified object model, steps 1-2; coordinator 2026-11-17).

The stores keep their names as keys (a die folder, a configuration file, a project id, a production folder).  This module
gives every object a permanent opaque id and records the links between them, WITHOUT inserting anything into an existing
YAML / JSON file: one sidecar ``<shared root>/object_registry/registry.json`` (override ``MOTRES_OBJECT_REGISTRY_ROOT``),
written only through :func:`backfill` / :func:`apply_rename` under the store's lock (``json_store.mutate_json``).  Every
other part of the application only READS it.  When links multiply this file moves to a relational table; the functions
here keep their signatures.

Objects (``kind`` / id prefix, the locator that finds today's source):

=========  ======  ==============================================================
family     fam_    a die (``catalog``: die name) or an electronics platform (``production``: the die name of an electronics production)
item       itm_    a motor configuration (``catalog``: die/config) or a board revision (``production``: die/config)
project    prj_    a project (``projects``: project id)
production prd_    a production (``production``: die/config)
=========  ======  ==============================================================

Links (``kind``): ``project_item`` (from ``linked_motors``: status current / trial; from ``motor_history``: unlinked),
``production_item`` (a production makes the item of its die/config), ``analysis_controller`` (the controller catalogue entry a
motor configuration is analysed with: an analysis setup, NOT proof of a board pairing, so ``confirmed`` is false).

Backfill is idempotent: a second run over the same sources mints nothing and changes nothing; an object is never deleted (a
source that disappeared is marked ``missing_since``).  What cannot be decided from the data is NOT guessed: it goes into
``review`` (codes in :data:`REVIEW_CODES`) for the owner.  Locators are exact, case-sensitive names; two names that differ
only by case are a review item, never merged.

CLI (a dry run writes only the report)::

    python -m motor_ai_sim.object_registry backfill --catalog DIR --projects DIR --production DIR \
        [--registry DIR] [--dry-run] [--report FILE]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from motor_ai_sim import json_store

VERSION = 1
PREFIX = {"family": "fam", "item": "itm", "project": "prj", "production": "prd"}
REVIEW_CODES = {
    "link_to_missing_motor": "a project links a die / configuration that is in no store",
    "production_without_item": "a production names a die / configuration that is not in the catalog",
    "case_collision": "two names differ only by upper / lower case",
    "duplicate_link": "a project lists the same motor twice with different statuses",
    "electronics_and_catalog": "a die / configuration is both a catalog motor and an electronics production",
}


def registry_root() -> Path:
    env = (os.environ.get("MOTRES_OBJECT_REGISTRY_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "object_registry"


def store_path(root: Optional[Path] = None) -> Path:
    return (root or registry_root()) / "registry.json"


def _empty() -> Dict[str, Any]:
    return {"version": VERSION, "objects": {}, "links": {}, "review": [], "backfills": []}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mint(kind: str, taken: Dict[str, Any]) -> str:
    while True:
        oid = f"{PREFIX[kind]}_{secrets.token_hex(6)}"
        if oid not in taken:
            return oid


# ── reading (the only thing the rest of the application does) ─────────────────────────────────────────────────────────

def load(root: Optional[Path] = None) -> Dict[str, Any]:
    data = json_store.read_json(store_path(root), default=None)
    if not isinstance(data, dict):
        return _empty()
    for key, default in _empty().items():
        if not isinstance(data.get(key), type(default)):
            data[key] = default
    return data


class Index:
    """Fast lookups over one loaded registry document."""

    def __init__(self, doc: Dict[str, Any]):
        self.doc = doc
        self.by_locator: Dict[Tuple[str, str, str], str] = {}
        for oid, obj in doc.get("objects", {}).items():
            loc = obj.get("locator") or {}
            self.by_locator[(obj.get("kind", ""), loc.get("ns", ""), loc.get("key", ""))] = oid
            for alias in obj.get("aliases") or []:
                self.by_locator.setdefault((obj.get("kind", ""), loc.get("ns", ""), alias), oid)
        self.links_from: Dict[str, List[dict]] = {}
        self.links_to: Dict[str, List[dict]] = {}
        for link in doc.get("links", {}).values():
            self.links_from.setdefault(link.get("from", ""), []).append(link)
            self.links_to.setdefault(link.get("to", ""), []).append(link)

    def resolve(self, kind: str, ns: str, key: str) -> Optional[str]:
        return self.by_locator.get((kind, ns, key))

    def obj(self, oid: str) -> Optional[dict]:
        return self.doc.get("objects", {}).get(oid)


def index(root: Optional[Path] = None) -> Index:
    return Index(load(root))


# ── the sources (read-only) ───────────────────────────────────────────────────────────────────────────────────────────

def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _visible_dirs(root: Path) -> List[Path]:
    try:
        return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def scan_catalog(dies_root: Path) -> Dict[str, Dict[str, Any]]:
    """``{die: {"configs": {config: {"controller": catalogue id or ""}}}}`` from ``<root>/<die>/die.yaml`` + ``<config>.yaml``."""
    out: Dict[str, Dict[str, Any]] = {}
    for d in _visible_dirs(dies_root):
        if not (d / "die.yaml").is_file():
            continue
        configs = {}
        for f in sorted(d.glob("*.yaml")):
            if f.name == "die.yaml" or f.name.startswith("."):
                continue
            configs[f.stem] = {"controller": _controller_of(f)}
        out[d.name] = {"configs": configs}
    return out


def _controller_of(cfg_file: Path) -> str:
    try:
        import yaml
        doc = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    except Exception:                                       # noqa: BLE001 - an unreadable config has no controller
        return ""
    ctl = doc.get("controller") if isinstance(doc, dict) else None
    if isinstance(ctl, dict):
        return str(ctl.get("catalog_id") or ctl.get("id") or "")
    return ""


def scan_projects(projects_root: Path) -> List[Dict[str, Any]]:
    out = []
    for d in _visible_dirs(projects_root):
        p = _read_json_file(d / "project.json")
        if isinstance(p, dict) and p.get("id") == d.name:
            out.append(p)
    return out


def scan_productions(production_root: Path) -> List[Dict[str, Any]]:
    out = []
    for d in _visible_dirs(production_root):
        for c in _visible_dirs(d):
            rec = _read_json_file(c / "production.json")
            if isinstance(rec, dict) and rec.get("die") and rec.get("config"):
                out.append(rec)
    return out


# ── backfill ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _case_collisions(names: Iterable[str]) -> List[List[str]]:
    seen: Dict[str, List[str]] = {}
    for n in names:
        seen.setdefault(n.casefold(), []).append(n)
    return [sorted(v) for v in seen.values() if len(v) > 1]


def plan(doc: Dict[str, Any], catalog: Dict[str, Dict[str, Any]], projects: List[dict], productions: List[dict],
         *, at: Optional[str] = None) -> Dict[str, Any]:
    """Apply the sources to ``doc`` IN PLACE (mint missing ids, add missing links, refresh review); returns the report.

    Pure apart from the id minting: no file is read or written here."""
    at = at or _now()
    for key, default in _empty().items():
        if not isinstance(doc.get(key), type(default)):
            doc[key] = default
    objects, links = doc["objects"], doc["links"]
    idx = Index(doc)
    report: Dict[str, Any] = {"minted": {k: 0 for k in PREFIX}, "links_added": 0, "links_updated": 0, "missing": 0}
    review: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def ensure(kind: str, ns: str, key: str, **attrs: Any) -> str:
        oid = idx.resolve(kind, ns, key)
        if oid is None:
            oid = _mint(kind, objects)
            objects[oid] = {"id": oid, "kind": kind, "locator": {"ns": ns, "key": key}, "aliases": [],
                            "created_at": at, **attrs}
            idx.by_locator[(kind, ns, key)] = oid
            report["minted"][kind] += 1
        else:
            obj = objects[oid]
            for k, v in attrs.items():
                if obj.get(k) != v:
                    obj[k] = v
            obj.pop("missing_since", None)
        seen_ids.add(oid)
        return oid

    def link(kind: str, frm: str, to: str, **attrs: Any) -> None:
        lid = f"{kind}:{frm}:{to}"
        cur = links.get(lid)
        if cur is None:
            links[lid] = {"id": lid, "kind": kind, "from": frm, "to": to, "created_at": at, **attrs}
            report["links_added"] += 1
        elif any(cur.get(k) != v for k, v in attrs.items()):
            cur.update(attrs)
            cur["updated_at"] = at
            report["links_updated"] += 1

    # families and items of the catalog
    item_of: Dict[Tuple[str, str], str] = {}
    for die, d in catalog.items():
        fam = ensure("family", "catalog", die, family_kind="die", name=die)
        for cfg, info in d["configs"].items():
            iid = ensure("item", "catalog", f"{die}/{cfg}", item_kind="motor", family=fam, die=die, config=cfg)
            item_of[(die, cfg)] = iid
            if info.get("controller"):
                link("analysis_controller", iid, f"controller_catalog:{info['controller']}", confirmed=False)
        for group in _case_collisions(d["configs"]):
            review.append({"code": "case_collision", "where": f"catalog/{die}", "names": group})
    for group in _case_collisions(catalog):
        review.append({"code": "case_collision", "where": "catalog", "names": group})

    # productions; an electronics production defines its own platform family and board item
    for rec in productions:
        die, cfg = str(rec["die"]), str(rec["config"])
        prd = ensure("production", "production", f"{die}/{cfg}", die=die, config=cfg,
                     product_type=str(rec.get("product_type") or "motor"))
        iid = item_of.get((die, cfg))
        if rec.get("product_type") == "electronics":
            if iid is not None:
                review.append({"code": "electronics_and_catalog", "where": f"{die}/{cfg}"})
            else:
                fam = ensure("family", "production", die, family_kind="platform", name=die)
                iid = ensure("item", "production", f"{die}/{cfg}", item_kind="board", family=fam, die=die, config=cfg)
                item_of[(die, cfg)] = iid
        if iid is None:
            review.append({"code": "production_without_item", "where": f"{die}/{cfg}", "production": prd})
            continue
        link("production_item", prd, iid)

    # projects and their motors
    for p in projects:
        pid = str(p["id"])
        prj = ensure("project", "projects", pid, title=str(p.get("title") or ""))
        statuses: Dict[Tuple[str, str], set] = {}
        for m in p.get("linked_motors") or []:
            if isinstance(m, dict) and m.get("die") and m.get("config"):
                statuses.setdefault((str(m["die"]), str(m["config"])), set()).add(str(m.get("status") or "current"))
        linked = set(statuses)
        for row in p.get("motor_history") or []:          # the stage-3 ledger in project.json (the audit log is not read)
            if isinstance(row, dict) and row.get("die") and row.get("config") and row.get("unlinked_at"):
                key = (str(row["die"]), str(row["config"]))
                if key not in linked:
                    statuses.setdefault(key, set()).add("unlinked")
        for (die, cfg), sts in statuses.items():
            iid = item_of.get((die, cfg))
            if iid is None:
                review.append({"code": "link_to_missing_motor", "where": f"{pid} -> {die}/{cfg}", "project": prj})
                continue
            if len(sts) > 1:
                review.append({"code": "duplicate_link", "where": f"{pid} -> {die}/{cfg}", "statuses": sorted(sts)})
            status = "current" if "current" in sts else sorted(sts)[0]
            link("project_item", prj, iid, status=status)

    for oid, obj in objects.items():
        if oid not in seen_ids and not obj.get("missing_since"):
            obj["missing_since"] = at
            report["missing"] += 1
    doc["review"] = review
    report["review"] = len(review)
    report["objects"] = len(objects)
    report["links"] = len(links)
    return report


def backfill(*, catalog_root: Path, projects_root: Path, production_root: Path, registry: Optional[Path] = None,
             dry_run: bool = False) -> Dict[str, Any]:
    """Read the three stores (never written) and merge them into the registry; ``dry_run`` computes on a copy."""
    catalog = scan_catalog(Path(catalog_root))
    projects = scan_projects(Path(projects_root))
    productions = scan_productions(Path(production_root))
    if dry_run:
        doc = load(registry)
        report = plan(doc, catalog, projects, productions)
        return {"dry_run": True, **report, "review_items": doc["review"]}
    path = store_path(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {}

    def mutate(doc: Any) -> Any:
        doc = doc if isinstance(doc, dict) else _empty()
        rep = plan(doc, catalog, projects, productions)
        doc.setdefault("backfills", []).append({"at": _now(), **{k: v for k, v in rep.items() if k != "review_items"}})
        del doc["backfills"][:-50]
        out.update(rep, review_items=doc["review"])
        return doc

    json_store.mutate_json(path, mutate, default=_empty())
    return {"dry_run": False, **out}


# ── a die / configuration rename or move: the locators follow, the ids stay (die_rename.py, config_move.py) ─────────────

def rename_die_in(doc: Any, old: str, new: str) -> int:
    """The family and item locators of the catalog die ``old`` become ``new``; the old names stay as aliases."""
    n = 0
    for obj in (doc.get("objects") or {}).values() if isinstance(doc, dict) else []:
        loc = obj.get("locator") or {}
        if loc.get("ns") not in ("catalog", "production"):
            continue
        key = str(loc.get("key") or "")
        die, sep, cfg = key.partition("/")
        if obj.get("kind") == "family" and key == old:
            new_key = new
        elif obj.get("kind") in ("item", "production") and sep and die == old:
            new_key = f"{new}/{cfg}"
        else:
            continue
        obj.setdefault("aliases", []).append(key)
        loc["key"] = new_key
        if "die" in obj:
            obj["die"] = new
        if obj.get("kind") == "family":
            obj["name"] = new
        n += 1
    return n


def move_config_in(doc: Any, die: str, cfg: str, new_die: str, new_cfg: str) -> int:
    """The item / production locators of ``die/cfg`` become ``new_die/new_cfg`` (the item keeps its id; its family follows
    when the configuration moved to another die that is registered)."""
    if not isinstance(doc, dict):
        return 0
    objects = doc.get("objects") or {}
    fam_of = {o["locator"]["key"]: oid for oid, o in objects.items()
              if o.get("kind") == "family" and (o.get("locator") or {}).get("ns") == "catalog"}
    n = 0
    for obj in objects.values():
        loc = obj.get("locator") or {}
        if obj.get("kind") in ("item", "production") and loc.get("ns") in ("catalog", "production") \
                and loc.get("key") == f"{die}/{cfg}":
            obj.setdefault("aliases", []).append(loc["key"])
            loc["key"] = f"{new_die}/{new_cfg}"
            obj["die"], obj["config"] = new_die, new_cfg
            if obj.get("kind") == "item" and loc.get("ns") == "catalog" and new_die in fam_of:
                obj["family"] = fam_of[new_die]
            n += 1
    return n


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.object_registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="ids and links from the catalog, project and production stores")
    b.add_argument("--catalog", required=True, type=Path, help="the dies folder (<die>/die.yaml)")
    b.add_argument("--projects", required=True, type=Path)
    b.add_argument("--production", required=True, type=Path)
    b.add_argument("--registry", type=Path, default=None, help="registry folder (default: the app's)")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--report", type=Path, default=None)
    a = ap.parse_args(argv)
    rep = backfill(catalog_root=a.catalog, projects_root=a.projects, production_root=a.production,
                   registry=a.registry, dry_run=a.dry_run)
    text = json.dumps(rep, indent=2, ensure_ascii=False)
    if a.report:
        a.report.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "review_items"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
