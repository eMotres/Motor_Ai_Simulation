"""Move ONE account's data to its home region (docs/regions/DESIGN.md section 13; CLI ``scripts/migrate_account_region.py``).

Every step is a DRY RUN unless ``apply`` is asked for, and prints a report.  The steps, in order:

=================  =========  ==============================================================================================
step               runs on    what it does with ``apply``
=================  =========  ==============================================================================================
``export``         source     freezes the account's writes (``region_freeze``: 423 on every write of every credential,
                              reads go on), copies its data into ``identity/region_bundles/<id>/`` (0700, a host mount)
                              with a sha256 per file and the root hash signed with the peer secret
``transfer``       source     ``rsync`` of the bundle folder to the target host over WireGuard - ON THE HOST (the API
                              image has no rsync; in a container it refuses and prints the host command)
``import``         target     checks every hash and the signature, refuses staff and any file that exists there with
                              other content, writes a ``pending`` receipt, installs the files, creates the account with
                              the SAME password hash, grants and home region (or accepts the identical record of an
                              interrupted run), merges its lines, marks the receipt ``done``, deletes the bundle copy
``finish``         source     re-hashes every bundle file HERE and compares the password digest and disabled state with
                              the bundle (refuses on any change: a write past the freeze would be lost), reads the
                              target's ``done`` receipt over the signed peer channel (pull, read-only); then home region,
                              guard on, agent keys + OAuth grants here revoked, dies pinned, directory, freeze lifted
``unfreeze``       source     aborts a migration before ``finish`` (lifts the freeze, changes nothing else)
``delete-source``  source     LATER, only after the target's backup of the account has run once: deletes the source copy of
                              every bundle file whose bytes are still those of the bundle (a changed file outside a die
                              blocks; a die with a changed file, a project that stays or another client is kept whole),
                              the die access entries and the directory person; purges the account; deletes the bundle
=================  =========  ==============================================================================================

WHAT THE BUNDLE HOLDS (areas = the store roots, resolved on each server from its own environment, so the two hosts may lay
their trees out differently):

* ``workspaces/<ws_id>/`` and ``published/<ws_id>/``;
* the development projects whose member with role ``owner`` is this account (``projects/<id>/``);
* the dies those projects link (``linked_motors`` and ``motor_history``), every die derived from one of them
  (``derived_from``, transitively) and any die named with ``extra_dies``, each with ``dies/<die>/``,
  ``dies/.history/<die>/``, ``production/<die>/``, ``motor_tests/<die>/``, ``cad/<die>/``, ``passports/<die>/``, its die
  access entry and its rows of the shared duty-results ledger;
* the verification cases of the account (``verification/cases/<id>.json``, ``verification/documents/<id>/`` and the
  case events), its signed NDA PDFs (``legal/nda_signed/<id>.pdf``) and NDA signature lines;
* its directory person, affiliations and the companies they point at;
* its Terms / Privacy acceptance lines, communication-preference state and history, admin-audit lines (subject or
  target = the account) and sign-in events;
* its ``users.json`` record (password hash, role, grants, NDA flags ...).

NOT moved (by design): sessions, agent keys and OAuth grants (tokens are per server: the person signs in on the target
and reconnects AI apps there), and anything of another account.  The plan BLOCKS on: a staff account (staff work on both
servers), an account whose home region is not the target yet (set it first: classifier ``--apply`` or the admin
override), an account already guarded, no configured peer, another non-staff member in a moved project, a moved die
that a project which stays also links or another account has in its access list, a die open in the public catalog, and
running or queued jobs of the account (each of the last four with an explicit ``allow_*`` switch for the owner).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import store as RS

log = logging.getLogger(__name__)

BUNDLE_VERSION = 1
SKIP_SUFFIXES = (".tmp", "~")
JOB_ACTIVE_STATES = frozenset({"running", "queued", "pending", "starting"})


class MigrationError(Exception):
    """A step refused to run; the message says why."""


# ── helpers ──────────────────────────────────────────────────────────────────

def _U():
    from motor_ai_sim import users as U
    return U


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _safe_rel(rel: str) -> bool:
    parts = Path(rel).parts
    return bool(parts) and not Path(rel).is_absolute() and ".." not in parts and ":" not in parts[0]


def _slug(name: str) -> str:
    from motor_ai_sim.production.store import _safe_slug
    return _safe_slug(name)


def area_roots() -> Dict[str, Optional[Path]]:
    """The store root of every bundle area ON THIS SERVER (its own environment)."""
    from motor_ai_sim import workspace as W
    out: Dict[str, Optional[Path]] = {}

    def _try(name: str, fn) -> None:
        try:
            v = fn()
            out[name] = Path(str(v)) if v is not None else None
        except Exception:                                   # noqa: BLE001 - an unconfigured store is "none here"
            out[name] = None

    shared = Path(str(W.shared_root()))
    _try("workspaces", W.workspaces_root)
    _try("published", lambda: W.published_root() if W.workspaces_root() is not None else None)

    def _projects():
        from motor_ai_sim.development import store as DS
        return DS.root()

    def _production():
        from motor_ai_sim.production import store as PR
        return PR.production_root()

    def _tests():
        from motor_ai_sim.motor_tests import store as MT
        return MT.root()

    def _cad():
        from motor_ai_sim import model3d
        return model3d.cad_root()

    def _verification():
        from motor_ai_sim.verification import store as VS
        return VS.root()

    def _legal():
        from motor_ai_sim import nda as N
        return N.legal_root()

    _try("projects", _projects)
    _try("production", _production)
    _try("motor_tests", _tests)
    _try("dies", lambda: shared / "dies")
    _try("cad", _cad)
    _try("passports", lambda: shared / "passports")
    _try("verification", _verification)
    _try("legal", _legal)
    return out


def _walk(base: Path) -> Iterable[Path]:
    if base.is_file():
        yield base
        return
    if not base.is_dir():
        return
    for p in sorted(base.rglob("*")):
        if p.is_file() and not p.is_symlink() and not p.name.endswith(SKIP_SUFFIXES):
            yield p


def _jsonl_lines(path: Path, match) -> List[str]:
    """The raw lines of a JSONL file whose parsed object satisfies ``match`` (raw text, so the hash survives)."""
    if not path.is_file():
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            s = ln.rstrip("\n")
            if not s.strip():
                continue
            try:
                r = json.loads(s)
            except ValueError:
                continue
            if isinstance(r, dict) and match(r):
                out.append(s)
    return out


def _is(email: str, *fields: str):
    U = _U()

    def _m(r: dict) -> bool:
        return any(U._norm(str(r.get(f) or "")) == email for f in fields)
    return _m


# ── what belongs to the account ──────────────────────────────────────────────

def _owned_projects(email: str) -> Tuple[List[dict], List[dict]]:
    """(projects whose owner is this account, every other project)."""
    from motor_ai_sim.development import store as DS
    U = _U()
    mine, other = [], []
    for p in DS.list_projects():
        owners = [U._norm(str(m.get("email") or "")) for m in (p.get("members") or [])
                  if isinstance(m, dict) and m.get("role") == "owner"]
        (mine if email in owners else other).append(p)
    return mine, other


def _project_dies(p: dict) -> List[str]:
    out = []
    for key in ("linked_motors", "motor_history"):
        for row in p.get(key) or []:
            d = str((row or {}).get("die") or "").strip() if isinstance(row, dict) else ""
            if d and d not in out:
                out.append(d)
    return out


def _derived_from(die_yaml: Path) -> str:
    try:
        import yaml
        doc = yaml.safe_load(die_yaml.read_text(encoding="utf-8")) or {}
    except Exception:                                       # noqa: BLE001
        return ""
    d = doc.get("derived_from") if isinstance(doc, dict) else None
    return str(d.get("die") or "") if isinstance(d, dict) else ""


def _dies_closure(seed: List[str], dies_root: Optional[Path]) -> List[str]:
    """The seed dies plus every die derived from one of them (transitively).  Ancestors are NOT added: a base die is
    usually a catalog die other accounts use; name it with ``extra_dies`` if it belongs to the account too."""
    out = list(dict.fromkeys(seed))
    if dies_root is None or not dies_root.is_dir():
        return out
    parent = {p.parent.name: _derived_from(p) for p in dies_root.glob("*/die.yaml")}
    changed = True
    while changed:
        changed = False
        for child, par in sorted(parent.items()):
            if par and par in out and child not in out:
                out.append(child)
                changed = True
    return out


def _die_items(die: str, roots: Dict[str, Optional[Path]]) -> List[Tuple[str, str]]:
    items = [("dies", die), ("dies", f".history/{die}"), ("production", _slug(die)), ("motor_tests", _slug(die)),
             ("cad", die), ("passports", die)]
    return [(a, r) for a, r in items if roots.get(a) is not None and (roots[a] / r).exists()]


def _active_jobs(email: str) -> Optional[List[dict]]:
    try:
        from motor_ai_sim import cluster_monitor as CM
        U = _U()
        items = CM.jobs_view().get("items") or []
    except Exception:                                       # noqa: BLE001
        return None
    return [{"run_id": it.get("run_id"), "kind": it.get("kind"), "state": it.get("state")} for it in items
            if isinstance(it, dict) and U._norm(str(it.get("owner") or "")) == email
            and str(it.get("state") or "").lower() in JOB_ACTIVE_STATES]


def collect_records(email: str, *, dies: List[str], cases: List[str], rec: dict) -> Dict[str, Any]:
    """The account's lines and entries in the shared stores (everything that is not a whole file of its own)."""
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim import comm_prefs as CP
    from motor_ai_sim import legal_texts as LT
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import nda as N
    from motor_ai_sim import sessions as S
    from motor_ai_sim.verification import store as VS
    out: Dict[str, Any] = {"v": BUNDLE_VERSION, "email": email}
    user = {k: v for k, v in rec.items() if k not in ("region_guard", "region_freeze")}
    out["user"] = user
    out["terms_acceptances"] = _jsonl_lines(LT.acceptances_path(), _is(email, "user", "email"))
    try:
        out["comm_prefs_state"] = CP._load().get(email)
    except OSError:
        out["comm_prefs_state"] = None
    out["comm_prefs_history"] = _jsonl_lines(CP._HISTORY_FILE, _is(email, "email", "user"))
    out["nda_signatures"] = _jsonl_lines(N.signatures_path(), _is(email, "user", "email"))
    case_set = set(cases)
    out["verification_events"] = _jsonl_lines(VS.events_path(), lambda r: str(r.get("case") or "") in case_set)
    out["admin_audit"] = _jsonl_lines(AA.audit_path(), _is(email, "subject", "target"))
    out["auth_events"] = _jsonl_lines(Path(S._EVENTS_FILE), _is(email, "email"))
    out["directory"] = _directory_of(email)
    access = MA._load_die_access()
    out["die_access"] = {d: access[d] for d in dies if d in access}
    out["duty_results"] = _duty_rows(dies)
    return out


