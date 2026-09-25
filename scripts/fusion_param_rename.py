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

WHAT IT DOES (revised 2026-09-25 per the owner's correction -- formulas are
not to be changed except the one named below; see docs/FUSION_SCRIPTS_HOWTO.md)
  * renames the 27 plain 1:1 rows (Name column only -- Expression/Value are
    carried over VERBATIM, byte for byte, never reformatted);
  * rewrites, TOKEN-SAFELY, every OTHER row's Expression wherever it
    references one of those 27 renamed names (e.g. the untouched-by-name
    helper `stator_mid_r = stator_up_r - slot_h - core_h/2` has its
    `slot_h`/`core_h` tokens renamed to `slot_height`/`core_thickness`,
    nothing else about it changes) -- a plain identifier substitution, no
    other rewriting, no parenthesising, no reformatting;
  * `stator_diameter` <- `stator_up_r`: CREATES `stator_diameter` fresh,
    holding OUR value with an explicit unit ("12 mm", never a bare number
    for a length) computed as `stator_up_r * 2` (old was a RADIUS, canonical
    is the DIAMETER) -- and changes exactly ONE existing formula, per the
    owner: `stator_up_r`'s own Expression becomes `"stator_diameter / 2"`.
    `stator_up_r` itself is NEVER renamed, so every OTHER row that already
    referenced it (motor_d, stator_mid_r, stator_down_r, rotor_up_r, ...)
    needs no change at all and gets none;
  * `magnet_lamination` <- `mag_step`: CREATES `magnet_lamination` fresh,
    its value computed from `mag_step`'s current value (0 when it equals
    the motor length, i.e. no axial slicing; otherwise carried over as the
    segment length) -- `mag_step` itself is left COMPLETELY untouched (no
    rename, no rewritten expression: there is no clean, always-valid
    inverse formula for it, see fusion_param_common.lamination_backward);
  * creates the rows that have no legacy counterpart at all
    (num_slots_per_segment, shaft_height, sleeve_thickness) or only
    conditionally (stator_fillet_r1 <- stator_r1, rotor_fill_r <- rotor_r1,
    created fresh if the legacy name is absent), using motor_ai_sim's
    current default geometry (config/motor_config.yaml) as the value, with
    an explicit unit in the Expression;
  * leaves every other row (mechanical parts, offsets, derived sketch
    helpers, `wire_split`, `mag_step`) exactly as it was, apart from the
    token rewrite of renamed references described above;
  * refuses the whole run on any name collision (two input rows that would
    end up with the same Name) instead of silently overwriting one.

USAGE
    python scripts/fusion_param_rename.py IN.csv OUT.csv
    python scripts/fusion_param_rename.py IN.csv OUT.csv --dry-run
    python scripts/fusion_param_rename.py IN.csv OUT.csv --config path/to/motor_config.yaml

`--dry-run` prints the full diff (renamed / created / rewritten-references)
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
    """Current default geometry values for every CREATE-capable entry
    (create / rename_or_create / create_and_derive_old / create_from_legacy
    -- the last two only as a FALLBACK when the legacy name is absent, so
    the canonical parameter can still always be created), read straight
    from motor_config.yaml -- no motor_ai_sim import needed, just its
    config file."""
    import yaml
    d = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    geo = dict(d.get("geometry") or {})
    wanted = [e["canonical"] for e in FPC.ENTRIES
              if e["action"] in ("create", "rename_or_create",
                                  "create_and_derive_old", "create_from_legacy")]
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
        self.renamed: List[str] = []       # "old -> new" lines (Name only)
        self.derived: List[str] = []       # "old: expr -> expr" (stator_up_r)
        self.created: List[str] = []       # "new = val (created)" lines
        self.unknown: List[str] = []       # names left exactly as-is
        self.rewritten_exprs: List[str] = []  # "name: expr -> expr" lines
        self.unchanged_legacy: List[str] = []  # e.g. mag_step, left as-is on purpose
        # (before, after) row snapshots for every row that changed in any
        # way, `before` is None for a freshly created row -- for
        # `format_full_diff()`.
        self.diffs: List[Tuple[Optional[Dict[str, str]], Dict[str, str]]] = []


def _row_value(row: Dict[str, str], cols: Dict[str, str]) -> Optional[float]:
    raw = row.get(cols.get("value", "Value")) or row.get(cols.get("expression", "Expression"))
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _to_row6(out: Dict[str, str], cols: Dict[str, str]) -> Dict[str, str]:
    """Normalise a (possibly source-column-keyed) row dict to the canonical
    six-column HEADER shape."""
    row6 = {h: "" for h in HEADER}
    for h in HEADER:
        src = cols.get(h.lower(), h)
        if src in out:
            row6[h] = out[src]
        elif h in out:
            row6[h] = out[h]
    if not row6.get("Favorite"):
        row6["Favorite"] = "False"
    return row6


