#!/usr/bin/env python
"""Copy the approved legal texts verbatim into config/legal (docs/LEGAL_TEXTS.md).

    python scripts/sync_legal_texts.py <folder with the *_v0.4.md files> [--version v0.4]

Four documents x two languages are copied as they are, only CRLF line ends become LF (the SHA-256 recorded with an acceptance must be the same
on every checkout); nothing is edited, translated or filled.  Re-run it whenever the approved
texts change BEFORE a release; once a version has been shown to users, a change is a NEW version (bump legal_texts.TERMS_VERSION).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: source file stem -> (directory under config/legal, target stem)
MAP = {"terms_of_use": ("terms_of_use", "terms_of_use"), "privacy_notice": ("privacy", "privacy"),
       "platform_agreement": ("platform_agreement_public", "platform_agreement"), "schedules": ("platform_agreement_public", "schedules")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("--version", default="v0.4")
    a = ap.parse_args(argv)
    missing = [str(a.source / f"{s}_{l}_{a.version}.md") for s in MAP for l in ("en", "zh-CN")
               if not (a.source / f"{s}_{l}_{a.version}.md").is_file()]
    if missing:
        print("missing source files:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2
    for s, (sub, stem) in MAP.items():
        for l in ("en", "zh-CN"):
            dst = ROOT / "config" / "legal" / sub / a.version / f"{stem}_{l}.md"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((a.source / f"{s}_{l}_{a.version}.md").read_bytes().replace(b"\r\n", b"\n"))
            print("copied", dst.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
