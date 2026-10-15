"""Build manifest: what a CAD output set was made from (die, config, parameter fingerprint, versions)."""
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess

from . import store

SCHEMA = "motres-cad-manifest/1"


def git_sha(root=None):
    """Commit the builder runs from: $MOTRES_GIT_SHA, else <root>/GIT_SHA (written into the image), else git."""
    root = root or store.ROOT
    if os.environ.get("MOTRES_GIT_SHA"):
        return os.environ["MOTRES_GIT_SHA"]
    f = os.path.join(root, "GIT_SHA")
    if os.path.exists(f):
        return open(f, encoding="utf-8").read().strip() or "unknown"
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            dirty = subprocess.run(["git", "-C", root, "status", "--porcelain", "--", "cad", "src/motor_ai_sim/cad"],
                                   capture_output=True, text=True, timeout=20).stdout.strip()
            return r.stdout.strip() + ("+dirty" if dirty else "")
    except Exception:
        pass
    return "unknown"


def template_info(template="segmented_flatwire", cad_dir=None):
    """Template name, TEMPLATE_VERSION (parsed from the module, no import) and a content hash of its .py files."""
    d = os.path.join(cad_dir or store.CAD_DIR, "motor_templates", template)
    src = open(os.path.join(d, "__init__.py"), encoding="utf-8").read()
    m = re.search(r'^TEMPLATE_VERSION\s*=\s*"([^"]+)"', src, re.M)
    h = hashlib.sha256()
    for f in sorted(os.listdir(d)):
        if f.endswith(".py"):
            h.update(f.encode())
            h.update(open(os.path.join(d, f), "rb").read())
    return dict(name=template, version=m.group(1) if m else "unknown", sha256=h.hexdigest())


def file_rows(out_dir, names):
    rows = {}
    for n in names:
        p = os.path.join(out_dir, n)
        if os.path.exists(p):
            rows[n] = dict(sha256=store.sha256_file(p), bytes=os.path.getsize(p))
    return rows


def _board_summary(pb):
    """Phase board evidence in the manifest: STEP hash, J13 error, clashes, minimum clearances, lead fit."""
    if not pb:
        return None
    imp = pb.get("import_") or {}
    return dict(step=imp.get("step"), step_sha256=imp.get("sha256"), solids=imp.get("solids"), j13_error_mm=pb.get("j13_error_mm"),
                clashes=[r["group"] for r in pb.get("groups", []) if r.get("status") == "CLASH"],
                min_distance_mm={r["group"]: r["min_distance_mm"] for r in pb.get("groups", [])},
                lead_overlaps=[(l["coil"], l["lead"]) for l in pb.get("leads", []) if l["overlap_mm3"] > 1e-6],
                min_lead_clearance_mm=min([l["clearance_to_board_mm"] for l in pb.get("leads", []) if l["clearance_to_board_mm"] is not None] or [None]))


def build_manifest(eff, profile, pull_report, build_report, step_check, files, freecad_version, seconds, kind="store"):
    """eff = None for a build from an explicit profile file (kind 'profile_file')."""
    sim = (profile.get("profile", {}).get("source", {}) or {}).get("simulation", {}) or {}
    fp = pull_report.get("fingerprint") or sim.get("fingerprint") or {}
    return dict(
        schema=SCHEMA,
        kind=kind,
        die=(eff or {}).get("die"),
        config=(eff or {}).get("variant"),
        motor_id=profile["profile"]["motor_id"],
        cad_variant=profile["profile"].get("cad_variant") or "full",
        param_hash=fp.get("cad_param_hash"),
        cad_overrides_hash=fp.get("cad_overrides_hash") or "none",
        geo_sig=fp.get("geo_sig"),
        geo_sig_hash=fp.get("geo_sig_hash"),
        store_files=(eff or {}).get("files") or sim.get("files"),
        profile_sha256=store.canonical_hash(profile),
        materials=profile["profile"].get("materials"),
        freecad_version=freecad_version,
        template=template_info(profile["profile"].get("template", "segmented_flatwire")),
        git_sha=git_sha(),
        built_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        host=platform.node(),
        seconds=seconds,
        checks=dict(
            gate_ok=bool((build_report.get("gate") or {}).get("ok", False)),
            sketches_fully_constrained="%s/%s" % (build_report.get("sketches_fully_constrained"), build_report.get("sketches_total")),
            volumes=build_report.get("volumes"),
            step_solids=step_check.get("n_solid_objects"),
            step_total_volume=step_check.get("total_volume"),
            step_expected_volume=step_check.get("expected_total_volume"),
            coil_leads=step_check.get("leads"),
            phase_board=_board_summary(build_report.get("phase_board")),
            summary=pull_report.get("summary")),
        files=files)
