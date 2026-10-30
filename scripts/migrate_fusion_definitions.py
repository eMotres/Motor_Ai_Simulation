#!/usr/bin/env python
"""Translate stored geometry to Fusion's parameter definitions.  Dry-run by default.

WHY (2026-10-30).  ``cadquery_geometry`` now builds tooth2_width,
magnet_fill_down and magnet_fill_up the way Fusion 360 does (see
``motor_ai_sim.geometry.fusion_definitions`` for the table).  Every value
stored before that was measured the OLD way, so without this translation the
same file draws a different machine (Ø150: magnet up to 0.3 mm off, every
stator leg 0.1 mm narrower).

WHAT IT DOES
  * finds every ``die.yaml``, every configuration yaml beside one and every
    ``motor_config.yaml`` under the roots (``.history``/``runs``/backups skipped
    — same discovery as ``migrate_derived_geometry.py``);
  * per file computes the new values of ``tooth2_width``, ``magnet_fill_down``,
    ``magnet_fill_up`` and ``rotor_hole`` that keep its outline (stator leg and
    pocket lips exact, magnet top corners exact, the foot within the printed
    deviation) and prints ``key: old -> new`` plus the outline deviation from the
    old build BEFORE (code deployed, values not translated) and AFTER;
  * a configuration is judged on its die's geometry with its own overrides on
    top: it gets its own value for a key it overrides, and a key it does NOT
    override but now needs (it overrides wire_spacing_x, say) is ADDED to its
    ``geometry_overrides`` — never silently left to draw another outline;
  * ``--fusion-values pins.json`` (``{"<die name>": {"tooth2_width": 5.8, …}}``):
    for a die that has a Fusion reference the pinned values are written
    instead of the translation (Fusion is the design truth); ``"as_stored"``
    instead of the dict keeps a die whose values were typed in from Fusion
    verbatim (``POST /api/fusion/import`` copies values by name and leaves no
    trace, so the script cannot tell those apart).  The report then shows how
    far the outline MOVES (that machine needs a rerun);
  * with ``--apply``: ``<file>.bak-<date>`` first, value tokens replaced in place
    (indentation, comments and line endings kept), re-parse checked, atomic
    write; then a manifest ``<root>/.fusion_definitions_migrated.json``.  A root
    that has the manifest is REFUSED on a second ``--apply``: the translation
    is not idempotent (a translated value translated again is wrong), so it is
    one-shot per root.  Run it with the API stopped, right after the code that
    reads the new definitions is in place and before it serves again — a die
    the new code wrote would be translated a second time.

WHAT IT DOES NOT TOUCH, AND REPORTS
  * stored results, duty ``geometry`` stamps, passports, bench records: records
    of what was solved.  Every configuration whose ``build_sig`` moves is listed
    with its stamped results — "outline identical" ones only read "older build",
    "outline changed" ones need a rerun;
  * JSON stores under the roots that carry these keys (catalog cards, presets,
    drafts): listed with their translation, never rewritten here.

    python scripts/migrate_fusion_definitions.py <root> [<root> ...]
        [--apply] [--date YYYYMMDD] [--fusion-values pins.json]
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from motor_ai_sim.geometry.fusion_definitions import (  # noqa: E402
    INPUT_KEYS, SAME_OUTLINE_MM, TRANSLATED_KEYS, outline_change, translate_geometry)

#: an override of any of these makes a configuration's outline its own
OUTLINE_INPUTS = frozenset(INPUT_KEYS) | {"wire_split", "wire_width",
                                          "insulation_thickness", "tooth_width"}


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mdg = _load_sibling("migrate_derived_geometry")
BLOCK_OF = mdg.BLOCK_OF
MANIFEST = ".fusion_definitions_migrated.json"
#: a pin that says "this die's stored values were typed in from Fusion": keep them
AS_STORED = "as_stored"


def _fmt(v: Any) -> str:
    return mdg._fmt(v)


def add_to_block(text: str, block: str, new: Dict[str, Any]) -> str:
    """Append ``key: value`` lines for keys the top-level ``block:`` mapping does
    not carry, at its children's indentation, after its last child line."""
    if not new:
        return text
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    head = re.compile(r"^%s:\s*(#.*)?$" % re.escape(block))
    start = next((i for i, l in enumerate(lines) if head.match(l.rstrip("\r\n"))), None)
    if start is None:
        raise ValueError(f"no top-level '{block}:' block")
    child_ind, last = None, start
    for j in range(start + 1, len(lines)):
        body = lines[j].rstrip("\r\n")
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        ind = len(body) - len(body.lstrip(" "))
        if ind == 0:
            break
        if child_ind is None:
            child_ind = ind
        last = j
    if child_ind is None:
        raise ValueError(f"'{block}:' is not a block mapping with children")
    if not lines[last].endswith(("\n", "\r")):
        lines[last] += eol
    add = [f"{' ' * child_ind}{k}: {_fmt(v)}{eol}" for k, v in new.items()]
    return "".join(lines[:last + 1] + add + lines[last + 1:])


