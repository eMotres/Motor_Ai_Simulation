"""Promote a verified administrator's legacy workspace catalog into shared.

This is deliberately dry-run by default. It copies only catalog YAML and
source-owned, build-matched duty evidence; it never removes or edits the
administrator's workspace. Differing shared files are promoted only when the
admin source is newer. Ambiguous provenance, stale build signatures, geometry
mismatches, and newer shared files are reported as conflicts and left alone.

Example (run on the server as a maintenance operator)::

    ADMIN_EMAILS=owner@example.com python scripts/promote_admin_catalog.py \
      --email owner@example.com --workspaces-root /srv/motres/workspaces \
      --shared-root /srv/motres/shared --die "CIANO14 40 new" \
      --authoritative-die "CIANO14 40 new"

Add ``--apply`` only after reviewing the printed plan. Apply creates a dated
backup of every shared file it replaces and rolls back replaced files if a
later replacement fails.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:  # pragma: no cover - dependency is in app runtime
    raise SystemExit("PyYAML is required to inspect catalog documents") from exc


@dataclass(frozen=True)
class PlannedFile:
    source: Path | None
    destination: Path
    content: bytes | None
    reason: str


def workspace_id(email: str) -> str:
    return hashlib.sha1(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def _read_yaml(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"not a YAML mapping: {path}")
    return doc


def _fingerprint(die: dict[str, Any], cfg: dict[str, Any]) -> str:
    """The same build fingerprint used by family catalog saved duties."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from motor_ai_sim.routes.family import _build_sig
    return _build_sig(die, cfg)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _safe_source(source: Path, root: Path) -> bool:
    if not _is_within(source, root):
        return False
    try:
        rel = source.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    current = root
    if current.is_symlink():
        return False
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _safe_destination(path: Path, root: Path) -> bool:
    """Reject path traversal and every symlink component at a write seam."""
    root = root.resolve()
    try:
        rel = path.absolute().relative_to(root)
        if not path.resolve(strict=False).is_relative_to(root):
            return False
    except (OSError, ValueError):
        return False
    current = root
    if current.is_symlink():
        return False
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _geometry(doc: dict[str, Any]) -> str:
    return json.dumps(doc.get("geometry") or {}, sort_keys=True,
                      separators=(",", ":"), default=str)


def _has_foreign_provenance(doc: dict[str, Any], path: Path) -> bool:
    name = str(doc.get("name") or path.stem)
    return bool(doc.get("published_by") or " · by " in name)


