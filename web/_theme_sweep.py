# One-shot: replace hardcoded dark-palette hex colors with CSS design tokens
# (var(--...) from index.css) across src/**/*.ts(x), excluding canvas/three.js
# files where CSS vars cannot be resolved by the drawing APIs.
import re
import sys
from pathlib import Path

SRC = Path(__file__).parent / "src"

EXCLUDE = {
    "components/compare/EfficiencyMap.tsx",
    "components/compare/GeometryProjections.tsx",
    "components/mesh/FemMeshViewer2D.tsx",
    "components/mesh/FemMeshViewer3D.tsx",
    "components/mesh/MeshPanel.tsx",
    "components/simulation/FemFieldChart.tsx",
    "components/sweep/Scatter3D.tsx",
    "components/viewer3d/ApiMotorMesh.tsx",
    "components/viewer3d/MagnetMesh.tsx",
    "components/viewer3d/MotorScene.tsx",
    "components/viewer3d/PointCloudMesh.tsx",
    "components/viewer3d/RotorMesh.tsx",
    "components/viewer3d/STLMesh.tsx",
    "components/viewer3d/ShaftMesh.tsx",
    "components/viewer3d/Viewcube.tsx",
}

# Ordered: border-context first so the remaining #1e293b maps to panel.
RULES = [
    (re.compile(r"solid #1e293b", re.I), "solid var(--line-soft)"),
    (re.compile(r"#1e293b", re.I), "var(--panel)"),
    (re.compile(r"#0f172a", re.I), "var(--app-bg)"),
    (re.compile(r"#0b1220|#0b0f1a|#151d2e|#1a2332|#111827|#0d1117", re.I), "var(--panel-2)"),
    (re.compile(r"#334155", re.I), "var(--line)"),
    (re.compile(r"#475569", re.I), "var(--text-4)"),
    (re.compile(r"#64748b", re.I), "var(--text-3)"),
    (re.compile(r"#94a3b8", re.I), "var(--text-2)"),
    (re.compile(r"#cbd5e1", re.I), "var(--text-1)"),
    (re.compile(r"#e2e8f0|#f1f5f9|#f8fafc", re.I), "var(--text-0)"),
    (re.compile(r"rgba\(0,\s*0,\s*0,\s*0\.7\d?\)"), "var(--overlay)"),
    (re.compile(r"rgba\(0,\s*0,\s*0,\s*0\.8\d?\)"), "var(--overlay)"),
]

total = 0
for f in sorted(SRC.rglob("*.ts*")):
    rel = f.relative_to(SRC).as_posix()
    if rel in EXCLUDE or f.suffix not in (".ts", ".tsx"):
        continue
    text = f.read_text(encoding="utf-8")
    n_file = 0
    for rx, sub in RULES:
        text, n = rx.subn(sub, text)
        n_file += n
    if n_file:
        f.write_text(text, encoding="utf-8", newline="\n")
        print(f"{rel}: {n_file}")
        total += n_file
print(f"TOTAL: {total}")
