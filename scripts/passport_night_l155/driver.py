"""L155 passport night driver (runs inside the container).  Orchestration only: the stages are the stock
passport_v1 stages (stage_*), the tasks are the stock passport_full tasks; only the ORDER and the width follow the
owner's priority (rated/peak + current x angle grid first, losses, demag, then the rest)."""
import argparse, json, os, sys, threading, time
from pathlib import Path
sys.path.insert(0, "/work/jobs")
import l155_adapt as A
PP = A.PP
W = Path("/work")
M = "L155"
SPEC = "/work/spec/CIANO10_200_opt.yaml"

STAGES_A = ["calib", "probe", "hot", "loss", "demag", "cold", "extra", "checks", "refine"]
STAGES_B = ["loss2", "loss3"]


def stage_pass(name, R, snap, st, od):
    from motor_ai_sim.passport_v1 import stages
    ts = time.time()
    A.qlog("STAGE %s start" % name)
    print("== %s stage %s" % (M, name), flush=True)
    getattr(stages, "stage_" + name)(R, snap, st)
    PP._save_state(od, st)
    st.setdefault("stage_wall_s", {})[name] = st.get("stage_wall_s", {}).get(name, 0.0) + time.time() - ts
    PP._save_state(od, st)
    A.qlog("STAGE %s done %.0f s" % (name, time.time() - ts))


def demag_limit_from_seq(levels, I0):
    """Highest current whose subsequent rated-point torque drop is <= 1 % (owner default 2, pending owner),
    linear interpolation between the bracketing levels; None when no valid result."""
    pts = []
    for fI in levels:
        p = W / "out" / "full" / ("demagseq_%s_%g.json" % (M, fI))
        if not p.exists():
            continue
        d = json.load(open(p))
        if d.get("valid") and d.get("torque_drop_pct") is not None:
            pts.append((fI * I0, float(d["torque_drop_pct"])))
    pts.sort()
    if not pts:
        return None, pts
    prev = (0.0, 0.0)
    for I, drop in pts:
        if drop > 1.0:
            I_a, d_a = prev
            lim = I_a + (1.0 - d_a) * (I - I_a) / (drop - d_a) if drop != d_a else I_a
            return lim, pts
        prev = (I, drop)
    return pts[-1][0], pts          # no level dropped > 1 %: capped at the top level


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-calib", action="store_true")
    a = ap.parse_args()
    PP._use_spec(SPEC)
    os.environ["MOTOR_AI_SIM_CONFIG"] = str(W / ("ws_" + M) / "motor_config.yaml")
    od = W / "out" / M
    (W / "out" / "full").mkdir(parents=True, exist_ok=True)
    snap = PP._snap(od)
    from motor_ai_sim.passport_v1 import psimap
    psimap.set_winding_basis(snap)
    st = PP._state(od)
    ns = argparse.Namespace(cmd="run", machine=M, stages="driver:" + ",".join(STAGES_A + STAGES_B),
                            workers=A.read_cap(), spec=SPEC)
    st.setdefault("baseline_runs", []).append(PP._baseline(W, ns))
    PP._save_state(od, st)
    R = A.AdaptiveRunner(od, snap, A.MAXW)
    I0 = float(snap["rated_duty"]["current_arms"])           # WINDING basis (delta)
    levels_holder = {}
    seq_done = threading.Event()
    stage_a_flag = {"done": False}

    def seq_thread():
        try:
            # the demag-limit pairs are whole tasks; they start after the loss and demag stages (priority order)
            kn = None
            while not kn:
                kn = PP._state(od).get("demag_knee")
                time.sleep(60)
            lv = (1.5, 2.0, 2.5)
            if kn and kn.get("mtpa_retention"):
                # retention at 1.5 I0 already below the 99.5 % knee: bracket lower
                r15 = [r for I, r in kn["mtpa_retention"] if abs(I / I0 - 1.5) < 0.01]
                if r15 and r15[0] < 99.5:
                    lv = (1.0, 1.25, 1.5)
            levels_holder["levels"] = lv
            ths = []
            for fI in lv:
                t = threading.Thread(target=A.run_task, args=("demagseq_%s_%g" % (M, fI),
                                                              "demagseq --machine %s --fi %g" % (M, fI)))
                t.start(); ths.append(t)
            for t in ths:
                t.join()
        finally:
            seq_done.set()

    tb = threading.Thread(target=seq_thread, daemon=True)
    tb.start()
    stages_a = ["calib"] if a.only_calib else STAGES_A
    for s in stages_a:
        stage_pass(s, R, snap, st, od)
    if a.only_calib:
        return
    seq_done.wait()
    lim, pts = demag_limit_from_seq(levels_holder.get("levels", ()), I0)
    if lim is not None:
        st["demag_limit_I_rms"] = lim
        st["demag_limit_source"] = ("demagseq pairs (rated-point torque drop <= 1 % after the overload, owner default "
                                    "2, pending owner), WINDING current, linear interpolation; drops [A_w, %%]: %s"
                                    % json.dumps(pts))
    else:
        st["demag_limit_source"] = "NO valid demagseq pair - loss3 capped at 2.5 I0 (top static level)"
    PP._save_state(od, st)
    for s in STAGES_B:
        stage_pass(s, R, snap, st, od)
    A.qlog("STAGE-1 + loss3 DONE")
    (od / "STAGE1_DONE").write_text(time.strftime("%F %T") + "\n")
    # lowest priority: 3-D Stage A at the own stack length (heavy; 2 slots)
    ok = A.run_task("stage_a_L155", "stage_a --machine %s --length 155" % M, slots=2, threads=2)
    A.qlog("stage_a_L155 %s" % ("ok" if ok else "FAILED/MISSING"))
    R.close()
    (W / "out" / "DONE_ALL").write_text(time.strftime("%F %T") + "\n")


if __name__ == "__main__":
    main()
