"""P1 slice 1 — geometry mesh endpoints accept an optional `geo` override.

Local verification (no server restart): imports the freshly-edited route module
and checks (1) _resolve_geo_dict honours `geo`, (2) absent/malformed `geo` falls
back to the global config (back-compat), (3) the real mesh2d handler builds with
an override without error and reflects it.
"""
import json
from motor_ai_sim.routes.geometry import _resolve_geo_dict, get_geometry_mesh2d

base = _resolve_geo_dict(None)
tw = float(base.get("tooth_width", 12.0))
print(f"[1] no geo            -> tooth_width={base.get('tooth_width')} (global config)")

ov = _resolve_geo_dict(json.dumps({"tooth_width": 99.0}))
print(f"[2] geo override      -> tooth_width={ov.get('tooth_width')} (expect 99.0); "
      f"other fields kept: {ov.get('stator_diameter') == base.get('stator_diameter')}")

bad = _resolve_geo_dict("not-json{{{")
print(f"[3] malformed geo     -> falls back to config: {bad.get('tooth_width') == base.get('tooth_width')}")


def stator_xspan(m):
    comp = m.get("stator_core") or m.get("stator") or {}
    v = comp.get("vertices") or []
    xs = [p[0] for p in v] if v and isinstance(v[0], (list, tuple)) else []
    return (round(min(xs), 2), round(max(xs), 2), len(v)) if xs else None


safe_tw = round(max(8.0, tw - 1.0), 2)   # smaller tooth = always valid
try:
    m_base = get_geometry_mesh2d(geo=None)
    m_ov = get_geometry_mesh2d(geo=json.dumps({"tooth_width": safe_tw}))
    print(f"[4] handler base mesh comps: {sorted(k for k in m_base if not k.startswith('_'))[:6]}")
    print(f"[4] base stator span      : {stator_xspan(m_base)}")
    print(f"[4] tooth_width={safe_tw} span: {stator_xspan(m_ov)}")
    handler_ok = bool(m_ov) and m_ov != m_base
except Exception as e:
    handler_ok = False
    print(f"[4] handler raised: {e}")
print("RESULT:", "OK — geo override honoured end-to-end"
      if (handler_ok and ov.get("tooth_width") == 99.0
          and bad.get("tooth_width") == base.get("tooth_width"))
      else "CHECK — review output above")
