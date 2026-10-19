"""freecadcmd script of tests/test_cad_40_fusion_parts.py: the Fusion shaft stretched by +8 mm in its constant rotor-seat section (L12 -> L20), and
cut back by -8 mm, as JSON facts: volume = Fusion + 8 x section, ends move +-4 mm, one valid solid, round trip = the Fusion shaft."""
import json, os, sys
sys.path.insert(0, os.environ["FC_CAD_ROOT"])
import Part
from motor_templates.segmented_flatwire import fusion_parts as FP
from motor_templates.segmented_flatwire.runstatus import finish

sol = max(Part.read(os.environ["FC_STEP"]).Solids, key=lambda s: s.Volume)
area = FP._section_face(sol, 0.0).Area
longer, fill = FP.stretch_solid(sol, 0.0, 8.0)
back, _ = FP.stretch_solid(longer, 0.0, -8.0)
rt = abs(back.Volume - sol.Volume)      # boolean differences of near-identical Fusion surfaces are unreliable: compare volume, extent and face count
rt_ext = max(abs(back.BoundBox.ZMax - sol.BoundBox.ZMax), abs(back.BoundBox.ZMin - sol.BoundBox.ZMin))
rep = dict(fusion_volume=sol.Volume, section_area=area, longer_minus_fusion=longer.Volume - sol.Volume, filler=fill,
           solids=len(longer.Solids), valid=longer.isValid(),
           z_max_shift=longer.BoundBox.ZMax - sol.BoundBox.ZMax, z_min_shift=longer.BoundBox.ZMin - sol.BoundBox.ZMin,
           round_trip_volume_error=rt, round_trip_extent_error=rt_ext, faces_back=len(back.Faces), faces_fusion=len(sol.Faces), faces_longer=len(longer.Faces))
with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
    json.dump(rep, fh, indent=1)
finish(True, "stretch checked")
