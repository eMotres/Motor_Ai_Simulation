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
import contextlib
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


@contextlib.contextmanager
def _file_lock(p):
    """Cross-process advisory lock for queue mutations and the single builder."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a+b") as fh:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            if not fh.read(1):
                fh.seek(0)
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _record_lock(root, die, config):
    """Serialize API requests and worker transitions for one die/config record."""
    return _file_lock(_path(root, die, config) + ".lock")


def _worker_lock(root):
    """Ensure only one build process uses the CAD runtime/output root at once."""
    return _file_lock(os.path.join(queue_dir(root), "worker.lock"))


def read(root, die, config):
    try:
        with open(_path(root, die, config), encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def request(root, die, config, *, requested_by="", overrides_hash="none", build_inputs_hash=None, layer="shared"):
    """Queue one build for the newest input hash, retaining edits made during an active build."""
    target_hash = str(build_inputs_hash or overrides_hash)
    with _record_lock(root, die, config):
        cur = read(root, die, config)
        old_target = (cur or {}).get("build_inputs_hash") or (cur or {}).get("overrides_hash")
        if cur and cur.get("state") in ("pending", "building") and old_target == target_hash:
            # Catalog save hooks may queue before the route adds its actor and
            # layer context. Preserve dedupe while enriching that same request.
            if requested_by and (not cur.get("requested_by") or cur.get("layer") != layer):
                cur = dict(cur, requested_by=str(requested_by)[:200], layer=layer)
                return _write(root, cur)
            return cur
        now = time.time()
        common = dict(die=die, config=config, layer=layer, build_inputs_hash=target_hash,
                      overrides_hash=str(overrides_hash), requested_by=str(requested_by or "")[:200],
                      requested_at=round(now, 3), requested_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                      message="", finished_at=None)
        if cur and cur.get("state") == "building":
            # Keep the active state visible; run_one will turn this into pending
            # after its current snapshot finishes.
            rec = dict(cur, **common, state="building")
            rec["active_build_inputs_hash"] = cur.get("active_build_inputs_hash") or old_target
        else:
            rec = dict(common, state="pending")
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
        except (OSError, ValueError) as exc:
            print("warning: unreadable CAD rebuild request %s: %s" % (os.path.join(d, f), exc), file=sys.stderr)
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
        rec = read(root, die, config)
        queued = bool(rec) and (rec.get("build_inputs_hash") or rec.get("overrides_hash")) == current_hash
        state = rec.get("state") if queued and rec.get("state") in ("pending", "building", "failed") else "none"
        return dict(state=state, stale=state != "none", rebuilding=state == "building",
                    requested=bool(queued and state in ("pending", "building")),
                    message=(rec.get("message") or "") if queued else "", built_hash=None,
                    built_inputs_hash=None, current_hash=current_hash, build_inputs_hash=current_hash)
    built = manifest.get("build_inputs_hash")
    # Missing input hashes were produced by the old CAD-only freshness scheme.
    stale = not built or built != current_hash
    rec = read(root, die, config)
    rec_hash = (rec or {}).get("build_inputs_hash") or (rec or {}).get("overrides_hash")
    same = bool(rec) and rec_hash == current_hash
    if not stale:
        return dict(state="current", stale=False, rebuilding=False, requested=False, message="", built_hash=built,
                    built_inputs_hash=built, current_hash=current_hash, build_inputs_hash=current_hash)
    if same and rec.get("state") == "building":
        state = "building"
    elif same and rec.get("state") == "failed":
        state = "failed"
    else:
        state = "pending"
    return dict(state=state, stale=True, rebuilding=state == "building",
                requested=bool(same and rec.get("state") in ("pending", "building")),
                message=(rec.get("message") if same else "") or "", built_hash=built, current_hash=current_hash,
                built_inputs_hash=built, build_inputs_hash=current_hash,
                requested_at=(rec.get("requested_iso") if same else None), requested_by=(rec.get("requested_by") if same else None))


# ------------------------------------------------------------------------------------------------ the worker
def _tail(text, n=1200):
    text = (text or "").strip()
    return text[-n:]


def run_one(root, rec, dies_dir=None, extra_args=()):
    """Run exactly one CAD request at a time per output root, across workers."""
    with _worker_lock(root):
        return _run_one_locked(root, rec, dies_dir=dies_dir, extra_args=extra_args)


def _run_one_locked(root, rec, dies_dir=None, extra_args=()):
    """Build preview then full; requeue if the requested inputs changed during either build."""
    with _record_lock(root, rec["die"], rec["config"]):
        latest = read(root, rec["die"], rec["config"])
        if not latest or latest.get("state") != "pending":
            return latest or rec
        rec = dict(latest, state="building", started_at=round(time.time(), 3))
        rec["active_build_inputs_hash"] = rec.get("build_inputs_hash") or rec.get("overrides_hash")
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
        preview_cmd = cmd + ["--preview"]
        preview = subprocess.run(preview_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3600, **kw)
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3600, **kw)
        ok, out = p.returncode == 0, p.stdout
        preview_note = ("preview failed (exit %d): %s; " % (preview.returncode, _tail(preview.stdout))
                        if preview.returncode else "")
        msg = preview_note + ("built" if ok else "full build refused or failed (exit %d): %s" % (p.returncode, _tail(out)))
    except subprocess.TimeoutExpired as e:
        ok, msg = False, "build timed out after one hour: %s" % _tail((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else e.stdout)
    except OSError as e:
        ok, msg = False, "could not start the build: %s" % e
    with _record_lock(root, rec["die"], rec["config"]):
        latest = read(root, rec["die"], rec["config"]) or rec
        active_hash = rec.get("active_build_inputs_hash")
        desired_hash = latest.get("build_inputs_hash") or latest.get("overrides_hash")
        if desired_hash != active_hash:
            latest = dict(latest, state="pending", message="inputs changed during build; newest request queued",
                          finished_at=None, started_at=None)
            latest.pop("active_build_inputs_hash", None)
        else:
            latest = dict(rec, state="done" if ok else "failed", message=msg,
                          finished_at=round(time.time(), 3))
            latest.pop("active_build_inputs_hash", None)
        return _write(root, latest)


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
