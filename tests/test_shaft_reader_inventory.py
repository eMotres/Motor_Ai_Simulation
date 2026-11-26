"""Every source reader of a shaft-convention metric must have a reviewed inventory row."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KEYS = re.compile(
    r"P_shaft_W|P_shaft_net_W|P_mech_W|T_shaft_Nm|T_em_avg_Nm|T_coupling_Nm|"
    r"efficiency|torque_density|shaft_conv|torque_per_mass|power_per_mass|torque_to_mass|eta_shaft|eta_em"
)
EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def readers(root=ROOT):
    found = {}
    for folder in ("src", "web/src"):
        for path in (root / folder).rglob("*"):
            if (not path.is_file() or path.suffix not in EXTENSIONS
                    or {"__tests__", "locales"}.intersection(path.parts)
                    or re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", path.name)):
                continue
            hits = [i for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                    if KEYS.search(line)]
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
