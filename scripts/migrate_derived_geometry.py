#!/usr/bin/env python
"""Rewrite STALE derived geometry fields from their own inputs.  Dry-run by default.

WHY (2026-09-30).  die.yaml, the configuration yamls and motor_config.yaml
store derived copies (stator_inner_radius, rotor_outer_radius,
rotor_inner_radius, slot_width, counts, angles, pitches) next to the inputs
they are computed from, and on most dies the copies had gone stale: the
geometry PUT rewrote an edited input but only the counts/angles of the derived
block, and the die sync then copied that block into die.yaml.  See
``geometry.motor_geometry.refresh_derived_geometry`` for the rule.

WHAT IT DOES
  * finds every ``die.yaml``, every configuration yaml beside one, and every
    ``motor_config.yaml`` under the given roots (``.history``/``runs``/backups
    skipped);
  * computes the fresh value of every derived field the file CARRIES — a
    configuration's ``geometry_overrides`` against its die's inputs merged —
    and prints ``file: key stored -> fresh`` for each stale one;
  * with ``--apply``: writes ``<file>.bak-<date>`` first (refused when a
    same-date backup exists and differs from the file), then replaces ONLY the
    value token of each stale ``key: value`` line - indentation, spacing,
    inline comments, CRLF line endings and every other line are kept - via a
    same-directory temp file + os.replace, after re-parsing the result to prove
    nothing else moved.  A second run finds nothing: idempotent.

WHAT ELSE MOVES, AND IS REPORTED — never silently re-keyed
  * ``family._build_sig`` hashes the die geometry INCLUDING the derived copies,
    and the configuration's overrides, so every duty result stamped under the
    stale build reads "computed on an older build" afterwards.  Counted per
    configuration AFTER the whole change set (die and configuration edits);
    ``--restamp-results``
    (with ``--apply``) moves exactly those stamps to the new signature — the
    one case ``family._restamp_results`` exists for: the build did not change,
    only its description did.
  * ``routes.simulation._geometry_fingerprint`` hashes the raw
    motor_config.yaml geometry block, which a ▶ load fills from the die's
    stored copies.  With ``--fp-base <that workspace's motor_config.yaml>`` the
    script simulates the ▶ load of every die/configuration found, before and
    after the migration, and names every Stage A passport
    (``--passports``) and bench Ld/Lq record (``--bench``) keyed on a print
    that changes.  ``--rekey`` (with ``--apply``) ADDS each one under its new
    print (``rekeyed_from`` = the old key); the old entry is kept.

    python scripts/migrate_derived_geometry.py <root> [<root> ...]
        [--apply] [--date YYYYMMDD] [--restamp-results]
        [--fp-base motor_config.yaml --passports end_effect_passports.json
         --bench .bench_ldq.json [--rekey]]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

SKIP_PARTS = {".history", "runs", "__pycache__", ".git"}
BLOCK_OF = {"die": "geometry", "motor_config": "geometry",
            "configuration": "geometry_overrides"}


# ── discovery ────────────────────────────────────────────────────────────────

def classify(p: Path) -> Optional[str]:
    if p.name == "die.yaml":
        return "die"
    if p.name == "motor_config.yaml":
        return "motor_config"
    if (p.parent / "die.yaml").is_file():
        return "configuration"
    return None


def iter_targets(roots: List[str]) -> Iterator[Tuple[Path, str]]:
    seen = set()
    for r in roots:
        root = Path(r)
        cands = [root] if root.is_file() else sorted(root.rglob("*.yaml"))
        for p in cands:
            if any(part in SKIP_PARTS for part in p.parts):
                continue
            if ".bak" in p.name or p.name.endswith(".tmp"):
                continue
            kind = classify(p)
            if kind and p.resolve() not in seen:
                seen.add(p.resolve())
                yield p, kind


def load_yaml(p: Path) -> dict:
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return d if isinstance(d, dict) else {}


# ── the per-file change set ──────────────────────────────────────────────────

def stale_changes(p: Path, kind: str, doc: dict) -> Dict[str, Dict[str, Any]]:
    """``{key: {stored, fresh}}`` for the file's own derived block."""
    from motor_ai_sim.geometry.motor_geometry import (fresh_derived_values,
                                                      stale_derived_fields)
    blk = doc.get(BLOCK_OF[kind])
    if not isinstance(blk, dict) or not blk:
        return {}
    if kind != "configuration":
        return stale_derived_fields(blk)
    # An override is judged against the machine it describes: the die's
    # inputs with the override on top.
    try:
        die_geo = dict(load_yaml(p.parent / "die.yaml").get("geometry") or {})
    except Exception:                                      # noqa: BLE001
        die_geo = {}
    merged = {**die_geo, **{k: v for k, v in blk.items() if v is not None}}
    fresh = fresh_derived_values(merged)
    out = {}
    for k, v in blk.items():
        if k in fresh and not isinstance(v, bool):
            try:
                if abs(float(v) - float(fresh[k])) <= 1e-9 * max(
                        1.0, abs(float(v)), abs(float(fresh[k]))):
                    continue
            except (TypeError, ValueError):
                pass
            out[k] = {"stored": v, "fresh": fresh[k]}
    return out


