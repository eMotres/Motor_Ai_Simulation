"""Rebuild requests for the server CAD model of a die/config (Geometry tab "Full", owner 2026-10-15).

The API image has no FreeCAD: a saved CAD edit (``cad_overrides`` of the configuration) is turned into a new model by the CAD
runtime (``motor_ai_sim.cad.build``, the ``motres-cad`` image).  This module is the hand-over between the two:

* the API writes a request file ``<cad root>/.rebuild/<id>.json`` (state ``pending``) when an edit is saved or an admin asks;
* a worker next to the CAD runtime runs ``python -m motor_ai_sim.cad.rebuild_queue run`` (once, or ``--loop``): it builds the
  pending requests of the shared catalog one at a time at low priority, and writes the outcome (``done`` / ``failed`` with the
  tail of the build output - e.g. the pre-check sentence the template refused with) back into the request;
* the old model stays on disk and in use until the build has passed every check (``build.py`` replaces the directory last), so
  the UI shows it with a "rebuilding" badge instead of blocking.

``status()`` is pure and cheap: it compares the override hash the model was built with (``manifest.cad_overrides_hash``) with the
hash of the saved overrides, so it needs no FreeCAD and no queue entry to know that a model is stale.

Stdlib only (like the rest of this package).  Requests of a user's workspace die (``layer != "shared"``) are listed but not built
by the worker unless it is told where that workspace's dies are (``--include-layer``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

STATES = ("pending", "building", "done", "failed")
QUEUE_DIRNAME = ".rebuild"


def _id(die, config):
    return hashlib.sha1(("%s\x00%s" % (die, config)).encode("utf-8")).hexdigest()[:16]


def queue_dir(root):
    return os.path.join(str(root), QUEUE_DIRNAME)


def _path(root, die, config):
    return os.path.join(queue_dir(root), _id(die, config) + ".json")


def _write(root, rec):
    os.makedirs(queue_dir(root), exist_ok=True)
    p = _path(root, rec["die"], rec["config"])
    tmp = p + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    return rec


def read(root, die, config):
    try:
        with open(_path(root, die, config), encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def request(root, die, config, *, requested_by="", overrides_hash="none", layer="shared", fusion_version=None):
    """Queue (or re-queue) a rebuild.  A request that is already ``pending`` / ``building`` for the same overrides (and the same
    Fusion export version, docs/FUSION_SYNC.md) is kept as it is (one build per state, however many clicks)."""
    cur = read(root, die, config)
    if cur and cur.get("state") in ("pending", "building") and cur.get("overrides_hash") == overrides_hash             and cur.get("fusion_version") == fusion_version:
        return cur
    now = time.time()
    rec = dict(die=die, config=config, layer=layer, state="pending", overrides_hash=overrides_hash,
               requested_by=str(requested_by or "")[:200], requested_at=round(now, 3),
               requested_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), message="", finished_at=None)
    if fusion_version is not None:
        rec["fusion_version"] = int(fusion_version)
    return _write(root, rec)


def pending(root, layer="shared"):
    d = queue_dir(root)
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if rec.get("state") == "pending" and (layer is None or rec.get("layer") == layer):
            out.append(rec)
    return sorted(out, key=lambda r: r.get("requested_at") or 0)


def status(root, die, config, current_hash, *, manifest=None):
    """What the UI shows about the model of this die/config.

    ``state``: ``none`` (no model built), ``current`` (the model was built with the saved CAD edits), ``pending`` (stale; queued or
    waiting for a request), ``building``, ``failed`` (the last build for these edits was refused; ``message`` says why).
    ``stale`` = the model on the server does not yet reflect the saved edits."""
    if manifest is None:
        return dict(state="none", stale=False, requested=False, message="", built_hash=None, current_hash=current_hash)
    built = manifest.get("cad_overrides_hash") or "none"
    stale = built != current_hash
    rec = read(root, die, config)
    same = bool(rec) and rec.get("overrides_hash") == current_hash
    if not stale:
        return dict(state="current", stale=False, requested=False, message="", built_hash=built, current_hash=current_hash)
    if same and rec.get("state") == "building":
        state = "building"
    elif same and rec.get("state") == "failed":
        state = "failed"
    else:
        state = "pending"
    return dict(state=state, stale=True, requested=bool(same and rec.get("state") in ("pending", "building")),
                message=(rec.get("message") if same else "") or "", built_hash=built, current_hash=current_hash,
                requested_at=(rec.get("requested_iso") if same else None), requested_by=(rec.get("requested_by") if same else None))


# ------------------------------------------------------------------------------------------------ the worker
def _tail(text, n=1200):
    text = (text or "").strip()
    return text[-n:]


def run_one(root, rec, dies_dir=None, extra_args=()):
    """Build one request; returns the updated record.  The build runs in a child process at low priority."""
    rec = dict(rec, state="building", started_at=round(time.time(), 3))
    _write(root, rec)
    cmd = [sys.executable, "-m", "motor_ai_sim.cad.build", "build", "--die", rec["die"], "--config", rec["config"],
           "--out-root", str(root)]
    if dies_dir:
        cmd += ["--dies-dir", str(dies_dir)]
    cmd += list(extra_args)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00004000                       # BELOW_NORMAL_PRIORITY_CLASS
    else:
        kw["preexec_fn"] = lambda: os.nice(19)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3600, **kw)
        ok, out = p.returncode == 0, p.stdout
        msg = "built" if ok else "build refused or failed (exit %d): %s" % (p.returncode, _tail(out))
    except subprocess.TimeoutExpired as e:
        ok, msg = False, "build timed out after one hour: %s" % _tail((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else e.stdout)
    except OSError as e:
        ok, msg = False, "could not start the build: %s" % e
    rec = dict(rec, state="done" if ok else "failed", message=msg, finished_at=round(time.time(), 3))
    return _write(root, rec)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the pending CAD rebuild requests of the shared catalog.")
    ap.add_argument("action", choices=("run", "list"))
    ap.add_argument("--root", default=os.environ.get("MOTRES_CAD_ROOT") or "/srv/motres/shared/cad")
    ap.add_argument("--dies-dir", help="store of dies (default as for cad.build)")
    ap.add_argument("--loop", action="store_true", help="keep polling every 30 s instead of one pass")
    ap.add_argument("--include-layer", action="append", default=[], help="also build requests of this layer (e.g. a workspace)")
    a = ap.parse_args(argv)
    layers = ["shared"] + list(a.include_layer)
    if a.action == "list":
        for r in pending(a.root, layer=None):
            print(json.dumps(r, ensure_ascii=False))
        return 0
    while True:
        todo = [r for ly in layers for r in pending(a.root, layer=ly)]
        for r in todo:
            done = run_one(a.root, r, dies_dir=a.dies_dir)
            print("%s/%s -> %s %s" % (r["die"], r["config"], done["state"], done["message"][:200]))
        if not a.loop:
            return 0
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
