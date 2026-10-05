"""Write the versioned passport store (config/passports/<die>/<config>.json) from
passport-pilot records (docs/data/passport_pilot_d40/full/passport_<tag>.json).

Only identity + ``pwm_variants`` go in (what Configure reads, see
``motor_ai_sim/passport_store.py``); the heavy raw blocks stay in docs/data.

    python scripts/export_passport_store.py docs/data/passport_pilot_d40/full/passport_L12.json [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "config" / "passports"


def export(src: Path) -> Path:
    rec = json.loads(src.read_text(encoding="utf-8"))
    mach = rec["machine"]
    die, cfg = str(mach["die"]), str(mach["configuration"])
    slim = {
        "schema": rec.get("schema"),
        "machine": mach,
        "source": str(src.relative_to(ROOT)).replace("\\", "/") if src.is_relative_to(ROOT) else src.name,
        "pwm_variants": [v for v in rec.get("pwm_variants", [])
                         if isinstance(v, dict) and v.get("points")],
    }
    out = STORE / die / f"{cfg}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(slim, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for a in sys.argv[1:]:
        o = export(Path(a).resolve())
        print(f"{a} -> {o.relative_to(ROOT)} ({o.stat().st_size // 1024} KB)")
