"""Would this catalog be ambiguous on a case-sensitive filesystem?

WHY
===
``routes/family._die_dir()`` looks a die up by exact name.  On NTFS ``CILN28``
and ``ciln28`` are one directory, so the question never arises; on ext4 they
are two, and the pair behaves like a single die that intermittently answers
with the other one's geometry.  ``startup_checks.check_die_case`` refuses to
boot a server in that state — this script asks the same question *before* the
transfer, on the Windows box, where it is still one rename to fix.

It is deliberately the SAME function: ``startup_checks.case_collisions``.  A
second implementation would drift, and the one that drifted would be the one
the server trusts.

TWO QUESTIONS, NOT ONE
----------------------
* **Within a directory** — a real collision, already present.  On NTFS this is
  impossible, so a hit here means the tree was built somewhere else.
* **Across the layers** (workspace / published / shared) — a *latent*
  collision.  ``resolve_die_dir`` reads through the layers by exact name, so
  ``shared/dies/CILN28`` and a workspace's ``ciln28`` are, on Linux, two
  different dies with one Windows history.  Reported as a warning: it is not
  wrong, but it is never intentional.

USAGE
    python scripts/check_case_collisions.py config/dies
    python scripts/check_case_collisions.py /srv/motres/shared/dies \\
                                            /srv/motres/workspaces/*/dies
    python scripts/check_case_collisions.py --tree /srv/motres

Exit code 0 = clean, 1 = a within-directory collision, 2 = a usage error.
Cross-layer warnings alone do not fail: they are a review item, not a fault.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from motor_ai_sim.startup_checks import DIE_MARKER, case_collisions  # noqa: E402


def die_names(directory: Path) -> List[str]:
    try:
        return sorted(p.name for p in directory.iterdir()
                      if p.is_dir() and (p / DIE_MARKER).is_file())
    except OSError:
        return []


def roots_of_tree(tree: Path) -> List[Path]:
    """The catalog directories of a three-layer server tree."""
    out: List[Path] = []
    shared = tree / "shared" / "dies"
    if shared.is_dir():
        out.append(shared)
    ws = tree / "workspaces"
    if ws.is_dir():
        out.extend(sorted(d / "dies" for d in ws.iterdir()
                          if (d / "dies").is_dir()))
    pub = tree / "published"
    if pub.is_dir():
        # published/<ws_id>/ IS the dies directory for that namespace.
        out.extend(sorted(d for d in pub.iterdir() if d.is_dir()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="*", type=Path,
                    help="catalog directories (whose children are die folders)")
    ap.add_argument("--tree", type=Path,
                    help="a three-layer server tree; expands to every layer's "
                         "catalog directory")
    args = ap.parse_args(argv)

    roots: List[Path] = list(args.roots)
    if args.tree:
        roots.extend(roots_of_tree(args.tree))
    if not roots:
        ap.error("give one or more catalog directories, or --tree")

    fatal = 0
    seen: Dict[str, List[str]] = {}          # folded name -> "dir::realname"

    for d in roots:
        if not d.is_dir():
            print(f"skip (not a directory): {d}")
            continue
        names = die_names(d)
        print(f"{d}: {len(names)} die(s)")
        for _folded, group in case_collisions(d):
            fatal += 1
            print(f"  COLLISION  {' and '.join(repr(n) for n in group)} "
                  f"differ only by case")
        for n in names:
            seen.setdefault(n.lower(), []).append(f"{d}::{n}")

    cross = 0
    for _folded, where in sorted(seen.items()):
        spellings = {w.split("::", 1)[1] for w in where}
        if len(spellings) > 1:
            cross += 1
            print(f"  WARN cross-layer: {sorted(spellings)} -> {where}")

    if fatal:
        print(f"\n{fatal} within-directory collision(s) — a server would "
              f"refuse to boot on this tree.")
        return 1
    tail = f"; {cross} cross-layer spelling difference(s) to review" if cross else ""
    print(f"\nclean: no within-directory collisions{tail}")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