def _new_row6(name: str, unit: str, value: float, comment: str) -> Dict[str, str]:
    expr = FPC.expr_str(value, unit)
    return {"Name": name, "Unit": unit, "Expression": expr, "Value": FPC.num(value),
            "Comment": comment, "Favorite": "False"}


def rename(rows: List[Dict[str, str]], create_defaults: Dict[str, float],
           fieldnames: List[str]) -> RenameResult:
    cols = {c.strip().lower(): c for c in fieldnames}
    name_col = cols.get("name", "Name")
    expr_col = cols.get("expression", "Expression")
    value_col = cols.get("value", "Value")

    res = RenameResult()

    present_names = {str(r.get(name_col, "")).strip() for r in rows}
    present_names.discard("")

    # The old->canonical substitution table for TOKEN-SAFE reference rewrites
    # in OTHER rows' expressions -- built dynamically, only from names that
    # are ACTUALLY renamed in this run (a rename_or_create entry only
    # renames when its old name happens to be present; stator_up_r and
    # mag_step are NEVER in this table -- they are never renamed).
    subs: Dict[str, str] = {}
    for old in FPC.plain_rename_old_names():
        if old in present_names:
            subs[old] = FPC.BY_OLD[old]["canonical"]

    # motor_length's value is needed for the magnet_lamination conversion --
    # read from its OLD row (stator_w, itself a plain rename so its VALUE is
    # unaffected either way) up front, before any row is processed.
    motor_length_value: Optional[float] = None
    for r in rows:
        if str(r.get(name_col, "")).strip() == "stator_w":
            motor_length_value = _row_value(r, cols)
            break
    if motor_length_value is None:
        motor_length_value = create_defaults.get("motor_length")

    new_rows: List[Dict[str, str]] = []
    final_names: List[str] = []
    stator_up_r_value: Optional[float] = None
    mag_step_value: Optional[float] = None

    for r in rows:
        old_name = str(r.get(name_col, "")).strip()
        if not old_name:
            continue
        entry = FPC.BY_OLD.get(old_name)
        before = dict(r)
        out = dict(r)

        if entry is not None and entry["action"] == "create_and_derive_old":
            # stator_up_r: keep the NAME, change ONLY this one formula.
            stator_up_r_value = _row_value(r, cols)
            if stator_up_r_value is None:
                raise FusionRenameError(
                    "%s: could not read a numeric value -- refusing to guess" % old_name)
            factor = entry["conversion"][1]
            out[expr_col] = FPC.derive_stator_up_r_expression(factor)
            out[value_col] = FPC.num(stator_up_r_value)  # unchanged, now via formula
            res.derived.append("%s: %r -> %r" % (old_name, r.get(expr_col, ""), out[expr_col]))
            final_names.append(old_name)
            new_rows.append(out)
            row6 = _to_row6(out, cols)
            res.diffs.append((_to_row6(before, cols), row6))
            continue

        if entry is not None and entry["action"] == "create_from_legacy":
            # mag_step: read its value (to compute magnet_lamination below),
            # otherwise leave the row COMPLETELY as it was.
            mag_step_value = _row_value(r, cols)
            res.unchanged_legacy.append(
                "%s (used to compute %s, left as-is -- see "
                "fusion_param_common.lamination_backward for why)"
                % (old_name, entry["canonical"]))
            final_names.append(old_name)
            new_rows.append(out)
            continue

        if entry is not None and entry["action"] in ("rename", "rename_or_create"):
            # Plain 1:1: Name changes, Expression/Value carried over VERBATIM.
            out[name_col] = entry["canonical"]
            final_names.append(entry["canonical"])
            res.renamed.append("%s -> %s" % (old_name, entry["canonical"]))
            new_rows.append(out)
            res.diffs.append((_to_row6(before, cols), _to_row6(out, cols)))
            continue

        # Not one of the 33 -- left alone by NAME, but its Expression may
        # reference one of the 27 plainly-renamed names (e.g. the
        # untouched-by-name helper `stator_mid_r`): rewrite ONLY those
        # tokens, nothing else.
        expr = str(r.get(expr_col, "") or "")
        new_expr, _touched = FPC.rewrite_expression(expr, subs)
        if new_expr != expr:
            out[expr_col] = new_expr
            res.rewritten_exprs.append("%s: %r -> %r" % (old_name, expr, new_expr))
            res.diffs.append((_to_row6(before, cols), _to_row6(out, cols)))
        else:
            res.unknown.append(old_name)
        final_names.append(old_name)
        new_rows.append(out)

    # CREATE-capable entries not yet satisfied.
    for entry in FPC.ENTRIES:
        canonical = entry["canonical"]
        action = entry["action"]
        if action in ("not_mapped", "rename"):
            continue
        if canonical in final_names or canonical in present_names:
            continue  # already present under the canonical name

        if action == "create":
            val = create_defaults[canonical]
            comment = ("created by fusion_param_rename.py (owner-approved 2026-09-25 "
                       "mapping) -- no equivalent parameter in the source file")
        elif action == "rename_or_create":
            if entry["old"] in present_names:
                continue  # was renamed above already
            val = create_defaults[canonical]
            comment = ("created by fusion_param_rename.py (owner-approved 2026-09-25 "
                       "mapping) -- %r not found in the source file" % (entry["old"],))
        elif action == "create_and_derive_old":
            if stator_up_r_value is not None:
                val = FPC.convert_forward(entry, stator_up_r_value)
                comment = ("created by fusion_param_rename.py -- value = stator_up_r * "
                           "%g (owner-approved 2026-09-25 mapping)" % entry["conversion"][1])
            else:
                val = create_defaults[canonical]
                comment = ("created by fusion_param_rename.py -- no stator_up_r in the "
                           "source file, used motor_ai_sim's default")
        elif action == "create_from_legacy":
            if mag_step_value is not None:
                val = FPC.convert_forward(entry, mag_step_value,
                                           motor_length_value=motor_length_value)
                comment = ("created by fusion_param_rename.py -- value from mag_step "
                           "(owner-approved 2026-09-25 mapping); mag_step itself left as-is")
            else:
                val = create_defaults[canonical]
                comment = ("created by fusion_param_rename.py -- no mag_step in the "
                           "source file, used motor_ai_sim's default")
        else:  # pragma: no cover - guarded by the ENTRIES action set itself
            raise FusionRenameError("unknown action %r for %s" % (action, canonical))

        row6 = _new_row6(canonical, entry["unit"], val, comment)
        new_rows.append(row6)
        final_names.append(canonical)
        res.created.append("%s = %s" % (canonical, row6["Expression"]))
        res.diffs.append((None, row6))

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
        res.rows.append(out if set(out) == set(HEADER) else _to_row6(out, cols))

    # NO PARAMETER IS EVER DELETED (owner, 2026-09-25: "чтобы никаких
    # переменных не уничтожалось, только переименования") -- every input
    # row survives (unchanged, renamed, or with its Expression's referenced
    # tokens rewritten), and the only rows added are the ones this run
    # explicitly created. A count mismatch here means a row silently
    # vanished, which must never happen -- refuse rather than write a
    # lossy file.
    n_in = sum(1 for r in rows if str(r.get(name_col, "")).strip())
    n_out = len(res.rows)
    if n_out != n_in + len(res.created):
        raise FusionRenameError(
            "internal error: %d input row(s) + %d created != %d output row(s) "
            "-- refusing a write that would lose a parameter" % (n_in, len(res.created), n_out))

    return res


