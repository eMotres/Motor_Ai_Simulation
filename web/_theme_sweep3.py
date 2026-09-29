# Third pass: the remaining dark hex family across ALL component files.
# Line-level skip of canvas/three drawing code (var(--..) unusable there).
import re
from pathlib import Path

ROOT = Path(__file__).parent / "src"

SKIP = re.compile(r"ctx\.|fillStyle|strokeStyle|THREE\.|new Color|"
                  r"scene\.|material|attach|args=|<canvas|getContext")

RULES = [
    (re.compile(r"#060d17|#0a1628|#0b1424|#0e1a2f|#0d1b30|#0f1d33|#0a1020|"
                r"#0f2036|#1f2937|#0a1018", re.I), "var(--panel-2)"),
    (re.compile(r"solid #1e3a5f", re.I), "solid var(--line-accent)"),
    (re.compile(r"#1e3a5f", re.I), "var(--line-accent)"),
    (re.compile(r"#14532d|#052e16|#0a2010|#1a2e1a", re.I), "var(--ok-bg)"),
]

total = 0
for f in sorted(ROOT.rglob("*.tsx")):
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
        print(f"{f.relative_to(ROOT).as_posix()}: {n_file}")
        total += n_file
print(f"TOTAL: {total}")
