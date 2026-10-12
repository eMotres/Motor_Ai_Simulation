"""Result channel between FreeCAD console scripts and tools/fc_run.py.

freecadcmd may return exit code 0 even when a script raised; therefore every tool script calls finish(ok, message)
which writes the JSON file named by $FC_STATUS (set by fc_run.py).  fc_run.py turns it into the process exit code:
0 = ok, 3 = refused / failed check (nothing saved or exported), 4 = script crashed before reporting.
"""
import json
import os
import sys


def finish(ok, message, **extra):
    path = os.environ.get("FC_STATUS")
    rec = dict(ok=bool(ok), message=message, **extra)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1, default=str)
    print(("OK: " if ok else "REFUSED: ") + message)
    sys.stdout.flush()
    return ok
