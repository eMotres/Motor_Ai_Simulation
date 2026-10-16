"""Poll the descent progress endpoint; print one line per state change."""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

URL = "http://localhost:8001/api/optimization/descent/progress"
last = ""
while True:
    try:
        with urllib.request.urlopen(URL, timeout=20) as r:
            d = json.load(r)
    except Exception as e:
        line = f"BACKEND UNREACHABLE: {e}"
        if line != last:
            print(line)
            last = line
        time.sleep(120)
        continue
    b = d.get("best") or {}
    err = d.get("error")
    if err:
        print(f"ERROR: {err}")
        break
    bits = (f"phase={d.get('phase')} round={d.get('walk_round')} "
            f"iter={d.get('iter')}/{d.get('max_iters')} evals={d.get('n_evals')} "
            f"gammaMTPA={d.get('mtpa_gamma_deg')} "
            f"best: T={b.get('T_em_Nm')} ripple={b.get('T_ripple_pct')} "
            f"eff={b.get('efficiency')}")
    if not d.get("running"):
        res = d.get("result") or {}
        print(f"FINISHED {bits} | result_keys={list(res)[:8]}")
        break
    if bits != last:
        print(bits)
        last = bits
    time.sleep(240)
