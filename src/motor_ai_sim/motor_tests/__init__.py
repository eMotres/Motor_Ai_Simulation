"""Motor tests: measured data of one motor configuration, each value compared with the simulation of the same point.

Owner 2026-10-26: «да, делай раздел Tests; нужно сделать отдельные подменю Parameters, Free-run, Loads».

* ``store``         - ``<shared>/motor_tests/<die>/<config>/tests.json`` + ``audit.jsonl`` (next to the production store)
* ``models``        - validation / normalisation of one test record (parameters | free_run | load)
* ``compare``       - the conversions: pure functions, no I/O (resistance, Ld/Lq fit, EMF, no-load loss, load match)
* ``model_source``  - what the configuration's stored results say (duty summaries, magnet cards, bearings)
* ``comparison``    - one record + the model source -> the measured | model | delta table the UI and the report show
* ``access``        - who may view / add / edit (production membership, admin; project view behind a flag)
* ``routes``        - ``/api/motor-tests/{die}/{config}`` and ``/api/projects/{id}/motors/tests``
* ``report``        - the "Tests vs simulation" section of the motor report (docx / pdf)
"""
from __future__ import annotations

KINDS = ("parameters", "free_run", "load")
