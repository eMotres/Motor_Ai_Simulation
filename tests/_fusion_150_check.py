"""freecadcmd script of tests/test_cad_150_fusion_full.py: the selection rules of profile.fusion_parts (template A.7.8) on the CIANO28 150_35 STEP
files, as JSON facts: the seed solid of each part and the number of placed Fusion instances."""
import json, os, sys
sys.path.insert(0, os.environ["FC_CAD_ROOT"])
from motor_templates.segmented_flatwire import fusion_parts as FP
from motor_templates.segmented_flatwire.runstatus import finish

cfg = json.load(open(os.environ["FC_FP"], encoding="utf-8"))
rep = {}
for bn in ("Magnet", "Coil", "Bearing_seal"):
    path, _ = FP.find_fusion_part(cfg["files"][bn], cfg["dir"])
    rule = (cfg.get("select") or {}).get(bn)
    sols = FP.select_solids(FP.read_step(path), rule, cfg["files"][bn])
    seed = FP.seed_solid(sols, rule, cfg["files"][bn])
    seed = FP.transformed(seed, FP.matrix(cfg["placement"][bn]))
    rep[bn] = dict(seed_solids=len(seed.Solids), seed_volume=seed.Volume, seed_zmin=seed.BoundBox.ZMin,
                   instances=len(FP.fusion_instances(cfg, bn)))
with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
    json.dump(rep, fh, indent=1)
finish(True, "selection checked")
