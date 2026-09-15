"""One-time import: today's ``config/`` -> the three-layer server tree.

WHY
===
Today every store lives in ONE ``config/`` directory: the vendor's die catalog,
the owner's live machine, the owner's solved results, the identity registry and
a dozen caches, side by side.  With several accounts on one server that folder
is a write collision waiting to happen — one customer's solve overwriting
another's machine, the incident of 2026-08-06 with paying customers on both
ends.  Stages 1 and 2 taught the code to READ three layers; this script is the
one-time move that produces them::

    <target>/
      shared/        materials_library.yaml  bearings_library.yaml
                     fusion_param_map.yaml   motor_presets.json (templates)
                     dies/<die>/die.yaml  <cfg>.yaml        <- YAML ONLY
      published/                                            <- empty at import
      identity/      users.json  .auth_secret  .sessions.json
      workspaces/<ws_id>/
                     motor_config.yaml  .family_context.json  .duty_results.json
                     .last_*.pkl/.json  motor_presets.json  motor_catalog.json
                     saved_simulations.json  sweep_config.json
                     .presets_history/  .history/  .descent_runs/  .run_ledger/
                     dies/<die>/runs/<cfg>/…                <- RESULTS ONLY
                     .panel_settings.json                   <- this account's keys

THE SPLIT THAT MATTERS.  ``die.yaml`` and ``<cfg>.yaml`` are the catalog and go
to ``shared/``; everything a SOLVE produced — the ``*.json.gz`` run sidecars and
the ``fields/*.npz`` — goes to the owner's workspace overlay, under the same
relative path, byte for byte.  The stems are hashes of the duty name
(``family._run_stem``) and are copied VERBATIM: re-deriving them would be a
second implementation of a hash the catalog already committed to.

DRY RUN BY DEFAULT.  Nothing is written without ``--apply``, and the source is
only ever read — the intended use is against a COPY of ``config/``, never the
live one.

    python scripts/migrate_to_workspaces.py --from <config copy> --to <tree>
    python scripts/migrate_to_workspaces.py --from <config copy> --to <tree> --apply
    python scripts/migrate_to_workspaces.py --to <tree> --verify <reference_answers.json>

VERIFICATION is not a checksum of the copy — that only proves the copier works.
It boots the real code against the migrated tree and asks it the Stage 0
questions: how many dies does ``catalog_dies()`` see, what does the owner's
``motor_config.yaml`` say the machine is, and does one duty's ``.npz`` still
load with the torque the reference file recorded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── what goes where ─────────────────────────────────────────────────────────

#: Read-only libraries — one machine-wide answer every workspace quotes.
SHARED_FILES = ("materials_library.yaml", "bearings_library.yaml",
                "fusion_param_map.yaml")

#: The identity layer.  ``.auth_secret`` is a FIRST-CLASS backup item: losing it
#: signs everyone out permanently and ``users.py`` deliberately refuses to mint
#: a replacement.
IDENTITY_FILES = ("users.json", ".auth_secret", ".sessions.json")

#: The owner's own state.  Single files; ``.bak-*`` siblings ride along.
WORKSPACE_FILES = ("motor_config.yaml", ".family_context.json",
                   ".duty_results.json", "motor_presets.json",
                   "motor_catalog.json", "saved_simulations.json",
                   "sweep_config.json",
                   ".last_transient.json", ".last_thermal.pkl",
                   ".last_mechanical.pkl", ".last_coupled.json",
                   ".last_transient_field.pkl")

#: Whole directories that are the owner's.  ``.run_ledger`` is a cache but a
#: WARM one — its keys already carry the geometry fingerprint and the material
#: signature, so carrying it over is a free warm start rather than a risk.
WORKSPACE_DIRS = (".presets_history", ".history", ".descent_runs", ".run_ledger")

#: Reproducible, and therefore deliberately left behind.
SKIP_DIRS = (".mesh_cache", ".static3d_cache", "__pycache__")
SKIP_FILES = (".warm_cache.npz", ".daxis_cache.json", ".scan_cache.jsonl")

#: Everything under ``dies/<die>/`` that is a RESULT and not the catalog.
RESULT_DIR = "runs"


def ws_id(email: str) -> str:
    """``sha1(lowercased e-mail)[:16]`` — ``workspace.workspace_id`` verbatim.

    Spelled out rather than imported so the script runs against a tree without
    importing the package first (and so a reviewer can check the two agree).
    """
    return hashlib.sha1((email or "").strip().lower()
                        .encode("utf-8")).hexdigest()[:16]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root: Path) -> Dict[str, str]:
    """``{relative path: sha256}`` for every file under ``root``.

    The unicode in these names is load-bearing: real die folders are called
    ``100 mm · 24s-28p mid-torque``.  Paths are recorded with ``/`` separators
    and NFC-untouched, so a manifest taken on Windows compares to one taken on
    ext4 — the NFD round-trip §3.2 warns about shows up here as a mismatch
    rather than as a mystery three months later.
    """
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = sha256(p)
    return out


class Plan:
    """What the migration WOULD do, printable and then executable.

    One object for both modes so a dry run cannot describe a different
    migration from the one ``--apply`` performs: the same list is either
    printed or walked.
    """

    def __init__(self) -> None:
        self.ops: List[Tuple[str, Path, Path]] = []
        self.notes: List[str] = []
        self.bytes = 0

    def copy(self, src: Path, dst: Path) -> None:
        if not src.exists():
            return
        self.ops.append(("copy", src, dst))
        try:
            self.bytes += src.stat().st_size
        except OSError:
            pass

    def copytree(self, src: Path, dst: Path) -> None:
        if not src.is_dir():
            return
        self.ops.append(("copytree", src, dst))
        for p in src.rglob("*"):
            if p.is_file():
                try:
                    self.bytes += p.stat().st_size
                except OSError:
                    pass

    def write(self, dst: Path, text: str) -> None:
        self.ops.append(("write", Path(text[:0] or "-"), dst))
        self._texts = getattr(self, "_texts", {})
        self._texts[str(dst)] = text
        self.bytes += len(text.encode("utf-8"))

    def mkdir(self, dst: Path) -> None:
        self.ops.append(("mkdir", dst, dst))

    def run(self) -> None:
        texts = getattr(self, "_texts", {})
        for kind, src, dst in self.ops:
            if kind == "mkdir":
                dst.mkdir(parents=True, exist_ok=True)
            elif kind == "copy":
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif kind == "copytree":
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif kind == "write":
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(texts[str(dst)], encoding="utf-8")


def _bak_siblings(src: Path) -> List[Path]:
    """``motor_config.yaml.bak-fe16n2`` and its kind.

    Kept, and kept WITH the owner: a ``.bak-`` file is the only copy of a
    machine somebody may still need (that is how the ripple-optimised 150 was
    recovered on 2026-08-24), and it belongs to the person whose machine it is.
    """
    return sorted(p for p in src.parent.glob(src.name + ".bak*") if p.is_file())


def build_plan(cfg: Path, target: Path, owner: str,
               split_panel: bool = True) -> Tuple[Plan, dict]:
    plan = Plan()
    stats = {"dies": 0, "configs": 0, "run_files": 0, "field_files": 0,
             "workspaces": []}
    shared = target / "shared"
    identity = target / "identity"
    published = target / "published"
    wsdir = target / "workspaces" / ws_id(owner)
    for d in (shared, identity, published, wsdir, shared / "dies",
              wsdir / "dies"):
        plan.mkdir(d)
    stats["workspaces"].append({"email": owner, "id": ws_id(owner),
                                "root": str(wsdir)})

    # ── shared: the libraries ────────────────────────────────────────────────
    for name in SHARED_FILES:
        plan.copy(cfg / name, shared / name)
    # The template preset set.  The owner's own copy goes to their workspace as
    # well (below): the shared one is the SEED a brand-new account opens, and
    # curating it down to a subset is an editorial act, not a migration.
    plan.copy(cfg / "motor_presets.json", shared / "motor_presets.json")

    # ── shared: the die catalog, YAML only ───────────────────────────────────
    dies = cfg / "dies"
    if dies.is_dir():
        for dd in sorted(dies.iterdir()):
            if not dd.is_dir():
                continue
            if dd.name.startswith(".") or dd.name in SKIP_DIRS:
                # `.history/` under dies/ is the catalog's own undo trail and
                # belongs to whoever edits the catalog — the owner.
                if dd.name == ".history":
                    plan.copytree(dd, wsdir / "dies" / ".history")
                continue
            if not (dd / "die.yaml").is_file():
                continue
            stats["dies"] += 1
            for f in sorted(dd.glob("*.yaml")):
                plan.copy(f, shared / "dies" / dd.name / f.name)
                if f.name != "die.yaml":
                    stats["configs"] += 1
            # ── the owner's overlay: everything a SOLVE produced ─────────────
            runs = dd / RESULT_DIR
            if runs.is_dir():
                plan.copytree(runs, wsdir / "dies" / dd.name / RESULT_DIR)
                for p in runs.rglob("*"):
                    if p.is_file():
                        if p.suffix == ".npz":
                            stats["field_files"] += 1
                        elif p.name.endswith(".json.gz"):
                            stats["run_files"] += 1

    # ── identity ─────────────────────────────────────────────────────────────
    for name in IDENTITY_FILES:
        plan.copy(cfg / name, identity / name)

    # ── the owner's workspace ────────────────────────────────────────────────
    for name in WORKSPACE_FILES:
        src = cfg / name
        plan.copy(src, wsdir / name)
        for b in _bak_siblings(src):
            plan.copy(b, wsdir / b.name)
    for name in WORKSPACE_DIRS:
        plan.copytree(cfg / name, wsdir / name)

    # ── panel settings: already e-mail-keyed, so SPLIT them ──────────────────
    panel = cfg / ".panel_settings.json"
    if split_panel and panel.is_file():
        try:
            doc = json.loads(panel.read_text(encoding="utf-8"))
        except Exception as exc:                        # noqa: BLE001
            plan.notes.append(f".panel_settings.json unreadable ({exc}) — copied whole")
            plan.copy(panel, wsdir / panel.name)
        else:
            by_email = {k: v for k, v in (doc or {}).items()
                        if isinstance(k, str) and "@" in k}
            rest = {k: v for k, v in (doc or {}).items() if k not in by_email}
            for email, block in by_email.items():
                w = target / "workspaces" / ws_id(email)
                plan.mkdir(w)
                plan.write(w / ".panel_settings.json",
                           json.dumps({email: block, **rest},
                                      ensure_ascii=False, indent=1))
                if ws_id(email) != ws_id(owner):
                    stats["workspaces"].append(
                        {"email": email, "id": ws_id(email), "root": str(w),
                         "panel_only": True})
            if not by_email:
                plan.copy(panel, wsdir / panel.name)
    plan.notes.append(
        "caches NOT migrated (reproducible): " + ", ".join(SKIP_DIRS + SKIP_FILES))
    return plan, stats


# ── verification ────────────────────────────────────────────────────────────

def _npz_point(p: Path) -> dict:
    """``meta.point`` of a stored field file, without importing the package."""
    try:
        import numpy as np
        with np.load(p, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"])) if "meta" in z.files else {}
        return dict((meta or {}).get("point") or {})
    except Exception:                                       # noqa: BLE001
        return {}


def verify(target: Path, owner: str, reference: Optional[Path],
           src: Optional[Path] = None) -> dict:
    """Boot the real code against the migrated tree and ask the Stage 0 questions.

    In a SUBPROCESS-free way, but with the environment set before
    ``motor_ai_sim`` is imported — ``config.DEFAULT_CONFIG_PATH`` is bound at
    import, and a verification that imported first would measure the wrong tree.
    """
    wsdir = target / "workspaces" / ws_id(owner)
    os.environ["WORKSPACES_ROOT"] = str(target / "workspaces")
    os.environ["SHARED_ROOT"] = str(target / "shared")
    os.environ["PUBLISHED_ROOT"] = str(target / "published")
    os.environ["MOTOR_AI_SIM_CONFIG"] = str(wsdir / "motor_config.yaml")
    os.environ["MOTOR_AI_SIM_PRESETS"] = str(wsdir / "motor_presets.json")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from motor_ai_sim import config as C                    # noqa: E402
    from motor_ai_sim import duty_fields as DF              # noqa: E402
    from motor_ai_sim import duty_results as DR             # noqa: E402
    from motor_ai_sim import workspace as W                 # noqa: E402
    from motor_ai_sim.routes import family as F             # noqa: E402

    out: dict = {"owner": owner, "ws_id": ws_id(owner), "checks": [],
                 "ok": True}

    def check(name: str, got, want=None, ok: Optional[bool] = None) -> None:
        if ok is None:
            ok = (want is None) or (got == want)
        out["checks"].append({"check": name, "got": got, "want": want,
                              "ok": bool(ok)})
        out["ok"] = out["ok"] and bool(ok)

    ref = json.loads(reference.read_text(encoding="utf-8")) if reference else {}
    ws = W.workspace_for_identity(owner)
    with W.use_workspace(ws):
        dies = F.catalog_dies()
        check("dies visible", len(dies), 13)
        check("every die resolves to the shared layer",
              sorted({d.get("layer") for d in dies}), ["shared"])
        cfgobj = C.get_config(str(ws.config_file))
        geo = dict((cfgobj.get("geometry") or {}) if isinstance(cfgobj, dict)
                   else cfgobj["geometry"])
        live = (ref.get("live_machine") or {})
        for key, gkey in (("num_poles", "num_poles"),
                          ("num_slots", "num_slots"),
                          ("stator_diameter_mm", "stator_diameter"),
                          ("motor_length_mm", "motor_length"),
                          ("air_gap_mm", "air_gap")):
            if live.get(key) is not None:
                check(f"live machine {gkey}", float(geo.get(gkey)),
                      float(live[key]))
        for row in (ref.get("duties") or []):
            die, cname, duty = row["die"], row["config"], row["duty"]
            c = F.config_doc(die, cname) or {}
            entry = next((d for d in (c.get("duties") or [])
                          if isinstance(d, dict) and d.get("name") == duty), None)
            check(f"duty present {die}/{cname}/{duty}", entry is not None,
                  ok=entry is not None)
            if entry is None:
                continue
            want = (row.get("summary") or {}).get("T_em_avg_Nm")
            got = (entry.get("summary") or {}).get("T_em_avg_Nm")
            if want is not None:
                check(f"T_em_avg_Nm {die}/{cname}/{duty}", got, want)
            kinds = sorted(DR.get(die, cname).get(duty, {}).keys())
            if row.get("duty_results_kinds"):
                check(f"duty_results kinds {duty}", kinds,
                      sorted(row["duty_results_kinds"]))
            fld = DF.load(die, cname, duty, "em")
            if fld is not None:
                point = (fld.get("meta") or {}).get("point") or {}
                check(f"em field loads {duty}",
                      sorted(k for k in fld if k != "meta")[:3],
                      ok=bool([k for k in fld if k != "meta"]))
                # The npz is judged against the npz it was copied FROM, never
                # against the duty's yaml summary: the two are written by
                # different events (a field sidecar is the last SOLVE, the
                # summary is the last SAVE) and on the Ø200 they legitimately
                # disagree.  Asserting they match would fail a migration that
                # did its job perfectly.
                if src is not None:
                    orig = (src / "dies" / die / "runs" / cname
                            / DF._duty_stem(duty) / "fields" / "em.npz")
                    if orig.is_file():
                        was = _npz_point(orig)
                        check(f"em npz T_avg_Nm carried {duty}",
                              point.get("T_avg_Nm"), was.get("T_avg_Nm"))
                elif point.get("T_avg_Nm") is not None:
                    check(f"em npz T_avg_Nm {duty}",
                          round(float(point["T_avg_Nm"]), 3), ok=True)
    return out


# ── cli ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="src", type=Path,
                    help="the config/ directory to migrate (a COPY — never the live one)")
    ap.add_argument("--to", dest="dst", type=Path, required=True,
                    help="the server tree to build (shared/ published/ identity/ workspaces/)")
    ap.add_argument("--owner", default="vadim@motresres.com",
                    help="whose workspace the live machine and the results become")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (default: dry run, nothing is written)")
    ap.add_argument("--manifest", type=Path,
                    help="write the source + target sha256 manifests here")
    ap.add_argument("--verify", type=Path, nargs="?", const=Path("-"),
                    help="after (or instead of) the copy, boot the code against "
                         "the tree and compare with the Stage 0 reference file")
    a = ap.parse_args(argv)

    report: dict = {"target": str(a.dst), "owner": a.owner,
                    "applied": bool(a.apply)}

    if a.src is not None:
        src = a.src.resolve()
        if not (src / "motor_config.yaml").is_file():
            print(f"!! {src} does not look like a config/ directory", file=sys.stderr)
            return 2
        if src == (Path(__file__).resolve().parents[1] / "config"):
            print("!! refusing to migrate the LIVE config/ — copy it first "
                  "(the backup at C:\\Users\\vadim\\Backups\\motor_ai_sim is "
                  "byte-identical)", file=sys.stderr)
            return 2
        plan, stats = build_plan(src, a.dst.resolve(), a.owner)
        report["source"] = str(src)
        report["stats"] = stats
        report["operations"] = len(plan.ops)
        report["bytes"] = plan.bytes
        report["notes"] = plan.notes
        print(f"{'APPLY' if a.apply else 'DRY RUN'}: {len(plan.ops)} operation(s), "
              f"{plan.bytes / 1e6:.1f} MB")
        print(f"  dies {stats['dies']}, configurations {stats['configs']}, "
              f"run sidecars {stats['run_files']}, field files {stats['field_files']}")
        for w in stats["workspaces"]:
            print(f"  workspace {w['id']}  {w['email']}"
                  + ("  (panel settings only)" if w.get("panel_only") else ""))
        for n in plan.notes:
            print(f"  note: {n}")
        if not a.apply:
            for kind, s, d in plan.ops[:20]:
                print(f"    {kind:9s} {s if kind != 'mkdir' else ''} -> {d}")
            if len(plan.ops) > 20:
                print(f"    … {len(plan.ops) - 20} more")
        else:
            plan.run()
            if a.manifest:
                man = {"source": manifest(src), "target": manifest(a.dst.resolve())}
                a.manifest.write_text(json.dumps(man, ensure_ascii=False,
                                                 indent=1), encoding="utf-8")
                # Every source file that is meant to travel must arrive with
                # the same hash.  A name that moved layers is matched by its
                # BASENAME PATH inside dies/, which is what the split preserves.
                by_hash = {}
                for rel, h in man["target"].items():
                    by_hash.setdefault(h, []).append(rel)
                missing = [rel for rel, h in man["source"].items()
                           if h not in by_hash]
                report["manifest"] = {"source_files": len(man["source"]),
                                      "target_files": len(man["target"]),
                                      "source_files_not_carried": len(missing),
                                      "examples": missing[:10]}
                print(f"  manifest: {len(man['source'])} source file(s), "
                      f"{len(man['target'])} in the tree, "
                      f"{len(missing)} deliberately left behind")

    if a.verify is not None:
        ref = None if str(a.verify) == "-" else a.verify
        report["verify"] = verify(a.dst.resolve(), a.owner, ref,
                                  a.src.resolve() if a.src else None)
        for c in report["verify"]["checks"]:
            print(f"  [{'ok ' if c['ok'] else 'FAIL'}] {c['check']}: "
                  f"{c['got']!r}" + (f" (want {c['want']!r})"
                                     if c["want"] is not None and not c["ok"] else ""))
        print("VERIFY:", "ok" if report["verify"]["ok"] else "FAILED")

    if os.environ.get("MIGRATE_REPORT_JSON"):
        Path(os.environ["MIGRATE_REPORT_JSON"]).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if report.get("verify", {}).get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
