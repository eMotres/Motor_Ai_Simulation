#!/usr/bin/env python
"""Read-only: every duty of every die under the roots, through the duty-load
guard (family.duty_geometry_diff) - how many would 409, and on which keys."""
import sys
from pathlib import Path

import yaml

from motor_ai_sim.routes import family as fam

n = n409 = 0
for root in sys.argv[1:]:
    for die in sorted(Path(root).rglob("die.yaml")):
        if any(p in (".history", "runs") for p in die.parts):
            continue
        d = yaml.safe_load(die.read_text(encoding="utf-8")) or {}
        for cf in sorted(die.parent.glob("*.yaml")):
            if cf.name == "die.yaml" or ".bak" in cf.name:
                continue
            c = yaml.safe_load(cf.read_text(encoding="utf-8")) or {}
            for duty in c.get("duties") or []:
                if not isinstance(duty, dict):
                    continue
                n += 1
                b = fam._blocking(fam.duty_geometry_diff(d, c, duty))
                if b:
                    n409 += 1
                    print(f"409 {die.parent.name}/{cf.stem}/{duty.get('name')}: "
                          + ", ".join(f"{x['key']} {x['live']}->{x['duty']}" for x in b[:6]))
print(f"== {n} duties, {n409} would 409")