def stale_duty_stamps(doc: dict) -> int:
    """Duty-level ``geometry`` dicts carrying a stale derived copy.  Reported
    only: those are the record of what a duty was solved on, never rewritten
    (the duty-load guard compares inputs only since 2026-09-30)."""
    from motor_ai_sim.geometry.motor_geometry import stale_derived_fields
    n = 0
    for d in (doc.get("duties") or []):
        g = (d or {}).get("geometry") if isinstance(d, dict) else None
        if isinstance(g, dict) and stale_derived_fields(g):
            n += 1
    return n


# ── text-level rewrite ───────────────────────────────────────────────────────

def _fmt(v: Any) -> str:
    return yaml.safe_dump(v, default_flow_style=True).splitlines()[0]


#: `  key:<space>value<suffix>` — a PLAIN scalar value (no spaces, no quotes),
#: then optional trailing spaces and an inline comment.  The indentation, the
#: spacing after the colon and the suffix are kept verbatim; only the value
#: token is replaced.  A changed key whose line does not look like this
#: (quoted, flow-style, multi-line) is refused, never guessed at.
_KEY_RE = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*):([ \t]+)([^\s#'\"{}\[\]][^\s]*)"
                     r"([ \t]*(?:#.*)?)$")


def rewrite_block(text: str, block: str, new: Dict[str, Any]) -> str:
    """Replace the VALUE token of ``key: value`` for every key in ``new``
    among the FIRST-LEVEL children of the top-level ``block:`` mapping.  Line
    endings (LF or CRLF), indentation, post-colon spacing, inline comments
    and every other line are kept byte for byte."""
    lines = text.splitlines(keepends=True)
    head = re.compile(r"^%s:\s*(#.*)?$" % re.escape(block))
    start = next((i for i, l in enumerate(lines)
                  if head.match(l.rstrip("\r\n"))), None)
    if start is None:
        raise ValueError(f"no top-level '{block}:' block")
    child_ind = None
    done = set()
    for j in range(start + 1, len(lines)):
        raw = lines[j]
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        ind = len(body) - len(body.lstrip(" "))
        if ind == 0:
            break
        if child_ind is None:
            child_ind = ind
        if ind != child_ind:
            continue
        m = _KEY_RE.match(body)
        if m and m.group(2) in new:
            lines[j] = (f"{m.group(1)}{m.group(2)}:{m.group(3)}"
                        f"{_fmt(new[m.group(2)])}{m.group(5)}{eol}")
            done.add(m.group(2))
    missing = set(new) - done
    if missing:
        raise ValueError(f"keys not found as plain `key: value` lines: {sorted(missing)}")
    return "".join(lines)


def planned_text(p: Path, kind: str, doc: dict,
                 ch: Dict[str, Dict[str, Any]]) -> Tuple[str, dict]:
    """(new text, new document).  The re-parse of the new text must equal the
    old document with exactly these values changed — proof nothing else moved."""
    text = p.read_bytes().decode("utf-8")
    new_text = rewrite_block(text, BLOCK_OF[kind],
                             {k: v["fresh"] for k, v in ch.items()})
    want = copy.deepcopy(doc)
    for k, v in ch.items():
        want[BLOCK_OF[kind]][k] = v["fresh"]
    if yaml.safe_load(new_text) != want:
        raise ValueError("re-parse differs from the intended change")
    return new_text, want


def backup_path(p: Path, date: str) -> Path:
    return p.with_name(f"{p.name}.bak-{date}")


def check_backup(p: Path, date: str) -> None:
    """A same-date backup that differs from the file as it is NOW would be
    kept and the version about to be replaced lost: refuse."""
    b = backup_path(p, date)
    if b.exists() and b.read_bytes() != p.read_bytes():
        raise ValueError(f"{b.name} exists and differs from the current file — "
                         f"pass another --date")


def atomic_write(p: Path, data: bytes) -> None:
    """Same-directory temp file, fsync, os.replace: an interrupted run leaves
    either the old file or the new one, never a truncated one."""
    tmp = p.with_name(f".{p.name}.migrate-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def write_with_backup(p: Path, data: bytes, date: str) -> Path:
    check_backup(p, date)
    b = backup_path(p, date)
    if not b.exists():
        shutil.copy2(p, b)
    atomic_write(p, data)
    return b


# ── build_sig impact ─────────────────────────────────────────────────────────

def _result_holders(c: dict):
    for d in (c.get("duties") or []):
        if not isinstance(d, dict):
            continue
        for h in [d.get("result")] + [(r or {}).get("result")
                                      for r in (d.get("runs") or {}).values()]:
            if isinstance(h, dict):
                yield h


def build_sig_impact(die_dir: Path, new_docs: Dict[Path, dict]) -> List[dict]:
    """Every configuration of ``die_dir``: its build_sig BEFORE and AFTER the
    whole planned change set (die AND configuration edits, from ``new_docs``),
    and how many stored results carry the old one."""
    from motor_ai_sim.routes.family import _build_sig
    die_p = die_dir / "die.yaml"
    old_die = load_yaml(die_p)
    new_die = new_docs.get(die_p, old_die)
    out = []
    for cf in sorted(die_dir.glob("*.yaml")):
        if cf.name == "die.yaml" or ".bak" in cf.name:
            continue
        old_c = load_yaml(cf)
        new_c = new_docs.get(cf, old_c)
        old, new = _build_sig(old_die, old_c), _build_sig(new_die, new_c)
        n_old = sum(1 for h in _result_holders(old_c) if h.get("build_sig") == old)
        n_other = sum(1 for h in _result_holders(old_c)
                      if h.get("build_sig") not in (None, old))
        out.append({"config": cf, "old": old, "new": new,
                    "results_on_old": n_old if old != new else 0,
                    "results_already_other": n_other})
    return out


def restamp_text(text: str, doc: dict, old: str, new: str) -> str:
    """Move the result stamps ``build_sig: <old>`` to ``<new>`` — ONLY in the
    result holders ``family._build_sig`` staleness reads (a duty's ``result``
    and each ``runs.<drive>.result``).  The text edit touches only lines that
    are exactly ``build_sig: <old>``; the re-parse must equal ``doc`` with
    exactly those holders changed, or nothing is written."""
    want = copy.deepcopy(doc)
    n = 0
    for h in _result_holders(want):
        if h.get("build_sig") == old:
            h["build_sig"] = new
            n += 1
    if not n:
        return text
    pat = re.compile(r"^(\s*build_sig:[ \t]+)(['\"]?)%s\2([ \t]*(?:#.*)?)$"
                     % re.escape(old))
    lines = text.splitlines(keepends=True)
    for i, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        m = pat.match(body)
        if m:
            lines[i] = (f"{m.group(1)}{m.group(2)}{new}{m.group(2)}{m.group(3)}"
                        f"{raw[len(body):]}")
    out = "".join(lines)
    if yaml.safe_load(out) != want:
        raise ValueError("restamp would touch more than the result stamps")
    return out


# ── _geometry_fingerprint impact (simulated ▶ load) ──────────────────────────

class FpSim:
    """Reproduces what ▶ writes into motor_config.yaml (payload → PUT) and
    the print `_geometry_fingerprint` takes of it, old code vs new."""

    def __init__(self, base_cfg: Path):
        self.base_text = base_cfg.read_text(encoding="utf-8")
        self.tmp = Path(tempfile.mkdtemp()) / "motor_config.yaml"
        os.environ["MOTOR_AI_SIM_CONFIG"] = str(self.tmp)
        from motor_ai_sim.config import clear_config_cache
        from motor_ai_sim.routes._validation import SCHEMA_FALLBACK
        from motor_ai_sim.routes.simulation import _geometry_fingerprint
        from motor_ai_sim.services import geometry_service
        self._clear, self._fb = clear_config_cache, SCHEMA_FALLBACK
        self._fp, self._gs = _geometry_fingerprint, geometry_service

    def _print_of(self, config: dict) -> str:
        with open(self.tmp, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False,
                      sort_keys=False)
        self._clear()
        self._gs.get_current_geometry(reload=True)
        return self._fp(None)

    def live(self, refreshed: bool) -> str:
        from motor_ai_sim.geometry.motor_geometry import refresh_derived_geometry
        config = yaml.safe_load(self.base_text)
        if refreshed:
            config["geometry"] = refresh_derived_geometry(config.get("geometry") or {})
        if not refreshed:
            self.tmp.write_text(self.base_text, encoding="utf-8")
            self._clear()
            self._gs.get_current_geometry(reload=True)
            return self._fp(None)
        return self._print_of(config)

    def load(self, geo: dict, refreshed: bool) -> str:
        from motor_ai_sim.geometry.motor_geometry import refresh_derived_geometry
        config = yaml.safe_load(self.base_text)
        if refreshed:       # the migrated base, as the app will hold it
            config["geometry"] = refresh_derived_geometry(config.get("geometry") or {})
        sec = config.setdefault("geometry", {})
        for k, v in geo.items():
            if v is not None and (k in sec or k in self._fb):
                sec[k] = v
        ns = float(sec.get("num_seg", 0) or 0)
        pps = float(sec.get("num_poles_per_segment", 0) or 0)
        sps = float(sec.get("num_slots_per_segment", 0) or 0)
        if ns > 0 and pps > 0 and "num_poles" in sec:
            sec["num_poles"] = int(round(ns * pps))
            if "angle_pole" in sec:
                sec["angle_pole"] = 360.0 / (ns * pps)
        if ns > 0 and sps > 0 and "num_slots" in sec:
            sec["num_slots"] = int(round(ns * sps))
            if "angle_slot" in sec:
                sec["angle_slot"] = 360.0 / (ns * sps)
        if refreshed:       # MOTOR_AI_SIM_FRESH_DERIVED=1 in the PUT
            config["geometry"] = refresh_derived_geometry(sec)
        return self._print_of(config)


_ABSENT = {"sleeve_thickness": 0.0, "wire_parallel": 1, "wire_split": 1}


def payload_geo(die_doc: dict, cfg_doc: dict) -> dict:
    geo = dict(die_doc.get("geometry") or {})
    geo.update({k: v for k, v in (cfg_doc.get("geometry_overrides") or {}).items()
                if v is not None})
    for k, v in _ABSENT.items():
        if geo.get(k) is None:
            geo[k] = v
    return geo


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default: dry-run, nothing written)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="suffix of the .bak-<date> copies")
    ap.add_argument("--restamp-results", action="store_true",
                    help="with --apply: move results stamped with the old "
                         "build_sig of a migrated die/configuration to the new one")
    ap.add_argument("--fp-base", help="the workspace's motor_config.yaml to "
                    "simulate ▶ loads on (fingerprint impact)")
    ap.add_argument("--passports", help="end_effect_passports.json to check")
    ap.add_argument("--bench", help=".bench_ldq.json to check")
    ap.add_argument("--rekey", action="store_true",
                    help="with --apply: ADD passport/bench entries under the "
                         "new prints (old entries kept)")
    a = ap.parse_args(argv)

    fps = FpSim(Path(a.fp_base)) if a.fp_base else None   # sets the env first
    passports = (json.loads(Path(a.passports).read_text(encoding="utf-8"))
                 if a.passports else {})
    bench = (json.loads(Path(a.bench).read_text(encoding="utf-8"))
             if a.bench else {})

    mode = "APPLY" if a.apply else "DRY-RUN"
    print(f"== migrate_derived_geometry {mode} ({datetime.now():%Y-%m-%d %H:%M})")
    n_files = n_fields = n_err = 0
    field_count: Dict[str, int] = {}
    texts: Dict[Path, str] = {}        # path -> the text the run will write
    new_docs: Dict[Path, dict] = {}    # path -> its re-parsed document
    die_dirs = set()
    for p, kind in iter_targets(a.roots):
        try:
            doc = load_yaml(p)
            ch = stale_changes(p, kind, doc)
            nd = stale_duty_stamps(doc) if kind == "configuration" else 0
        except Exception as e:                             # noqa: BLE001
            print(f"!! {p}: unreadable ({e})"); n_err += 1
            continue
        if nd:
            print(f"   {p}: {nd} duty geometry stamp(s) carry stale derived "
                  f"copies - records, left as they are")
        if not ch:
            continue
        try:
            new_text, new_doc = planned_text(p, kind, doc, ch)
            if a.apply:
                check_backup(p, a.date)
        except Exception as e:                             # noqa: BLE001
            print(f"!! {p}: cannot rewrite safely ({e}) - fix by hand"); n_err += 1
            continue
        n_files += 1
        n_fields += len(ch)
        print(f"-- {p}  [{kind}.{BLOCK_OF[kind]}]"
              + ("  (CRLF)" if "\r\n" in new_text else ""))
        for k, v in sorted(ch.items()):
            field_count[k] = field_count.get(k, 0) + 1
            print(f"     {k}: {v['stored']!r} -> {v['fresh']!r}")
        texts[p], new_docs[p] = new_text, new_doc
        if kind in ("die", "configuration"):
            die_dirs.add(p.parent)

    # ── build_sig: after the WHOLE change set (die + configuration edits) ──
    restamps: List[Tuple[Path, str, str, int]] = []
    for dd in sorted(die_dirs):
        try:
            imp = build_sig_impact(dd, new_docs)
        except Exception as e:                             # noqa: BLE001
            print(f"   {dd.name}: build_sig impact unavailable ({e})")
            continue
        for r in imp:
            if r["old"] == r["new"]:
                continue
            print(f"   build_sig {dd.name}/{r['config'].stem}: {r['old']} -> "
                  f"{r['new']}; {r['results_on_old']} result stamp(s) on the old "
                  f"sig would read 'older build'"
                  + (f" ({r['results_already_other']} already on another sig)"
                     if r["results_already_other"] else ""))
            if r["results_on_old"]:
                restamps.append((r["config"], r["old"], r["new"], r["results_on_old"]))
    if a.apply and a.restamp_results:
        for cf, old, new, _n in restamps:
            try:
                base_text = texts.get(cf) or cf.read_bytes().decode("utf-8")
                base_doc = new_docs.get(cf) or load_yaml(cf)
                t2 = restamp_text(base_text, base_doc, old, new)
                check_backup(cf, a.date)
            except Exception as e:                         # noqa: BLE001
                print(f"!! {cf}: restamp refused ({e})"); n_err += 1
                continue
            texts[cf] = t2
            new_docs[cf] = yaml.safe_load(t2)

    # ── _geometry_fingerprint: simulated ▶ loads, before vs after ──────────
    rekeys: Dict[str, str] = {}
    if fps is not None:
        for dd in sorted(die_dirs):
            die_p = dd / "die.yaml"
            old_die = load_yaml(die_p)
            new_die = new_docs.get(die_p, old_die)
            for cf in sorted(dd.glob("*.yaml")):
                if cf.name == "die.yaml" or ".bak" in cf.name:
                    continue
                old_c = load_yaml(cf)
                new_c = new_docs.get(cf, old_c)
                try:
                    old = fps.load(payload_geo(old_die, old_c), refreshed=False)
                    new = fps.load(payload_geo(new_die, new_c), refreshed=True)
                except Exception as e:                     # noqa: BLE001
                    print(f"   fingerprint {dd.name}/{cf.stem}: unavailable ({e})")
                    continue
                hit_p = old in passports
                hit_b = [k for k in bench if k.startswith(old + "_")]
                print(f"   fingerprint {dd.name}/{cf.stem}: {old} -> {new}"
                      + ("  PASSPORT keyed on the old print" if hit_p else "")
                      + (f"  BENCH x{len(hit_b)} keyed on the old print" if hit_b else ""))
                if (hit_p or hit_b) and old != new:
                    rekeys[old] = new
        lo, ln = fps.live(False), fps.live(True)
        hit_p = lo in passports
        hit_b = [k for k in bench if k.startswith(lo + "_")]
        print(f"   fingerprint LIVE {a.fp_base}: {lo} -> {ln}"
              + ("  PASSPORT" if hit_p else "") + (f"  BENCH x{len(hit_b)}" if hit_b else ""))
        if (hit_p or hit_b) and lo != ln:
            rekeys[lo] = ln

    print(f"== {n_files} file(s), {n_fields} field(s) stale"
          + (f", {n_err} problem(s) need a hand" if n_err else ""))
    for k in sorted(field_count):
        print(f"     {k}: {field_count[k]} file(s)")
    if restamps:
        print(f"== build_sig: {sum(r[3] for r in restamps)} result stamp(s) in "
              f"{len(restamps)} configuration(s) would read 'older build' "
              + ("- restamped (--restamp-results)" if a.apply and a.restamp_results
                 else "- NOT restamped (needs --apply --restamp-results)"))
    if rekeys:
        print(f"== fingerprint: {len(rekeys)} passport/bench print(s) change "
              + ("- re-keyed additively (--rekey)" if a.apply and a.rekey
                 else "- NOT re-keyed (needs --apply --rekey)"))

    if not a.apply:
        print("== dry-run: nothing written")
        return 1 if n_err else 0
    if n_err:
        # all or nothing: a half-applied migration (radii fixed, their results
        # not restamped, a backup refused) is worse than none
        print(f"== APPLY ABORTED: {n_err} problem(s) above - nothing written")
        return 1

    for p, t in texts.items():
        b = write_with_backup(p, t.encode("utf-8"), a.date)
        print(f"   wrote {p} (backup {b.name})")
    if a.rekey and rekeys:
        for path, store, bench_like in ((a.passports, passports, False),
                                        (a.bench, bench, True)):
            if not path:
                continue
            added = 0
            for old, new in rekeys.items():
                keys = ([k for k in list(store) if k.startswith(old + "_")]
                        if bench_like else ([old] if old in store else []))
                for k in keys:
                    nk = new + k[len(old):]
                    if nk not in store:
                        store[nk] = {**store[k], "rekeyed_from": k}
                        added += 1
            if added:
                write_with_backup(Path(path), json.dumps(
                    store, ensure_ascii=False, indent=1).encode("utf-8"), a.date)
                print(f"   re-keyed {added} entries in {path}")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