def _safe_relative_payload(raw: Any, cfg_name: str) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    posix = PurePosixPath(raw.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or len(posix.parts) < 3:
        return None
    if posix.parts[0] != "runs" or posix.parts[1] != cfg_name:
        return None
    return posix.as_posix()


def _evidence_files(source_die: Path, cfg_name: str, cfg: dict[str, Any],
                    build_sig: str, warnings: list[str],
                    provenance: list[dict[str, Any]] | None = None
                    ) -> tuple[list[Path], bool]:
    """Return only run and field files referenced by current matching duties."""
    run_root = source_die / "runs" / cfg_name
    allowed: set[Path] = set()
    field_dirs: set[Path] = set()
    field_dir_anchors: dict[Path, dict[str, Any]] = {}
    references_valid = True
    for duty in cfg.get("duties") or []:
        if not isinstance(duty, dict):
            continue
        duty_sig = duty.get("build_sig")
        if duty_sig and duty_sig != build_sig:
            # A result recorded under a different build is retained in the
            # original workspace but is not promoted with the new definition.
            if duty.get("runs"):
                if any(isinstance(run, dict) and run.get("payload_file")
                       for run in duty["runs"].values()):
                    references_valid = False
                warnings.append(
                    f"duty build fingerprint mismatch; evidence retained in source: {source_die.name}/{cfg_name}/{duty.get('name')}")
            continue
        runs = duty.get("runs") or {}
        if not isinstance(runs, dict):
            continue
        for drive, run in runs.items():
            if not isinstance(run, dict):
                continue
            if run.get("build_sig") != build_sig:
                if run.get("payload_file"):
                    references_valid = False
                warnings.append(
                    f"run build fingerprint mismatch; evidence retained in source: {source_die.name}/{cfg_name}/{duty.get('name')}/{drive}")
                continue
            rel = _safe_relative_payload(run.get("payload_file"), cfg_name)
            if not rel:
                if run.get("payload_file"):
                    references_valid = False
                continue
            path = source_die / Path(*PurePosixPath(rel).parts)
            if not _safe_source(path, source_die) or not path.is_file():
                references_valid = False
                warnings.append(f"missing or unsafe run payload skipped: {path}")
                continue
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    payload = json.load(f)
                if (str(payload.get("name") or "") != str(duty.get("name") or "")
                        or str(payload.get("drive") or "") != str(drive)):
                    references_valid = False
                    warnings.append(f"run payload identity mismatch skipped: {path}")
                    continue
            except Exception as exc:  # malformed evidence stays in source
                references_valid = False
                warnings.append(f"unreadable run payload skipped {path}: {type(exc).__name__}")
                continue
            allowed.add(path)
            inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
            geo_fp = inner.get("geo_fingerprint") or inner.get("geometry_fingerprint")
            run_anchor = None
            if isinstance(geo_fp, str) and geo_fp:
                run_anchor = {
                    "die": source_die.name, "config": cfg_name,
                    "duty": str(duty.get("name") or ""), "drive": str(drive),
                    "build_sig": build_sig, "geometry_fingerprint": geo_fp,
                    "geometry_fingerprint_v2": inner.get("geometry_fingerprint_v2")
                        or inner.get("geo_fingerprint_v2"),
                    "catalog_refs": inner.get("catalog_refs"),
                    "computed_at": inner.get("computed_at"),
                    "point": _run_operating_point(inner),
                }
                if provenance is not None:
                    provenance.append(run_anchor)
            suffix = f".{drive}.json.gz"
            filename = path.name
            if filename.endswith(suffix):
                field_dir = run_root / filename[:-len(suffix)]
                field_dirs.add(field_dir)
                if run_anchor is not None:
                    field_dir_anchors[field_dir] = run_anchor

    for directory in field_dirs:
        if not directory.is_dir() or not _safe_source(directory, source_die):
            continue
        for p in directory.glob("fields/*.npz"):
            if not (_safe_source(p, source_die) and p.is_file()):
                continue
            anchor = field_dir_anchors.get(directory)
            if anchor is None:
                references_valid = False
                warnings.append(f"unmatched field directory skipped: {directory}")
                continue
            if not _valid_field_metadata(p, anchor):
                references_valid = False
                warnings.append(f"field metadata does not match current run; evidence retained in source: {p}")
                continue
            allowed.add(p)
    return sorted(allowed), references_valid


def _run_operating_point(payload: dict[str, Any]) -> dict[str, Any]:
    """Pick only operating-point fields that legacy result rows may repeat."""
    out: dict[str, Any] = {}
    sources = [payload]
    for name in ("_summary_args", "summary", "point", "params"):
        val = payload.get(name)
        if isinstance(val, dict):
            sources.append(val)
    aliases = {
        "rpm": ("rpm",),
        "i_phase_rms": ("I_phase_rms", "i_phase_rms", "current_a"),
        "gamma_deg": ("gamma_deg", "gamma_effective_deg"),
        "coil_temp_c": ("coil_temp_c", "coil_temp_C"),
        "magnet_temp_c": ("magnet_temp_c", "magnet_temp_C"),
        "ambient_temp": ("ambient_temp", "ambient_temp_c", "ambient_C"),
    }
    for normalized, names in aliases.items():
        for source in sources:
            value = next((source[n] for n in names if source.get(n) is not None), None)
            if value is not None:
                try:
                    out[normalized] = float(value)
                except (TypeError, ValueError):
                    pass
                break
    return out


def _valid_field_metadata(path: Path, anchor: dict[str, Any]) -> bool:
    """Only promote NPZ evidence whose embedded identity and geometry match."""
    try:
        import numpy as np
        with np.load(path, allow_pickle=False) as data:
            raw = data["meta"].item()
        meta = json.loads(str(raw))
    except Exception:
        return False
    return (isinstance(meta, dict)
            and meta.get("die") == anchor.get("die")
            and meta.get("config") == anchor.get("config")
            and meta.get("duty") == anchor.get("duty")
            and meta.get("geometry_fingerprint") == anchor.get("geometry_fingerprint")
            and meta.get("kind") == path.stem)


def _catalog_refs_match_config(refs: Any, die: str, cfg: dict[str, Any]) -> bool:
    """Check a legacy row names this die, configured items, and named defaults."""
    if not isinstance(refs, list) or not refs:
        return False
    actual: set[tuple[str, str]] = set()
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("kind"), str) \
                or not isinstance(ref.get("id"), str):
            return False
        actual.add((ref["kind"], ref["id"]))
    if len(actual) != len(refs) or ("dies", die) not in actual:
        return False
    expected: set[tuple[str, str]] = {("dies", die)}
    materials = cfg.get("materials")
    if isinstance(materials, dict):
        for value in materials.values():
            if isinstance(value, str) and value.strip():
                name = value.strip()
                match = next((row for row in actual if row[0] == "materials"
                              and (row[1] == name or row[1].endswith("/" + name))), None)
                if match is None:
                    return False
                expected.add(match)
    controller = cfg.get("controller")
    if isinstance(controller, dict) and isinstance(controller.get("device"), str):
        device = controller["device"].strip()
        if device:
            expected.add(("devices", device))
    bearings = cfg.get("bearings")
    bearing_names: set[str] = set()
    if isinstance(bearings, dict):
        bearing_names.update(v.strip() for v in bearings.values()
                             if isinstance(v, str) and v.strip())
        for value in bearings.values():
            if isinstance(value, dict):
                bearing_names.update(v.strip() for v in value.values()
                                     if isinstance(v, str) and v.strip())
    if actual - expected:
        for kind, ident in actual - expected:
            if kind == "bearings" and any(ident == n or ident.endswith("/" + n)
                                            for n in bearing_names):
                expected.add((kind, ident))
            elif kind == "materials" and ident == "coolant/air":
                # The geometry's air_gap/in_band/out_band are always air,
                # including when no coolant is assigned in the saved config.
                # Require a resolved catalog card; an arbitrary extra
                # material must never make an old result eligible.
                from motor_ai_sim.passport_v1.snapshot import AIR_PARTS
                ref = next(r for r in refs if r["kind"] == kind and r["id"] == ident)
                if not AIR_PARTS or not ref.get("source") or not ref.get("revision") \
                        or ref.get("error"):
                    return False
                expected.add((kind, ident))
            else:
                return False
    return actual == expected


