"""Activity digests: one e-mail per working group (a development project or a production) saying what changed.

Nothing here sends by itself.  ``python -m motor_ai_sim.digest run`` (the server's daily timer) only writes DRAFTS; the owner
previews the single text and the recipient list in Admin -> Digests and clicks Send or Discard
(:mod:`motor_ai_sim.digest.approval`).  See ``docs/DIGEST.md``.
"""