def _directory_of(email: str) -> Dict[str, List[dict]]:
    try:
        from motor_ai_sim.directory import service as D
        from motor_ai_sim.directory import store as DS
        person = D.find_person_by_email(email)
        if not person:
            return {"people": [], "affiliations": [], "companies": []}
        affs = [a for a in DS.load_affiliations() if a.get("person_id") == person.get("id")]
        ids = {a.get("company_id") for a in affs}
        comps = [c for c in DS.load_companies() if c.get("id") in ids]
        return {"people": [person], "affiliations": affs, "companies": comps}
    except Exception:                                       # noqa: BLE001
        log.exception("migrate: directory lookup failed")
        return {"people": [], "affiliations": [], "companies": []}


def _duty_rows(dies: List[str]) -> Dict[str, Dict[str, Any]]:
    try:
        from motor_ai_sim import duty_results as DR
        with DR.using_store(DR.shared_store_path()):
            doc = DR.read_all()
    except Exception:                                       # noqa: BLE001
        return {"results": {}, "history": {}}
    return {b: {d: (doc.get(b) or {}).get(d) for d in dies if d in (doc.get(b) or {})} for b in ("results", "history")}


# ── the plan (dry run of export) ─────────────────────────────────────────────

def _staff_emails() -> set:
    U = _U()
    return {em for em, r in U._load_soft().items() if RS.is_staff(r)}


def _other_members(p: dict, email: str, staff: set) -> List[str]:
    """Non-staff members of a project other than ``email``: they would keep writing to the EU copy after the move."""
    U = _U()
    out = []
    for m in p.get("members") or []:
        em = U._norm(str((m or {}).get("email") or "")) if isinstance(m, dict) else ""
        if em and em != email and em not in staff and em not in out:
            out.append(em)
    return out


def _die_clients(die: str, email: str) -> List[str]:
    """Accounts other than ``email`` in the die's access list (they see or use it on THIS server)."""
    from motor_ai_sim import motor_access as MA
    U = _U()
    try:
        clients = MA.get_die_access(die).get("clients") or []
    except Exception:                                       # noqa: BLE001
        return ["<unreadable die access>"]
    return sorted({U._norm(str(c)) for c in clients if U._norm(str(c)) and U._norm(str(c)) != email})


