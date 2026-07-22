"""P1 slice 2 — simulation endpoints accept an optional `geo` override.

Local check: (1) _current_geom_hash_and_params overlays geo + changes the hash;
(2) the real build2d_sliding_band mesh handler runs without geo (back-compat) and
with a valid geo override, and the resulting mesh differs.
"""
import asyncio
import json
from motor_ai_sim.routes.simulation import (
    _current_geom_hash_and_params,
    build_fem_mesh_2d_sliding_band,
)

h0, p0 = _current_geom_hash_and_params(None)
h1, p1 = _current_geom_hash_and_params(json.dumps({"tooth_width": 99.0}))
print(f"[1] no-geo  tooth_width={p0.get('tooth_width')}  hash={h0}")
print(f"[2] geo     tooth_width={p1.get('tooth_width')} (expect 99.0)  hash={h1}  hash-differs={h0 != h1}")

tw = float(p0.get("tooth_width", 11.0))
safe_tw = round(max(8.0, tw - 1.0), 2)


def stats(m):
    return {k: m.get(k) for k in ("n_vertices", "n_triangles", "n_stator_tris", "n_rotor_tris")}


async def run():
    m0 = await build_fem_mesh_2d_sliding_band(mesh_size_mm=6.0)                       # no geo
    m1 = await build_fem_mesh_2d_sliding_band(mesh_size_mm=6.0, geo=json.dumps({"tooth_width": safe_tw}))
    return m0, m1


m0, m1 = asyncio.run(run())
print(f"[3] build2d_sliding_band no-geo : {stats(m0)}")
print(f"[4] build2d_sliding_band tw={safe_tw}: {stats(m1)}")
print("RESULT:", "OK — sim geo override honoured (mesh differs, back-compat intact)"
      if (h0 != h1 and p1.get("tooth_width") == 99.0 and m0 and m1 and stats(m0) != stats(m1))
      else "CHECK — review output above")
