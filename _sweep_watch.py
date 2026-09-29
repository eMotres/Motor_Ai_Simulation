"""Background watcher for the running Sweep scan. Polls progress, logs to stdout,
exits on completion or a real stall (so the monitoring loop can react)."""
import json, time, urllib.request

URL = 'http://127.0.0.1:8001/api/optimization/scan/progress'
POLL_S = 60
MAX_POLLS = 9          # ~9 min per watcher run (re-launched by the loop)
STALL_POLLS = 7        # 'done' flat this many polls (~7 min) => real stall

def poll():
    d = json.loads(urllib.request.urlopen(URL, timeout=10).read())
    return d

prev = None
flat = 0
for i in range(MAX_POLLS):
    try:
        d = poll()
    except Exception as e:
        print(f'[{time.strftime("%H:%M:%S")}] poll {i}: ERROR {e}', flush=True)
        time.sleep(POLL_S); continue
    done, total, running = d.get('done', 0), d.get('total', 0), d.get('running')
    npts = len(d.get('points') or [])
    err = d.get('error')
    nbuilt = None
    res = d.get('result') or {}
    if res:
        nbuilt = res.get('n_built')
    print(f'[{time.strftime("%H:%M:%S")}] poll {i}: done={done}/{total} pts={npts} '
          f'running={running} cached={d.get("cached")} err={err} n_built={nbuilt}', flush=True)
    if not running:
        print('SCAN_COMPLETE', flush=True)
        break
    if err:
        print(f'SCAN_ERROR: {err}', flush=True)
        break
    if done == prev:
        flat += 1
    else:
        flat = 0
    prev = done
    if flat >= STALL_POLLS:
        print(f'STALL_DETECTED done stuck at {done}/{total} for ~{flat} min', flush=True)
        break
    time.sleep(POLL_S)
else:
    print('WATCHER_TIMEOUT still running, relaunch', flush=True)