def die_sharing(die: str, email: str, moving_projects: Iterable[str]) -> List[str]:
    """Why a die is NOT the account's alone right now: projects that stay and link it, other die-access clients.  The
    same check runs in the plan (blocker) and again in delete-source (the die is then kept whole)."""
    moving = set(moving_projects)
    why = []
    try:
        from motor_ai_sim.development import store as DS
        stay = sorted(str(p.get("id")) for p in DS.list_projects()
                      if str(p.get("id")) not in moving and die in _project_dies(p))
    except Exception:                                       # noqa: BLE001 - fail closed
        stay = ["<projects unreadable>"]
    if stay:
        why.append("also linked by project(s) that stay: " + ", ".join(stay))
    others = _die_clients(die, email)
    if others:
        why.append(f"used by {len(others)} other account(s) through its access list")
    return why


def plan(email: str, *, target_region: Optional[str] = None, extra_dies: Iterable[str] = (),
         allow_shared_dies: bool = False, allow_open_dies: bool = False, allow_active_jobs: bool = False,
         allow_project_members: bool = False) -> Dict[str, Any]:
    """Everything the export would take, and what blocks it.  Writes nothing."""
    from motor_ai_sim.workspace import workspace_id
    from motor_ai_sim import motor_access as MA
    U = _U()
    em = U._norm(email)
    target = RC.norm_region(target_region) or RC.peer_region()
    blockers: List[str] = []
    warnings: List[str] = []
    rec = U.get_user(em)
    if not isinstance(rec, dict):
        raise MigrationError("no such account on this server")
    if target == RC.local_region():
        blockers.append(f"the target region '{target}' is this server's region")
    if RS.is_staff(rec):
        blockers.append("staff account: staff work on both servers and are not migrated")
    if RS.effective_region(rec) != target:
        blockers.append(f"home region is '{RS.effective_region(rec)}', not '{target}': set it first "
                        "(classify_regions.py --apply, or the admin override)")
    if RS.is_guarded(rec):
        blockers.append("already migrated (its region guard is on)")
    if not RC.peer_configured() or RC.peer_region() != target:
        blockers.append("no configured peer for the target region (the bundle is signed with the peer secret)")
    roots = area_roots()
    ws_id = workspace_id(em)
    items: List[Tuple[str, str]] = []
    for area in ("workspaces", "published"):
        if roots.get(area) is not None and (roots[area] / ws_id).exists():
            items.append((area, ws_id))
    if roots.get("workspaces") is None:
        warnings.append("WORKSPACES_ROOT is not set on this server: no workspace to move")
    mine, other = _owned_projects(em)
    staff = _staff_emails()
    for p in mine:
        items.append(("projects", str(p.get("id"))))
        members = _other_members(p, em, staff)
        if members:
            # they would keep writing to the source copy, which stops being the project after the move
            msg = (f"project {p.get('id')}: {len(members)} other non-staff member(s) work in it on this server; "
                   "they must move too or be removed first")
            (warnings if allow_project_members else blockers).append(msg)
    seed = []
    for p in mine:
        seed += [d for d in _project_dies(p) if d not in seed]
    seed += [d for d in extra_dies if d and d not in seed]
    dies = _dies_closure(seed, roots.get("dies"))
    moving_ids = [str(p.get("id")) for p in mine]
    for d in dies:
        if roots.get("dies") is None or not (roots["dies"] / d / "die.yaml").is_file():
            warnings.append(f"die '{d}': no catalog folder on this server (nothing of it moves but its records)")
        for why in die_sharing(d, em, moving_ids):
            (warnings if allow_shared_dies else blockers).append(f"die '{d}' is {why}")
        try:
            vis = MA.get_die_access(d).get("visibility")
        except Exception:                                   # noqa: BLE001
            vis = None
        if vis == "public":
            msg = f"die '{d}' is open in the public catalog; moving it takes it out of this server's catalog"
            (warnings if allow_open_dies else blockers).append(msg)
        items += _die_items(d, roots)
    cases: List[str] = []
    try:
        from motor_ai_sim.verification import store as VS
        for c in VS.cases_of(em):
            cid = str(c.get("id") or "")
            if VS.valid_case_id(cid):
                cases.append(cid)
                items.append(("verification", f"cases/{cid}.json"))
                if (VS.documents_dir() / cid).is_dir():
                    items.append(("verification", f"documents/{cid}"))
    except Exception:                                       # noqa: BLE001
        log.exception("migrate: verification lookup failed")
        warnings.append("verification store could not be read")
    nda_ids = []
    try:
        from motor_ai_sim import nda as N
        for ln in _jsonl_lines(N.signatures_path(), _is(em, "user", "email")):
            rid = str(json.loads(ln).get("id") or "")
            if rid and (N.legal_root() / "nda_signed" / f"{rid}.pdf").is_file():
                nda_ids.append(rid)
                items.append(("legal", f"nda_signed/{rid}.pdf"))
    except Exception:                                       # noqa: BLE001
        log.exception("migrate: NDA lookup failed")
    jobs = _active_jobs(em)
    if jobs is None:
        warnings.append("the job queue could not be read: make sure the account runs no job")
    elif jobs:
        msg = f"{len(jobs)} running or queued job(s) of the account"
        (warnings if allow_active_jobs else blockers).append(msg)
    files = []
    seen = set()
    for area, rel in items:
        base = roots[area] / rel
        for f in _walk(base):
            r = f"{area}/{f.relative_to(roots[area]).as_posix()}"
            if r not in seen:
                seen.add(r)
                files.append({"rel": r, "bytes": f.stat().st_size})
    return {"email": em, "ws_id": ws_id, "source_region": RC.local_region(), "target_region": target,
            "blockers": blockers, "warnings": warnings, "projects": [str(p.get("id")) for p in mine],
            "dies": dies, "cases": cases, "nda_records": nda_ids, "items": [f"{a}/{r}" for a, r in items],
            "files": files, "file_count": len(files), "bytes": sum(f["bytes"] for f in files), "active_jobs": jobs or []}


# ── export ───────────────────────────────────────────────────────────────────

def _bundle_id(ws_id: str) -> str:
    return "mig-" + time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + ws_id[:8]


def root_hash(manifest: Dict[str, Any]) -> str:
    core = {k: manifest.get(k) for k in ("v", "bundle_id", "email", "source_region", "target_region", "files",
                                         "records_sha256")}
    return hashlib.sha256(_canon(core)).hexdigest()


def root_hmac(root_sha256: str, secret: Optional[str] = None) -> str:
    """The bundle's root hash signed with the peer secret: only a server holding the secret made this bundle, so a
    bundle edited on the way (manifest and files re-hashed together) is refused by import."""
    import hmac as _hmac
    sec = RC.peer_secret() if secret is None else secret
    if not sec:
        return ""
    return _hmac.new(sec.encode("utf-8"), b"motres-bundle-v1\n" + str(root_sha256).encode("ascii"),
                     hashlib.sha256).hexdigest()