def format_diff(res: RenameResult) -> str:
    def block(title: str, lines: List[str]) -> str:
        if not lines:
            return ""
        return "\n%s (%d)\n  %s" % (title, len(lines), "\n  ".join(lines))

    return (
        block("RENAMED (Name only, Expression/Value unchanged)", res.renamed)
        + block("DERIVED (the one formula the owner asked to change)", res.derived)
        + block("CREATED", res.created)
        + block("EXPRESSIONS REWRITTEN (referenced a renamed name)", res.rewritten_exprs)
        + block("LEFT AS-IS ON PURPOSE (no clean inverse)", res.unchanged_legacy)
        + block("UNCHANGED (not one of the 33, no reference to a renamed name)", res.unknown)
    ).lstrip("\n")


def format_full_diff(res: RenameResult) -> str:
    """Every row that changed in ANY way, old -> new, Name/Expression/Value."""
    lines = []
    for before, after in res.diffs:
        if before is None:
            lines.append("+ %-22s %-30s = %s" % (after["Name"], after["Expression"], after["Value"]))
            continue
        b = "%-22s %-30s = %s" % (before["Name"], before["Expression"], before["Value"])
        a = "%-22s %-30s = %s" % (after["Name"], after["Expression"], after["Value"])
        if b != a:
            lines.append("  %s\n    -> %s" % (b, a))
    return "\n".join(lines)


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
    print("\nFULL DIFF (every changed row, old -> new)\n" + format_full_diff(res))
    if args.dry_run:
        print("\n(dry run -- nothing written)")
        return 0

    write_param_csv(args.output, res.rows)
    print("\nwrote %s (%d rows)" % (args.output, len(res.rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
