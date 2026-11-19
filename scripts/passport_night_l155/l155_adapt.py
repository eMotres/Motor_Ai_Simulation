"""Adaptive width + whole-task scheduling for the L155 passport night job (orchestration only; no physics).

Wraps passport_pilot.Runner so that the number of FEM jobs in flight follows /work/workers (0 = hold; raised by
the host-side grower when the d40 lanes' lock files are acquired) and never starts a job while /work/pause exists.
Result records are written exactly as the stock Runner writes them (results.jsonl, raw/*.json.gz).
"""
import json, os, subprocess, sys, threading, time
from concurrent.futures import FIRST_COMPLETED, wait

W = "/work"
sys.path.insert(0, W + "/code/scripts")
import passport_pilot as PP


def read_cap():
    try:
        return int(open(W + "/workers").read().strip())
    except Exception:
        return 1


def paused():
    return os.path.exists(W + "/pause")


class Cap:
    """Shared slot counter (stage jobs and whole tasks)."""
    def __init__(self):
        self.lock = threading.Condition()
        self.inflight = 0

    def acquire(self, n=1):
        while True:
            with self.lock:
                if not paused() and self.inflight + n <= max(read_cap(), n if self.inflight == 0 else 0):
                    self.inflight += n
                    return
            time.sleep(10)

    def release(self, n=1):
        with self.lock:
            self.inflight -= n


CAP = Cap()
MAXW = 16


class AdaptiveRunner(PP.Runner):
    def pool(self):
        if self._pool is None:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            from motor_ai_sim.passport_v1.jobs import worker_init
            mats = {k: v["name"] for k, v in self.snap["materials"].items()}
            self._pool = ProcessPoolExecutor(
                max_workers=MAXW, mp_context=mp.get_context("spawn"), max_tasks_per_child=6,
                initializer=worker_init, initargs=(mats,))
        return self._pool

    def run(self, jobs):
        from motor_ai_sim.passport_v1.jobs import run_job
        todo = [j for j in jobs if j["id"] not in self.done]
        if todo:
            pool = self.pool()
            futs = {}
            queue = list(todo)
            while queue or futs:
                while queue:
                    # non-blocking probe of the shared cap
                    with CAP.lock:
                        ok = (not paused()) and CAP.inflight + 1 <= read_cap()
                        if ok:
                            CAP.inflight += 1
                    if not ok:
                        break
                    j = queue.pop(0)
                    futs[pool.submit(run_job, j, str(self.od))] = j
                if not futs:
                    time.sleep(10)
                    continue
                done, _ = wait(list(futs), timeout=15, return_when=FIRST_COMPLETED)
                for f in done:
                    j = futs.pop(f)
                    CAP.release()
                    try:
                        rec = f.result()
                    except Exception as e:      # noqa: BLE001 - pool crash (e.g. OOM kill)
                        rec = {"id": j["id"], "ok": False, "meta": j.get("meta"),
                               "error": f"pool: {type(e).__name__}: {e}"}
                        self._pool = None        # a broken pool is replaced
                        pool = self.pool()
                    with open(self.res_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec, default=str) + "\n")
                    if rec.get("ok"):
                        self.done[rec["id"]] = rec
                    print(f"  {rec['id']}: ok={rec.get('ok')} {rec.get('wall_s', 0):.1f} s"
                          + ("" if rec.get("ok") else "  " + str(rec.get("error"))[:300]), flush=True)
                    if not rec.get("ok") and str(rec.get("error", "")).startswith("pool:"):
                        # requeue once after a pool crash
                        if not j.get("_retried"):
                            j["_retried"] = True
                            queue.append(j)
            if (self.od / "stop").exists():
                raise SystemExit("stop file present")
        return {j["id"]: self.done[j["id"]] for j in jobs if j["id"] in self.done}


def run_task(name, args, slots=1, threads=1, retries=1):
    """A whole passport_full task as one subprocess (own sandbox workspace); counts `slots` against the cap."""
    SPEC = W + "/spec/CIANO10_200_opt.yaml"
    for attempt in range(retries + 1):
        CAP.acquire(slots)
        t0 = time.time()
        try:
            env = dict(os.environ)
            log = open(W + "/out/full/qlog_%s.txt" % name, "a")
            rc = subprocess.call([sys.executable, "-u", W + "/code/scripts/passport_full.py", "--spec", SPEC,
                                  "--work", W, "parallel", "--", "threads=%d %s" % (threads, args)],
                                 stdout=log, stderr=subprocess.STDOUT, env=env)
        finally:
            CAP.release(slots)
        ok = os.path.exists(W + "/out/full/" + name + ".json")
        qlog("TASK %s rc=%s out=%s %.0f s (attempt %d)" % (name, rc, "ok" if ok else "MISSING", time.time() - t0, attempt))
        if ok:
            return True
    return False


def qlog(msg):
    os.makedirs(W + "/out/full", exist_ok=True)
    with open(W + "/out/full/queue.log", "a") as fh:
        fh.write("%s %s\n" % (time.strftime("%F %T"), msg))
