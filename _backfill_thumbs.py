"""Backfill real-geometry thumbnails onto existing user-saved catalog cards
(so motors saved before the feature also show their true cross-section).
Reuses the SAME generator the save path now uses. Throwaway."""
import json
from pathlib import Path
from motor_ai_sim.routes.presets import _gen_thumb_svg

ROOT = Path(__file__).parent
CAT = ROOT / "config" / "motor_catalog.json"
PRE = ROOT / "config" / "motor_presets.json"
cat = json.loads(CAT.read_text(encoding="utf-8"))
pre = json.loads(PRE.read_text(encoding="utf-8"))

n = 0
for m in cat.get("motors", []):
    if m.get("owner") != "user":
        continue
    pid = m.get("preset")
    geo = (pre.get(pid) or {}).get("geometry") if pid else None
    if not geo:
        print(f"  skip {m['id']} (no preset geometry: {pid})")
        continue
    svg = _gen_thumb_svg(geo)
    if svg and svg.startswith("<svg"):
        m["thumb_svg"] = svg
        n += 1
        print(f"  ok   {m['id']}  ({len(svg)} chars, {svg.count('<path')} paths)")
    else:
        print(f"  FAIL {m['id']}  (generator returned {svg!r:.40})")

CAT.write_text(json.dumps(cat, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"backfilled {n} user motors -> {CAT.name}")