def die_name_of(p: Path, kind: str) -> Optional[str]:
    if kind == "die":
        return p.parent.name
    if kind == "configuration":
        return p.parent.name
    return None


def plan_file(p: Path, kind: str, doc: dict, pins: Dict[str, dict],
              die_docs: Dict[Path, dict]) -> Optional[dict]:
    """The change set of one file: ``{"replace", "add", "old_geo", "new_geo",
    "problems"}`` or None when it carries no translatable geometry."""
    blk = doc.get(BLOCK_OF[kind])
    if not isinstance(blk, dict) or not blk:
        return None
    problems: List[str] = []
    if kind == "configuration":
        die_p = p.parent / "die.yaml"
        die_old = dict((mdg.load_yaml(die_p).get("geometry") or {}))
        die_plan = die_docs.get(die_p)
        die_new = dict(die_plan["new_geo"]) if die_plan else dict(die_old)
        ov = {k: v for k, v in blk.items() if v is not None}
        old_geo = {**die_old, **ov}
        if not any(k in ov for k in OUTLINE_INPUTS):
            return None           # the die's translation is this configuration's
        pin = pins.get(die_name_of(p, kind) or "")
        if pin == AS_STORED:
            return None           # the die's values already are Fusion's: so are these
        if pin:
            want = {k: v for k, v in pin.items() if k in TRANSLATED_KEYS}
            problems.append("die pinned to Fusion values; this configuration overrides "
                            "lamination keys - check it against Fusion by hand")
        else:
            t = translate_geometry(old_geo)
            want, problems = t["values"], list(t["problems"])
        inherited = {**die_new, **ov}
        replace, add = {}, {}
        for k, v in want.items():
            if k in ov:
                if abs(float(ov[k]) - float(v)) > 1e-12:
                    replace[k] = v
            elif k in inherited and abs(float(inherited[k]) - float(v)) > 1e-9:
                add[k] = v
        new_geo = {**die_new, **ov, **replace, **add}
        if not replace and not add:
            return None
        return {"replace": replace, "add": add, "old_geo": old_geo, "new_geo": new_geo,
                "problems": problems}
    old_geo = dict(blk)
    pin = pins.get(die_name_of(p, kind) or "") if kind == "die" else None
    if pin == AS_STORED:
        return {"replace": {}, "add": {}, "old_geo": old_geo, "new_geo": old_geo,
                "problems": [], "pinned": AS_STORED}
    if pin:
        unknown = sorted(set(pin) - set(TRANSLATED_KEYS))
        if unknown:
            problems.append(f"pinned keys outside the translated set ignored: {unknown}")
        want = {k: float(v) for k, v in pin.items() if k in TRANSLATED_KEYS and k in blk}
    else:
        t = translate_geometry(old_geo)
        want, problems = t["values"], problems + list(t["problems"])
    replace = {k: v for k, v in want.items()
               if k in blk and abs(float(blk[k]) - float(v)) > 1e-12}
    if not replace:
        return None
    return {"replace": replace, "add": {}, "old_geo": old_geo,
            "new_geo": {**old_geo, **replace}, "problems": problems, "pinned": bool(pin)}


def planned_text(p: Path, kind: str, doc: dict, plan: dict) -> Tuple[str, dict]:
    text = p.read_bytes().decode("utf-8")
    block = BLOCK_OF[kind]
    new_text = mdg.rewrite_block(text, block, plan["replace"]) if plan["replace"] else text
    new_text = add_to_block(new_text, block, plan["add"])
    want = copy.deepcopy(doc)
    want[block].update(plan["replace"])
    want[block].update(plan["add"])
    if yaml.safe_load(new_text) != want:
        raise ValueError("re-parse differs from the intended change")
    return new_text, want


def _json_mentions(roots: List[str]) -> List[Path]:
    out = []
    for r in roots:
        root = Path(r)
        for p in ([root] if root.is_file() else sorted(root.rglob("*.json"))):
            if any(part in mdg.SKIP_PARTS for part in p.parts) or p.name == MANIFEST:
                continue
            try:
                t = p.read_text(encoding="utf-8")
            except Exception:                                  # noqa: BLE001
                continue
            if "magnet_fill_up" in t or "tooth2_width" in t:
                out.append(p)
    return out