def bundles_dir() -> Path:
    """Where bundles are written by default: a 0700 folder beside the registry (``/srv/motres/identity`` on a server).
    It is a bind mount of the host, so the bundle (it holds the password hash) survives a blue/green switch and the HOST
    can rsync it; the container layer and ``/srv/motres/run`` are not mounted into the API containers."""
    env = (os.environ.get("MOTRES_REGION_BUNDLES_DIR") or "").strip()
    return Path(env) if env else Path(_U()._USERS_FILE).parent / "region_bundles"


def export(email: str, out_dir: Optional[Path] = None, *, apply: bool = False, actor: str = "migration",
           **plan_kw: Any) -> Dict[str, Any]:
    """Dry run: the plan.  Apply: freeze the account, copy its files into ``<out_dir or bundles_dir()>/<bundle id>/``
    with hashes and the signed root hash."""
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    out_dir = Path(out_dir) if out_dir is not None else bundles_dir()
    p = plan(email, **plan_kw)
    rep = {"step": "export", "dry_run": not apply, **p}
    if not apply:
        return rep
    if p["blockers"]:
        raise MigrationError("export refused: " + "; ".join(p["blockers"]))
    em = p["email"]
    RS.set_region_freeze(em, True, by=actor, reason="region migration export")
    try:
        p = plan(email, **plan_kw)                          # again, now that nothing of the account can change
        roots = area_roots()
        bid = _bundle_id(p["ws_id"])
        ensure_private_dir(Path(out_dir))
        bdir = ensure_private_dir(Path(out_dir) / bid)
        files: Dict[str, Dict[str, Any]] = {}
        for f in p["files"]:
            area, rel = f["rel"].split("/", 1)
            src = roots[area] / rel
            dst = bdir / "files" / f["rel"]
            ensure_private_dir(dst.parent)
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o600)
            files[f["rel"]] = {"sha256": sha256_file(dst), "bytes": dst.stat().st_size}
            if sha256_file(src) != files[f["rel"]]["sha256"]:
                raise MigrationError(f"{f['rel']} changed while it was copied")
        records = collect_records(em, dies=p["dies"], cases=p["cases"], rec=_U().get_user(em) or {})
        raw = _canon(records)
        with open_private(bdir / "records.json", "w") as fh:
            fh.write(raw.decode("utf-8"))                   # compact JSON: no newline to translate
        raw = (bdir / "records.json").read_bytes()
        manifest = {"v": BUNDLE_VERSION, "bundle_id": bid, "created": _now_iso(), "email": em, "ws_id": p["ws_id"],
                    "source_region": p["source_region"], "target_region": p["target_region"],
                    "projects": p["projects"], "dies": p["dies"], "cases": p["cases"], "files": files,
                    "records_sha256": hashlib.sha256(raw).hexdigest(),
                    "record_counts": {k: len(v) for k, v in records.items() if isinstance(v, (list, dict))}}
        manifest["root_sha256"] = root_hash(manifest)
        manifest["root_hmac"] = root_hmac(manifest["root_sha256"])
        if not manifest["root_hmac"]:
            raise MigrationError("no peer secret: the bundle cannot be signed")
        with open_private(bdir / "manifest.json", "w") as fh:
            json.dump(manifest, fh, indent=1, sort_keys=True, ensure_ascii=False)
    except Exception:
        RS.set_region_freeze(em, False)
        raise
    AA.record(actor, "region.migrate_export", em, subject=em,
              details={"bundle": bid, "files": len(files), "root_sha256": manifest["root_sha256"],
                       "target": p["target_region"]})
    return {**rep, "bundle_id": bid, "bundle_dir": str(bdir), "root_sha256": manifest["root_sha256"], "frozen": True}


# ── bundle check ─────────────────────────────────────────────────────────────

