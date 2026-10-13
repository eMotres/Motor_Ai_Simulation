"""CAD builder CLI: simulation die + config  ->  <motor_id>.FCStd, full STEP, datums JSON, manifest.

  python -m motor_ai_sim.cad.build --die "CIANO14 40 new" --config L12
  python cad/run_build.py build --die "CIANO14 40 new" --config L20          (FreeCAD image: no FEM stack needed)
  python -m motor_ai_sim.cad.build back --die "CIANO14 40 new" --config L20 --fcstd my_copy.FCStd [--propose]

Outputs go to <out-root>/<die>/<config>/ (default /srv/motres/shared/cad on the server):
  <motor_id>.FCStd             parametric model, opens ready to look at (visibility, colours, isometric camera)
  <motor_id>_active.step       full STEP (Stator/Core, Winding/Coils/Coil_n, Rotor/RotorCore, Magnets/Magnet_nn,
                               Mechanics/Shaft, bearings, holders, rings, Prop_fix, Screws)
  <motor_id>_datums.json       frame, axial / radial datums, coil lead end faces
  <motor_id>_full.glb          the model for the web viewer (one coloured node per part): viewing is open to every signed-in user who
                               may see the motor; the STEP download (<motor_id>_active.step) is for NDA accounts and the owner
  params.json                  the FreeCAD profile built from the store (what the template was fed)
  pull_report.json             simulation vs CAD parameter table
  build_report.json / step_check.json / gui_report.json / gui_check.json
  manifest.json                die, config, param hash (_geoSig + CAD parameter hash), FreeCAD / template version, git sha

The build goes into a temporary sibling directory and replaces the old output only when every check passed.
Exit code: 0 ok, 2 bad input, 3 refused by a check (pre-check / health gate / STEP check), 4 crash.
"""
import argparse
import json
import os
import shutil
import sys
import time

from . import manifest as mf
from . import runner, store

SCRIPTS = os.path.join(store.CAD_DIR, "fc_scripts")
DEFAULT_OUT_ROOT = "/srv/motres/shared/cad"


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False, default=str)


