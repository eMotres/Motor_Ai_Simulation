"""Add the motor PWM extra loss SPLIT BY PART (iron / magnet / copper_ac / other)
to stored passport records, from their anchor run JSONs — no FEM.

    python scripts/rebuild_pwm_loss_parts.py [STORE_OR_FILE ...] [--runs DIR ...] [--write]

* ``STORE_OR_FILE`` — a passport store folder (``<root>/<die>/<config>.json``) or
  single record files.  Default: the store Configure reads
  (``passport_store.store_dir()``: ``<shared>/passports`` on the server).
* ``--runs DIR`` — where the anchor runs (``pwm_<M>_*.json``, the tags in each
  variant's ``provenance.motor_pwm_fem_runs``) are searched, recursively.  The
  folder of the record's ``source`` is searched first when it resolves.
* Without ``--write`` it is a dry run: it prints what it would do.  With it, each
  changed record is written in place after a copy ``<config>.json.pre-parts.bak``.

A variant is left exactly as it was (and reported) when one of its anchors is
missing, when a tag matches several runs and none / several of them agree with
the variant's stored basis, or when the loaded anchors' modulation indices do not
match the ones its stored points list.  Stored totals never change.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.passport_v1 import pwm_parts as PP  # noqa: E402


def _load(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class RunIndex:
    """``tag -> [paths]`` over the run folders (built once per folder)."""

    def __init__(self, dirs: List[Path]):
        self.idx: Dict[str, List[Path]] = {}
        for d in dirs:
            if not d.is_dir():
                continue
            for f in d.rglob("pwm_*.json"):
                self.idx.setdefault(f.stem, []).append(f)

    def candidates(self, tag: str, first: List[Path]) -> List[Path]:
        own = [d / f"{tag}.json" for d in first if (d / f"{tag}.json").is_file()]
        return own or list(dict.fromkeys(self.idx.get(tag, [])))


def _records(args: List[str]) -> List[Path]:
    if not args:
        from motor_ai_sim.passport_store import store_dir
        args = [str(store_dir())]
    out: List[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out += sorted(p.glob("*/*.json"))
        elif p.is_file():
            out.append(p)
        else:
            print(f"skip: {a} does not exist")
    return out


def _source_dirs(rec: Dict[str, Any], f: Path) -> List[Path]:
    src = str(rec.get("source") or "")
    if not src:
        return []
    out = []
    for base in (ROOT, f.parent):
        q = (base / src).parent
        if q.is_dir():
            out.append(q)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("records", nargs="*")
    ap.add_argument("--runs", action="append", default=[], help="anchor run folder (repeatable)")
    ap.add_argument("--write", action="store_true", help="write the records (default: dry run)")
    a = ap.parse_args(argv)
    index = RunIndex([Path(d) for d in a.runs])
    n_changed = 0
    for f in _records(a.records):
        rec = _load(f)
        if not isinstance(rec, dict) or not rec.get("pwm_variants"):
            continue
        first = _source_dirs(rec, f)
        cache: Dict[str, Optional[Dict[str, Any]]] = {}

        def load_run(tag: str, _first=first, _cache=cache) -> Optional[Dict[str, Any]]:
            if tag not in _cache:
                c = index.candidates(tag, _first)
                if len(c) > 1:
                    # the same tag in several folders (another die's L12 …): only a run
                    # whose machine matches the tag is a candidate, and it must be ONE
                    M = tag.split("_")[1] if tag.count("_") >= 2 else None
                    c = [p for p in c if (_load(p) or {}).get("machine") == M] or c
                _cache[tag] = _load(c[0]) if len(c) == 1 else None
                if len(c) > 1:
                    print(f"  {tag}: {len(c)} runs match — ambiguous, not used "
                          f"({', '.join(str(p) for p in c[:3])})")
            return _cache[tag]

        new, reps = PP.rebuild_record(rec, load_run)
        print(f"{f}:")
        for r in reps:
            if r.get("refused"):
                print(f"  {r['id']}: LEFT AS IS — {r['refused']}")
            else:
                print(f"  {r['id']}: {r['split']} points split, modes {r.get('modes')}, "
                      f"unsplit {len(r['unsplit'])}, max |sum - total| {r['max_sum_err_W']:.2e} W")
        if new == rec:
            continue
        n_changed += 1
        if a.write:
            bak = f.with_name(f.name + ".pre-parts.bak")
            if not bak.exists():
                shutil.copy2(f, bak)
            f.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"  written (backup {bak.name})")
    print(f"{n_changed} record(s) {'written' if a.write else 'would change (dry run; --write to apply)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