def load_manifest(bundle_dir: Path) -> Dict[str, Any]:
    try:
        m = json.loads((Path(bundle_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise MigrationError(f"no readable manifest.json in the bundle ({type(e).__name__})")
    if not isinstance(m, dict) or m.get("v") != BUNDLE_VERSION or not isinstance(m.get("files"), dict):
        raise MigrationError("not a migration bundle of this version")
    return m


def verify_bundle(bundle_dir: Path, *, signed: bool = True) -> Dict[str, Any]:
    """Every file and the records against the manifest, the root hash and (``signed``) its HMAC with the peer secret
    -> ``{ok, bad, missing, extra, ...}``.  ``signed=False`` only for the host-side transfer, which has no secret; the
    import on the target always checks the signature."""
    bdir = Path(bundle_dir)
    m = load_manifest(bdir)
    bad, missing = [], []
    for rel, meta in sorted(m["files"].items()):
        if not _safe_rel(rel):
            bad.append(rel)
            continue
        f = bdir / "files" / rel
        if not f.is_file():
            missing.append(rel)
        elif sha256_file(f) != meta.get("sha256"):
            bad.append(rel)
    on_disk = {p.relative_to(bdir / "files").as_posix() for p in _walk(bdir / "files")} if (bdir / "files").is_dir() else set()
    extra = sorted(on_disk - set(m["files"]))
    try:
        rec_ok = hashlib.sha256((bdir / "records.json").read_bytes()).hexdigest() == m.get("records_sha256")
    except OSError:
        rec_ok = False
    root_ok = root_hash(m) == m.get("root_sha256")
    import hmac as _hmac
    want = root_hmac(str(m.get("root_sha256") or ""))
    sig_ok = bool(want) and _hmac.compare_digest(str(m.get("root_hmac") or ""), want)
    return {"ok": not bad and not missing and not extra and rec_ok and root_ok and (sig_ok or not signed), "bad": bad,
            "missing": missing, "extra": extra, "records_ok": rec_ok, "root_ok": root_ok, "signature_ok": sig_ok,
            "files": len(m["files"]), "bundle_id": m.get("bundle_id"), "root_sha256": m.get("root_sha256")}


# ── transfer ─────────────────────────────────────────────────────────────────

def transfer_command(bundle_dir: Path, dest: str, *, ssh: str = "ssh", dry_run: bool = True) -> List[str]:
    """``rsync`` of the bundle folder to ``dest`` (``host:/path`` over WireGuard).  Checksummed, private modes."""
    src = str(Path(bundle_dir)).rstrip("/\\") + "/"
    target = dest.rstrip("/") + "/" + Path(bundle_dir).name + "/"
    cmd = ["rsync", "-a", "--checksum", "--chmod=D700,F600", "-e", ssh]
    if dry_run:
        cmd.append("--dry-run")
    return cmd + [src, target]


def transfer(bundle_dir: Path, dest: str, *, apply: bool = False, ssh: str = "ssh", runner=None) -> Dict[str, Any]:
    """Run ON THE HOST (the API image has no rsync / ssh).  The bundle folder sits under ``/srv/motres/identity``, the
    same path on the host as in the container.  In a container without rsync it refuses and prints the host command."""
    v = verify_bundle(bundle_dir, signed=False)
    if not v["ok"]:
        raise MigrationError("the bundle does not match its manifest; export it again")
    cmd = transfer_command(bundle_dir, dest, ssh=ssh, dry_run=not apply)
    rep = {"step": "transfer", "dry_run": not apply, "command": cmd, "bundle_id": v["bundle_id"]}
    if runner is None:
        import subprocess
        if shutil.which("rsync") is None:
            import shlex
            raise MigrationError("no rsync here (the API container has none): run on the host: "
                                 + " ".join(shlex.quote(c) for c in transfer_command(bundle_dir, dest, ssh=ssh,
                                                                                      dry_run=False)))
        runner = lambda c: subprocess.run(c, check=False).returncode   # noqa: E731
    rc = runner(cmd)
    if rc != 0:
        raise MigrationError(f"rsync exited with {rc}")
    return {**rep, "ok": True}


# ── import (on the target) ───────────────────────────────────────────────────

def receipts_dir() -> Path:
    env = (os.environ.get("MOTRES_REGION_MIGRATIONS_DIR") or "").strip()
    return Path(env) if env else Path(_U()._USERS_FILE).parent / "region_migrations"


def load_receipt(bundle_id: str) -> Optional[Dict[str, Any]]:
    bid = str(bundle_id or "")
    if not bid.startswith("mig-") or not all(c.isalnum() or c == "-" for c in bid) or len(bid) > 64:
        return None
    try:
        r = json.loads((receipts_dir() / f"{bid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return r if isinstance(r, dict) else None


def public_receipt(r: Dict[str, Any]) -> Dict[str, Any]:
    """What the peer channel shows of a receipt: hashes and counts, no e-mail, no paths.  ``verified`` is true only for
    a ``done`` import (an interrupted one stays ``pending``)."""
    out = {k: r.get(k) for k in ("bundle_id", "root_sha256", "files", "installed", "imported_iso")}
    out["state"] = r.get("state", "done")
    out["verified"] = r.get("verified") is True and out["state"] == "done"
    return out


def _append_lines(path: Path, lines: List[str]) -> int:
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    if not lines:
        return 0
    have = set()
    if path.is_file():
        with open(path, encoding="utf-8", errors="replace") as fh:
            have = {ln.rstrip("\n") for ln in fh}
    new = [ln for ln in lines if ln not in have]
    if not new:
        return 0
    ensure_private_dir(path.parent)
    with open_private(path, "a") as fh:
        for ln in new:
            fh.write(ln + "\n")
    return len(new)


def _pin_die(die_yaml: Path, region: str) -> bool:
    """Mark a moved die as customer data of ``region`` (the catalog mirror leaves it out).  A line is appended or
    replaced textually so comments and layout of the YAML stay as they are."""
    if not die_yaml.is_file():
        return False
    nl, lines = _yaml_lines(die_yaml.read_bytes())
    want = f"home_region: {region}"
    for i, ln in enumerate(lines):
        if ln.startswith("home_region:"):
            if ln.strip() == want:
                return False
            lines[i] = want
            break
    else:
        lines.append(want)
    die_yaml.write_bytes((nl.join(lines) + nl).encode("utf-8"))
    return True


def _yaml_lines(data: bytes) -> Tuple[str, List[str]]:
    """(newline style, lines) of a text file, read as bytes so the file's own line endings are kept."""
    text = data.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    body = text[:-len(nl)] if text.endswith(nl) else text
    return nl, (body.split(nl) if body else [])


def _unpinned_sha(die_yaml: Path, region: str) -> str:
    """The hash the die.yaml had before :func:`_pin_die` appended its line (``finish`` pins the source copy too)."""
    try:
        nl, lines = _yaml_lines(die_yaml.read_bytes())
    except (OSError, UnicodeDecodeError):
        return ""
    if not lines or lines[-1] != f"home_region: {region}":
        return ""
    return hashlib.sha256((nl.join(lines[:-1]) + nl).encode("utf-8")).hexdigest()


_ACCOUNT_LOCAL_KEYS = ("home_region", "home_region_set", "region_guard", "region_freeze")


def _same_account(existing: Optional[dict], bundled: Optional[dict]) -> bool:
    """The record here IS the bundle's (a rerun after an interrupted import): equal but for the region fields."""
    if not isinstance(existing, dict) or not isinstance(bundled, dict):
        return False

    def strip(r: dict) -> dict:
        return {k: v for k, v in r.items() if k not in _ACCOUNT_LOCAL_KEYS}
    return strip(existing) == strip(bundled)


def _write_receipt(receipt: Dict[str, Any]) -> None:
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    ensure_private_dir(receipts_dir())
    p = receipts_dir() / f"{receipt.get('bundle_id')}.json"
    tmp = p.with_name(p.name + ".tmp")
    with open_private(tmp, "w") as fh:
        json.dump(receipt, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)


def import_bundle(bundle_dir: Path, *, apply: bool = False, actor: str = "migration",
                  keep_bundle: bool = False) -> Dict[str, Any]:
    """Verify and install a bundle on THIS (target) server.

    The signature of the root hash (peer secret) and every file hash are checked first.  A ``pending`` receipt is
    written BEFORE anything is installed and turned ``done`` at the end, so an interrupted import can be run again: an
    account that already exists here is accepted only when its record equals the bundle's.  A staff account is never
    imported (staff live on both servers).  After a ``done`` import the bundle folder here is deleted (it holds the
    password hash; the installed copy is the data now) unless ``keep_bundle``."""
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim.private_files import ensure_private_dir
    U = _U()
    bdir = Path(bundle_dir)
    v = verify_bundle(bdir)
    m = load_manifest(bdir)
    blockers: List[str] = []
    if not v["ok"]:
        blockers.append("the bundle does not match its manifest or signature (bad %d, missing %d, extra %d, records %s, "
                        "root %s, signature %s)" % (len(v["bad"]), len(v["missing"]), len(v["extra"]), v["records_ok"],
                                                     v["root_ok"], v["signature_ok"]))
    if RC.norm_region(m.get("target_region")) != RC.local_region():
        blockers.append(f"the bundle is for region '{m.get('target_region')}', this server is '{RC.local_region()}'")
    if RC.norm_region(m.get("source_region")) != RC.peer_region():
        blockers.append("the bundle does not come from this server's configured peer region")
    em = U._norm(str(m.get("email") or ""))
    receipt = load_receipt(str(m.get("bundle_id")))
    if (receipt is not None and receipt.get("root_sha256") == m.get("root_sha256")
            and receipt.get("state", "done") == "done"):
        return {"step": "import", "dry_run": not apply, "already_imported": True, "bundle_id": m.get("bundle_id"),
                "root_sha256": m.get("root_sha256"), "blockers": [], "conflicts": []}
    try:
        bundled_user = (json.loads((bdir / "records.json").read_text(encoding="utf-8")) or {}).get("user")
    except (OSError, ValueError):
        bundled_user = None
    if RS.is_staff(bundled_user):
        blockers.append("the bundle is of a staff account: staff work on both servers and are never imported")
    existing = U.get_user(em)
    rerun = existing is not None and _same_account(existing, bundled_user)
    if existing is not None and not rerun:
        blockers.append("an account with this e-mail already exists on this server (never merged automatically)")
    roots = area_roots()
    conflicts, todo, same = [], [], 0
    for rel, meta in sorted(m["files"].items()):
        area, sub = rel.split("/", 1)
        if roots.get(area) is None:
            blockers.append(f"store '{area}' is not configured on this server")
            continue
        dst = roots[area] / sub
        if dst.exists():
            if dst.is_file() and (sha256_file(dst) == meta.get("sha256") or (
                    dst.name == "die.yaml" and _unpinned_sha(dst, RC.local_region()) == meta.get("sha256"))):
                same += 1
                continue
            conflicts.append(rel)
        else:
            todo.append((rel, dst))
    if conflicts:
        blockers.append(f"{len(conflicts)} file(s) already exist here with other content")
    blockers = list(dict.fromkeys(blockers))
    rep = {"step": "import", "dry_run": not apply, "bundle_id": m.get("bundle_id"), "email": em,
           "root_sha256": m.get("root_sha256"), "verify": v, "blockers": blockers, "conflicts": conflicts[:50],
           "to_install": len(todo), "already_identical": same, "projects": m.get("projects"), "dies": m.get("dies")}
    if not apply:
        return rep
    if blockers:
        raise MigrationError("import refused: " + "; ".join(blockers))
    records = json.loads((bdir / "records.json").read_text(encoding="utf-8"))
    _write_receipt({"bundle_id": m.get("bundle_id"), "email": em, "root_sha256": m.get("root_sha256"),
                    "files": len(m["files"]), "state": "pending", "verified": False, "started_iso": _now_iso()})
    for rel, dst in todo:
        if rel.startswith(("workspaces/", "verification/", "legal/")):
            ensure_private_dir(dst.parent)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".mig.tmp")
        shutil.copyfile(bdir / "files" / rel, tmp)
        if sha256_file(tmp) != m["files"][rel]["sha256"]:
            tmp.unlink()
            raise MigrationError(f"{rel}: hash changed during install")
        os.replace(tmp, dst)
    merged = _merge_records(records, m, actor=actor)
    pinned = [d for d in (m.get("dies") or []) if roots.get("dies") is not None
              and _pin_die(roots["dies"] / d / "die.yaml", RC.local_region())]
    _rebuild_indexes()
    installed = len(todo)
    receipt = {"bundle_id": m.get("bundle_id"), "email": em, "root_sha256": m.get("root_sha256"),
               "files": len(m["files"]), "installed": installed, "state": "done", "verified": True,
               "imported_iso": _now_iso(), "merged": merged, "pinned_dies": pinned, "rerun": rerun}
    _write_receipt(receipt)
    AA.record(actor, "region.migrate_import", em, subject=em,
              details={"bundle": m.get("bundle_id"), "files": installed, "root_sha256": m.get("root_sha256"),
                       "from": m.get("source_region"), "rerun": rerun})
    removed = False
    if not keep_bundle:
        shutil.rmtree(bdir, ignore_errors=True)
        removed = not bdir.exists()
    return {**rep, "installed": installed, "merged": merged, "pinned_dies": pinned, "receipt": public_receipt(receipt),
            "bundle_removed": removed}


def _merge_records(records: Dict[str, Any], m: Dict[str, Any], *, actor: str) -> Dict[str, Any]:
    from motor_ai_sim import comm_prefs as CP
    from motor_ai_sim import legal_texts as LT
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import nda as N
    from motor_ai_sim import sessions as S
    from motor_ai_sim.verification import store as VS
    U = _U()
    em = U._norm(str(m.get("email") or ""))
    out: Dict[str, Any] = {}
    user = dict(records.get("user") or {})
    user.pop("region_guard", None)
    user.pop("region_freeze", None)
    user["home_region"] = RC.local_region()
    user["home_region_set"] = {"by": "admin", "actor": actor[:200], "reason": f"migration {m.get('bundle_id')}"[:200],
                               "iso": _now_iso()}
    with U._LOCK:
        users = U._load()
        if em in users:
            if not _same_account(users[em], user):
                raise MigrationError("an account with this e-mail appeared on this server during the import")
            out["account"] = 0                                 # a rerun: the record is already the bundle's
        else:
            users[em] = user
            U._save(users)
            out["account"] = 1
    out["terms_acceptances"] = _append_lines(LT.acceptances_path(), records.get("terms_acceptances") or [])
    out["comm_prefs_history"] = _append_lines(Path(CP._HISTORY_FILE), records.get("comm_prefs_history") or [])
    state = records.get("comm_prefs_state")
    if isinstance(state, dict):
        d = CP._load()
        if em not in d:
            d[em] = state
            CP._save(d)
            out["comm_prefs_state"] = 1
    out["nda_signatures"] = _append_lines(N.signatures_path(), records.get("nda_signatures") or [])
    out["verification_events"] = _append_lines(VS.events_path(), records.get("verification_events") or [])
    from motor_ai_sim import admin_audit as AA
    out["admin_audit"] = _append_lines(AA.audit_path(), records.get("admin_audit") or [])
    out["auth_events"] = _append_lines(Path(S._EVENTS_FILE), records.get("auth_events") or [])
    out["directory"] = _merge_directory(records.get("directory") or {})
    acc = records.get("die_access") or {}
    if acc:
        with MA._DA_LOCK:
            d = MA._load_die_access()
            n = 0
            for die, entry in acc.items():
                if die not in d:
                    d[die] = entry
                    n += 1
            if n:
                MA._save_die_access(d)
        out["die_access"] = n
    out["duty_results"] = _merge_duty_rows(records.get("duty_results") or {})
    return out


def _mutate_dir_list(path: Path, key: str, fn) -> None:
    """Apply ``fn(list) -> list`` to one directory file (``{key: [...]}``; a bare list is read too)."""
    from motor_ai_sim import json_store

    def _m(cur):
        rows = cur.get(key) if isinstance(cur, dict) else cur
        new = fn(list(rows) if isinstance(rows, list) else [])
        if isinstance(cur, dict):
            cur[key] = new
            return cur
        return {key: new}
    json_store.mutate_json(path, _m, default={key: []})


def _merge_directory(directory: Dict[str, List[dict]]) -> Dict[str, int]:
    from motor_ai_sim.directory import store as DS
    paths = {"people": DS.people_path(), "affiliations": DS.affiliations_path(), "companies": DS.companies_path()}
    out = {}
    for kind, rows in directory.items():
        if kind not in paths or not rows:
            continue
        added = {"n": 0}

        def _fn(cur, rows=rows):
            have = {r.get("id") for r in cur if isinstance(r, dict)}
            for r in rows:
                if isinstance(r, dict) and r.get("id") not in have:
                    cur.append(r)
                    added["n"] += 1
            return cur
        _mutate_dir_list(paths[kind], kind, _fn)
        out[kind] = added["n"]
    return out


def _merge_duty_rows(rows: Dict[str, Dict[str, Any]]) -> int:
    if not any(rows.get(b) for b in ("results", "history")):
        return 0
    from motor_ai_sim import duty_results as DR
    n = 0
    with DR.using_store(DR.shared_store_path()):
        with DR._store_write_lock():
            doc = DR.read_all()
            for b in ("results", "history"):
                node = doc.setdefault(b, {})
                for die, v in (rows.get(b) or {}).items():
                    if v is not None and not node.get(die):
                        node[die] = v
                        n += 1
            if n and not DR._write_all(doc):
                raise MigrationError("could not write the duty-results ledger")
    return n


def _rebuild_indexes() -> None:
    for fn in ("development.store:rebuild_membership_index", "verification.store:reindex"):
        mod, name = fn.split(":")
        try:
            import importlib
            getattr(importlib.import_module("motor_ai_sim." + mod), name)()
        except Exception:                                   # noqa: BLE001 - an index is rebuilt on its next read too
            log.exception("migrate: %s failed", fn)


# ── finish / unfreeze (on the source) ────────────────────────────────────────

def _peer_receipt(bundle_id: str, transport: Any = None) -> Optional[Dict[str, Any]]:
    from urllib.parse import quote
    from motor_ai_sim.regions import peer as P
    got = P.get("/migration/receipt?bundle=" + quote(bundle_id, safe=""), transport=transport)
    return got if isinstance(got, dict) else None


def _source_changes(m: Dict[str, Any], *, pinned_ok: bool) -> Tuple[List[str], List[str]]:
    """Re-hash THIS server's copy of every bundle file -> ``(changed, missing)``.  ``pinned_ok``: a die.yaml that
    differs only by the ``home_region`` pin of a finish counts as unchanged."""
    roots = area_roots()
    target = str(m.get("target_region"))
    changed, missing = [], []
    for rel, meta in sorted(m["files"].items()):
        area, sub = rel.split("/", 1)
        if roots.get(area) is None:
            missing.append(rel)
            continue
        f = roots[area] / sub
        if not f.is_file():
            missing.append(rel)
            continue
        if sha256_file(f) == meta.get("sha256"):
            continue
        if pinned_ok and f.name == "die.yaml" and _unpinned_sha(f, target) == meta.get("sha256"):
            continue
        changed.append(rel)
    return changed, missing


def _bundled_user(bundle_dir: Path) -> Dict[str, Any]:
    try:
        return (json.loads((Path(bundle_dir) / "records.json").read_text(encoding="utf-8")) or {}).get("user") or {}
    except (OSError, ValueError):
        raise MigrationError("records.json of the bundle cannot be read")


def finish(email: str, bundle_dir: Path, *, apply: bool = False, actor: str = "migration",
           transport: Any = None) -> Dict[str, Any]:
    """Switch the account over, on the SOURCE.  Refuses unless: the target holds a ``done`` receipt of exactly this
    bundle (signed root hash, read over the peer channel), the target's directory lists the account, EVERY bundle file
    on this server still has the bytes it had at the export (a write that slipped past the freeze - a staff edit, a
    member of a moved project - would otherwise be lost), and the account's password and disabled state are the
    bundle's (a change during the freeze would be lost on the target).  Then: home region, guard on, the account's agent
    keys and OAuth grants here revoked (those channels would act on the stale copy), dies pinned, directory entry,
    freeze lifted."""
    import hmac as _hmac
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim.regions import handoff as H
    U = _U()
    em = U._norm(email)
    m = load_manifest(bundle_dir)
    blockers: List[str] = []
    if U._norm(str(m.get("email"))) != em:
        raise MigrationError("the bundle is of another account")
    if not RC.peer_configured() or RC.peer_region() != RC.norm_region(m.get("target_region")):
        blockers.append("the target region is not this server's configured peer")
    rec = U.get_user(em)
    if not isinstance(rec, dict):
        raise MigrationError("no such account on this server")
    want = root_hmac(str(m.get("root_sha256") or ""))
    if not want or not _hmac.compare_digest(str(m.get("root_hmac") or ""), want) or root_hash(m) != m.get("root_sha256"):
        blockers.append("the bundle manifest is not the one this server signed")
    changed, missing = _source_changes(m, pinned_ok=True)
    if changed or missing:
        blockers.append(f"{len(changed)} file(s) changed and {len(missing)} missing on this server since the export: "
                        "the bundle is stale - unfreeze, export again")
    bundled = _bundled_user(bundle_dir)
    if H.pw_digest(rec) != H.pw_digest(bundled):
        blockers.append("the password changed on this server since the export: unfreeze, export again")
    if bool(rec.get("disabled")) != bool(bundled.get("disabled")):
        blockers.append("the account was enabled / disabled since the export: unfreeze, export again")
    got = _peer_receipt(str(m.get("bundle_id")), transport=transport) if not blockers else None
    if not blockers and got is None:
        blockers.append("the target has no receipt of this bundle (import it there first) or did not answer")
    elif got is not None and (got.get("root_sha256") != m.get("root_sha256") or got.get("verified") is not True):
        blockers.append("the target's receipt does not match this bundle's root hash or is still pending")
    look = RS.peer_lookup(em, timeout=4.0, transport=transport) if not blockers else None
    if not blockers and not (look and look.get("hosted")):
        blockers.append("the target's directory does not list the account yet")
    rep = {"step": "finish", "dry_run": not apply, "email": em, "bundle_id": m.get("bundle_id"),
           "root_sha256": m.get("root_sha256"), "target_receipt": got, "blockers": blockers,
           "changed": changed[:50], "missing": missing[:50],
           "will": ["set home region " + str(m.get("target_region")), "turn the account's region guard on",
                    "revoke its agent keys and OAuth grants on this server",
                    "pin the moved dies to " + str(m.get("target_region")), "put the account in the directory",
                    "lift the write freeze"]}
    if not apply:
        return rep
    if blockers:
        raise MigrationError("finish refused: " + "; ".join(blockers))
    target = str(m.get("target_region"))
    if RS.effective_region(rec) != target or RS.region_source(rec) == "default":
        RS.set_home_region(em, target, by="admin", actor=actor, reason=f"migration {m.get('bundle_id')}")
    RS.set_region_guard(em, True, by=actor, bundle=str(m.get("bundle_id")))
    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import oauth as O
    revoked = {"agent_keys": K.revoke_all_keys(em), "oauth_grants": O.revoke_all_grants(em)}
    roots = area_roots()
    pinned = [d for d in (m.get("dies") or []) if roots.get("dies") is not None
              and _pin_die(roots["dies"] / d / "die.yaml", target)]
    RS.upsert_remote_entry(em, look["entry"], target)
    RS.set_region_freeze(em, False)
    AA.record(actor, "region.migrate", em, subject=em,
              details={"bundle": m.get("bundle_id"), "root_sha256": m.get("root_sha256"), "to": target,
                       "pinned_dies": len(pinned), "revoked": revoked})
    return {**rep, "guard": True, "frozen": False, "pinned_dies": pinned, "revoked": revoked}


def unfreeze(email: str, *, apply: bool = False, actor: str = "migration") -> Dict[str, Any]:
    from motor_ai_sim import admin_audit as AA
    U = _U()
    em = U._norm(email)
    rec = U.get_user(em)
    if not isinstance(rec, dict):
        raise MigrationError("no such account on this server")
    rep = {"step": "unfreeze", "dry_run": not apply, "email": em, "frozen": RS.is_frozen(rec),
           "guarded": RS.is_guarded(rec)}
    if apply and RS.is_frozen(rec):
        RS.set_region_freeze(em, False)
        AA.record(actor, "region.migrate_abort", em, subject=em, details={})
        rep["frozen"] = False
    return rep


# ── delete the source copy (later, after the target's backup) ────────────────

def _die_of(rel: str, dies: List[str]) -> Optional[str]:
    """The moved die a bundle file belongs to, or None (workspace, project, verification ...)."""
    area, sub = rel.split("/", 1)
    first = sub.split("/", 1)[0]
    if area == "dies" and first == ".history":
        first = (sub.split("/") + ["", ""])[1]
    if area in ("dies", "cad", "passports"):
        return first if first in dies else None
    if area in ("production", "motor_tests"):
        by_slug = {_slug(d): d for d in dies}
        return by_slug.get(first)
    return None


def delete_source(email: str, bundle_dir: Path, *, apply: bool = False, target_backup_confirmed: bool = False,
                  actor: str = "migration", transport: Any = None) -> Dict[str, Any]:
    """Remove this server's copy of a migrated account, then the bundle folder itself.

    Only files whose bytes still equal the bundle's are deleted.  A changed file OUTSIDE a die (workspace, project,
    verification ...) blocks the whole step: the purge would delete the workspace whatever it holds, and nothing that
    was written after the export is ever thrown away silently.  A DIE is kept whole (all its files, its access entry)
    when any of its files changed or, checked again now, a project that stays links it or another account is in its
    access list.  Append-only logs (audit, verification events, NDA signature lines, duty-results rows) stay and are
    pseudonymised by the purge where the purge does that."""
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim import motor_access as MA
    U = _U()
    em = U._norm(email)
    m = load_manifest(bundle_dir)
    if U._norm(str(m.get("email"))) != em:
        raise MigrationError("the bundle is of another account")
    rec = U.get_user(em)
    blockers: List[str] = []
    if not target_backup_confirmed:
        blockers.append("confirm that the target region's backup of the account has run (--target-backup-confirmed)")
    if not isinstance(rec, dict) or not RS.is_guarded(rec):
        blockers.append("the account is not migrated here (no region guard): run finish first")
    got = _peer_receipt(str(m.get("bundle_id")), transport=transport)
    if got is None or got.get("root_sha256") != m.get("root_sha256") or got.get("verified") is not True:
        blockers.append("the target's receipt is missing or does not match this bundle")
    dies = list(m.get("dies") or [])
    changed, _missing = _source_changes(m, pinned_ok=True)
    keep_dies: Dict[str, List[str]] = {}
    for rel in changed:
        d = _die_of(rel, dies)
        if d is None:
            continue
        keep_dies.setdefault(d, []).append("file changed since the export: " + rel)
    for d in dies:
        for why in die_sharing(d, em, m.get("projects") or []):
            keep_dies.setdefault(d, []).append(why)
    outside = [r for r in changed if _die_of(r, dies) is None]
    if outside:
        blockers.append(f"{len(outside)} file(s) outside the dies changed on this server since the export: review "
                        "them first (nothing written after the export is deleted silently)")
    roots = area_roots()
    delete = []
    for rel in sorted(m["files"]):
        area, sub = rel.split("/", 1)
        if roots.get(area) is None or rel in changed or _die_of(rel, dies) in keep_dies:
            continue
        if (roots[area] / sub).is_file():
            delete.append(rel)
    rep = {"step": "delete-source", "dry_run": not apply, "email": em, "bundle_id": m.get("bundle_id"),
           "blockers": blockers, "delete": len(delete), "changed": changed[:50],
           "kept_dies": {d: why for d, why in sorted(keep_dies.items())},
           "kept_logs": ["admin audit", "verification events", "NDA signature lines", "duty-results ledger rows"]}
    if not apply:
        return rep
    if blockers:
        raise MigrationError("delete-source refused: " + "; ".join(blockers))
    dirs = set()
    for rel in delete:
        area, sub = rel.split("/", 1)
        f = roots[area] / sub
        f.unlink()
        dirs.update(q for q in f.parents if roots[area] in q.parents)
    for dd in sorted(dirs, key=lambda q: len(q.parts), reverse=True):
        try:
            dd.rmdir()                                      # only empty folders
        except OSError:
            pass
    with MA._DA_LOCK:
        acc = MA._load_die_access()
        gone = [k for k in dies if k in acc and k not in keep_dies]
        for k in gone:
            acc.pop(k, None)
        if gone:
            MA._save_die_access(acc)
    _drop_directory_person(em)
    from motor_ai_sim import account_lifecycle as AL
    purge = AL.purge(em, actor=actor, reason="region_migrated")
    shutil.rmtree(Path(bundle_dir), ignore_errors=True)    # the bundle holds the password hash: gone with the copy
    AA.record(actor, "region.migrate_delete_source", em, subject=em,
              details={"bundle": m.get("bundle_id"), "deleted": len(delete), "kept_dies": sorted(keep_dies)})
    return {**rep, "deleted": len(delete), "purge": purge.get("steps"), "bundle_removed": not Path(bundle_dir).exists()}


def _drop_directory_person(email: str) -> None:
    from motor_ai_sim.directory import service as D
    from motor_ai_sim.directory import store as DS
    person = D.find_person_by_email(email)
    if not person:
        return
    pid = person.get("id")
    _mutate_dir_list(DS.affiliations_path(), "affiliations",
                     lambda cur: [a for a in cur if not (isinstance(a, dict) and a.get("person_id") == pid)])
    _mutate_dir_list(DS.people_path(), "people",
                     lambda cur: [p for p in cur if not (isinstance(p, dict) and p.get("id") == pid)])