def _read(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def _fc(script, env, args, gui=False):
    code, log, st = runner.run_script(os.path.join(SCRIPTS, script), dict(env, FC_CAD_ROOT=store.CAD_DIR),
                                      freecadcmd=args.freecadcmd, timeout=args.timeout,
                                      low_priority=not args.normal_priority, gui=gui)
    if args.verbose or code:
        print(log)
    return code, st


def _bom(work, mid, fcstd, eff, pj, args):
    """Bill of materials next to the STEP (bom.py / bom_store.py).  Never refuses the build: a failure is recorded in the manifest."""
    from . import bom_store
    dump = os.path.join(work, mid + "_bom_model_dump.json")
    code, st = _fc("bom_dump.py", dict(FC_MODEL=fcstd, FC_OUT=dump), args)
    if code:
        print("BOM skipped: model dump failed (%s): %s" % (code, st.get("message")))
        return dict(ok=False, message=st.get("message"))
    try:
        info = bom_store.write(dump, eff, pj, os.path.join(work, mid))
    except Exception as e:                                           # a BOM problem must not lose a good model
        print("BOM skipped: %s: %s" % (type(e).__name__, e))
        return dict(ok=False, message="%s: %s" % (type(e).__name__, e))
    info["ok"] = True
    return info


def _models3d(work, mid, fcstd, args):
    """3D-viewer model (export_models.py): the full GLB.  A failure of its checks refuses the build; returns (code, info)."""
    rep = os.path.join(work, mid + "_models3d.json")
    code, st = _fc("export_models.py", dict(FC_MODEL=fcstd, FC_OUT_DIR=work, FC_MOTOR_ID=mid, FC_REPORT=rep), args)
    if code:
        print("3D models refused/failed (%s): %s" % (code, st.get("message")))
        return code, None
    r = _read(rep)
    return 0, dict(ok=True, full_glb=r["glb"], triangles=r["triangles"], tessellation_mm=r["tessellation_mm"],
                   worst_mesh_volume_error=r["worst_mesh_volume_error"])


def build(args):
    t0 = time.time()
    if args.profile_file:                                     # explicit profile (e.g. the Fusion reference profile)
        pj = json.load(open(args.profile_file, encoding="utf-8"))
        eff, pull = None, dict(summary={}, fingerprint=None)
        kind = "profile_file"
        out_dir = args.out_dir
        if not out_dir:
            print("--profile-file needs --out-dir")
            return 2
    else:
        if not (args.die and args.config):
            print("--die and --config are required")
            return 2
        dies = args.dies_dir or store.default_dies_dir()
        try:
            eff = store.load_effective(dies, args.die, args.config, args.materials, args.bearings)
        except FileNotFoundError as e:
            print(e)
            return 2
        mp = store.load_mapping()
        pj, pull = store.make_profile(eff, mp)
        kind = "store"
        out_dir = args.out_dir or os.path.join(args.out_root or DEFAULT_OUT_ROOT, args.die, eff["variant"])
    mid = pj["profile"]["motor_id"]
    out_dir = os.path.abspath(out_dir)
    work = out_dir + ".building-%d" % os.getpid()
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    ok = False
    try:
        profile_path = os.path.join(work, "params.json")
        _write(profile_path, pj)
        _write(os.path.join(work, "pull_report.json"), pull)
        if args.dry_run:
            print("dry run: profile %s written to %s" % (mid, work))
            ok = True                                          # keep the work directory
            return 0
        fcstd = os.path.join(work, mid + ".FCStd")
        env = dict(FC_PROFILE=profile_path, FC_OUT=fcstd, FC_REPORT=os.path.join(work, "build_report.json"))
        if args.set:
            env["FC_SET"] = args.set
        code, st = _fc("build_model.py", env, args)
        if code:
            print("build refused/failed (%s): %s" % (code, st.get("message")))
            return code
        if not args.no_gui:
            code, st = _fc("gui_finish.py", dict(FC_MODEL=fcstd, FC_REPORT=os.path.join(work, "gui_report.json")), args, gui=True)
            if code:
                print("GUI finishing failed (%s): %s" % (code, st.get("message")))
                return code
            code, st = _fc("check_gui.py", dict(FC_MODEL=fcstd, FC_REPORT=os.path.join(work, "gui_check.json")), args, gui=True)
            if code:
                print("the model would not open ready to look at (%s): %s" % (code, st.get("message")))
                return code
            for f in os.listdir(work):                      # FreeCAD's automatic backups of the saves
                if f.endswith(".FCBak"):
                    os.remove(os.path.join(work, f))
        step = os.path.join(work, mid + "_active.step")
        if not args.no_step:
            code, st = _fc("export_step.py", dict(FC_MODEL=fcstd, FC_OUT=step, FC_DATUMS=os.path.join(work, mid + "_datums.json"),
                                                  FC_CHECK=os.path.join(work, "step_check.json")), args)
            if code:
                print("STEP export refused/failed (%s): %s" % (code, st.get("message")))
                return code
        models3d = None
        if not args.no_models and not args.no_step:
            code, models3d = _models3d(work, mid, fcstd, args)
            if code:
                return code
        bom_info = None
        if kind == "store" and not args.no_bom and not args.no_step:
            bom_info = _bom(work, mid, fcstd, eff, pj, args)
        build_report = _read(os.path.join(work, "build_report.json"))
        step_check = _read(os.path.join(work, "step_check.json"))
        gui_report = _read(os.path.join(work, "gui_report.json"))
        gui_check = _read(os.path.join(work, "gui_check.json"))
        names = [mid + ".FCStd", mid + "_active.step", mid + "_datums.json", "params.json", "pull_report.json",
                 "build_report.json", "step_check.json", "gui_report.json", "gui_check.json"]
        if models3d:
            names += [models3d["full_glb"], mid + "_models3d.json"]
        if bom_info and bom_info.get("files"):
            names += bom_info["files"] + [mid + "_bom_model_dump.json"]
        m = mf.build_manifest(eff, pj, pull, build_report, step_check, mf.file_rows(work, names),
                              build_report.get("freecad_version") or step_check.get("freecad_version"),
                              round(time.time() - t0, 1), kind=kind)
        m["gui"] = dict(done=bool(gui_report.get("ok")), camera=gui_report.get("camera"), freecad_version=gui_report.get("freecad_version"),
                        opens_ready=bool(gui_check.get("ok")), visible_objects=gui_check.get("visible"))
        m["bom"] = bom_info
        m["models3d"] = models3d
        _write(os.path.join(work, "manifest.json"), m)
        if os.path.exists(out_dir):
            old = out_dir + ".old-%d" % os.getpid()
            os.rename(out_dir, old)
            os.rename(work, out_dir)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.makedirs(os.path.dirname(out_dir), exist_ok=True)
            os.rename(work, out_dir)
        ok = True
        print("built %s -> %s (%.1f s, FreeCAD %s, param_hash %s)" % (mid, out_dir, m["seconds"], m["freecad_version"],
                                                                      (m["param_hash"] or "-")[:12]))
        return 0
    finally:
        if not ok:
            shutil.rmtree(work, ignore_errors=True)


def back(args):
    dies = args.dies_dir or store.default_dies_dir()
    eff = store.load_effective(dies, args.die, args.config, args.materials, args.bearings)
    cells = store.read_fcstd_params(args.fcstd)
    res = store.back(eff, store.load_mapping(), cells, args.out_dir or "sync", propose=args.propose, origin=args.fcstd)
    print(json.dumps({k: res[k] for k in ("die", "variant", "compared", "equal", "differences", "files")}, indent=1))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("build", "back", "-h", "--help"):
        argv.insert(0, "build")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--die", help="die directory name in the store, e.g. 'CIANO14 40 new'")
    common.add_argument("--config", help="config in the die, e.g. L12 (or L12.yaml)")
    common.add_argument("--dies-dir", help="store of dies (default $MOTRES_DIES_DIR, /srv/motres/shared/dies, config/dies)")
    common.add_argument("--materials", help="materials library yaml (default $MOTRES_MATERIALS_LIBRARY or the server's)")
    common.add_argument("--bearings", help="bearing library yaml (default $MOTRES_BEARINGS_LIBRARY, the server's, config/)")
    b = sub.add_parser("build", parents=[common], help="build FCStd + STEP + datums + manifest")
    b.add_argument("--out-root", help="default %s" % DEFAULT_OUT_ROOT)
    b.add_argument("--out-dir", help="exact output directory (overrides <out-root>/<die>/<config>)")
    b.add_argument("--profile-file", help="build from this profile JSON instead of the store (reference builds, tests)")
    b.add_argument("--freecadcmd", help="path of freecadcmd (default $FREECADCMD, PATH, Program Files)")
    b.add_argument("--timeout", type=int, default=1800)
    b.add_argument("--set", help="what-if overrides 'name=expr;name2=expr' (tests; manifest then differs from the store)")
    b.add_argument("--no-gui", action="store_true", help="skip the headless GUI step (no GuiDocument.xml)")
    b.add_argument("--no-step", action="store_true")
    b.add_argument("--no-bom", action="store_true", help="skip the bill of materials (<motor_id>_BOM.csv / .xlsx)")
    b.add_argument("--no-models", action="store_true", help="skip the 3D-viewer model (<motor_id>_full.glb)")
    b.add_argument("--dry-run", action="store_true", help="only write params.json / pull_report.json")
    b.add_argument("--normal-priority", action="store_true", help="do not lower the FreeCAD process priority")
    b.add_argument("-v", "--verbose", action="store_true")
    k = sub.add_parser("back", parents=[common], help="owner's edited Params (FCStd) -> diff / proposal vs the store")
    k.add_argument("--fcstd", required=True)
    k.add_argument("--propose", action="store_true")
    k.add_argument("--out-dir", help="where the review files go (default ./sync)")
    a = ap.parse_args(argv)
    if a.cmd == "back":
        return back(a)
    return build(a)


if __name__ == "__main__":
    sys.exit(main())
