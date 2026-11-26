"""Regenerates ``cost_estimate_pre_801.json``.  Run it against a checkout of the BASE branch WITHOUT #801, never against current code:

    git worktree add /tmp/base 1ba6e9edeaffb8ed0e40f6df3e908bb4ef6a86aa      # origin/pre-migration-freeze-2026-09-15 before #801
    cd /tmp/base && PYTHONPATH=src python <this file> <output.json>

The output must equal the checked-in file byte for byte (the test pins what the OLD code did)."""
import json
import sys
from motor_ai_sim.development import cost_estimate as CE, cost_quote as Q
schema=CE.load_schema()
def line(amount=None, weeks=None, note=""):
    d=CE.blank_line("EUR")
    if amount is not None:
        d.update({"status":"filled","amount":amount,"source":"manual","by":"admin@example.test","date":"2026-11-02"})
    d["weeks"]=weeks; d["note"]=note
    return d
stored={"version":2,"schema_version":2,"project_id":"alpha","revision":4,
 "settings":{"complex":False,"iterations":2,"currency":"EUR","contingency_pct":10.0,"ship_to":{"country":"AR","city":"Buenos Aires"},"payment_terms":"70 % before each iteration"},
 "iterations":[
  {"index":1,"motors":2,"bench":"dyno","weeks_override":None,"lines":{"tooling":line(1000.0,6),"manufacturing":line(333.33,4),"testing":line(250.0,2),"shipping":line(101.11,3,"note")}},
  {"index":2,"motors":3,"bench":"none","weeks_override":9,"lines":{"tooling":line(200.0,3),"manufacturing":line(333.33,4),"testing":line(),"shipping":line(120.0,2)}}],
 "updated_at":"2026-11-02T10:00:00+00:00","updated_by":"admin@example.test"}
est=CE.from_stored(json.loads(json.dumps(stored)),schema,"alpha")
out={"stored":stored,"normalized":est,"computed":CE.compute(est,schema),"diff":CE.diff(None,est,schema),
     "weeks":[CE.iteration_weeks(i) for i in est["iterations"]],"quote":Q.build_quote(est,schema,"2026-11-03")}
open(sys.argv[1],'w').write(json.dumps(out,indent=1)+"\n")
print(sorted(out["quote"]), sorted(out["normalized"]["settings"]))