def _legacy_row_matches_run(record: dict[str, Any], anchor: dict[str, Any],
                            cfg: dict[str, Any]) -> bool:
    fp = anchor.get("geometry_fingerprint")
    if not fp or record.get("geometry_fingerprint") != fp:
        return False
    if not _catalog_refs_match_config(record.get("catalog_refs"),
                                      str(anchor.get("die") or ""), cfg):
        return False
    record_fp2 = record.get("geometry_fingerprint_v2")
    anchor_fp2 = anchor.get("geometry_fingerprint_v2")
    if record_fp2 and anchor_fp2 and record_fp2 != anchor_fp2:
        return False
    anchor_refs = anchor.get("catalog_refs")
    if isinstance(anchor_refs, list) and anchor_refs \
            and record.get("catalog_refs") != anchor_refs:
        return False
    point = record.get("point")
    if isinstance(point, dict) and point:
        observed = _run_operating_point(point)
        expected = anchor.get("point") if isinstance(anchor.get("point"), dict) else {}
        common = set(observed) & set(expected)
        if not common:
            return False
        for key in common:
            if abs(observed[key] - expected[key]) > max(0.02, abs(expected[key]) * 1e-4):
                return False
    else:
        # Point-less coupled records are acceptable only when their source
        # timestamp is the exact verified EM run they summarize.
        if not record.get("computed_at") or record.get("computed_at") != anchor.get("computed_at"):
            return False
    return True


