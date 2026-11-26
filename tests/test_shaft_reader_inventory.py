"""Every source reader of a shaft-convention metric must have a reviewed inventory row."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Distinctive motor metrics are safe to scan in comments/types too. Generic
# efficiency and compact keys need an actual summary/point access, not prose.
KEYS = re.compile(
    r"P_shaft_W|P_shaft_net_W|P_mech_W|T_shaft_Nm|T_em_avg_Nm|T_em_Nm|T_avg_Nm|T_coupling_Nm|"
    r"efficiency_shaft|torque_density|shaft_conv|torque_per_mass|power_per_mass|torque_to_mass|eta_shaft|eta_em"
)
SUMMARY = r"(?:[A-Za-z_]*summary\w*|[A-Za-z_]*point\w*|metrics|baseline|bline)"
POINT_ALIAS = rf"(?:{SUMMARY}|p|pt|q|r|s|rec|row|m)"

def access(receiver, keys):
    return re.compile(rf"\b{receiver}\s*(?:\??\.\s*(?:{keys})\b|"
                      rf"(?:\??\.)?\[\s*['\"](?:{keys})['\"]|"
                      rf"\.get\(\s*['\"](?:{keys})['\"])")

COMPACT = access(rf"(?:{SUMMARY}|p|pt|q|r|rec|row)", "td|eff|torque")
EFFICIENCY = access(SUMMARY, "efficiency")
MOTOR_EFFICIENCY = access(POINT_ALIAS, "efficiency")


def metric_lines(source):
    motor_context = bool(KEYS.search(source))
    return [i for i, line in enumerate(source.splitlines(), 1)
            if KEYS.search(line) or COMPACT.search(line) or EFFICIENCY.search(line)
            or (motor_context and MOTOR_EFFICIENCY.search(line))]


EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def readers(root=ROOT):
    found = {}
    for folder in ("src", "web/src"):
        for path in (root / folder).rglob("*"):
            if (not path.is_file() or path.suffix not in EXTENSIONS
                    or {"__tests__", "locales"}.intersection(path.parts)
                    or re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", path.name)):
                continue
            hits = metric_lines(path.read_text(encoding="utf-8"))
            if hits:
                found[path.relative_to(root).as_posix()] = hits
    return found


def test_every_shaft_metric_reader_is_in_the_reviewed_inventory():
    doc = (ROOT / "docs/SHAFT_CONVENTION_READERS.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `((?:src|web/src)/[^`:]+)(?::\d+)?`", doc, re.M))
    missing = {name: lines[0] for name, lines in readers().items() if name not in listed}
    assert not missing, (
        "Review the convention/missing-value/legacy behavior and add an inventory row for: "
        + ", ".join(f"{name}:{line}" for name, line in sorted(missing.items()))
    )


def invalid_dispositions(doc):
    invalid = []
    for line in doc.splitlines():
        if not re.match(r"^\| `(?:src|web/src)/", line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        reason = re.fullmatch(r"(?:Confirmed|Fixed)(?: in `[^`]+`)?\s*:\s*(.+)", cells[2]) if len(cells) == 3 else None
        if not reason or len(reason[1].strip()) < 12:
            invalid.append(line)
    return invalid


def test_every_inventory_disposition_has_a_review_reason():
    doc = (ROOT / "docs/SHAFT_CONVENTION_READERS.md").read_text(encoding="utf-8")
    assert not invalid_dispositions(doc), "Missing review reasons: " + "\n".join(invalid_dispositions(doc))


def test_inventory_guard_matches_summary_access_without_generic_prose(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    positive = {
        "field.py": 'return r.T_em_Nm * omega',
        "average.ts": 'return summary.T_avg_Nm;',
        "compact.py": 'return point.get("td"), p["eff"], row.get("torque")',
        "optional.ts": 'return point?.td + pt?.eff + summary?.["torque"];',
        "eta.py": 'return summary.get("efficiency")',
        "nested.ts": 'return result.summary.efficiency;',
    }
    negative = {
        "gears.py": 'efficiency = gear.efficiency; torque = gear.torque',
        "propeller.ts": 'const label = "efficiency"; return propeller.efficiency;',
        "fins.py": 'efficiency = fin.get("efficiency"); td = 3; eff = .9; torque = 4',
        "labels.ts": 'const labels = { efficiency: "Efficiency", torque: "Torque" };',
    }
    for name, content in {**positive, **negative}.items():
        (src / name).write_text(content, encoding="utf-8")
    assert set(readers(tmp_path)) == {"src/" + name for name in positive}


def test_inventory_guard_rejects_yes_or_reasonless_dispositions():
    for disposition in ("yes", "Confirmed", "Fixed:", "Confirmed:", "Fixed in `src/a.py:123`:"):
        assert invalid_dispositions(f"| `src/a.py:1` (1) | no | {disposition} |")
    assert not invalid_dispositions("| `src/a.py:1` (1) | yes | Confirmed: carries the complete summary unchanged. |")
