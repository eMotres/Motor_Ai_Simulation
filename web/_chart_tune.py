# One-shot: halve chart data-point radii and thin the series lines across the
# recharts components (user request: dots ~2x smaller, lines a bit thinner).
# Touches ONLY chart data-series props (dot/activeDot/strokeWidth on series),
# not geometry schematics.
import re
from pathlib import Path

SRC = Path(__file__).parent / "src" / "components"

FILES = [
    "admin/AdminPanel.tsx",
    "compare/BatteryPanel.tsx",
    "compare/PerformanceCharts.tsx",
    "materials/MaterialDetailView.tsx",
    "simulation/BHCurveChart.tsx",
    "simulation/PhysicsDashboard.tsx",
    "simulation/TransientCharts.tsx",
    "sweep/DescentPanel.tsx",
    "sweep/SweepStudyPanel.tsx",
]

RULES = [
    # data-point dots: r halved
    (re.compile(r"dot=\{\{ r: 3 \}\}"), "dot={{ r: 1.5 }}"),
    (re.compile(r"dot=\{\{ r: 2 \}\}"), "dot={{ r: 1 }}"),
    (re.compile(r"dot=\{\{ r: 2,"), "dot={{ r: 1,"),
    (re.compile(r"activeDot=\{\{ r: 5 \}\}"), "activeDot={{ r: 3 }}"),
    (re.compile(r"activeDot=\{\{ r: 4 \}\}"), "activeDot={{ r: 2.5 }}"),
    # series lines: a bit thinner
    (re.compile(r"strokeWidth=\{2\.5\}"), "strokeWidth={1.5}"),
    (re.compile(r"strokeWidth=\{2\}"), "strokeWidth={1.25}"),
    (re.compile(r"strokeWidth=\{1\.5\}"), "strokeWidth={1}"),
]

total = 0
for rel in FILES:
    f = SRC / rel
    text = f.read_text(encoding="utf-8")
    n_file = 0
    for rx, sub in RULES:
        text, n = rx.subn(sub, text)
        n_file += n
    # objective-space scatter points (SweepStudyPanel): 2.4 -> 1.2, sel 5.5 -> 4
    if rel.endswith("SweepStudyPanel.tsx"):
        text, n = re.subn(r"r=\{sel \? 5\.5 : 2\.4\}", "r={sel ? 4 : 1.2}", text)
        n_file += n
    if n_file:
        f.write_text(text, encoding="utf-8", newline="\n")
        print(f"{rel}: {n_file}")
        total += n_file
print(f"TOTAL: {total}")
