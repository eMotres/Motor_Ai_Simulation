# Second pass: the mixed files (MUI sx + canvas/three drawing).  Replace hex
# colors with CSS tokens ONLY on lines that are not canvas/three drawing code
# (ctx.*, fillStyle/strokeStyle, THREE materials) — the drawing APIs cannot
# resolve var(--...).  Canvas viewports deliberately STAY dark in both themes
# (CAD-style dark viewport); only the surrounding chrome flips.
import re
from pathlib import Path

SRC = Path(__file__).parent / "src" / "components"

FILES = [
    "compare/EfficiencyMap.tsx",
    "compare/GeometryProjections.tsx",
    "mesh/FemMeshViewer2D.tsx",
    "mesh/FemMeshViewer3D.tsx",
    "mesh/MeshPanel.tsx",
    "simulation/FemFieldChart.tsx",
    "sweep/Scatter3D.tsx",
]

SKIP = re.compile(r"ctx\.|fillStyle|strokeStyle|THREE\.|new Color|color\(|"
                  r"scene\.|material|attach|args=|<canvas|getContext")

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
for rel in FILES:
    f = SRC / rel
    lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
    n_file = 0
    out = []
    for line in lines:
        if not SKIP.search(line):
            for rx, sub in RULES:
                line, n = rx.subn(sub, line)
                n_file += n
        out.append(line)
    if n_file:
        f.write_text("".join(out), encoding="utf-8", newline="")
        print(f"{rel}: {n_file}")
        total += n_file
print(f"TOTAL: {total}")
