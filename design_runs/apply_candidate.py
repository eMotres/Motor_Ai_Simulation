"""Bake an optimization candidate into a motor's final.yaml + the live config, then
it can be re-evaluated/registered. Merges a geometry override and sets the operating
point (current, gamma via phase_offset_deg, rpm).

Usage:  PYTHONPATH=src python design_runs/apply_candidate.py FINAL.yaml '<json overrides>' max_current gamma rpm
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import yaml
from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache

FINAL = sys.argv[1]
OVER = json.loads(sys.argv[2])
CUR = float(sys.argv[3]); GAMMA = float(sys.argv[4]); RPM = float(sys.argv[5])

ROOT = Path(__file__).parent.parent
FP = ROOT / "design_runs" / FINAL
raw = yaml.safe_load(FP.read_text(encoding="utf-8"))
raw["geometry"].update({k: float(v) for k, v in OVER.items()})
raw["simulation"]["max_current"] = CUR
raw["simulation"]["phase_offset_deg"] = GAMMA
raw["simulation"]["rpm"] = RPM

FP.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
Path(DEFAULT_CONFIG_PATH).write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
clear_config_cache()
g = raw["geometry"]
print(f"applied to {FINAL}: tooth={g['tooth_width']} mag_h={g['magnet_height']} "
      f"core={g['core_thickness']} slot={g['slot_height']}  | op: {CUR}A γ={GAMMA} {RPM}rpm")