def _r(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.4f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default: dry-run, nothing written)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="suffix of the .bak-<date> copies")
    ap.add_argument("--fusion-values",
                    help="JSON {die name: {key: value} | \"as_stored\"}: Fusion's values for "
                         "dies with a Fusion reference, written instead of the translation; "
                         "\"as_stored\" = the stored values were typed in from Fusion, keep them")
    a = ap.parse_args(argv)
    pins = (json.loads(Path(a.fusion_values).read_text(encoding="utf-8"))
            if a.fusion_values else {})

    done = [Path(r) / MANIFEST for r in a.roots if (Path(r) / MANIFEST).is_file()]
    mode = "APPLY" if a.apply else "DRY-RUN"
    print(f"== migrate_fusion_definitions {mode} ({datetime.now():%Y-%m-%d %H:%M})")
    if done:
        for m in done:
            print(f"!! {m} exists: this root was already migrated on "
                  f"{json.loads(m.read_text(encoding='utf-8')).get('date')}")
        if a.apply:
            print("!! refusing a second --apply: the translation is one-shot per root")
            return 2

    targets = list(mdg.iter_targets(a.roots))
    # dies first, so a configuration is judged against its die's NEW values
    targets.sort(key=lambda t: (0 if t[1] == "die" else 1, str(t[0])))
    plans: Dict[Path, dict] = {}
    texts: Dict[Path, str] = {}
    new_docs: Dict[Path, dict] = {}
    n_err = 0
    rerun: List[str] = []
    stamp_only: List[str] = []
    for p, kind in targets:
        try:
            doc = mdg.load_yaml(p)
            plan = plan_file(p, kind, doc, pins, plans)
        except Exception as e:                                 # noqa: BLE001
            print(f"!! {p}: unreadable ({e})"); n_err += 1
            continue
        if plan is None:
            continue
        plans[p] = plan
        if plan["replace"] or plan["add"]:
            try:
                new_text, new_doc = planned_text(p, kind, doc, plan)
                if a.apply:
                    mdg.check_backup(p, a.date)
            except Exception as e:                             # noqa: BLE001
                print(f"!! {p}: cannot rewrite safely ({e}) - fix by hand"); n_err += 1
                del plans[p]
                continue
            texts[p], new_docs[p] = new_text, new_doc
        before = outline_change(plan["old_geo"], plan["old_geo"])
        after = outline_change(plan["old_geo"], plan["new_geo"])
        tag = {True: "  [FUSION VALUES]", AS_STORED: "  [KEPT AS STORED: already Fusion's values]"
               }.get(plan.get("pinned"), "")
        print(f"-- {p}  [{kind}.{BLOCK_OF[kind]}]{tag}")
        for k, v in plan["replace"].items():
            print(f"     {k}: {plan['old_geo'].get(k)!r} -> {v!r}")
        for k, v in plan["add"].items():
            print(f"     {k}: (inherited) -> {v!r}  ADDED to the overrides")
        print(f"     outline vs the old build [mm]   stator leg  magnet  lip   magnet area")
        print(f"       before (values as stored)   {_r(before['stator_leg']):>8} {_r(before['magnet']):>7} "
              f"{_r(before['lip']):>6}  {_r(before['magnet_area_pct'])} %")
        print(f"       after  (written values)     {_r(after['stator_leg']):>8} {_r(after['magnet']):>7} "
              f"{_r(after['lip']):>6}  {_r(after['magnet_area_pct'])} %")
        for pr in plan["problems"]:
            print(f"     !! {pr}")
        moved = max(v for v in (after["stator_leg"], after["magnet"], after["lip"], 0.0)
                    if v is not None)
        label = f"{p.parent.name}/{p.stem}" if kind != "motor_config" else str(p)
        (rerun if moved > SAME_OUTLINE_MM else stamp_only).append(
            f"{label} (max {moved:.4f} mm)")

    # build_sig: every configuration of a touched die, after the whole change set
    print("== stored results (never rewritten here)")
    for dd in sorted({p.parent for p, _ in targets if p in plans and p.name != "motor_config.yaml"}):
        try:
            imp = mdg.build_sig_impact(dd, new_docs)
        except Exception as e:                                 # noqa: BLE001
            print(f"   {dd.name}: build_sig impact unavailable ({e})")
            continue
        for r in imp:
            if r["old"] != r["new"]:
                print(f"   {dd.name}/{r['config'].stem}: build_sig {r['old']} -> {r['new']}, "
                      f"{r['results_on_old']} stamped result(s) will read 'older build'")
    print("== machines whose outline CHANGES (rerun their results):")
    for s in rerun or ["(none)"]:
        print(f"   {s}")
    print("== outline identical, only the stamps move (rerun optional):")
    for s in stamp_only or ["(none)"]:
        print(f"   {s}")
    js = _json_mentions(a.roots)
    if js:
        print("== JSON stores carrying these keys - NOT rewritten (catalog / presets / "
              "drafts / records): translate seeds by hand with "
              "motor_ai_sim.geometry.fusion_definitions.translate_geometry")
        for p in js:
            print(f"   {p}")

    if a.apply and texts:
        written = {}
        for p, t in texts.items():
            b = mdg.write_with_backup(p, t.encode("utf-8"), a.date)
            written[str(p)] = {"backup": b.name, "values": {**plans[p]["replace"], **plans[p]["add"]}}
        for r in a.roots:
            if Path(r).is_dir():
                (Path(r) / MANIFEST).write_text(json.dumps(
                    {"date": datetime.now().isoformat(timespec="seconds"),
                     "files": {k: v for k, v in written.items()
                               if Path(k).resolve().is_relative_to(Path(r).resolve())}},
                    indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"== wrote {len(texts)} file(s)")
    else:
        print(f"== {len(texts)} file(s) would change" + ("" if not a.apply else " (none written)"))
    if n_err:
        print(f"!! {n_err} file(s) need a hand fix")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
