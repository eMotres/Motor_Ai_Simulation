"""Run a FreeCAD console script (freecadcmd) at low priority and read its status file.

freecadcmd can exit 0 after a Python exception, so every script reports through $FC_STATUS
(cad/motor_templates/segmented_flatwire/runstatus.py).  Result code: 0 ok, 3 the script refused (pre-check / health
gate / failed STEP check - nothing saved), 4 it crashed or did not report.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

WIN_CANDIDATES = (r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe", r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe")
_NOISE = ("FreeCAD 1.", "(C) 2001", "FreeCAD is free", "It is licensed", "free and open")


def find_freecadcmd(explicit=None):
    for c in (explicit, os.environ.get("FREECADCMD")):
        if c:
            return c
    for name in ("freecadcmd", "FreeCADCmd"):
        p = shutil.which(name)
        if p:
            return p
    for c in WIN_CANDIDATES:
        if os.path.exists(c):
            return c
    raise FileNotFoundError("freecadcmd not found: give --freecadcmd, set $FREECADCMD or put it on PATH "
                            "(the server image deploy/Dockerfile.cad has it)")


def find_freecad_gui(freecadcmd=None):
    """The GUI binary (FreeCAD / FreeCAD.exe) for the headless view post-step: $FREECAD_GUI, PATH, next to freecadcmd."""
    if os.environ.get("FREECAD_GUI"):
        return os.environ["FREECAD_GUI"]
    cmd = find_freecadcmd(freecadcmd)
    d = os.path.dirname(cmd)
    for n in ("FreeCAD.exe", "FreeCAD", "freecad"):
        if d and os.path.exists(os.path.join(d, n)):
            return os.path.join(d, n)
    p = shutil.which("freecad") or shutil.which("FreeCAD")
    if p:
        return p
    raise FileNotFoundError("FreeCAD GUI binary not found (set $FREECAD_GUI)")


def _low_priority_kwargs():
    if os.name == "nt":
        return dict(creationflags=0x00004000)                      # BELOW_NORMAL_PRIORITY_CLASS
    return dict(preexec_fn=lambda: os.nice(19))


def run_script(script, env, freecadcmd=None, timeout=1800, low_priority=True, gui=False):
    """-> (code, log text, status dict).  `env` entries are added to os.environ (FC_* variables of the script)."""
    cmd = [find_freecad_gui(freecadcmd) if gui else find_freecadcmd(freecadcmd), os.path.abspath(script)]
    full = dict(os.environ)
    if gui:                                                        # headless GUI: no window on any desktop
        full.setdefault("QT_QPA_PLATFORM", "offscreen")
        if os.environ.get("MOTRES_CAD_XVFB") == "1" and shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a"] + cmd
            full["QT_QPA_PLATFORM"] = "xcb"
    full.update({k: str(v) for k, v in env.items()})
    fd, status = tempfile.mkstemp(prefix="fc_status_", suffix=".json")
    os.close(fd)
    os.remove(status)
    full["FC_STATUS"] = status
    home = full.get("HOME") or os.path.expanduser("~")
    if os.name != "nt" and not os.access(home, os.W_OK):           # FreeCAD writes its user config under $HOME
        full["HOME"] = tempfile.mkdtemp(prefix="fc_home_")
    kw = _low_priority_kwargs() if low_priority else {}
    t0 = time.time()
    p = subprocess.Popen(cmd, env=full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        out, _ = p.communicate()
        out += b"\nTIMEOUT - process killed"
    txt = out.decode("utf-8", "replace")
    txt = re.sub(r"Recompute\.+|\(\s*\d+ %\)", "", txt)
    log = "\n".join(l for l in txt.splitlines() if l.strip() and not l.startswith(_NOISE))
    st = {}
    code = p.returncode
    if os.path.exists(status):
        with open(status, encoding="utf-8") as fh:
            st = json.load(fh)
        os.remove(status)
        if code == 0 and not st.get("ok"):
            code = 3
    elif code == 0:
        log += "\nNO STATUS: the script did not report (crash or exception) - treating as failure"
        code = 4
    st["seconds"] = round(time.time() - t0, 1)
    return code, log, st