def _copy_decision(source: Path, destination: Path,
                   conflicts: list[str]) -> tuple[bool, str]:
    if not destination.exists():
        return True, "new shared file"
    if not destination.is_file() or destination.is_symlink():
        conflicts.append(f"shared destination is not a regular file: {destination}")
        return False, "destination conflict"
    if _sha(source) == _sha(destination):
        return False, "identical"
    try:
        source_mtime = source.stat().st_mtime_ns
        target_mtime = destination.stat().st_mtime_ns
    except OSError as exc:
        conflicts.append(f"cannot compare timestamps for {destination}: {exc}")
        return False, "timestamp conflict"
    if source_mtime <= target_mtime:
        conflicts.append(f"shared file differs and is not older than admin source: {destination}")
        return False, "newer or ambiguous shared copy"
    return True, "newer verified admin source (shared file will be backed up)"


def _result_rows(source_store: Path, die_doc: dict[str, Any], cfg_docs: dict[str, dict[str, Any]],
                 eligible: set[str], warnings: list[str],
                 conflicts: list[str],
                 run_anchors: dict[str, list[dict[str, Any]]] | None = None
                 ) -> dict[str, Any]:
    if not source_store.exists():
        return {}
    try:
        raw = json.loads(source_store.read_text(encoding="utf-8"))
    except Exception as exc:
        conflicts.append(f"cannot read admin result ledger {source_store}: {type(exc).__name__}")
        return {}
    source_results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(source_results, dict):
        conflicts.append(f"admin result ledger has no results mapping: {source_store}")
        return {}
    node = source_results.get(str(die_doc.get("name") or ""), {})
    out: dict[str, Any] = {}
    for cfg_name in eligible:
        cfg = cfg_docs[cfg_name]
        expected = _fingerprint(die_doc, cfg)
        duty_names = {str(duty.get("name")) for duty in (cfg.get("duties") or [])
                      if isinstance(duty, dict) and duty.get("name")}
        cfg_rows = node.get(cfg_name, {}) if isinstance(node, dict) else {}
        if not isinstance(cfg_rows, dict):
            continue
        kept = {}
        for duty, kinds in cfg_rows.items():
            if str(duty) not in duty_names:
                warnings.append(f"orphan result skipped: {die_doc.get('name')}/{cfg_name}/{duty}")
                continue
            if not isinstance(kinds, dict):
                continue
            anchors = (run_anchors or {}).get(cfg_name, [])
            valid = {}
            for kind, rec in kinds.items():
                if not isinstance(rec, dict):
                    continue
                if rec.get("build_sig") == expected:
                    valid[kind] = rec
                    continue
                # Older compact thermal/mechanical records predate build_sig.
                # They can be retained only when a source run in this exact
                # config/duty was independently validated against the current
                # configuration and their own geometry/catalog/point provenance
                # binds them to that run. Never synthesize a build signature.
                if (not rec.get("build_sig") and any(
                        a.get("duty") == str(duty)
                        and _legacy_row_matches_run(rec, a, cfg)
                        for a in anchors)):
                    valid[kind] = rec
            for kind, rec in kinds.items():
                if (isinstance(rec, dict) and rec.get("build_sig") != expected
                        and kind not in valid):
                    warnings.append(f"stale/unfingerprinted result skipped: {die_doc.get('name')}/{cfg_name}/{duty}/{kind}")
            if valid:
                kept[str(duty)] = valid
        if kept:
            out[cfg_name] = kept
    return out


