#!/usr/bin/env python
"""Rename an OLD Fusion 360 "Parameter I/O" CSV export to motor_ai_sim's
canonical geometry names -- the owner-approved 33-row mapping of 2026-09-25
(docs/FUSION_PARAMETER_MAP_2026-09-25.md, config/fusion_param_map.yaml
`legacy_fusion_names_approved_2026_09_25`, and the executable copy of the
same table in scripts/fusion_param_common.py, which this script imports).

WHY A SEPARATE OFFLINE TOOL.  Our app's own Fusion round-trip
(`GET /api/fusion/params.csv`, `POST /api/fusion/import`, the web Geometry
tab's "Fusion CSV" buttons) already speaks this exact six-column format and
already uses these canonical names -- nothing there changed. This script is
for the OTHER direction the owner needs once: turning an OLDER Fusion model
(built before these names existed, e.g. C:\\Users\\vadim\\Downloads\\
ExportedParameters.csv) into one that uses them, so it can be edited in
Fusion, exported with Parameter I/O, and uploaded through the existing
"Fusion CSV" import button like any other model.

WHAT IT DOES
  * renames the 33 mapped rows (Name column) to their canonical name;
  * converts VALUES where the old and new parameter use a different basis
    (stator_up_r a RADIUS -> stator_diameter a DIAMETER, x2; mag_step a
    lamination SEGMENT LENGTH -> magnet_lamination, 0 when it equals the
    motor length);
  * rewrites every OTHER row's Expression TOKEN-SAFELY wherever it
    references a renamed name, so a derived/mechanical helper that is not
    itself renamed (e.g. `stator_mid_r = stator_up_r - slot_h - core_h/2`)
    still computes the same result after the primaries it depends on change
    name and, for stator_up_r, basis;
  * creates the rows that have no legacy counterpart at all
    (num_slots_per_segment, shaft_height, sleeve_thickness) or only
    conditionally (stator_fillet_r1 <- stator_r1, rotor_fill_r <- rotor_r1,
    created fresh if the legacy name is absent), using motor_ai_sim's
    current default geometry (config/motor_config.yaml) as the value;
  * leaves every other row (mechanical parts, offsets, derived sketch
    helpers, `wire_split`) exactly as it was, apart from the token rewrite
    above;
  * refuses the whole run on any name collision (two input rows that would
    end up with the same Name) instead of silently overwriting one.

USAGE
    python scripts/fusion_param_rename.py IN.csv OUT.csv
    python scripts/fusion_param_rename.py IN.csv OUT.csv --dry-run
    python scripts/fusion_param_rename.py IN.csv OUT.csv --config path/to/motor_config.yaml

`--dry-run` prints the full diff (renamed / converted / created / unknown)
and writes nothing.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fusion_param_common as FPC  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "motor_config.yaml"

HEADER = ["Name", "Unit", "Expression", "Value", "Comment", "Favorite"]


class FusionRenameError(RuntimeError):
    """Raised (never silently swallowed) when the rename cannot proceed as-is."""


# ─────────────────────────────────────────────────────────────────────────
# CSV I/O -- same six-column Parameter I/O shape our own /api/fusion route
# reads and writes (src/motor_ai_sim/routes/fusion.py), matched by HEADER
# NAME rather than position so a slightly different column order still works.
# ─────────────────────────────────────────────────────────────────────────
def read_param_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    rdr = csv.DictReader(io.StringIO(text))
    fieldnames = list(rdr.fieldnames or [])
    rows = [dict(r) for r in rdr]
    cols = {c.strip().lower(): c for c in fieldnames}
    for required in ("name", "expression"):
        if required not in cols:
            raise FusionRenameError(
                "expected the Parameter I/O columns Name, Unit, Expression, "
                "Value, Comment, Favorite (got %s)" % (fieldnames,))
    return fieldnames, rows


def write_param_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r.get(h, "") for h in HEADER])
    path.write_bytes(buf.getvalue().encode("utf-8-sig"))


def load_create_defaults(config_path: Path) -> Dict[str, float]:
    """Current default geometry values for the CREATE / rename_or_create
    entries, read straight from motor_config.yaml -- no motor_ai_sim import
    needed, just its config file."""
    import yaml
    d = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    geo = dict(d.get("geometry") or {})
    wanted = [e["canonical"] for e in FPC.ENTRIES
              if e["action"] in ("create", "rename_or_create")]
    missing = [k for k in wanted if k not in geo]
    if missing:
        raise FusionRenameError(
            "config %s has no default value for %s (needed to create the "
            "missing Fusion parameter(s))" % (config_path, missing))
    return {k: float(geo[k]) for k in wanted}


# ─────────────────────────────────────────────────────────────────────────
# Core rename
# ─────────────────────────────────────────────────────────────────────────
class RenameResult:
    def __init__(self) -> None:
        self.rows: List[Dict[str, str]] = []
        self.renamed: List[str] = []       # "old -> new: val1 -> val2" lines
        self.created: List[str] = []       # "new = val (created)" lines
        self.unknown: List[str] = []       # names left exactly as-is
        self.rewritten_exprs: List[str] = []  # "name: expr -> expr" lines
        self.nonlinear_flags: List[str] = []  # names needing manual review


def _row_value(row: Dict[str, str], cols: Dict[str, str]) -> Optional[float]:
    raw = row.get(cols.get("value", "Value")) or row.get(cols.get("expression", "Expression"))
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def rename(rows: List[Dict[str, str]], create_defaults: Dict[str, float],
           fieldnames: List[str]) -> RenameResult:
    cols = {c.strip().lower(): c for c in fieldnames}
    name_col = cols.get("name", "Name")
    expr_col = cols.get("expression", "Expression")
    value_col = cols.get("value", "Value")

    res = RenameResult()
    subs, nonlinear_names = FPC.substitution_map()

    # motor_length's OLD value (stator_w) is needed for the magnet_lamination
    # conversion -- find it up front, before any row is rewritten.
    motor_length_value: Optional[float] = None
    for r in rows:
        if str(r.get(name_col, "")).strip() == "stator_w":
            motor_length_value = _row_value(r, cols)
            break
    if motor_length_value is None:
        motor_length_value = create_defaults.get("motor_length")

    used_old_names = set()
    new_rows: List[Dict[str, str]] = []
    final_names: List[str] = []

    for r in rows:
        old_name = str(r.get(name_col, "")).strip()
        if not old_name:
            continue
        entry = FPC.BY_OLD.get(old_name)
        out = dict(r)
        if entry is not None:
            used_old_names.add(old_name)
            canonical = entry["canonical"]
            old_val = _row_value(r, cols)
            if old_val is None:
                raise FusionRenameError(
                    "%s: could not read a numeric value from %r -- refusing "
                    "to guess" % (old_name, r.get(value_col) or r.get(expr_col)))
            new_val = FPC.convert_forward(entry, old_val,
                                           motor_length_value=motor_length_value)
            out[name_col] = canonical
            out[expr_col] = FPC.num(new_val)
            out[value_col] = FPC.num(new_val)
            final_names.append(canonical)
            if abs(new_val - old_val) > 1e-9:
                res.renamed.append("%s -> %s: %s -> %s (converted)"
                                    % (old_name, canonical, FPC.num(old_val), FPC.num(new_val)))
            else:
                res.renamed.append("%s -> %s: %s" % (old_name, canonical, FPC.num(new_val)))
        else:
            # Not one of the 33 -- left alone by name, but its Expression may
            # still reference a renamed token (e.g. a derived helper like
            # stator_mid_r or arc): rewrite that, token-safely.
            expr = str(r.get(expr_col, "") or "")
            new_expr, touched = FPC.rewrite_expression(expr, subs)
            if new_expr != expr:
                out[expr_col] = new_expr
                res.rewritten_exprs.append("%s: %r -> %r" % (old_name, expr, new_expr))
                for t in touched:
                    if t in nonlinear_names:
                        res.nonlinear_flags.append(
                            "%s: references %s (non-linear conversion) via "
                            "the renamed token -- verify by hand" % (old_name, t))
            else:
                res.unknown.append(old_name)
            final_names.append(old_name)
        new_rows.append(out)

    # rename_or_create + create entries not satisfied by the input file.
    for entry in FPC.ENTRIES:
        canonical = entry["canonical"]
        if entry["action"] == "not_mapped":
            continue
        if entry["action"] == "create":
            already = any(str(r.get(name_col, "")).strip() == canonical for r in rows)
            if already:
                continue
            val = create_defaults[canonical]
            new_rows.append({name_col: canonical, cols.get("unit", "Unit"): entry["unit"],
                              expr_col: FPC.num(val), value_col: FPC.num(val),
                              cols.get("comment", "Comment"):
                                  "created by fusion_param_rename.py (owner-approved "
                                  "2026-09-25 mapping) -- no equivalent parameter in the source file",
                              cols.get("favorite", "Favorite"): "False"})
            final_names.append(canonical)
            res.created.append("%s = %s" % (canonical, FPC.num(val)))
        elif entry["action"] == "rename_or_create":
            if entry["old"] in used_old_names or canonical in final_names:
                continue  # renamed above already
            already = any(str(r.get(name_col, "")).strip() == canonical for r in rows)
            if already:
                continue
            val = create_defaults[canonical]
            new_rows.append({name_col: canonical, cols.get("unit", "Unit"): entry["unit"],
                              expr_col: FPC.num(val), value_col: FPC.num(val),
                              cols.get("comment", "Comment"):
                                  "created by fusion_param_rename.py (owner-approved "
                                  "2026-09-25 mapping) -- %r not found in the source file"
                                  % (entry["old"],),
                              cols.get("favorite", "Favorite"): "False"})
            final_names.append(canonical)
            res.created.append("%s = %s (no %s in the source)" % (canonical, FPC.num(val), entry["old"]))

    # Collision guard: refuse rather than silently drop/overwrite a row.
    seen: Dict[str, int] = {}
    for n in final_names:
        seen[n] = seen.get(n, 0) + 1
    collisions = sorted(n for n, c in seen.items() if c > 1)
    if collisions:
        raise FusionRenameError(
            "refusing to write: %d name(s) collide after renaming: %s"
            % (len(collisions), ", ".join(collisions)))

    # Normalise every output row to the canonical HEADER shape.
    for out in new_rows:
        row6 = {h: "" for h in HEADER}
        for h in HEADER:
            src = cols.get(h.lower(), h)
            if src in out:
                row6[h] = out[src]
            elif h in out:
                row6[h] = out[h]
        if not row6.get("Favorite"):
            row6["Favorite"] = "False"
        res.rows.append(row6)

    return res


def format_diff(res: RenameResult) -> str:
    def block(title: str, lines: List[str]) -> str:
        if not lines:
            return ""
        return "\n%s (%d)\n  %s" % (title, len(lines), "\n  ".join(lines))

    return (
        block("RENAMED", res.renamed)
        + block("CREATED", res.created)
        + block("EXPRESSIONS REWRITTEN (dependent rows)", res.rewritten_exprs)
        + block("NEEDS MANUAL REVIEW (non-linear conversion referenced elsewhere)",
                res.nonlinear_flags)
        + block("UNCHANGED (not one of the 33, no reference to a renamed name)", res.unknown)
    ).lstrip("\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", type=Path, help="old Fusion Parameter I/O CSV")
    ap.add_argument("output", type=Path, help="renamed CSV to write")
    ap.add_argument("--dry-run", action="store_true",
                     help="print the diff, write nothing")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                     help="motor_config.yaml to source CREATE-row default "
                          "values from (default: %(default)s)")
    args = ap.parse_args(argv)

    fieldnames, rows = read_param_csv(args.input)
    defaults = load_create_defaults(args.config)
    try:
        res = rename(rows, defaults, fieldnames)
    except FusionRenameError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2

    print(format_diff(res))
    if args.dry_run:
        print("\n(dry run -- nothing written)")
        return 0

    write_param_csv(args.output, res.rows)
    print("\nwrote %s (%d rows)" % (args.output, len(res.rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
