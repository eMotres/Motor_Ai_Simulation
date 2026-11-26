"""Regenerates ``cost_estimate_before_development_line.json``.  Run it against a checkout of the commit BEFORE the development line
(``8086a23da3dfff712bc82f32fb888f76d82f028f``, the #801 head), never against current code:

    git worktree add /tmp/before 8086a23da3dfff712bc82f32fb888f76d82f028f
    cd /tmp/before && PYTHONPATH=src python <this file> <output.json>"""
import json, sys
from motor_ai_sim.development import cost_estimate as CE
schema=CE.load_schema()
def line(amount=None, weeks=None, note=""):
    d=CE.blank_line("EUR")
    if amount is not None:
        d.update({"status":"filled","amount":amount,"source":"manual","by":"admin@example.test","date":"2026-11-02"})
    d["weeks"]=weeks; d["note"]=note
    return d
stored={"version":2,"schema_version":2,"project_id":"alpha","revision":4,
 "settings":{"complex":False,"iterations":2,"currency":"EUR","contingency_pct":10.0,"ship_to":{"country":"AR","city":"Buenos Aires"},"payment_terms":""},
 "iterations":[
  {"index":1,"motors":2,"bench":"dyno","weeks_override":None,"lines":{"tooling":line(1000.0,6),"manufacturing":line(333.33,4),"testing":line(250.0,2),"shipping":line(101.11,3,"note")}},
  {"index":2,"motors":3,"bench":"none","weeks_override":9,"lines":{"tooling":line(200.0,3),"manufacturing":line(333.33),"testing":line(),"shipping":line(None,2)}}],
 "updated_at":"2026-11-02T10:00:00+00:00","updated_by":"admin@example.test"}
est=CE.from_stored(stored,schema,"alpha")
out={"stored":stored,"normalized":est,"computed":CE.compute(est,schema),"diff":CE.diff(None,est,schema),"weeks":[CE.iteration_weeks(i) for i in est["iterations"]]}
open(sys.argv[1],'w').write(json.dumps(out,indent=1)+"\n")
print(out["computed"]["total"], out["computed"]["weeks_total"], out["computed"]["missing_count"])