def build_plan(email: str, admin_emails: Iterable[str], workspaces_root: Path,
               shared_root: Path, *, die_names: set[str] | None = None,
               authoritative_dies: set[str] | None = None
               ) -> tuple[list[PlannedFile], list[str], list[str]]:
    """Build a no-write plan for one registry-confirmed administrator."""
    normalized = email.strip().lower()
    admins = {e.strip().lower() for e in admin_emails if e.strip()}
    if not normalized or normalized not in admins:
        raise PermissionError("email must be present in configured ADMIN_EMAILS")
    workspaces_root = Path(workspaces_root).resolve()
    shared_root = Path(shared_root).resolve()
    source_root = workspaces_root / workspace_id(normalized)
    if source_root.parent != workspaces_root or source_root.is_symlink():
        raise PermissionError("admin source workspace path is not canonical")
    source_dies = source_root / "dies"
    shared_dies = shared_root / "dies"
    selected_dies = {str(x) for x in (die_names or ())}
    authorized_dies = {str(x) for x in (authoritative_dies or ())}
    if selected_dies and not authorized_dies.issubset(selected_dies):
        raise ValueError("--authoritative-die must also be selected with --die")
    if (not source_root.is_dir() or not _safe_source(source_dies, source_root)
            or not source_dies.is_dir() or not shared_dies.is_dir()
            or shared_dies.is_symlink()):
        raise FileNotFoundError("admin workspace and shared dies directories must exist")

    files: list[PlannedFile] = []
    warnings: list[str] = []
    conflicts: list[str] = []
    replacements: list[PlannedFile] = []
    shared_ledger = shared_root / ".duty_results.json"
    source_ledger = source_root / ".duty_results.json"
    if source_ledger.exists() and (not _safe_source(source_ledger, source_root)
                                   or not source_ledger.is_file()):
        conflicts.append(f"admin result ledger is not a regular contained file: {source_ledger}")
    try:
        shared_ledger_doc = (json.loads(shared_ledger.read_text(encoding="utf-8"))
                             if shared_ledger.is_file()
                             else {"version": 1, "results": {}})
        if not isinstance(shared_ledger_doc, dict) or not isinstance(
                shared_ledger_doc.get("results"), dict):
            raise ValueError("missing results mapping")
    except Exception as exc:
        shared_ledger_doc = None
        if shared_ledger.exists():
            conflicts.append(f"cannot read shared result ledger {shared_ledger}: {type(exc).__name__}")
    ledger_changed = False
    for source_die in sorted(p for p in source_dies.iterdir() if p.is_dir()):
        if selected_dies and source_die.name not in selected_dies:
            continue
        source_die_file = source_die / "die.yaml"
        if (not _safe_source(source_die, source_dies)
                or not _safe_source(source_die_file, source_dies)
                or not source_die_file.is_file()):
            warnings.append(f"skipped non-catalog or unsafe source folder: {source_die}")
            continue
        try:
            source_die_doc = _read_yaml(source_die_file)
        except Exception as exc:
            conflicts.append(f"cannot read source die document {source_die}: {type(exc).__name__}")
            continue
        if _has_foreign_provenance(source_die_doc, source_die):
            warnings.append(f"skipped non-admin provenance: {source_die.name}")
            continue
        target_die = shared_dies / source_die.name
        if target_die.exists() and (not target_die.is_dir() or target_die.is_symlink()):
            conflicts.append(f"shared die destination is unsafe: {target_die}")
            continue
        die_actions: list[PlannedFile] = []
        authoritative = source_die.name in authorized_dies
        target_die_doc_path = target_die / "die.yaml"
        if target_die_doc_path.is_file():
            try:
                target_die_doc = _read_yaml(target_die_doc_path)
            except Exception as exc:
                conflicts.append(f"cannot read shared die document {target_die_doc_path}: {type(exc).__name__}")
                continue
            if _geometry(source_die_doc) != _geometry(target_die_doc):
                if authoritative:
                    die_actions.append(PlannedFile(
                        source_die_file, target_die_doc_path, None,
                        "explicitly user-authorized authoritative geometry update"))
                else:
                    conflicts.append(f"geometry differs for {source_die.name}; no files promoted")
                    continue
        else:
            eligible_die, reason = _copy_decision(source_die / "die.yaml", target_die_doc_path, conflicts)
            if eligible_die:
                die_actions.append(PlannedFile(source_die / "die.yaml", target_die_doc_path, None, reason))

        cfg_docs: dict[str, dict[str, Any]] = {}
        eligible_configs: set[str] = set()
        anchors_by_config: dict[str, list[dict[str, Any]]] = {}
        config_actions: list[PlannedFile] = []
        evidence_actions: list[PlannedFile] = []
        die_complete = True
        for source_cfg_path in sorted(source_die.glob("*.yaml")):
            if source_cfg_path.name == "die.yaml":
                continue
            if not _safe_source(source_cfg_path, source_die):
                die_complete = False
                continue
            cfg_name = source_cfg_path.stem
            try:
                cfg = _read_yaml(source_cfg_path)
            except Exception as exc:
                target_cfg = target_die / source_cfg_path.name
                if authoritative and target_cfg.is_file() and not target_cfg.is_symlink():
                    warnings.append(f"unreadable source config preserved from shared: {source_cfg_path}")
                else:
                    conflicts.append(f"cannot read source configuration {source_cfg_path}: {type(exc).__name__}")
                    die_complete = False
                continue
            if _has_foreign_provenance(cfg, source_cfg_path) or str(cfg.get("die") or source_die.name) != source_die.name:
                target_cfg = target_die / source_cfg_path.name
                warnings.append(f"skipped ambiguous/foreign configuration: {source_cfg_path}")
                if not (authoritative and target_cfg.is_file() and not target_cfg.is_symlink()):
                    die_complete = False
                continue
            cfg_docs[cfg_name] = cfg
            local_conflicts: list[str] = []
            promote, reason = _copy_decision(source_cfg_path, target_die / source_cfg_path.name,
                                             local_conflicts)
            if local_conflicts:
                target_cfg = target_die / source_cfg_path.name
                if (authoritative and target_cfg.is_file() and not target_cfg.is_symlink()
                        and all("shared file differs" in item for item in local_conflicts)):
                    warnings.extend(f"source config preserved from shared: {item}"
                                    for item in local_conflicts)
                else:
                    conflicts.extend(local_conflicts)
                    die_complete = False
                continue
            eligible_configs.add(cfg_name)
            build_sig = _fingerprint(source_die_doc, cfg)
            run_anchors: list[dict[str, Any]] = []
            evidence, references_valid = _evidence_files(
                source_die, cfg_name, cfg, build_sig, warnings, run_anchors)
            if not references_valid:
                warnings.append(f"configuration not promoted because source run references are incomplete: {source_die.name}/{cfg_name}")
                eligible_configs.discard(cfg_name)
                if not (authoritative and (target_die / source_cfg_path.name).is_file()):
                    die_complete = False
                continue
            anchors_by_config[cfg_name] = run_anchors
            if promote:
                config_actions.append(PlannedFile(source_cfg_path, target_die / source_cfg_path.name,
                                                  None, reason))
            for src in evidence:
                dst = target_die / src.relative_to(source_die)
                ok, evidence_reason = _copy_decision(src, dst, local_conflicts)
                if ok:
                    evidence_actions.append(PlannedFile(src, dst, None, evidence_reason))
            if local_conflicts:
                conflicts.extend(local_conflicts)
                eligible_configs.discard(cfg_name)
                die_complete = False
                config_actions = [a for a in config_actions if a.source != source_cfg_path]
                evidence_actions = [a for a in evidence_actions if a.source not in evidence]

        if not die_complete or not eligible_configs:
            warnings.append(
                f"motor left unchanged because its complete catalog copy is not promotable: {source_die.name}")
            continue
        replacements.extend(die_actions)
        replacements.extend(config_actions)
        replacements.extend(evidence_actions)
        if source_ledger.exists() and (
                not _safe_source(source_ledger, source_root)
                or not source_ledger.is_file()):
            result_rows = {}
        else:
            result_rows = _result_rows(source_ledger, source_die_doc,
                                       cfg_docs, eligible_configs, warnings, conflicts,
                                       anchors_by_config)
        if result_rows and isinstance(shared_ledger_doc, dict):
            target_rows = shared_ledger_doc["results"].setdefault(source_die.name, {})
            for cfg_name, duties in result_rows.items():
                cfg_target = target_rows.setdefault(cfg_name, {})
                expected = _fingerprint(source_die_doc, cfg_docs[cfg_name])
                for duty, kinds in duties.items():
                    existing = cfg_target.get(duty)
                    if isinstance(existing, dict) and any(
                            isinstance(rec, dict) and rec.get("build_sig") != expected
                            for rec in existing.values()):
                        conflicts.append(
                            f"shared result row has a different build fingerprint: {source_die.name}/{cfg_name}/{duty}")
                        continue
                    # Results are build-scoped. Replace the whole duty row so
                    # stale kinds from another build cannot be mixed in.
                    cfg_target[duty] = kinds
            ledger_changed = True

    if ledger_changed and isinstance(shared_ledger_doc, dict):
        content = (json.dumps(shared_ledger_doc, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n").encode()
        replacements.append(PlannedFile(None, shared_ledger, content,
                                        "merge build-matched admin result rows"))
    files.extend(replacements)
    return files, warnings, conflicts


def apply_plan(files: list[PlannedFile], shared_root: Path) -> Path | None:
    """Stage all planned files, back up replacements, and roll back on failure."""
    if not files:
        return None
    shared_root = Path(shared_root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_base = shared_root / ".admin-catalog-backups"
    backup_root = backup_base / stamp
    if (not _safe_destination(backup_root, shared_root)
            or backup_root.exists()):
        raise ValueError(f"unsafe or existing backup destination: {backup_root}")
    stage_root = Path(tempfile.mkdtemp(prefix=".admin-catalog-stage-", dir=shared_root))
    staged: list[tuple[PlannedFile, Path]] = []
    backups: list[tuple[Path, Path | None]] = []
    try:
        for index, item in enumerate(files):
            if not _safe_destination(item.destination, shared_root):
                raise ValueError(f"planned destination escapes shared root: {item.destination}")
            stage = stage_root / f"{index:06d}"
            stage.parent.mkdir(parents=True, exist_ok=True)
            if item.source is not None:
                shutil.copy2(item.source, stage)
            else:
                stage.write_bytes(item.content or b"")
            staged.append((item, stage))
        for item, stage in staged:
            dest = item.destination
            if not _safe_destination(dest, shared_root):
                raise ValueError(f"shared destination changed since planning: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not _safe_destination(dest, shared_root):
                raise ValueError(f"unsafe shared destination parent: {dest}")
            if dest.exists():
                if dest.is_symlink() or not dest.is_file():
                    raise ValueError(f"shared destination changed since planning: {dest}")
                backup = backup_root / dest.relative_to(shared_root)
                if not _safe_destination(backup, shared_root):
                    raise ValueError(f"unsafe backup destination: {backup}")
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not _safe_destination(backup, shared_root):
                    raise ValueError(f"unsafe backup parent: {backup}")
                shutil.copy2(dest, backup)
                backups.append((dest, backup))
            else:
                backups.append((dest, None))
            if not _safe_destination(dest, shared_root):
                raise ValueError(f"unsafe destination at replace: {dest}")
            os.replace(stage, dest)
    except Exception as apply_error:
        rollback_errors = []
        for dest, backup in reversed(backups):
            try:
                if not _safe_destination(dest, shared_root):
                    raise ValueError(f"cannot safely roll back changed destination: {dest}")
                if backup is None:
                    dest.unlink()
                else:
                    if not _safe_destination(backup, shared_root):
                        raise ValueError(f"cannot safely roll back from backup: {backup}")
                    os.replace(backup, dest)
            except FileNotFoundError:
                pass
            except Exception as rollback_error:
                rollback_errors.append(f"{dest}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                f"apply failed ({apply_error}); rollback incomplete: {'; '.join(rollback_errors)}") from apply_error
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    return backup_root if backup_root.exists() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="configured administrator email")
    parser.add_argument("--workspaces-root", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--die", action="append", dest="dies",
                        help="limit the plan to this exact die name; may be repeated")
    parser.add_argument("--authoritative-die", action="append", default=[],
                        help="authorize this exact die's workspace geometry to replace shared geometry")
    parser.add_argument("--apply", action="store_true", help="apply the reviewed plan")
    args = parser.parse_args(argv)
    admins = [e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    try:
        files, warnings, conflicts = build_plan(args.email, admins,
                                                args.workspaces_root, args.shared_root,
                                                die_names=set(args.dies or ()),
                                                authoritative_dies=set(args.authoritative_die or ()))
    except Exception as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"Planned {len(files)} shared file update(s); source workspace is read-only.")
    for item in files:
        print(f"COPY {item.source or '[merged result ledger]'} -> {item.destination} ({item.reason})")
    for warning in warnings:
        print(f"SKIP {warning}")
    for conflict in conflicts:
        print(f"CONFLICT {conflict}")
    if not args.apply:
        print("Dry run only. Review conflicts and rerun with --apply to write.")
        return 1 if conflicts else 0
    if conflicts:
        print("REFUSED: conflicts remain; no files were changed.", file=sys.stderr)
        return 2
    try:
        backup = apply_plan(files, args.shared_root)
    except Exception as exc:
        print(f"Apply failed and replacements were rolled back: {exc}", file=sys.stderr)
        return 2
    print(f"Applied {len(files)} update(s); backup: {backup or 'none'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
