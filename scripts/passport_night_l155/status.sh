#!/bin/bash
B=/opt/motres/compute/passport-l155; cd $B
date; tail -3 logs/pausewatch.log 2>/dev/null; tail -4 logs/grow.log 2>/dev/null
[ -e work/pause ] && echo "PAUSED (a user job is running)"
echo "workers file: $(cat work/workers)"; docker ps --format '{{.Names}} {{.Status}}' | grep ppl155
python3 - <<'PY'
import json, os
p = "work/out/L155/results.jsonl"
n = ok = 0; wall = 0.0; sets = {}
if os.path.exists(p):
    for l in open(p):
        try: r = json.loads(l)
        except Exception: continue
        n += 1; ok += bool(r.get("ok")); wall += float(r.get("wall_s") or 0)
        s = (r.get("meta") or {}).get("set", "?"); sets.setdefault(s, [0, 0]); sets[s][0] += 1; sets[s][1] += bool(r.get("ok"))
print("stage-1 FEM jobs: %d recorded, %d ok, %.1f cpu-h" % (n, ok, wall / 3600))
print("  by set (recorded/ok):", {k: tuple(v) for k, v in sets.items()})
print("STAGE1_DONE" if os.path.exists("work/out/L155/STAGE1_DONE") else "stage 1 not done", "| DONE_ALL" if os.path.exists("work/out/DONE_ALL") else "")
PY
echo "--- queue.log (last 12)"; tail -12 work/out/full/queue.log 2>/dev/null
ls work/out/full/*.json 2>/dev/null
