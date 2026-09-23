"""Node-to-node unilateral contact between the rotor solids — Mechanical v2.

Written 2026-09-05 for the user's requirement, in Fusion-360 terminology:

    "Ещё нужно разобраться с контактами — они у нас все Separated по умолчанию."

v1 welded every part into one conforming body (see ``rotor_stress`` docstring),
so an interface transmitted TENSION.  On the live Ø124 spoke rotor that made the
7.65 kg of magnets hang off the iron through interface tension — the magnet/iron
traction came back at +8 MPa (tensile) already at standstill and the sleeve read
a comfortable 349 MPa at 20 000 rpm.  That is not the machine: the pockets are
wedges that OPEN outward, so nothing but the surface above the magnet can retain
it, and the retaining load has to appear in the sleeve.

WHAT "SEPARATION" MEANS HERE
---------------------------
Fusion's *Separation* contact: the two surfaces may open and slide, but not
penetrate.  Compression is transmitted, tension is not; friction is optional.
Formally, per contact node pair with outward normal ``n`` (pointing from the A
side into the B side):

    g = (u_B - u_A).n + g0 >= 0     (no penetration)
    p >= 0                          (only compression)
    p * g = 0                       (no pressure across an open gap)

which is a linear complementarity problem, solved here by an ACTIVE SET loop:
every pair starts closed (a bilateral normal tie = the v1 bonded behaviour),
the solve reports each pair's constraint force, pairs whose force came back
TENSILE are released, pairs that came back penetrating are re-closed, and the
loop repeats until the set stops changing.  Each iteration is one linear solve,
so the cost is (iterations x one v1 solve) per load case.

The other two types are the degenerate cases of the same machinery:

  * ``bonded``  — normal AND tangential tied, always.  On a conforming mesh this
    reproduces v1 to solver precision: tying the two vertex dofs and the midside
    dof of a matched P2 edge pins the whole quadratic trace, which is exactly
    what sharing the nodes did.  It is the regression guard.
  * ``sliding`` — normal tied (cannot separate), tangential free.  Fusion's
    "no separation".

FRICTION (mu > 0, ``separation`` only) — REGULARISED COULOMB
------------------------------------------------------------
Rewritten 2026-09-07, when the torque load made friction the load path instead
of a footnote (user: "добавь ещё и момент на ротор").  The law is

    F_t  =  min( k_t * |s| , mu * F_n ) * sign(s)

with ``s`` the tangential relative displacement of the pair and ``k_t`` the same
stiff spring the normal direction uses.  It is a REGULARISED Coulomb law: below
the cone the joint sticks elastically with a micro-slip of ``mu * F_n / k_t``
(the tangential twin of the penalty penetration, sub-micron on a real rotor and
visible in each interface's ``slip_max_um``); on the cone it slides at exactly
``mu * F_n``.

It is applied as a SECANT STIFFNESS,  k_t_eff = min(k_t, mu * F_n / |s|),
assembled into the matrix — not as a fixed force on the right-hand side.  That
distinction is the whole reason for the rewrite.  v2 did the textbook return
mapping: drop the tangential tie of a sliding pair and add a force of magnitude
mu*|F_n| opposing the slip direction MEASURED ON THE PREVIOUS ITERATE.  With
mu = 0 (the default until now) none of it ran.  With mu = 0.2 on the live 23 000
rpm rotor it diverged: the centrifugal clamp makes mu*F_n two orders of
magnitude larger than the driving shear, so a pair whose micro-slip changed sign
between iterations flipped an enormous force, and the loop ran to its iteration
cap with 1500 pairs frozen, 2 mm of penetration and 6 mm of "slip".  A secant
stiffness cannot flip a force: it is positive, symmetric and bounded by k_t, so
the saddle system stays definite and the iteration contracts.

FRICTION HAS A HISTORY — THE LOAD PATH (2026-09-09)
---------------------------------------------------
User, reading the hot G2-L40: *"получается, что от момента больше деформации,
чем от вращения?"*  No.  The hot rows with torque on them were not a deformation
at all, they were a LOCKED FRICTION STATE, and they are the reason the friction
above stopped being path-independent.

Coulomb friction is not a function of the current load: a joint that is stuck
carries whatever force the history put into it, and only the CONE bounds it.  On
the hot G2 that indeterminacy is not academic.  The pocket outgrows the magnet
by 10-20 µm (12 against 5 ppm/K), the magnet seats in a wedge that stands 7.5°
from its travel, and mu = 0.2 > tan 7.5° = 0.13 — the wedge SELF-LOCKS.  Every
pair starts closed and STUCK (the warm start above), so with the torque traction
applied in one go the active set opens on a statically admissible state chosen by
nothing but the arithmetic, and the one it picked carried seven times the load:

    rotor 211 MPa p99.5, magnet 153, contact 85 MPa, 30 iterations, no verdict

against 23 MPa for the same rotor spun hot with no torque at all.  Adding a
61 N·m torque to a 500 kW rotor cannot multiply its stress by nine, and the
tell is that the SAME solve without the seating-then-torque ordering — cold,
where the pocket still grips the magnet — comes out at 28 MPa.

So the load is walked, not applied:

  1. the loose parts are SEATED under the centrifugal (and thermal) load alone,
     which is the state the machine is in before the drive is switched on and
     which converges in three iterations;
  2. the torque traction is then ramped in equal increments, and each increment
     starts from the previous one's PHYSICAL state — the closed set, the stick /
     slip state, the tangential secant stiffnesses ``kt_sec`` and the seating
     offsets ``g0`` all carry across.  The last increment is the answer.

Inside one increment nothing changes: the same active-set loop, the same
best-iterate-on-cycle, the same ``residual``.  ``ContactSolution.load_steps``
counts the increments and ``step_residuals`` quotes each one's own residual, so a
step that limit-cycled is visible rather than averaged away.  A solve with no
ramp (every centrifugal-only case, and every case in the frozen Ø200 suite) runs
ONE step and the arithmetic is bit for bit what it was.

Still quasi-static and small-strain, and the path is the load path only — no
rate, no wear, no re-seating of a joint that has already slipped back.  It is
honest for sizing a sleeve and for asking whether a torque can cross a joint —
it is NOT a substitute for a proper frictional contact code.

MESHING: DUPLICATED INTERFACE NODES
-----------------------------------
The parts are meshed once as a conforming body (so the node pairs coincide
exactly, g0 = 0 to machine precision) and then every vertex that touches more
than one part is SPLIT into one copy per part.  Element ids and the per-element
part tags are untouched by the split, so everything downstream of the mesh —
material assignment, stress recovery, the field payload — is unchanged.

WHY THE RIGID-BODY HANDLING GREW A GRAPH
----------------------------------------
v1 removed exactly three rigid-body modes because the welded rotor was one body.
With separation contact a part can end up with EVERY pair released — a magnet in
a wedge pocket with no sleeve above it is the physical case — and it then floats
with three modes of its own.  So the null-space border is built per CONNECTED
COMPONENT of the graph "elements + currently active contact pairs".  A component
that is not the main body is reported by name: its centrifugal load has no load
path, which is the answer "this magnet is not retained by anything", not a
number to read.

Two frictionless bodies tied only in the normal direction along a CIRCULAR
interface (the sleeve on the rotor) can still spin relative to one another at
zero energy — a genuine mechanism, invisible to the component graph because the
graph says they are connected.  A very small tangential spring on closed
frictionless pairs removes it; at 1e-7 of the mean stiffness it transmits
micro-newtons against contact forces of meganewtons, and without it the saddle
system is singular.

SEATING A LOOSE PART (2026-09-09)
--------------------------------
User, on the live G2-L40 at the coupled temperatures (iron 134 °C, magnet
135 °C): *"магнит должен сесть на язычок, как в Fusion"*.

The magnet sits in an iron pocket whose lips overhang its shoulders with ZERO
clearance in the die cross-section, so at 20 °C the separation contact holds it.
Hot, the pocket grows more than the magnet does (12 against 5 ppm/K), and the
solve then finds EVERY pair of that magnet in tension, releases them all, and is
left with the magnet as a connected component of its own — ten microns short of
the lip it is about to rest on.  What the machine does next is travel those ten
microns and land; what the code did next was pin the magnet's rigid modes where
it stood, cycle the active set, freeze it and run away (4e8 mm of
"displacement"), after which the route re-solved the joint BONDED and added
hundreds of MPa of thermal-mismatch stress that no glued joint is there to carry.

So a component that comes loose is no longer pinned: it is SEATED.  It is moved,
as a RIGID BODY, to where its own unilateral contacts bring it to rest, and that
motion is carried as a per-pair gap OFFSET (``g0`` in the complementarity
statement above, zero on a conforming mesh until something moves) plus the same
motion added to the returned displacement field, so the map shows the magnet
where it now is.  The pairs it lands on close and the loop goes on exactly as
before — a landing pair that comes back tensile is released like any other, and
the offset stays, because the part is now where it physically is.

WHERE IT COMES TO REST — rewritten the same day, on the machine (2026-09-09)
---------------------------------------------------------------------------
The first version fell along the net load and stopped at the first pair it
touched.  That works on a rectangular pocket and fails on the real one, twice
over, and both failures were measured on the live G2-L40 at the coupled
temperatures (iron 134 °C, magnet 135 °C, 3 000 rpm, 61 N·m, mu 0.2):

  * WHAT IS BEHIND THE PART MATTERS.  The pocket has grown 75 µm outward around
    a magnet that has not moved, so its floor is 75 µm INSIDE the magnet while
    the wedge in front is 5 µm away.  Stopping at the first touch answers 41 µm
    and leaves the floor buried 34 µm deep — a penetration on a spring of ten
    times the mesh's own stiffness.  ``_seat_line`` therefore solves the 1-D
    unilateral equilibrium along the search direction instead: faces behind push
    the part forward until it is clear of them, faces in front stop it.
  * A RIGID PART CANNOT BE SLID INTO A POCKET THAT HAS EXPANDED.  The floor goes
    out 75 µm, the tab at r 70 goes out 96 µm, and the torque leans the two pole
    tips 14 µm in opposite directions.  One slide matches that at one radius and
    nips everywhere else: the best translation still nipped the wedge by 4.4 µm,
    where a translation PLUS a 0.085° rotation fits to 1.8 nm.  So the placement
    is the full rigid-body equilibrium — ``_seat_placement``, a semi-smooth
    Newton on the same springs the solve is about to assemble — and the magnet
    comes to rest touching on three faces at once, which is what a rigid body on
    unilateral supports does.

WHICH PARTS ARE SEATED
----------------------
Any part with no LOAD PATH, which is not the same question as "is this part a
connected component of its own".  A magnet can be attached by two pairs whose
normals cannot react its load and still be a mechanism; on the G2 that is how
the runaway came back after the magnets had already been seated (2.3e8 mm on
iteration 45), and the component graph called it "attached" throughout.
``_loose_pairs`` asks the stiffness question instead — put the part's own load on
the springs it is currently touching and see how far that moves it — and the
pairs of a part that fails it are cut out of the graph for that iteration, which
pins its rigid modes (an honest, non-singular trial) and hands it to the seating.

Three guards, in the order they matter:

  * a part with no NET load (a thermal eigenstrain alone is self-equilibrated on
    a free body) has nothing to seat under, and keeps the old handling — which is
    what keeps every standstill answer bit for bit what it was;
  * a part with no open pair facing the load is not retained by anything — that
    is the answer, and it stays ``free_parts`` and a refusal upstream.  It is
    also why a frictionless block sliding along a flat face is not called loose:
    there is no landing to be found, and ``TANGENT_REG`` already owns that case;
  * the accumulated motion is capped at ``SEAT_TRAVEL_FRAC`` of the rotor's own
    radius.  A part that has to move five per cent of the rotor to find a
    surface has not been seated, it has escaped, and it is flagged as the
    runaway it is.

Seating is attempted only for a part that was ALREADY floating when the solve
that produced these gaps was made, i.e. one whose rigid modes THIS iteration
pinned.  On the iteration that first releases the pairs, the gaps still come from
a solve in which the stiff contact springs were holding the part nearly tied, so
they measure the spring's stretch and not the clearance; one iteration later the
part is free, its rigid modes are pinned (so its displacement carries no drift to
be removed), and the gaps are the real thing.

It also runs BEFORE the active set is updated from those gaps, and that ordering
is the whole trick.  A pinned magnet is held in the mesh's own place while the
iron grows around it, so the pocket's INNER face penetrates it — and the update,
taken first, closes that face and re-attaches the magnet to the body by the one
surface that cannot possibly retain it, which puts the component back together
and leaves nothing to seat.  Measured on the 4-pole fixture (rotor 150 °C,
magnet 20 °C, 20 000 rpm): all closed -> all tensile -> released -> pinned ->
inner face penetrating -> re-closed -> tensile again, cycling out at iteration 4
with the magnet free and the whole solve refused.  Seated from the same gaps,
before they are graded, the magnet moves outward instead — the lip's pairs land,
the inner face opens by exactly the travel, the side walls (normals square to the
load) do not move — and the solve converges in three iterations.

WHAT SEATING IS NOT: a contact search.  It moves a part onto a pair it ALREADY
has, so the clearance it can cross is one the machine CREATES on a shared
boundary — thermal, centrifugal, a fit that opened.  A clearance that is DRAWN
(the 40 mm's ``magnet_up_gap 0.1``: 0.1 mm of air above the magnet, belonging to
no part's polygon and therefore not meshed) leaves the magnet's outer face and
the pocket's outer wall as two FREE surfaces with no pair between them — the
measurement is in tests/test_mechanical_seating.py, which counts zero
outward-facing facets there against sixteen on the same pocket cut to the magnet.
Nothing can be seated across it, that case stays the refusal it was, and the
route's bonded fallback (a glued magnet, which is what the built machine has)
remains the answer to it.

AND WHAT SEATING DOES NOT FIX: the G2's seated joint does not CONVERGE.  Its
wedge stands 7.5° from the magnet's travel and mu = 0.2, so it self-locks, the
contact patch grows and shrinks between iterations, and the active set
limit-cycles instead of repeating.  Two things make that reportable rather than
dangerous.  The marginal-pair freeze no longer holds a pair open while the parts
OVERLAP (``FREEZE_MARGIN_FRAC``) — freezing 1282 of 2044 pairs with 47 µm of iron
standing inside the magnets is what turned the limit cycle back into a runaway —
and the loop keeps the least-violating iterate it saw and returns that one, with
the violation itself quoted as ``ContactSolution.residual``.  Measured: the same
numbers at 30 iterations and at 120.

A HELD BOUNDARY (2026-09-07)
---------------------------
User: "добавь ещё и момент на ротор, пусть действуют все силы".  A torque is not
self-equilibrated, so the rotor can no longer float: something has to react it.
``HeldBoundary`` is that reaction — a set of rows ``u.d = 0`` (the shaft bore
held TANGENTIALLY, radial free) appended to the same constraint block the
bonded ties live in.  The component that carries a held vertex then gets NO
rigid-body columns: a full circle of tangential rows already removes all three
of its modes, and stacking the border on top would make the constraint set
rank-deficient and the saddle matrix singular.  The multipliers of those rows
are the reaction, and ``sum(mult * arm)`` is the torque that came out of the
bore — the number that must equal the torque that went in.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.sparse as sp

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Part ids
# ---------------------------------------------------------------------------
# Deliberately NOT the DOM_* magnetic tags — this mesh has no air, no stator and
# no per-magnet split, and reusing those numbers would invite somebody to feed a
# mechanical payload to a field viewer that colours by DOM_*.
PART_ROTOR = 0
PART_MAGNET = 1
PART_SLEEVE = 2
PART_SHAFT = 3
PART_NAMES: Dict[int, str] = {PART_ROTOR: "rotor", PART_MAGNET: "magnet",
                              PART_SLEEVE: "sleeve", PART_SHAFT: "shaft"}


# ---------------------------------------------------------------------------
# Contact settings
# ---------------------------------------------------------------------------

CONTACT_TYPES = ("separation", "bonded", "sliding")

#: Which two parts each contact pair joins, A first (A is the side the normal
#: points AWAY from, i.e. the part that would fly off).
PAIR_PARTS: Dict[str, Tuple[int, int]] = {
    "magnet_rotor": (PART_MAGNET, PART_ROTOR),
    "sleeve_rotor": (PART_SLEEVE, PART_ROTOR),
    "sleeve_magnet": (PART_SLEEVE, PART_MAGNET),
    "shaft_rotor": (PART_SHAFT, PART_ROTOR),
}
PAIR_ORDER: Tuple[str, ...] = ("magnet_rotor", "sleeve_rotor", "sleeve_magnet",
                               "shaft_rotor")


@dataclass(frozen=True)
class ContactSpec:
    type: str = "separation"
    mu: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "mu": float(self.mu)}


#: The user's default, 2026-09-05: "они у нас все Separated по умолчанию".
#: shaft <-> rotor core is the exception — it is a press-fit / keyed hub, which
#: is a bonded joint by construction, and modelling it as separation would let
#: the hub rattle inside the bore for no physical reason.
DEFAULT_CONTACTS: Dict[str, ContactSpec] = {
    "magnet_rotor": ContactSpec("separation", 0.0),
    "sleeve_rotor": ContactSpec("separation", 0.0),
    "sleeve_magnet": ContactSpec("separation", 0.0),
    "shaft_rotor": ContactSpec("bonded", 0.0),
}


class ContactConfigError(ValueError):
    """A malformed ``contacts=`` payload.

    Carries the offending field so the route can turn it into a 422 that names
    it — the project's client-facing validation rule: never quietly substitute a
    default for something the engineer meant to set.
    """

    def __init__(self, field_name: str, message: str):
        self.field_name = field_name
        super().__init__(message)


def parse_contacts(raw: Any) -> Dict[str, ContactSpec]:
    """``{pair: {"type": ..., "mu": ...}}`` (dict or JSON string) -> specs.

    Anything not named keeps its default.  Anything named wrongly raises —
    a typo'd pair name silently ignored would size a sleeve against a joint the
    engineer thought they had opened.
    """
    out = dict(DEFAULT_CONTACTS)
    if raw is None or raw == "":
        return out
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise ContactConfigError("contacts", f"contacts is not JSON: {exc}")
    if not isinstance(raw, dict):
        raise ContactConfigError(
            "contacts", "contacts must be an object keyed by contact pair, e.g. "
                        '{"magnet_rotor": {"type": "separation", "mu": 0.2}}')
    for label, val in raw.items():
        if label not in PAIR_PARTS:
            raise ContactConfigError(
                f"contacts.{label}",
                f"unknown contact pair {label!r} — known pairs are "
                f"{', '.join(PAIR_ORDER)}")
        if not isinstance(val, dict):
            raise ContactConfigError(
                f"contacts.{label}",
                f"contacts.{label} must be an object with 'type' and optional 'mu'")
        typ = str(val.get("type") or out[label].type)
        if typ not in CONTACT_TYPES:
            raise ContactConfigError(
                f"contacts.{label}.type",
                f"contact type {typ!r} is not one of {', '.join(CONTACT_TYPES)}")
        mu_raw = val.get("mu", val.get("friction", 0.0))
        try:
            mu = float(mu_raw if mu_raw is not None else 0.0)
        except (TypeError, ValueError):
            raise ContactConfigError(f"contacts.{label}.mu",
                                     f"mu must be a number, got {mu_raw!r}")
        if not np.isfinite(mu) or mu < 0.0 or mu > 2.0:
            raise ContactConfigError(
                f"contacts.{label}.mu",
                f"mu must be between 0 and 2, got {mu} — a Coulomb coefficient "
                "above ~1 already means the surfaces gall rather than slide")
        out[label] = ContactSpec(typ, mu)
    return out


# ---------------------------------------------------------------------------
# Interface discovery (on the CONFORMING mesh, before the split)
# ---------------------------------------------------------------------------

def interface_facets(mesh, part_tri: np.ndarray,
                     a: int, b: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Edges shared by an element of part ``a`` and an element of part ``b``.

    Returns (elem_a, elem_b, normal) with ``normal`` the unit edge normal
    pointing from a into b — the direction a lift-off would open.  Kept as the
    v1 signature because the element-stress traction report still uses it.
    """
    ea, eb, n, _nodes, _len = _interface_edges(mesh.p.T, mesh.t.T, part_tri, a, b)
    return ea, eb, n


def _interface_edges(p: np.ndarray, t: np.ndarray, part_tri: np.ndarray,
                     a: int, b: int):
    """(elem_a, elem_b, normal, edge_nodes, length) for the a|b interface.

    ``p`` is (nv, 2) in metres, ``t`` is (ne, 3).  ``edge_nodes`` are vertex ids
    of the CONFORMING mesh, i.e. before the interface split.
    """
    ne = t.shape[0]
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e_sorted = np.sort(e, axis=1)
    owner = np.tile(np.arange(ne), 3)
    key = e_sorted[:, 0].astype(np.int64) * (p.shape[0] + 1) + e_sorted[:, 1]
    order = np.argsort(key, kind="stable")
    key_s, owner_s, edge_s = key[order], owner[order], e_sorted[order]
    same = key_s[:-1] == key_s[1:]
    i0 = np.nonzero(same)[0]
    if i0.size == 0:
        return (np.zeros(0, int), np.zeros(0, int), np.zeros((0, 2)),
                np.zeros((0, 2), int), np.zeros(0))
    o1, o2 = owner_s[i0], owner_s[i0 + 1]
    pa, pb = part_tri[o1], part_tri[o2]
    sel = ((pa == a) & (pb == b)) | ((pa == b) & (pb == a))
    if not sel.any():
        return (np.zeros(0, int), np.zeros(0, int), np.zeros((0, 2)),
                np.zeros((0, 2), int), np.zeros(0))
    i0 = i0[sel]
    o1, o2 = owner_s[i0], owner_s[i0 + 1]
    swap = part_tri[o1] == b
    ea = np.where(swap, o2, o1)
    eb = np.where(swap, o1, o2)
    ed = edge_s[i0]
    d = p[ed[:, 1]] - p[ed[:, 0]]
    length = np.linalg.norm(d, axis=1)
    n = np.stack([d[:, 1], -d[:, 0]], axis=1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    # Point the normal from a into b, using the two element centroids.
    ca = (p[t[ea, 0]] + p[t[ea, 1]] + p[t[ea, 2]]) / 3.0
    cb = (p[t[eb, 0]] + p[t[eb, 1]] + p[t[eb, 2]]) / 3.0
    flip = np.einsum("ij,ij->i", n, cb - ca) < 0
    n[flip] *= -1.0
    return ea, eb, n, ed, length


# ---------------------------------------------------------------------------
# The split mesh + the pair tables
# ---------------------------------------------------------------------------

@dataclass
class Interface:
    """One contact pair: its facets, its dof pairs and its settings."""
    label: str
    spec: ContactSpec
    part_a: int
    part_b: int
    elem_a: np.ndarray          # (nf,) element ids (UNCHANGED by the split)
    elem_b: np.ndarray
    normal: np.ndarray          # (nf, 2) unit, A -> B
    length: np.ndarray          # (nf,) m
    seg: np.ndarray             # (nf, 2, 2) endpoint coordinates, m
    facet0: int                 # first row of this interface in the facet table
    pair0: int                  # first row of this interface in the pair table
    n_facets: int = 0
    n_pairs: int = 0

    @property
    def unilateral(self) -> bool:
        return self.spec.type == "separation"


@dataclass
class ContactSystem:
    """Everything the contact solve needs that does not depend on the load."""
    mesh: Any                       # skfem MeshTri with the interfaces SPLIT
    part_tri: np.ndarray
    order: int
    interfaces: List[Interface] = field(default_factory=list)

    # --- flat pair table (all interfaces concatenated) ----------------------
    pair_dofs: np.ndarray = field(default_factory=lambda: np.zeros((0, 4), int))
    pair_n: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    pair_iface: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    pair_va: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    pair_vb: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    pair_pos: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    #: force -> facet distribution (n_facets_total, n_pairs), sums to 1 per pair
    W_force: Any = None
    #: gap/open averaging (n_facets_total, n_pairs); each facet's row sums to 1
    W_gap: Any = None
    facet_length: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: STRESS-FREE gap of every pair, m — the ``g0`` of the complementarity
    #: statement in the module docstring.  Zero on a conforming mesh (the two
    #: copies of a split vertex are the same point to machine precision), and it
    #: stays zero here: the only thing that ever moves it is the SEATING step
    #: inside ``solve_contact``, which owns its own copy for the duration of one
    #: solve so the lift-off bisection's extra solves each start from the mesh.
    pair_g0: np.ndarray = field(default_factory=lambda: np.zeros(0))
    n_pairs: int = 0
    n_facets: int = 0

    # --- flags derived from the specs ---------------------------------------
    pair_unilateral: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    pair_tie_tangent: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    pair_mu: np.ndarray = field(default_factory=lambda: np.zeros(0))

    # --- cyclic symmetry (2026-09-09) ---------------------------------------
    #: The cut-face ties of a SECTOR model, or None for the full 360° rotor.
    #: ``solve_contact`` appends their rows to the same bordered system that
    #: already carries the rigid-mode border and the ``HeldBoundary``.
    cyclic: Optional["CyclicTies"] = None
    #: Pairs whose BILATERAL constraint rows must not be assembled.  All False
    #: on a full model, which is why nothing about that solve changes.  On a
    #: sector it marks the interface node pairs sitting ON the trailing cut
    #: face: a ``bonded``/``sliding`` row there is implied EXACTLY by its
    #: leading-face twin plus the two cyclic ties (``n_B = R n_A``, so
    #: ``(u_sB - u_rB).n_B == (u_sA - u_rA).n_A``), and a redundant row makes
    #: the saddle matrix singular.  The constraint is still enforced — through
    #: the twin — so nothing is released by dropping it.  Unilateral pairs are
    #: penalty springs, not rows, and are left alone.
    pair_no_row: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))

    def iface(self, label: str) -> Optional[Interface]:
        for it in self.interfaces:
            if it.label == label:
                return it
        return None


def build_contact_system(mesh, part_tri: np.ndarray,
                         contacts: Dict[str, ContactSpec],
                         order: int = 2) -> ContactSystem:
    """Split the conforming rotor mesh along every part interface and build the
    node-to-node pair tables.

    Every interface is split, whatever its contact type: a ``bonded`` pair then
    reproduces the welded mesh through its constraints (exactly, for P1 and P2),
    which keeps ONE code path and makes the bonded case a real regression guard
    rather than a different solver.
    """
    from skfem import MeshTri

    p = np.ascontiguousarray(mesh.p.T)          # (nv, 2) metres
    t = np.ascontiguousarray(mesh.t.T)          # (ne, 3)
    nv = p.shape[0]

    # ── 1. find the interfaces on the CONFORMING mesh ───────────────────────
    raw: Dict[str, Any] = {}
    for label in PAIR_ORDER:
        a, b = PAIR_PARTS[label]
        ea, eb, n, ed, ln = _interface_edges(p, t, part_tri, a, b)
        raw[label] = (ea, eb, n, ed, ln)

    # ── 2. split every vertex that touches more than one part ───────────────
    n_parts = int(max(PART_NAMES)) + 1
    touch = np.zeros((nv, n_parts), dtype=bool)
    for pid in range(n_parts):
        m = part_tri == pid
        if m.any():
            touch[np.unique(t[m]), pid] = True
    n_touch = touch.sum(axis=1)

    copy_idx = -np.ones((nv, n_parts), dtype=np.int64)
    # A vertex inside one part keeps its own index; a shared vertex keeps it for
    # its lowest part id and gets a fresh copy for each of the others.
    single = n_touch <= 1
    for pid in range(n_parts):
        m = single & touch[:, pid]
        copy_idx[m, pid] = np.nonzero(m)[0]
    shared = np.nonzero(n_touch > 1)[0]
    extra: List[int] = []
    nxt = nv
    for v in shared:
        first = True
        for pid in range(n_parts):
            if not touch[v, pid]:
                continue
            if first:
                copy_idx[v, pid] = v
                first = False
            else:
                copy_idx[v, pid] = nxt
                extra.append(int(v))
                nxt += 1
    p_new = np.vstack([p, p[np.asarray(extra, dtype=np.int64)]]) if extra else p

    t_new = t.copy()
    for pid in range(n_parts):
        m = part_tri == pid
        if m.any():
            t_new[m] = copy_idx[t[m], pid]
    if (t_new < 0).any():
        raise RuntimeError("interface split left an unassigned node — the part "
                           "tags and the element table disagree")

    split_mesh = MeshTri(np.ascontiguousarray(p_new.T),
                         np.ascontiguousarray(t_new.T.astype(np.int64)))

    cs = ContactSystem(mesh=split_mesh, part_tri=part_tri, order=order)

    # ── 3. dof lookup on the split mesh ─────────────────────────────────────
    from skfem import Basis, ElementTriP1, ElementTriP2, ElementVector
    elem = ElementVector(ElementTriP2() if order == 2 else ElementTriP1())
    basis = Basis(split_mesh, elem, intorder=1)
    nodal = basis.nodal_dofs                    # (2, nv_new)
    fdofs = basis.facet_dofs                    # (2, nfacet) for P2, (0, nf) P1
    has_mid = fdofs.shape[0] >= 2
    fac = split_mesh.facets                     # (2, nfacet), rows sorted
    nv_new = p_new.shape[0]
    fkey = fac[0].astype(np.int64) * nv_new + fac[1]
    facet_of = {int(k): i for i, k in enumerate(fkey)}

    def _facet(u: int, v: int) -> int:
        a, b = (u, v) if u < v else (v, u)
        f = facet_of.get(int(a) * nv_new + int(b))
        if f is None:
            raise RuntimeError(f"interface edge ({u},{v}) is not a mesh facet "
                               "after the split")
        return f

    # ── 4. build the pair table, interface by interface ─────────────────────
    dofs: List[np.ndarray] = []
    norms: List[np.ndarray] = []
    ifidx: List[np.ndarray] = []
    va: List[np.ndarray] = []
    vb: List[np.ndarray] = []
    pos: List[np.ndarray] = []
    wf_r: List[np.ndarray] = []
    wf_c: List[np.ndarray] = []
    wf_v: List[np.ndarray] = []
    wg_r: List[np.ndarray] = []
    wg_c: List[np.ndarray] = []
    wg_v: List[np.ndarray] = []
    flen: List[np.ndarray] = []

    n_pair = 0
    n_fac = 0
    for k, label in enumerate(PAIR_ORDER):
        ea, eb, n, ed, ln = raw[label]
        a, b = PAIR_PARTS[label]
        spec = contacts.get(label, DEFAULT_CONTACTS[label])
        it = Interface(label=label, spec=spec, part_a=a, part_b=b,
                       elem_a=ea, elem_b=eb, normal=n, length=ln,
                       seg=(p[ed] if ed.size else np.zeros((0, 2, 2))),
                       facet0=n_fac, pair0=n_pair,
                       n_facets=int(ed.shape[0]))
        cs.interfaces.append(it)
        if ed.shape[0] == 0:
            continue
        nf = ed.shape[0]
        f_ids = n_fac + np.arange(nf)
        flen.append(ln)

        # -- vertex pairs: one per distinct interface vertex ------------------
        verts, inv = np.unique(ed.reshape(-1), return_inverse=True)
        inv = inv.reshape(nf, 2)
        nvp = verts.size
        # length-weighted average of the adjacent facet normals
        acc = np.zeros((nvp, 2))
        wsum = np.zeros(nvp)
        for c in range(2):
            np.add.at(acc, inv[:, c], n * (0.5 * ln)[:, None])
            np.add.at(wsum, inv[:, c], 0.5 * ln)
        nn = np.linalg.norm(acc, axis=1)
        # A vertex whose two facets face nearly opposite ways (a hairline crack
        # tip) has no meaningful average — fall back to the longer facet's
        # normal instead of normalising noise.
        weak = nn < 0.2 * np.maximum(wsum, 1e-30)
        if weak.any():
            first_facet = np.zeros(nvp, dtype=np.int64)
            for c in range(2):
                first_facet[inv[:, c]] = np.arange(nf)
            acc[weak] = n[first_facet[weak]]
            nn[weak] = 1.0
        acc /= np.maximum(nn, 1e-30)[:, None]

        da = nodal[:, copy_idx[verts, a]]       # (2, nvp)
        db = nodal[:, copy_idx[verts, b]]
        dofs.append(np.stack([da[0], da[1], db[0], db[1]], axis=1))
        norms.append(acc)
        ifidx.append(np.full(nvp, k))
        va.append(copy_idx[verts, a])
        vb.append(copy_idx[verts, b])
        pos.append(p[verts])
        vp_id = n_pair + np.arange(nvp)
        # A vertex's force belongs to its adjacent facets in proportion to
        # their length; its gap/open share of a facet is the 1/6 end weight of
        # the quadratic (1/2 for P1, where a facet has only its two ends).
        end_w = (1.0 / 6.0) if has_mid else 0.5
        for c in range(2):
            wf_r.append(f_ids)
            wf_c.append(vp_id[inv[:, c]])
            wf_v.append(0.5 * ln / np.maximum(wsum[inv[:, c]], 1e-30))
            wg_r.append(f_ids)
            wg_c.append(vp_id[inv[:, c]])
            wg_v.append(np.full(nf, end_w))
        n_pair += nvp

        # -- midside pairs: one per facet (P2 only) ---------------------------
        if has_mid:
            fa = np.array([_facet(copy_idx[e0, a], copy_idx[e1, a])
                           for e0, e1 in ed], dtype=np.int64)
            fb = np.array([_facet(copy_idx[e0, b], copy_idx[e1, b])
                           for e0, e1 in ed], dtype=np.int64)
            ma, mb = fdofs[:, fa], fdofs[:, fb]
            dofs.append(np.stack([ma[0], ma[1], mb[0], mb[1]], axis=1))
            norms.append(n.copy())
            ifidx.append(np.full(nf, k))
            va.append(copy_idx[ed[:, 0], a])
            vb.append(copy_idx[ed[:, 0], b])
            pos.append(0.5 * (p[ed[:, 0]] + p[ed[:, 1]]))
            mp_id = n_pair + np.arange(nf)
            wf_r.append(f_ids); wf_c.append(mp_id); wf_v.append(np.ones(nf))
            wg_r.append(f_ids); wg_c.append(mp_id); wg_v.append(np.full(nf, 2.0 / 3.0))
            n_pair += nf

        it.n_pairs = n_pair - it.pair0
        n_fac += nf

    cs.n_pairs = n_pair
    cs.n_facets = n_fac
    if n_pair == 0:
        raise ValueError("no contact interfaces found between the rotor parts — "
                         "the mesh has nothing to hold together")
    cs.pair_dofs = np.vstack(dofs)
    cs.pair_n = np.vstack(norms)
    cs.pair_iface = np.concatenate(ifidx)
    cs.pair_va = np.concatenate(va)
    cs.pair_vb = np.concatenate(vb)
    cs.pair_pos = np.vstack(pos)
    cs.pair_g0 = np.zeros(n_pair)
    cs.facet_length = np.concatenate(flen) if flen else np.zeros(0)
    cs.W_force = sp.csr_matrix(
        (np.concatenate(wf_v), (np.concatenate(wf_r), np.concatenate(wf_c))),
        shape=(n_fac, n_pair))
    cs.W_gap = sp.csr_matrix(
        (np.concatenate(wg_v), (np.concatenate(wg_r), np.concatenate(wg_c))),
        shape=(n_fac, n_pair))

    typ = np.array([cs.interfaces[i].spec.type for i in cs.pair_iface])
    cs.pair_unilateral = typ == "separation"
    cs.pair_tie_tangent = typ == "bonded"
    cs.pair_mu = np.array([cs.interfaces[i].spec.mu for i in cs.pair_iface])
    # Nothing is dropped on a full model — see the field's docstring; a sector
    # solve fills this in afterwards, from the geometry of its cut faces.
    cs.pair_no_row = np.zeros(n_pair, dtype=bool)
    return cs


# ---------------------------------------------------------------------------
# The active-set solve
# ---------------------------------------------------------------------------

#: Per-pair tangential state, reported as ``slip_state``.
ST_OPEN = 0
ST_SLIP = 1
ST_STICK = 2


@dataclass
class HeldBoundary:
    """Rows ``u . d = 0`` on a set of dofs — the shaft bore held tangentially.

    Added 2026-09-07 with the torque load (user: "добавь ещё и момент на ротор").
    One row per held node: ``dofs`` are its (x, y) dof ids on the SPLIT mesh,
    ``dirs`` the unit direction that is held (the hoop direction, so the bore is
    free to breathe radially) and ``arm`` its radius, which turns the row's
    multiplier into a moment about z.  ``vertices`` are the mesh vertices the
    rows touch: the component that owns them loses its rigid-body border,
    because these rows already remove its modes.
    """
    dofs: np.ndarray                # (m, 2) int
    dirs: np.ndarray                # (m, 2) unit
    arm: np.ndarray                 # (m,) radius, m
    vertices: np.ndarray            # (k,) split-mesh vertex ids
    label: str = "shaft bore"

    @property
    def n_rows(self) -> int:
        return int(self.dofs.shape[0])


@dataclass
class CyclicTies:
    """Rows ``u_B = R(theta) . u_A`` on matching nodes of two cut faces.

    Added 2026-09-09 for the user's request: *"нагрузка на все зубы должна быть
    одинакова … так используй периодичность, как я во Fusion"* — in Fusion 360
    he solves ONE pole sector of the rotor with cyclic-symmetry boundary
    conditions.  This is the same statement: the model is a wedge of
    ``2*pi/n_sectors``, and every displacement on the trailing cut face (B) is
    the leading face's (A) turned by the sector angle.  Every pole then carries
    an identical load BY CONSTRUCTION rather than by luck of the mesh.

    ``dofs_a`` / ``dofs_b`` are the (x, y) dof ids of matched points on the two
    faces of the SPLIT mesh — nodal dofs at matched vertices and, for P2, the
    facet (mid-edge) dofs of matched edges, because a quadratic trace that is
    only tied at its ends is not tied.  The pairing is geometric and exact:
    ``|R x_A - x_B| < 1e-9`` m is checked when the ties are built, and refused
    if it fails (see ``symmetry.build_cyclic_ties``).

    ``vertices`` are the split-mesh vertices the rows touch.  A connected
    component that owns any of them keeps only ONE rigid-body column — the
    rotation about the axis.  A translation is not periodic for ``n >= 2``
    (``u_B - R u_A = (I - R) c != 0`` for a constant ``c``), so the cyclic rows
    have already removed the two translations, and bordering them again would
    make the constraint set rank-deficient and the saddle matrix singular.
    """
    dofs_a: np.ndarray              # (m, 2) int — leading face
    dofs_b: np.ndarray              # (m, 2) int — trailing face
    R: np.ndarray                   # (2, 2) rotation A -> B
    vertices: np.ndarray            # (k,) split-mesh vertex ids on either face
    n_sectors: int = 1
    angle_rad: float = 0.0
    label: str = "cyclic symmetry"
    #: worst ``|R x_A - x_B|`` over the matched points, m — how well the mesh
    #: actually came out periodic.  Measured on the G2-L40: 4.8e-13 m, against
    #: the 1e-9 m ``symmetry.MATCH_TOL_M`` refuses above.
    match_error_m: float = 0.0

    @property
    def n_rows(self) -> int:
        return 2 * int(self.dofs_a.shape[0])


@dataclass
class ContactSolution:
    u: np.ndarray                   # (ndof,)
    lam_n: np.ndarray               # (n_pairs,) constraint multiplier, N/m
    force_n: np.ndarray             # (n_pairs,) = -lam_n, >0 in COMPRESSION
    force_t: np.ndarray             # (n_pairs,) tangential force, N/m
    gap: np.ndarray                 # (n_pairs,) m, >0 = open
    slip: np.ndarray                # (n_pairs,) m, tangential relative motion
    closed: np.ndarray              # (n_pairs,) bool
    stick: np.ndarray               # (n_pairs,) bool
    iterations: int
    converged: bool
    n_components: int
    free_parts: List[str]           # parts that ended up with no load path
    rigid_residual: float
    #: deepest penetration of a unilateral pair, m.  The price of the compliant
    #: (spring) contact — quote it, never hide it.
    max_penetration: float = 0.0
    #: pairs that flip-flopped and were frozen open to end the loop
    n_frozen: int = 0
    #: How far this answer is from satisfying the complementarity conditions, m:
    #: the widest gap on which the assembled active set and the solved gaps
    #: disagree (2026-09-09).  Zero on a converged solve, and the number that
    #: chose between the reported iterate and the thirty others on one that did
    #: not converge — quote it rather than only ``converged``.
    residual: float = 0.0
    #: (n_pairs,) ST_OPEN / ST_SLIP / ST_STICK — the tangential state each pair
    #: ended in.  A frictionless separation pair counts as SLIP: its tangential
    #: spring is 1e-6 of the contact stiffness, i.e. it transmits nothing.
    slip_state: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int8))
    #: ── SEATING (2026-09-09) ────────────────────────────────────────────────
    #: One entry per component that came loose and was travelled onto a surface
    #: (see the module docstring).  Empty on every solve where nothing floats,
    #: which is every solve this module used to make.
    #:   part            — the part most of the component's elements belong to
    #:   component_id    — the component's lowest split-mesh vertex, a stable id
    #:   travel_m        — the accumulated rigid distance it moved, FROM THE MESH
    #:   travel_rel_m    — …and how far that is IN THE POCKET (see below)
    #:   direction       — (dx, dy) unit, the net applied load's direction
    #:   landed_on       — label of the contact pair it came to rest on
    #:   n_pairs_closed  — how many pairs of that joint the landing shut
    #:
    #: ``travel_m`` is measured from the REFERENCE MESH, so on a hot rotor most
    #: of it is the magnet riding outward with the iron that surrounds it, which
    #: is no motion at all in the machine: the G2-L40 at the coupled temperatures
    #: reads 105 µm of which ~90 is the pocket's own growth.  ``travel_rel_m`` is
    #: that same travel MINUS the mean displacement of the pocket nodes the part
    #: landed on (the landing pairs' opposite side) — how far the magnet moved in
    #: its pocket, which is the CTE mismatch and nothing else.  Quote the
    #: relative one; keep the absolute beside it (2026-09-09).
    seated: List[Dict[str, Any]] = field(default_factory=list)
    #: Parts whose seating travel hit ``SEAT_TRAVEL_FRAC`` of the rotor radius.
    #: A part that has to move that far to find a surface is not seated, it has
    #: escaped — reported so ``rotor_stress`` refuses the solve by name.
    seating_capped: List[str] = field(default_factory=list)
    #: The RIGID part of ``u``: the seating translation of each component, zero
    #: everywhere else.  ``u`` already contains it (the map must show the magnet
    #: where it now is); this is kept separately so a report that must NOT be
    #: moved by another part's travel — the rotor's OD growth — can take it out.
    u_seat: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: Final per-pair stress-free gap, m: ``cs.pair_g0`` plus whatever the
    #: seating moved.  ``gap`` above already includes it.
    pair_g0: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: multipliers of the CYCLIC tie rows, N/m of stack (None when the model is
    #: the full 360°).  Their sum is the force the neighbouring sector hands
    #: across the cut — zero for a self-equilibrated body, and the honest check
    #: that the periodicity was not doing work it should not (2026-09-09).
    cyclic_mult: Optional[np.ndarray] = None
    #: ── THE LOAD PATH (2026-09-09) ─────────────────────────────────────────
    #: How many load increments this answer was walked in: 1 with no ramp (every
    #: centrifugal-only solve, and the frozen Ø200 suite), 1 + the ramp's steps
    #: when a torque was ramped on top of a seated rotor.  See the module
    #: docstring: with a self-locking wedge the friction state is a function of
    #: the history and not of the load, so the number of steps is part of the
    #: answer and is reported beside it.
    load_steps: int = 1
    #: …and each step's own ``residual`` (m), in order.  A step that limit-cycled
    #: is visible here even when the last one settled.
    step_residuals: List[float] = field(default_factory=list)
    #: multipliers of the ``HeldBoundary`` rows, N/m of stack (None if no hold)
    hold_mult: Optional[np.ndarray] = None
    #: moment the held boundary fed back, N·m per metre of stack.  Equal and
    #: opposite to whatever moment the load applied — that is the check.
    hold_torque_n_m_per_m: float = 0.0


def _pair_rows(cs: ContactSystem, sel: np.ndarray, tangential: bool):
    """Constraint rows  (u_B - u_A).d = 0  for the selected pairs.

    ``d`` is the normal for ``tangential=False`` and the in-plane tangent
    otherwise.  Returns (rows, cols, vals, pair_index_per_row).
    """
    idx = np.nonzero(sel)[0]
    if idx.size == 0:
        return (np.zeros(0, int), np.zeros(0, int), np.zeros(0),
                np.zeros(0, int))
    n = cs.pair_n[idx]
    d = np.stack([-n[:, 1], n[:, 0]], axis=1) if tangential else n
    dof = cs.pair_dofs[idx]                      # ax, ay, bx, by
    r = np.repeat(np.arange(idx.size), 4)
    c = dof.reshape(-1)
    v = np.stack([-d[:, 0], -d[:, 1], d[:, 0], d[:, 1]], axis=1).reshape(-1)
    return r, c, v, idx


def _components(cs: ContactSystem, linked: np.ndarray, nv: int) -> np.ndarray:
    """Connected components of "elements + currently linked contact pairs"."""
    from scipy.sparse.csgraph import connected_components

    t = cs.mesh.t.T
    r = np.concatenate([t[:, 0], t[:, 1], t[:, 2], cs.pair_va[linked]])
    c = np.concatenate([t[:, 1], t[:, 2], t[:, 0], cs.pair_vb[linked]])
    g = sp.coo_matrix((np.ones(r.size), (r, c)), shape=(nv, nv))
    _n, lab = connected_components(g, directed=False)
    return lab


def _part_bodies(cs: ContactSystem) -> np.ndarray:
    """One label per PHYSICAL piece of metal, ignoring the contacts entirely.

    ``_components`` answers "what is attached to what right now", which changes
    every iteration.  This answers "which magnet is this node part of", which
    never does — the interface split gave every part its own copy of every shared
    vertex, so the element graph alone already separates the twenty-eight magnets
    from the rotor and from each other.  Added 2026-09-09 for the looseness test
    in ``solve_contact``, which has to ask its question about a PART and not
    about whatever the active set has currently glued it to.
    """
    from scipy.sparse.csgraph import connected_components

    t = cs.mesh.t.T
    nv = cs.mesh.p.shape[1]
    r = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    c = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    g = sp.coo_matrix((np.ones(r.size), (r, c)), shape=(nv, nv))
    _n, lab = connected_components(g, directed=False)
    return lab


def _component_part(cs: ContactSystem, lab: np.ndarray, elem_v0: np.ndarray,
                    comp: int) -> str:
    """Name of the part most of a component's elements belong to.

    A loose component is one magnet, not "the magnets", so the report needs a
    part name and the component id beside it.  Taken from the elements and not
    from the vertices because ``part_tri`` is per element (2026-09-09).
    """
    m = lab[elem_v0] == comp
    if not m.any():
        return "?"
    pid = int(np.bincount(np.asarray(cs.part_tri)[m].astype(np.int64)).argmax())
    return PART_NAMES.get(pid, str(pid))


def _dof_components(basis, lab_vertex: np.ndarray, mesh) -> np.ndarray:
    """The component label of every DOF, from the label of every vertex.

    Both nodal dofs of a vertex inherit its label; a P2 midside dof inherits the
    label of its facet's first endpoint, which is the same component as its
    second (an element's three vertices are always one component — element edges
    are in the graph).  Pulled out of ``_rigid_basis`` on 2026-09-09 because the
    seating step needs exactly the same mapping to sum a component's load and to
    translate it.
    """
    ndof = basis.N
    nodal = basis.nodal_dofs
    fdofs = basis.facet_dofs
    lab_dof = np.zeros(ndof, dtype=np.int64)
    lab_dof[nodal[0]] = lab_vertex
    lab_dof[nodal[1]] = lab_vertex
    if fdofs.shape[0] >= 2:
        lf = lab_vertex[mesh.facets[0]]
        lab_dof[fdofs[0]] = lf
        lab_dof[fdofs[1]] = lf
    return lab_dof


def _rigid_basis(basis, lab_vertex: np.ndarray, mesh,
                 held_vertices: Optional[np.ndarray] = None,
                 cyclic_vertices: Optional[np.ndarray] = None
                 ) -> Tuple[np.ndarray, int]:
    """Orthonormal basis of the rigid-body modes, THREE PER COMPONENT.

    v1 removed exactly three modes because the welded rotor was one body; with
    separation contact a released part becomes its own body and carries three
    more.  Missing them was the bug that made v1's first mesh return
    displacements in kilometres while every stress still looked healthy.

    ``held_vertices`` (2026-09-07) names the vertices a ``HeldBoundary``
    constrains.  The component that owns any of them gets NO columns: a full
    circle of tangential rows on a bore already removes its translations as well
    as its rotation (``-a sin(t) + b cos(t) = 0`` for every t forces a = b = 0),
    so keeping the border too would make the constraint set rank-deficient and
    the saddle matrix singular.

    ``cyclic_vertices`` (2026-09-09) names the vertices a ``CyclicTies`` ties.
    The component that owns any of them gets ONE column — the ROTATION about
    the axis — instead of three.  A translation is not a periodic field: the
    tie reads ``u_B - R u_A = (I - R) c`` for a constant ``c``, which is zero
    only for ``c = 0`` when ``R != I``.  So the ties have already removed the
    two translations, and bordering them a second time is a rank-deficient
    constraint set (a singular saddle matrix).  The rotation, on the other
    hand, IS periodic — ``u(x) = omega * z_hat x x`` gives ``u(Rx) = R u(x)``
    exactly — so it survives the ties and still has to be pinned.
    """
    ndof = basis.N
    lab_dof = _dof_components(basis, lab_vertex, mesh)
    comps = np.unique(lab_dof)
    if held_vertices is not None and held_vertices.size:
        held_comps = np.unique(lab_vertex[held_vertices])
        comps = np.array([c for c in comps if c not in set(held_comps.tolist())],
                         dtype=comps.dtype)
    if comps.size == 0:
        return np.zeros((ndof, 0)), 0
    cyc: set = set()
    if cyclic_vertices is not None and np.size(cyclic_vertices):
        cyc = {int(c) for c in np.unique(lab_vertex[cyclic_vertices])}
    ix = np.arange(0, ndof, 2)                   # ElementVector interleaves x, y
    iy = ix + 1
    px, py = basis.doflocs[0], basis.doflocs[1]
    # One column block per component: 3 (free body) or 1 (tied by periodicity).
    ncol = [1 if int(cc) in cyc else 3 for cc in comps]
    off = np.concatenate([[0], np.cumsum(ncol)]).astype(int)
    R = np.zeros((ndof, int(off[-1])))
    for j, cc in enumerate(comps):
        mx = lab_dof[ix] == cc
        my = lab_dof[iy] == cc
        j0 = off[j]
        if ncol[j] == 3:
            R[ix[mx], j0 + 0] = 1.0
            R[iy[my], j0 + 1] = 1.0
            j0 += 2
        R[ix[mx], j0] = -py[ix[mx]]
        R[iy[my], j0] = px[iy[my]]
    R, _ = np.linalg.qr(R)
    return R, comps.size


def _solver():
    """PARDISO if the project's pypardiso is importable, SuperLU otherwise.

    Same guarded pattern the 3-D solver uses: MKL is much faster on these saddle
    systems but returns NaN silently from a damaged factorization, so the result
    is checked before it is believed.
    """
    try:
        from pypardiso import spsolve as _ps
        from ..pardiso_lifetime import global_pardiso_session

        def _solve(A, b):
            Ac = A.tocsr()
            bb = np.asarray(b, dtype=float)
            with global_pardiso_session():
                x = np.asarray(_ps(Ac, bb))
                if not np.isfinite(x).all():
                    try:
                        from pypardiso.scipy_aliases import pypardiso_solver as _pp
                        _pp.remove_stored_factorization()
                    except Exception:  # noqa: BLE001
                        pass
                    x = np.asarray(_ps(Ac, bb))
            return x
        return "pypardiso", _solve
    except Exception:  # noqa: BLE001
        import scipy.sparse.linalg as spla

        def _solve(A, b):
            return np.asarray(spla.spsolve(A.tocsc(), np.asarray(b, dtype=float)))
        return "superlu", _solve


#: Normal contact stiffness of a ``separation`` pair, as a multiple of the mean
#: diagonal stiffness of K.  See ``solve_contact`` for why the unilateral
#: constraint is a stiff SPRING and not a Lagrange tie.  At 10x the interface
#: penetrates by ~0.2 % of the part's own elastic deformation (reported as
#: ``max_penetration_um`` so the number is never hidden).
CONTACT_STIFFNESS = 10.0

#: How many times a unilateral pair may change state before it is a candidate
#: for being declared marginal and frozen OPEN.  Named on 2026-09-09 (it was a
#: literal 3) because the G2's wedge made it matter: see ``solve_contact``.
FREEZE_FLIPS = 3

#: …and how wide the gap it flips across may be for that to be honest, as a
#: fraction of the rotor's outer radius (73 nm on the G2, 50 nm on a Ø100).  It
#: is the scale of the penalty method's own penetration — the cold G2 reports
#: 0.06 µm as the price of the contact springs — and four orders below the 47 µm
#: the hot wedge was flipping across when the old, unconditional freeze put a
#: third of the joint's iron inside the magnets.
FREEZE_MARGIN_FRAC = 1e-6

#: How many iterations a load step may run WITHOUT improving on the best
#: iterate it has already found before it is called finished (2026-09-09).
#:
#: The active set is not always a contraction.  On a self-locking wedge
#: (mu 0.2 > tan 7.5° on the G2's pocket) the frictional contact problem has more
#: than one statically admissible answer, and the iteration, left alone, walks
#: away from the one the load path continued into and towards a locked branch —
#: measured on the hot G2, the violation went 0.03 -> 8.5 -> 25 -> 15 -> 35 -> 67 µm
#: over six iterations while the closed set grew from 591 pairs to 826.  The
#: best iterate is kept and returned either way (see ``ContactSolution.residual``),
#: so those twenty-five further solves changed the ANSWER not at all and cost
#: most of the run time; with the load path they are also the wrong place to
#: spend it, because a step whose violation will not come down is a step that is
#: too big, and the answer to that is a smaller step and not a longer search.
SET_STALL = 4

#: How much the largest tangential force on a frictional joint may still be
#: moving, as a fraction of the Coulomb cone that bounds it, for the tangential
#: state to count as settled (2026-09-09).  See ``solve_contact``: the stick/slip
#: FLAG is not the state — the force is — and a pair sitting exactly on the cone
#: transmits mu*F_n under either label.  A tenth of a per cent of the cone is
#: three orders below the accuracy of any friction coefficient an engineer will
#: ever type into the panel.
FT_SET_TOL = 1e-3

#: Tangential spring on closed frictionless pairs, as a fraction of the normal
#: contact stiffness.  See the module docstring: it removes the relative-spin
#: mechanism of two bodies tied only in the normal direction.  At 1e-6 of the
#: normal stiffness it transmits micronewtons where the contact carries
#: meganewtons, i.e. an effective friction coefficient of order 1e-6.
TANGENT_REG = 1e-6

#: SEATING (2026-09-09) — how squarely an open pair must face the load before it
#: counts as somewhere the loose part could land.  ``n·d`` is the cosine between
#: the direction the part must move to shut that pair and the direction its net
#: load points, so 0.05 excludes the pocket SIDE WALLS (whose normals are
#: circumferential and whose closing distance would come out as gap/0 = infinity
#: on a radial load) while keeping any face with a real component of retention.
SEAT_MIN_COS = 0.05

#: …and how far it may travel in total, as a fraction of the rotor's own outer
#: radius.  A clearance a machine opens by itself is microns to a tenth of a
#: millimetre — the G2's lips at the coupled temperatures are 10-15 µm, the
#: 4-pole fixture at ΔT 130 K is 79 µm — so five per cent of the radius (2.5 mm
#: on a Ø100 rotor) is two orders of magnitude of headroom.
#: Past it the part has not been seated, it has escaped, and it is flagged as
#: exactly the runaway it is instead of being quietly walked across the rotor.
SEAT_TRAVEL_FRAC = 0.05

#: A component whose net load is below this fraction of the sum of the absolute
#: load on its own dofs has nothing to seat under.  The case that matters is a
#: thermal eigenstrain, which is self-equilibrated on a free body: it comes out
#: at ~1e-15 here, against ~0.5 for a centrifugal load.  This is what keeps every
#: standstill answer in the suite exactly the answer it was.
SEAT_LOAD_REL = 1e-9

#: …and, for a part with no net load at all, how much its overlaps must CANCEL
#: before it counts as trapped rather than pushed (2026-09-09).  ``|sum of the
#: overlap vectors| / sum of the overlaps``: 1.0 is a part pressed on one face
#: only, which rides out on that face's spring and needs no placement; 0 is a
#: part nipped between two faces that oppose each other exactly, which cannot
#: ride anywhere.  Half is the natural line, and both cases sit far from it —
#: the 4-pole fixture's rectangular pocket pushes its magnet from the floor
#: alone, the G2's wedge closes on its magnet from both walls at once.
SEAT_TRAP_FRAC = 0.5

#: How many times one component may be seated in a single solve.  Landing can
#: legitimately happen twice (a part that lands, tips, and settles on a second
#: face), but a part that keeps needing to be moved is oscillating, and the
#: travel cap should not be the only thing that stops it.
SEAT_MAX_STEPS = 8


#: Newton steps of the rigid-body placement below.  The problem is convex,
#: piecewise quadratic and three-dimensional, and every step is exact along its
#: own line, so it lands in a handful; the rest is headroom.
SEAT_NEWTON_STEPS = 40

#: Tikhonov term of that Newton, as a fraction of one contact spring.  It is
#: what lets the step leave the subspace the already-touching springs restrain —
#: see ``_seat_placement``.  Small enough that a fully supported part is solved
#: by the springs and not by the regularisation.
SEAT_REG = 1e-6

#: …and when its gradient (an out-of-balance force) is a small enough fraction
#: of the applied load to call the part placed.
SEAT_GRAD_TOL = 1e-6

#: How far a part's own load may move it, as a rigid body on the contacts it is
#: currently touching, before the part counts as LOOSE — as a fraction of the
#: rotor's outer radius.  See ``_loose_pairs``: a properly supported part comes
#: out at nanometres and one on a mechanism at millimetres, so 1e-3 of the radius
#: (73 µm on the G2) sits five orders of magnitude clear of both.
SEAT_LOOSE_FRAC = 1e-3


def _body_rows(cs: ContactSystem, sel: np.ndarray, mine_is_a: np.ndarray,
               cen: np.ndarray, arm: float,
               tangential: bool = False) -> np.ndarray:
    """d(gap)/da — or d(slip)/da — of the selected pairs under a rigid motion.

    ``a = (t_x, t_y, arm·theta)`` about ``cen``; the rotation is scaled by
    ``arm`` so all three unknowns are lengths (see ``_seat_placement``).  Used by
    both the placement and the looseness test, which must agree about what a
    rigid motion does to a gap or they would answer different questions.
    """
    v = np.where(mine_is_a[:, None], cs.pair_n[sel], -cs.pair_n[sel])
    if tangential:
        v = np.stack([-v[:, 1], v[:, 0]], axis=1)
    rr = cs.pair_pos[sel] - cen
    return np.stack([
        -v[:, 0], -v[:, 1],
        -(v[:, 1] * rr[:, 0] - v[:, 0] * rr[:, 1]) / max(arm, 1e-12),
    ], axis=1)


def _loose_pairs(cs: ContactSystem, body: np.ndarray, body_dof: np.ndarray,
                 f: np.ndarray, closed: np.ndarray, uni: np.ndarray,
                 skip: set, ix: np.ndarray, iy: np.ndarray,
                 px: np.ndarray, py: np.ndarray, kc: float,
                 r_out: float, kt_ratio: np.ndarray) -> np.ndarray:
    """Pairs of every part that CANNOT HOLD ITS OWN LOAD — the mechanism test.

    Added 2026-09-09.  Seating used to ask a topological question — "is this part
    a connected component of its own?" — and that is not the question.  Measured
    on the live G2-L40 at the coupled temperatures: after the magnets had been
    seated and re-attached, the marginal-pair freeze (see ``FREEZE_FLIPS``) shut
    1636 of the 2044 magnet/iron pairs OPEN over forty iterations, and each
    magnet was left hanging on two or three of them.  The component graph still
    said "attached"; the linear system was a MECHANISM, and it ran away at
    2.3e8 mm on iteration 45 — the same failure the seating was written to end,
    reached from the other side.

    Asking whether the part is DETACHED cannot see that, and neither can asking
    how far it moved: on a rotor at temperature every part moves, and the magnet
    that is quietly falling out is riding on 100 µm of iron growth while it does
    it.  So the test is a question about STIFFNESS, and it is asked of the
    contact set rather than of the displacement:

        put the part's own applied load on the springs it is CURRENTLY
        touching, as a rigid body, and see how far that moves it.

        H = Σ_i G_i^T G_i   over its closed unilateral pairs   (per unit k_c)
        a = (H + reg·I)^-1 · Q / k_c

    ``a`` is a length, and the three cases are nowhere near each other.  Measured
    on the 4-pole fixture with one magnet's own 7.6 kN/m and this solve's k_c
    (tests/test_mechanical_seating.py runs all three):

        held on its radial faces          0.0014 µm    (weakest eigenvalue ~1)
        on frictionless side walls only     70   µm    (~2e-5: TANGENT_REG)
        touching nothing at all           1400   µm    (1e-6: ``reg`` alone)

    ``SEAT_LOOSE_FRAC`` of the rotor radius — 50 µm there, 73 µm on the G2 —
    stands four and a half orders of magnitude above a part that is held and
    below both parts that are not.  That is why the threshold is a statement
    about load paths and not a tuning knob.

    It covers all THREE rigid modes on purpose.  Translation along the load is
    the obvious one, and it is what the user sees ("магнит должен сесть на
    язычок"), but the G2 fails through the other two: a magnet resting on two
    pairs of one wedge wall can still ROTATE about them, and rotation is what
    took the displacement to 2.3e8 mm.

    FRICTION counts, and it counts exactly as much as the solve gives it: each
    closed pair's tangential secant spring ``k_t`` from the previous iterate goes
    into ``H`` beside its normal one.  It has to.  A block resting on a flat
    face is held sideways by nothing else, and calling that loose would seat the
    two-block Coulomb fixture sideways into its travel cap — measured, 2026-09-09.
    Equally, ``k_t`` is ``mu·F_n/|s|`` on the cone and ``TANGENT_REG`` (a
    millionth) with no friction at all, so the G2's magnet lying against pocket
    walls it exerts no pressure on gets no credit for them, which is the case
    this test exists to catch.  The load path is read off the assembled stiffness
    rather than argued about.

    A pair that is OPEN counts for nothing: it may be a micron away and about to
    carry everything; it carries nothing now, and "now" is what the solve
    assembles.

    A BILATERAL crossing pair (``bonded`` / ``sliding``) is the opposite: it ties
    the part in both directions and settles the question on its own, which is
    what keeps a press-fit hub out of this.

    ``skip`` names the bodies that are the frame of reference — the main mass and
    anything a ``HeldBoundary`` holds.  Returns the mask of the pairs CROSSING a
    loose body: the caller cuts them out of the component graph, which gives the
    body its rigid-body columns (an honest, non-singular trial) and hands it to
    the seating.
    """
    out = np.zeros(cs.n_pairs, dtype=bool)
    va, vb = body[cs.pair_va], body[cs.pair_vb]
    reg = SEAT_REG * np.eye(3)
    limit = SEAT_LOOSE_FRAC * max(r_out, 1e-12)
    for b in np.unique(body):
        if int(b) in skip:
            continue
        bdx = body_dof[ix] == b
        bdy = body_dof[iy] == b
        if not (bdx.any() and bdy.any()):
            continue
        fx = float(f[ix][bdx].sum())
        fy = float(f[iy][bdy].sum())
        fmag = math.hypot(fx, fy)
        scale = float(np.abs(f[ix][bdx]).sum() + np.abs(f[iy][bdy]).sum())
        if fmag <= SEAT_LOAD_REL * scale:
            # Nothing pulling it anywhere: a free-body eigenstrain is
            # self-equilibrated, and a part with no net load cannot be loose
            # under one.  This is what keeps every standstill answer put.
            continue
        in_a, in_b = va == b, vb == b
        cross = in_a ^ in_b
        if not cross.any():
            continue
        if (cross & (~uni)).any():
            continue                     # a bonded / sliding tie holds it
        # Is there anywhere for it to GO?  The question this test asks is not
        # "is this part free" in the abstract but "should it be re-seated", and
        # seating moves a part onto a pair it already has (module docstring).
        # A part with no OPEN pair facing its load has no landing to be found —
        # a frictionless block pressed flat onto a face slides sideways for ever
        # and no travel invents a stop for it, and detaching it here would only
        # walk it into the travel cap (measured on the two-block Coulomb fixture
        # in tests/test_mechanical_torque.py, mu = 0).  That case belongs to
        # ``TANGENT_REG`` and, if it matters, to the "held by nothing" refusal.
        d = np.array([fx / fmag, fy / fmag])
        dot = np.where(in_a[:, None], cs.pair_n, -cs.pair_n) @ d
        if not (cross & uni & (~closed) & (dot > SEAT_MIN_COS)).any():
            continue
        cen = np.array([float(px[ix[bdx]].mean()), float(py[iy[bdy]].mean())])
        act = np.nonzero(cross & closed & uni)[0]
        if act.size:
            arm = float(np.abs(cs.pair_pos[act] - cen).max())
            G = _body_rows(cs, act, in_a[act], cen, arm)
            T = _body_rows(cs, act, in_a[act], cen, arm, tangential=True)
            H = G.T @ G + T.T @ (kt_ratio[act][:, None] * T)
        else:
            arm = max(r_out, 1e-12)
            H = np.zeros((3, 3))
        mz = float(((px[iy[bdy]] - cen[0]) * f[iy][bdy]).sum()
                   - ((py[ix[bdx]] - cen[1]) * f[ix][bdx]).sum())
        Q = np.array([fx, fy, mz / max(arm, 1e-12)])
        try:
            a = np.linalg.solve(H + reg, Q / kc)
        except np.linalg.LinAlgError:                      # pragma: no cover
            a = np.full(3, np.inf)
        if not np.isfinite(a).all() or float(np.hypot(a[0], a[1]) + abs(a[2])) > limit:
            out |= cross
    return out


def _seat_line(gap: np.ndarray, rate: np.ndarray, target: float,
               cap: float, n_bisect: int = 80) -> Tuple[float, bool]:
    """Exact line search of the seating energy: how far along one direction.

    ``rate`` is d(gap)/ds of every pair along the search direction, so the
    penetration of pair i after a step s is ``p_i(s) = max(0, -(g_i + s·rate_i))``
    and the directional derivative of the penalty energy is

        phi'(s) / k_c  =  Σ p_i(s)·(-rate_i)  -  target       ( target = Q·v / k_c )

    Every term is non-decreasing in s — a face in FRONT of the part (rate < 0) is
    pressed harder as it advances, a face BEHIND it (rate > 0) is unburied — so
    phi' is monotone, the stationary point is unique, and a bisection finds it.

    This is the whole of the seating in one dimension, and it is why the travel
    is no longer "as far as the first pair you touch" (2026-09-09).  Measured on
    the live G2-L40 at the coupled temperatures, magnet 1 of 28, at the iteration
    its component came loose (tests/test_mechanical_seating.py builds the case):

        pocket floor   n·e_r −1.00   rate +1.00   gap  −75.5 µm  (BURIED)
        parallel walls n·e_r  0.00   rate +0.13   gap   −3.2 µm
        tapered wedge  n·e_r +0.24   rate −0.13   gap   +5.4 µm  <- first touch
        the tab        n·e_r +0.50   rate −0.42   gap  +32.1 µm

    The pocket has grown 75 µm outward around a magnet that has not moved, so the
    floor BEHIND the magnet is 75 µm inside it while the wedge in FRONT is 5 µm
    away.  "Stop at the first thing you touch" answers 41 µm and leaves the floor
    buried 34 µm deep — a penetration on a spring of ten times the mesh's own
    stiffness, i.e. a force that has nothing to do with the machine.  The active
    set then spent 27 iterations arguing with it and ran away at 2.5e8 mm.

    ``target`` is the penetration the applied load itself needs — nanometres
    against the microns above — and it is what makes the part come to rest ON its
    support instead of floating in the free space between two faces.  With
    nothing behind it, phi' is flat at −target until the first facing pair is
    reached and the root is that first touch plus a hair: the answer the 4-pole
    fixture had before this rewrite, to the micron (its regression guard is in
    tests/test_mechanical_seating.py).

    Returns ``(s, hit_cap)``.  ``hit_cap`` means the stationary point is further
    than ``cap`` — the part has not been seated, it has escaped.
    """
    if cap <= 0.0:
        return 0.0, True

    def _h(ss: float) -> float:
        return float((np.maximum(-(gap + ss * rate), 0.0) * -rate).sum())

    if _h(0.0) >= target:
        return 0.0, False                   # already resting on its supports
    if _h(cap) < target:
        return cap, True                    # nothing within reach stops it
    lo, hi = 0.0, cap
    for _ in range(n_bisect):
        mid = 0.5 * (lo + hi)
        if _h(mid) < target:
            lo = mid
        else:
            hi = mid
    return hi, False


def _seat_placement(gap: np.ndarray, G: np.ndarray, Q: np.ndarray, kc: float,
                    cap: float) -> Tuple[np.ndarray, bool]:
    """Where a loose part comes to rest: TWO translations and a ROTATION.

    Added 2026-09-09, second half of the same G2-L40 finding.  Seating the magnet
    by a translation alone — even the right translation — could not work, and the
    reason is geometric rather than numerical: the pocket does not move, it
    EXPANDS, and it is sheared by the torque on top of that.  Its floor at
    r = 54 mm goes out 75 µm, its tab at r = 70 mm goes out 96 µm, and the two
    pole tips beside it lean by 14 µm in opposite directions.  A rigid magnet
    slid along one line matches that at exactly one radius; everywhere else it
    is nipped.  Measured on magnet 1 of 28 at the iteration it came loose:

        best translation only     : 75.3 µm,  residual penetration 4.4 µm
        translation + rotation    : 102  µm + 0.085°, residual penetration 1.8 nm

    4.4 µm on the contact spring is a force some three orders of magnitude above
    the magnet's own weight in the field; 1.8 nm is the arithmetic's noise.  With
    the first the active set never settled (30 iterations, 1170 pairs frozen); the
    second lands the magnet on three faces and it is done.

    So the placement is the full rigid-body equilibrium of the part on its own
    unilateral springs — the same springs ``solve_contact`` is about to assemble:

        minimise  Phi(a) = ½ k_c Σ max(0, −(g_i + G_i·a))²  −  Q·a

    over ``a = (t_x, t_y, arm·θ)``, with ``G_i`` the rate at which pair i's gap
    changes under that rigid motion and ``Q = (F_x, F_y, M/arm)`` the applied
    resultant about the part's own centroid.  ``arm`` (the part's outermost
    contact radius) scales the rotation into a length so all three unknowns and
    all three loads are in the same units — without it the Newton step mixes
    metres with radians and the line search has no meaning.

    Phi is convex and piecewise quadratic, so this is a semi-smooth Newton: the
    active springs give the Hessian, a least-squares solve gives the direction
    (least-squares and not an inverse because a part touching nothing, or
    touching along one line only, has a singular Hessian — the minimum-norm step
    then leaves the unrestrained modes alone instead of exploding), and
    ``_seat_line`` walks exactly to the stationary point along it.  With no
    spring active at all the direction is the load itself, which is the old
    "fall along d" as the first step of the same iteration.

    Returns ``(a, hit_cap)``; ``hit_cap`` if the rest position is further than
    ``cap`` (see ``SEAT_TRAVEL_FRAC``).
    """
    a = np.zeros(3)
    if cap <= 0.0:
        return a, True
    qn = float(np.linalg.norm(Q))
    # With NO applied load the part is still placed, and it is placed by the
    # overlap alone (2026-09-09).  Phi's second term vanishes and the first one
    # does not: a part standing inside the iron is pushed out until the springs
    # balance, which is the same minimisation with Q = 0.  The scale the
    # gradient is judged against then has to come from the penetration instead
    # of from the load, or the tolerance is zero and the Newton runs its full
    # budget chasing the arithmetic's noise.  See ``solve_contact`` for the case:
    # a magnet whose pocket floor has grown 75 µm into it while nothing at all
    # is pulling on the magnet.
    ref = qn if qn > 0.0 else kc * float(np.maximum(-gap, 0.0).max(initial=0.0))
    if ref <= 0.0:
        return a, False
    for _ in range(SEAT_NEWTON_STEPS):
        pen = np.maximum(-(gap + G @ a), 0.0)
        act = pen > 0.0
        # dPhi/da.  The gradient of the penalty is -kc·G^T p (a penetrating
        # spring pushes the part back out), and the load pulls the other way.
        grad = -(kc * (G[act].T @ pen[act]) + Q) if act.any() else -Q
        if float(np.linalg.norm(grad)) <= SEAT_GRAD_TOL * ref:
            break
        H = (kc * (G[act].T @ G[act]) if act.any() else np.zeros((3, 3)))
        # REGULARISED Newton, and the regularisation is not cosmetic.  Fewer than
        # three springs active leaves the Hessian singular, and a least-squares
        # step then lives entirely inside the subspace those springs already
        # restrain — the part never moves towards the face it has not reached
        # yet.  Measured on the G2: the magnet stopped with ONE pair touching and
        # the next 0.09 µm away, i.e. resting on a point, and the solve ran away
        # at 1.5e10 mm on the following iteration.  ``SEAT_REG`` lets the step
        # out into the unrestrained directions, where the exact line search then
        # decides how far to actually go.
        try:
            v = -np.linalg.solve(H + SEAT_REG * kc * np.eye(3), grad)
        except np.linalg.LinAlgError:                      # pragma: no cover
            v = -grad
        if not np.isfinite(v).all() or float(grad @ v) >= 0.0:
            v = -grad
        vn = float(np.linalg.norm(v))
        if vn <= 0.0:
            break
        v = v / vn
        rate = G @ v
        # How far this step may go before the total placement leaves the cap.
        # |a1,a2| + |a3| bounds the rigid displacement of any point within `arm`
        # of the centroid, so it is the honest thing to hold against the cap.
        used = float(np.hypot(a[0], a[1]) + abs(a[2]))
        room = cap - used
        if room <= 0.0:
            return a, True
        s, hit = _seat_line(gap + G @ a, rate, float(Q @ v) / kc, room)
        if s <= 0.0:
            break
        a = a + s * v
        if hit:
            return a, True
    return a, False


def _seat_travel_rel(cs: ContactSystem, u: np.ndarray, u_seat: np.ndarray,
                     land: np.ndarray, mine_is_a: np.ndarray,
                     lands_on: int) -> float:
    """How far the part moved IN ITS POCKET — the travel that means something.

    Added 2026-09-09, on the user's reading of the hot G2-L40: the panel said
    the magnet had travelled 105 µm and that number is true of the MESH and of
    nothing else.  The pocket it sits in grew 90-96 µm outward on the same
    solve, so ninety per cent of that "travel" is the magnet riding with the
    iron around it — no motion at all in the machine, and a figure that reads
    like a magnet walking out of its rotor.

    What the engineer is asking is the RELATIVE one: how far did the magnet move
    with respect to the pocket, which on an all-iron rotor with NdFeB in it is
    the CTE mismatch (12 against 5 ppm/K) and nothing else.  So

        travel_rel = | u_seat(the part's side of the landing pairs)
                      -   u  (the pocket's side of the same pairs) |

    ``u_seat`` is the part's RIGID travel — the placement's own translation and
    rotation, carried in no other array — and ``u`` at the opposite side of the
    same pairs is where the pocket wall went under its own thermal and
    centrifugal growth.  Their difference is the closing of the clearance, and it
    is what the retention verdict quotes; the absolute number stays beside it in
    the tooltip, because it is the one that has to match the map.

    The part's OWN elastic deformation is deliberately not in it: the pairs it
    landed on are touching, so the full relative displacement of the two surfaces
    is ~0 there by construction (the seating offset ``g0`` carries exactly the
    other way).  The rigid travel against the pocket's motion is the only pair of
    quantities that still says how far the part went.

    ``land`` are the landing pairs; with none of them recorded yet (a placement
    that came to rest on one face to within its own tolerance) the pair it is
    pressing hardest into, ``lands_on``, stands in for them.
    """
    pairs = np.asarray(land, dtype=np.int64)
    if pairs.size == 0:
        pairs = np.array([int(lands_on)], dtype=np.int64)
    own_a = np.asarray(mine_is_a)[pairs][:, None]
    mine = np.where(own_a, cs.pair_dofs[pairs, 0:2], cs.pair_dofs[pairs, 2:4])
    other = np.where(own_a, cs.pair_dofs[pairs, 2:4], cs.pair_dofs[pairs, 0:2])
    d = u_seat[mine].mean(axis=0) - u[other].mean(axis=0)
    return float(math.hypot(float(d[0]), float(d[1])))


def solve_contact(K, f: np.ndarray, cs: ContactSystem, basis,
                  closed0: Optional[np.ndarray] = None,
                  max_iter: int = 30,
                  solve_fn=None,
                  hold: Optional[HeldBoundary] = None,
                  progress=None,
                  f_ramp: Optional[np.ndarray] = None,
                  load_steps: int = 1) -> ContactSolution:
    """Solve the unilateral contact problem by a semi-smooth Newton (active set).

    ``progress`` (optional) is called ``progress(it, max_iter)`` at the TOP of
    every Newton iteration — the project's shared callback contract (see
    ``motor_ai_sim.progress``), with the iteration counter as the step and this
    loop's own budget as the total.  It is here and not one level up because
    this loop is where the seconds go: each iteration is a full sparse saddle
    solve, and on a spoke rotor there can be thirty of them per load case with
    nothing else reporting in between.  Whoever owns the bar (rotor_stress, for
    a solve that runs one of these per case) maps the local count onto its own.

    ``K`` and ``f`` are the ordinary linear-elasticity stiffness and load of the
    SPLIT mesh — the parts are connected by nothing but the contact treatment.
    Each iteration solves

        [ K + Kc + Kt   C^T ] [ u   ]   [ f   ]
        [ C             0   ] [ lam ] = [ 0   ]

    where ``C`` stacks the rigid-body border (three rows per connected
    component), the BILATERAL ties (``bonded``: normal + tangential,
    ``sliding``: normal) and any ``HeldBoundary`` rows; ``Kc`` is the unilateral
    part, a stiff normal spring on every ``separation`` pair whose gap came back
    NEGATIVE on the previous iterate; and ``Kt`` is the friction, a tangential
    spring whose SECANT stiffness saturates the force on the Coulomb cone (see
    the module docstring).  Nothing about friction reaches the right-hand side.

    WHY THE UNILATERAL PART IS A SPRING AND NOT A LAGRANGE TIE
    ----------------------------------------------------------
    The first version tied the closed pairs exactly and released whichever came
    back tensile.  On the live Ø124 spoke rotor that never converged: 1220 of
    1310 magnet/iron pairs are tensile in the bonded state, releasing them all
    left each magnet hanging on ~6 nodes, the magnets then moved 250 um and
    re-closed half the wall, and the set oscillated between "the magnet hangs on
    the bridge above it" and "the magnet wedges on the pocket walls" for ever —
    30 iterations with 50-80 pairs flipping each time and the SOLUTION barely
    moving (|u|max stayed at 52-55 um throughout).  The oscillation is the
    discontinuity, not the physics: an exact tie jumps from carrying the full
    load to carrying nothing the instant a pair flips.  A spring is continuous
    at the crossing — a pair on the boundary carries ~0 either way — so the
    fixed point is stable, and the price is a penetration of ~0.1 um which is
    reported rather than hidden.

    ``lam`` for a bilateral row is the tie multiplier: NEGATIVE in compression
    (the tie only pulls when it is asked to hold tension), so the reported
    contact force is ``-lam``; for a unilateral pair the force is the spring's,
    ``k_c * penetration``, which is >= 0 by construction.

    ``hold`` (2026-09-07) appends the reaction rows of a ``HeldBoundary`` to
    ``C`` — the shaft bore held tangentially, so a torque load has something to
    react against.  Its multipliers come back in ``hold_mult`` and their moment
    in ``hold_torque_n_m_per_m``.

    ``cs.cyclic`` (2026-09-09) appends the CYCLIC-SYMMETRY rows of a
    ``CyclicTies`` to the same ``C`` — ``u_B = R(theta) u_A`` on every matched
    point of the two cut faces of a one-pole sector (user: *"так используй
    периодичность, как я во Fusion"*).  They are bilateral and load-independent,
    so they are assembled once, outside the active-set loop; their multipliers
    come back in ``cyclic_mult``.  Because a translation is not a periodic
    field, the rigid-body border of any component they touch drops from three
    columns to one — see ``_rigid_basis``.

    ``f_ramp`` / ``load_steps`` (2026-09-09) walk the load instead of applying it
    in one go: the solve is repeated for ``f + lambda * f_ramp`` with lambda
    stepping 0, 1/n, 2/n … 1 (so ``load_steps`` = n + 1 solves), and every step
    starts from the previous step's PHYSICAL state — ``closed``, ``stick``,
    ``kt_sec`` and the seating offsets ``g0``/``u_seat`` all carry across, the
    flip history and the per-step seat budget do not (they are judgements about a
    configuration the load has just changed).  With ``f_ramp=None`` there is one
    step and nothing about the arithmetic differs from before.  See the module
    docstring for the wedge that made this necessary.

    SEATING (2026-09-09) adds one array to that statement: a per-pair stress-free
    gap ``g0``, so the gap function is ``g = (u_B - u_A).n + g0``.  It is zero for
    every pair of every solve in which nothing comes loose.  When a component
    does come loose and is travelled onto a surface, its rigid translation is
    written into ``g0`` (one number per pair the translation moves) and into
    ``u_seat``; the penalty spring then carries the offset on BOTH sides —
    ``K + kc B^T B`` unchanged, ``f - kc B^T g0`` on the right — so a landing
    pair is at zero force exactly where the part now touches, and not where the
    undeformed mesh put it.  See the module docstring for why and for the guards.
    """
    ndof = K.shape[0]
    npair = cs.n_pairs
    kscale = float(K.diagonal().mean())
    s = math.sqrt(max(kscale, 1e-30))
    kc = CONTACT_STIFFNESS * kscale
    kt = TANGENT_REG * kc
    nv = cs.mesh.p.shape[1]
    solve_fn = solve_fn or _solver()[1]

    uni = cs.pair_unilateral
    muc = cs.pair_mu
    closed = (np.ones(npair, dtype=bool) if closed0 is None
              else np.asarray(closed0, dtype=bool).copy())
    closed[~uni] = True                          # bonded / sliding never open
    stick = np.ones(npair, dtype=bool)
    tang_all = np.stack([-cs.pair_n[:, 1], cs.pair_n[:, 0]], axis=1)
    #: secant tangential stiffness of every frictional pair, updated each
    #: iteration from the Coulomb cone: min(kc, mu*Fn/|s|).  Starts at kc, i.e.
    #: every pair starts STUCK, which is the same warm start the normal
    #: direction uses (every pair starts closed).
    kt_sec = np.full(npair, kc)

    # ── the held-boundary rows, assembled once (they do not depend on the set) ─
    n_hold = 0
    G_hold = None
    if hold is not None and hold.n_rows:
        n_hold = hold.n_rows
        rows = np.repeat(np.arange(n_hold), 2)
        cols = hold.dofs.reshape(-1)
        vals = hold.dirs.reshape(-1) * s
        G_hold = sp.coo_matrix((vals, (rows, cols)),
                               shape=(n_hold, ndof)).tocsr()

    # ── the CYCLIC-SYMMETRY rows (2026-09-09), also assembled once ───────────
    # Two rows per matched point,  u_B - R u_A = 0 :
    #     u_Bx - (R00 u_Ax + R01 u_Ay) = 0
    #     u_By - (R10 u_Ax + R11 u_Ay) = 0
    # They are bilateral and load-independent, so like the hold rows they live
    # outside the active-set loop.  ``cyc_vertices`` goes to ``_rigid_basis``
    # below, which then borders only the surviving rigid mode (the rotation).
    n_cyc = 0
    G_cyc = None
    cyc = getattr(cs, "cyclic", None)
    cyc_vertices = None
    if cyc is not None and cyc.n_rows:
        m = int(cyc.dofs_a.shape[0])
        n_cyc = 2 * m
        Rm = np.asarray(cyc.R, dtype=float)
        r_i = np.repeat(np.arange(n_cyc), 3)
        c_i = np.concatenate([
            np.stack([cyc.dofs_b[:, 0], cyc.dofs_a[:, 0], cyc.dofs_a[:, 1]],
                     axis=1),
            np.stack([cyc.dofs_b[:, 1], cyc.dofs_a[:, 0], cyc.dofs_a[:, 1]],
                     axis=1)]).reshape(-1)
        v_i = np.concatenate([
            np.tile([1.0, -Rm[0, 0], -Rm[0, 1]], (m, 1)),
            np.tile([1.0, -Rm[1, 0], -Rm[1, 1]], (m, 1))]).reshape(-1) * s
        G_cyc = sp.coo_matrix((v_i, (r_i, c_i)), shape=(n_cyc, ndof)).tocsr()
        cyc_vertices = np.asarray(cyc.vertices, dtype=np.int64)
    cyc_mult = np.zeros(n_cyc)

    seen: set = set()
    flips = np.zeros(npair, dtype=np.int32)
    #: widest gap each pair has ever flipped across — the freeze below is only
    #: allowed to hold a pair open if that number says it is carrying nothing.
    flip_amp = np.zeros(npair)
    frozen = np.zeros(npair, dtype=bool)
    converged = False
    u = np.zeros(ndof)
    u_prev = np.zeros(ndof)
    lam_n = np.zeros(npair)
    lam_t = np.zeros(npair)
    gap = np.zeros(npair)
    slip = np.zeros(npair)
    hold_mult = np.zeros(n_hold)

    # ── SEATING state (2026-09-09) ──────────────────────────────────────────
    # ``g0`` is the stress-free gap of the complementarity statement.  It starts
    # at the mesh's own (zero — the split put the two copies of a vertex on the
    # same point) and only the seating step ever moves it, so a solve in which
    # nothing floats runs the arithmetic it always ran.
    g0 = np.array(cs.pair_g0, dtype=float).copy() if cs.pair_g0.size == npair \
        else np.zeros(npair)
    u_seat = np.zeros(ndof)
    seat_travel: Dict[int, float] = {}
    seat_steps: Dict[int, int] = {}
    seated: Dict[int, Dict[str, Any]] = {}
    seat_capped: List[str] = []
    #: the rotor's own outer radius, from the mesh it is being solved on
    r_out = float(np.hypot(cs.mesh.p[0], cs.mesh.p[1]).max())
    travel_cap = SEAT_TRAVEL_FRAC * max(r_out, 1e-12)
    freeze_margin = FREEZE_MARGIN_FRAC * max(r_out, 1e-12)
    elem_v0 = cs.mesh.t.T[:, 0]

    # ── which piece of metal every node belongs to, and which of those pieces
    #    the rest of the rotor is measured against (2026-09-09, the looseness
    #    test in ``_loose_pairs``).  The biggest body is the frame of reference,
    #    and so is anything a HeldBoundary holds.
    body = _part_bodies(cs)
    body_dof = _dof_components(basis, body, cs.mesh)
    ix_all = np.arange(0, ndof, 2)                # ElementVector interleaves x, y
    iy_all = ix_all + 1
    frame: set = {int(np.bincount(body).argmax())}
    if hold is not None and hold.vertices.size:
        frame |= {int(b) for b in np.unique(body[hold.vertices])}
    # ── THE LOAD PATH (2026-09-09) ──────────────────────────────────────────
    # lambda = 0 first (the rotor spun and heated, the drive still off — this is
    # where a loose part seats), then the ramp in equal increments.  With no ramp
    # there is exactly one step at the full load, which is every solve this
    # module used to make.
    _ramp = (f_ramp is not None and f_ramp.size == ndof and bool(np.any(f_ramp)))
    n_ramp = max(1, int(load_steps)) if _ramp else 0
    lambdas = ([i / n_ramp for i in range(n_ramp + 1)] if _ramp else [0.0])
    n_steps = len(lambdas)
    step = 1
    it_step = 0
    f_step = f if not _ramp else f + lambdas[0] * f_ramp
    step_res: List[float] = []
    #: the least-violating iterate seen IN THIS STEP (see the residual below)
    best: Dict[str, Any] = {"viol": math.inf, "it": 0}
    #: …and how many iterations ago it was seen.  See ``SET_STALL``.
    stall = 0
    viol = 0.0
    it = 0
    for it in range(1, max_iter * n_steps + 1):
        it_step += 1
        if progress is not None:
            # Reported BEFORE the solve, not after: the step the user is waiting
            # on is the one now starting, and a bar that only moves on
            # completion sits still for the whole of the slowest iteration.
            # A broken callback must never kill a solve.
            try:
                progress(it, max_iter * n_steps)
            except Exception:  # noqa: BLE001
                progress = None
        # -- bilateral rows: bonded (n + t), sliding (n only) -----------------
        # ``pair_no_row`` is all False except on a cyclic sector, where it holds
        # the trailing-face copies whose rows the ties already imply exactly.
        no_row = (cs.pair_no_row if np.size(cs.pair_no_row) == npair
                  else np.zeros(npair, dtype=bool))
        bil_n = ~uni & ~no_row
        bil_t = cs.pair_tie_tangent & ~no_row
        rn, cn, vn, pn = _pair_rows(cs, bil_n, False)
        rt, ct, vt, pt = _pair_rows(cs, bil_t, True)

        # ── a part with no load path is DETACHED for this iteration ──────────
        # Not because the contact graph says so — it usually does not — but
        # because nothing it still touches can react its own load (see
        # ``_loose_pairs``).  Cutting its pairs out of the graph gives it its
        # rigid-body columns below, which is what keeps the trial solve honest
        # and non-singular, and puts it in front of the seating.  Its freeze
        # history goes with them: a marginal pair frozen OPEN is how the load
        # path was lost in the first place, and the part is about to be moved
        # somewhere those flips say nothing about.
        # The tangential stiffness the NEXT assembly will give every pair — the
        # Coulomb secant where it sticks, ``TANGENT_REG`` where it does not, zero
        # where the pair is open.  The looseness test reads it so that friction
        # counts for exactly what the solve lets it carry, and no more.
        _act_prev = closed & uni
        kt_ratio = (np.where(_act_prev & (muc > 0), kt_sec, kt)
                    * _act_prev) / kc
        loose = _loose_pairs(cs, body, body_dof, f_step, closed, uni, frame,
                             ix_all, iy_all, basis.doflocs[0], basis.doflocs[1],
                             kc, r_out, kt_ratio)
        if loose.any():
            flips[loose] = 0
            frozen[loose] = False
        lab = _components(cs, closed & ~loose, nv)
        R, _ncomp = _rigid_basis(basis, lab, cs.mesh,
                                 hold.vertices if hold is not None else None,
                                 cyclic_vertices=cyc_vertices)
        nr = R.shape[1]
        # Which components are floating RIGHT NOW, i.e. are about to be solved
        # with their rigid modes pinned.  ONLY those may be seated, and only from
        # THIS solve's gaps: a component that is still tied to the main body is
        # measuring the stretch of the springs that hold it, not a clearance
        # (module docstring, 2026-09-09).
        pinned_comps: List[int] = []
        _labs = np.unique(lab)
        if _labs.size > 1:
            _main = int(np.bincount(lab).argmax())
            _held = (set(np.unique(lab[hold.vertices]).tolist())
                     if (hold is not None and hold.vertices.size) else set())
            pinned_comps = [int(c) for c in _labs
                            if int(c) != _main and int(c) not in _held]

        # Rows scaled by sqrt(mean diag K) so the saddle matrix is not 1e11 in
        # one block and 1 in the other; the multipliers are unscaled below.
        G = sp.coo_matrix((np.concatenate([vn, vt]) * s,
                           (np.concatenate([rn, pn.size + rt]),
                            np.concatenate([cn, ct]))),
                          shape=(pn.size + pt.size, ndof)).tocsr()
        blocks = [sp.csr_matrix(R * s).T, G]
        if G_hold is not None:
            blocks.append(G_hold)
        if G_cyc is not None:
            blocks.append(G_cyc)
        Call = sp.vstack(blocks, format="csr")

        # -- unilateral part: normal spring + tangential stick/regularisation --
        Kx = K
        act = closed & uni
        f_eff = f_step
        # Tangential stiffness per pair: the Coulomb SECANT where the pair has
        # friction (kc while it sticks, mu*Fn/|s| once it is on the cone),
        # otherwise only the tiny mechanism-removing regularisation.
        kt_pair = np.where(act & (muc > 0), kt_sec, kt) * act
        if act.any():
            ri, ci, vi, ip1 = _pair_rows(cs, act, False)
            B = sp.coo_matrix((vi, (ri, np.asarray(ci))),
                              shape=(int(act.sum()), ndof)).tocsr()
            Kx = Kx + kc * (B.T @ B)
            # The penalty is 0.5*kc*(B u + g0)^2, so an offset pair contributes
            # -kc B^T g0 to the load (2026-09-09, seating).  ``g0`` is all zeros
            # until something is seated, and this term is then exactly zero —
            # the same right-hand side this loop has always assembled.
            if np.any(g0[ip1]):
                f_eff = f_step - kc * (B.T @ g0[ip1])
            rt2, ct2, vt2, ip2 = _pair_rows(cs, act, True)
            Bt = sp.coo_matrix((vt2 * np.repeat(np.sqrt(kt_pair[ip2]), 4),
                                (rt2, np.asarray(ct2))),
                               shape=(ip2.size, ndof)).tocsr()
            Kx = Kx + (Bt.T @ Bt)
        Kx = Kx.tocsr()

        A = sp.bmat([[Kx, Call.T], [Call, None]], format="csr")
        rhs = np.concatenate([f_eff, np.zeros(Call.shape[0])])
        sol = solve_fn(A, rhs)
        if not np.isfinite(sol).all():
            raise RuntimeError(
                "the contact solve returned non-finite values — the constrained "
                "system is singular. That normally means a part has no load "
                "path at all (every pair released and nothing else holding it).")
        u_prev, u = u, sol[:ndof]
        mult = sol[ndof:] * s                    # undo the row scaling

        lam_t_prev = lam_t.copy()        # the convergence test below is on this
        lam_n[:] = 0.0
        lam_t[:] = 0.0
        if pn.size:
            lam_n[pn] = mult[nr:nr + pn.size]
        if pt.size:
            lam_t[pt] = mult[nr + pn.size:nr + pn.size + pt.size]
        if n_hold:
            hold_mult = mult[nr + pn.size + pt.size:
                             nr + pn.size + pt.size + n_hold]
        if n_cyc:
            cyc_mult = mult[nr + pn.size + pt.size + n_hold:]

        # gaps and slips of EVERY pair, from the displacement field
        rel = u[cs.pair_dofs[:, 2:4]] - u[cs.pair_dofs[:, 0:2]]
        # g = (u_B - u_A).n + g0 — ``g0`` is the seating offset and is zero
        # unless a component has been travelled onto a surface.
        gap = np.einsum("ij,ij->i", rel, cs.pair_n) + g0
        # The TANGENTIAL direction carries no seating offset on purpose: the
        # travel is a rigid re-seating, not the elastic micro-slip the
        # regularised Coulomb spring measures.  Counting it as slip would make a
        # joint that has simply landed report a hundred microns of sliding, and
        # would swing the secant stiffness by orders of magnitude between
        # iterations — the divergence the secant form was written to end
        # (2026-09-07).  The force it can pass is still mu*F_n either way.
        slip = np.einsum("ij,ij->i", rel, tang_all)
        # The spring force on the A side is +k*g*n, which is the same sign
        # convention the bilateral multiplier carries (negative = the pair is in
        # compression), so every report downstream reads one array.  Clamped at
        # zero because the penalty law is F = k*max(0, -g): a pair that was
        # active in the assembly but came back with an OPEN gap must report
        # nothing, or a non-converged step would show a separation contact
        # holding tension, which is the one thing it cannot do.
        lam_n[uni] = kc * np.minimum(gap[uni], 0.0) * closed[uni]
        lam_t[act] = kt_pair[act] * slip[act]

        tol_g = max(1e-13, 1e-9 * float(np.abs(u).max()))

        # ── how far this iterate is from BEING a solution (2026-09-09) ───────
        # The active set is the semi-smooth Newton's unknown, so the residual is
        # the disagreement between the set this solve was ASSEMBLED with and the
        # one its own gaps ask for: an open pair that came back overlapping, or a
        # closed one that came back apart (and therefore PULLING, the penalty
        # spring being two-sided).  Zero exactly when the set repeats, which is
        # the loop's own convergence test.
        #
        # Summed in quadrature and not taken as a maximum, because the two
        # failures are not the same size.  A worst-case measure crowns the very
        # first iterate — every pair closed, every one of them a nanometre into
        # tension — over an honest one with three pairs a hundredth of a micron
        # out, and that "best" answer is a fully bonded rotor.
        _bad = uni & (closed != (gap <= tol_g))
        viol = float(np.linalg.norm(gap[_bad])) if _bad.any() else 0.0

        # ── SEATING: a loose part travels until it lands (2026-09-09) ────────
        # "магнит должен сесть на язычок, как в Fusion" — see the module
        # docstring.  Nothing here runs unless a component was already floating
        # when THIS solve was made, so a rotor whose parts are all held reaches
        # the active-set update below having executed one `if`.
        #
        # It runs BEFORE that update, and the ordering is the whole trick.  A
        # pinned magnet is held in the mesh's own place while the iron grows
        # around it, so the pocket's INNER face penetrates it — and the update,
        # taken first, closes that face and re-attaches the magnet to the body
        # by the one surface that cannot retain it.  Measured on the 4-pole
        # fixture (rotor 150 °C, magnet 20 °C, 20 000 rpm): all closed → all
        # tensile → released → pinned → inner face penetrating → re-closed →
        # tensile again, cycling out at iteration 4 with the magnet free and the
        # whole solve refused.  Seated from the same gaps, before they are
        # graded, the magnet moves outward instead: the lip's pairs land, the
        # inner face opens by exactly the travel, and the side walls — whose
        # normals are square to the load — do not move at all.
        moved_now = False
        land_now = np.zeros(npair, dtype=bool)
        moved_pairs = np.zeros(npair, dtype=bool)
        if pinned_comps:
            lab_dof = _dof_components(basis, lab, cs.mesh)
            ix = np.arange(0, ndof, 2)               # ElementVector interleaves
            iy = ix + 1
            px, py = basis.doflocs[0], basis.doflocs[1]
            in_a_all = lab[cs.pair_va]
            in_b_all = lab[cs.pair_vb]
            for comp in pinned_comps:
                key = int(np.nonzero(lab == comp)[0].min())
                if seat_steps.get(key, 0) >= SEAT_MAX_STEPS:
                    continue
                # -- 1. the net load on it, which is the direction it falls ----
                cdx = lab_dof[ix] == comp
                cdy = lab_dof[iy] == comp
                fx = float(f_step[ix][cdx].sum())
                fy = float(f_step[iy][cdy].sum())
                fmag = math.hypot(fx, fy)
                scale = float(np.abs(f_step[ix][cdx]).sum()
                              + np.abs(f_step[iy][cdy]).sum())
                # -- 2. the pairs that cross its boundary ---------------------
                in_a = in_a_all == comp
                in_b = in_b_all == comp
                cross = in_a ^ in_b          # exactly one side of the pair moves
                # The direction the component must translate in to shut a pair:
                # +n when it owns side A (the normal points away from A), -n when
                # it owns side B.  A pair with both sides inside the component
                # moves with it and its gap does not change.
                n_close = np.where(in_a[:, None], cs.pair_n, -cs.pair_n)
                if fmag <= SEAT_LOAD_REL * scale:
                    # ── NO NET LOAD, BUT IT IS STANDING IN THE IRON ──────────
                    # A thermal eigenstrain is self-equilibrated on a free body
                    # (it comes out at ~1e-15 of the load it puts on the dofs),
                    # so nothing is pulling this part anywhere — and for a part
                    # that is merely floating, that is the end of it: no load,
                    # no direction, no seating, which is what leaves every
                    # standstill answer in the suite exactly where it was.
                    #
                    # It is NOT the end of it when the part is OVERLAPPED
                    # (2026-09-09).  A pinned magnet is held in the cold mesh's
                    # place while the pocket grows around it, so the floor ends
                    # up standing 75 µm INSIDE it — and the penalty spring on
                    # that face is then carrying a force with no machine behind
                    # it.  Measured on the live G2-L40 at the coupled
                    # temperatures with the torque alone (no centrifugal load at
                    # all, so nothing pulls the magnets anywhere): rotor
                    # 477 MPa p99.5, magnet 295, contact 109 — against 23 MPa
                    # for the same rotor spun hot.  It is the pre-seating
                    # failure the module docstring describes, reached through
                    # the one door the load test left open.
                    #
                    # So the part is placed by the OVERLAP: the direction is the
                    # one that clears the iron out of it, and ``_seat_placement``
                    # minimises the same penalty energy with Q = 0.
                    #
                    # But only if it is TRAPPED, and that distinction is the
                    # whole of the guard.  A part overlapped from ONE side is not
                    # misplaced, it is being pushed: the pocket floor of the
                    # 4-pole fixture rises 40 µm into a magnet that has nothing
                    # above it, the ordinary active-set update closes that face,
                    # and the magnet rides out on the spring — a solve, and the
                    # right one, which is why every standstill answer in the
                    # suite is exactly where it was.  A part overlapped from
                    # OPPOSING sides cannot ride anywhere: the G2's wedge closes
                    # on the magnet as the pocket grows, and no active set can
                    # put a rigid body where it does not fit.  The two cases are
                    # told apart by the overlaps themselves — pushed from one
                    # side they add up, nipped from both they cancel.
                    pen0 = np.maximum(-gap, 0.0) * (cross & uni)
                    span = float(pen0.sum())
                    if float(pen0.max(initial=0.0)) <= freeze_margin:
                        continue
                    dvec = -(pen0[:, None] * n_close).sum(axis=0)
                    dn = float(np.hypot(dvec[0], dvec[1]))
                    if dn > SEAT_TRAP_FRAC * max(span, 1e-30):
                        continue                 # pushed, not trapped: let it ride
                    loaded = False
                    d = (dvec / dn if dn > 0.0 else
                         -n_close[int(np.argmax(pen0))])
                else:
                    d = np.array([fx / fmag, fy / fmag])
                    loaded = True
                dot = n_close @ d
                # Where it could come to rest.  Under a load that is a pair the
                # load faces; relieving a trapped overlap has no such direction —
                # the placement is the answer and every crossing pair is in it.
                cand = (cross & uni & (dot > SEAT_MIN_COS) if loaded
                        else cross & uni)
                if not cand.any():
                    # Nothing outward to land on: the part is genuinely
                    # unretained.  That is the answer — `free_parts` below says
                    # so and rotor_stress refuses the solve.
                    continue
                # -- 3. where it comes to rest: the rigid-body equilibrium ----
                # Two translations AND a rotation (see ``_seat_placement``): the
                # pocket the magnet has to fall back into has EXPANDED and been
                # sheared by the torque, and no single slide fits a rigid part
                # into it — on the G2 the best translation still nips the wedge
                # by 4.4 µm, the placement with the rotation by 1.8 nm.
                sel = np.nonzero(cross)[0]
                cen = np.array([float(px[ix[cdx]].mean()),
                                float(py[iy[cdy]].mean())])
                arm = float(np.abs(cs.pair_pos[sel] - cen).max())
                if arm <= 0.0:
                    continue
                G = _body_rows(cs, sel, in_a[sel], cen, arm)
                # The moment of the applied load about the part's own centroid,
                # in the same scaled units as the two forces.  With no net load
                # the whole resultant is zero — the placement is then driven by
                # the overlap alone (see above), and a moment read off an
                # eigenstrain's rounding is not a load.
                mz = (float(((px[iy[cdy]] - cen[0]) * f_step[iy][cdy]).sum()
                            - ((py[ix[cdx]] - cen[1]) * f_step[ix][cdx]).sum())
                      if loaded else 0.0)
                Q = (np.array([fx, fy, mz / arm]) if loaded else np.zeros(3))
                u_ = uni[sel]
                a_seat, hit_cap = _seat_placement(
                    gap[sel][u_], G[u_], Q, kc,
                    travel_cap - seat_travel.get(key, 0.0))
                t = float(np.hypot(a_seat[0], a_seat[1]))
                if hit_cap:
                    part_nm = _component_part(cs, lab, elem_v0, comp)
                    if part_nm not in seat_capped:
                        seat_capped.append(part_nm)
                    log.warning("contact seating: %s would have to move more "
                                "than %.3g mm to find a surface — past the cap",
                                part_nm, travel_cap * 1e3)
                    continue
                if float(t + abs(a_seat[2])) <= tol_g:
                    # It is already resting on its supports; the ordinary rule
                    # below closes those pairs, and a "seating" of zero would put
                    # a line on the panel about a part that never moved.
                    continue
                # -- 4. apply it as a gap offset + a rigid motion --------------
                dgap = G @ a_seat
                g0[sel] += dgap
                gap[sel] += dgap
                th = a_seat[2] / arm
                u_seat[ix[cdx]] += a_seat[0] - th * (py[ix[cdx]] - cen[1])
                u_seat[iy[cdy]] += a_seat[1] + th * (px[iy[cdy]] - cen[0])
                seat_travel[key] = seat_travel.get(key, 0.0) + t + abs(a_seat[2])
                seat_steps[key] = seat_steps.get(key, 0) + 1
                moved_pairs |= cross
                # Land on the pairs the placement actually brought into contact,
                # to the placement's own accuracy and NOT a micron more.  The
                # penalty spring is two-sided — the energy is ½k(Bu + g0)², not
                # ½k·max(0, ...)² — so a pair shut across a gap of g PULLS the
                # parts together with k·g.  The first version of this line used a
                # hundredth of the travel as the band, because a translation could
                # only ever put ONE pair at zero and a flat lip had to be helped;
                # on the G2 that band is 1.1 µm, i.e. a phantom force of 5.4 MN
                # per metre of stack against a magnet whose whole centrifugal load
                # is 7.6 kN/m.  The rigid-body placement needs no help: a part
                # resting on unilateral springs comes out touching on three faces
                # at once by construction, which is what the tolerance below sees.
                tol_seat = max(tol_g, 1e-6 * (t + abs(a_seat[2])))
                # `cand` is already every crossing pair when the part was
                # placed by its overlap rather than by a load (see above), so
                # it is the landing set in both cases.  (`cand | ~loaded` on a
                # Python bool made an int64 array and refused the |= — the bug
                # that took every seating solve down on 2026-09-09.)
                land_now |= cross & uni & (gap <= tol_seat) & cand
                # The joint it came to rest ON is the facing pair it is now
                # pressing hardest into — measured AFTER the motion, because the
                # placement is no longer "as far as the first pair allows".
                _ci = np.nonzero(cand)[0]
                lands_on = int(_ci[int(np.argmin(gap[_ci]))])
                dv = (np.array([a_seat[0] / t, a_seat[1] / t]) if t > 0.0 else d)
                rec = seated.setdefault(key, {})
                rec.update({
                    "part": _component_part(cs, lab, elem_v0, comp),
                    "component_id": key,
                    "travel_m": seat_travel[key],
                    "travel_rel_m": _seat_travel_rel(
                        cs, u, u_seat, np.nonzero(land_now & cross)[0],
                        in_a, lands_on),
                    "direction": [float(dv[0]), float(dv[1])],
                    "spin_urad": float(th) * 1e6,
                    "landed_on": cs.interfaces[int(cs.pair_iface[lands_on])].label,
                    "n_pairs_closed": int((land_now & cross).sum()),
                })
                moved_now = True

        # ── keep the LEAST-VIOLATING iterate (2026-09-09) ───────────────────
        # The G2's wedge at the coupled temperatures does not settle: the loop
        # limit-cycles between a state whose worst violation is 0.02 µm and one
        # whose worst is 100 µm, and which of the thirty it happens to stop on
        # decided whether the magnets read 100 MPa or 680.  A residual that has
        # been measured is a residual that can be chosen by, so the best iterate
        # is kept and returned if the loop never converges.  A converging solve
        # ends on its own last iterate exactly as before — that one has viol = 0.
        # Not on an iteration whose problem the seating has just changed: those
        # gaps belong to a geometry the solve behind them never saw.  Nor on the
        # first, whose active set is the warm start's GUESS and not the answer to
        # any solve — everything closed, which is the bonded rotor v1 was.
        stall += 1
        if it > 1 and not moved_now and viol < best["viol"]:
            stall = 0
            # ``kt_sec`` rides with it (2026-09-09, the load path): the next load
            # step starts from this state, and a stick/slip set restored without
            # the secant stiffnesses that produced it is not that state.
            best.update(viol=viol, it=it, u=u.copy(), lam_n=lam_n.copy(),
                        lam_t=lam_t.copy(), gap=gap.copy(), slip=slip.copy(),
                        closed=closed.copy(), stick=stick.copy(), g0=g0.copy(),
                        u_seat=u_seat.copy(), kt_sec=kt_sec.copy(),
                        hold_mult=np.array(hold_mult))

        # ── active set: a pair is closed iff it is penetrating ──────────────
        # "<=" and not "<": g = 0 with p = 0 satisfies the complementarity
        # condition and is the state of an UNLOADED joint.  With a strict "<"
        # every pair of a rotor at standstill came back OPEN — true in the sense
        # that it carried nothing, and unreadable as a report.
        new_closed = closed.copy()
        new_closed[uni] = gap[uni] <= tol_g
        # A pair that has flipped four times is marginal by definition, and
        # letting it keep flipping is how the loop used to burn thirty solves
        # while |u| moved by 1 %.  Freeze it OPEN, never closed: the normal
        # spring in K is two-sided, so a frozen-CLOSED pair with a positive gap
        # would quietly pull the parts together, which is the one thing a
        # separation contact must never do.  How many were frozen is reported.
        _flip = (new_closed != closed) & uni
        flips += _flip
        flip_amp[_flip] = np.maximum(flip_amp[_flip], np.abs(gap[_flip]))
        # …and MARGINAL means the gap it is flipping across is nothing, not just
        # that it has flipped (2026-09-09).  "Four flips" was the whole test
        # until the G2's wedge, where a pair flips because the magnet is
        # genuinely travelling: 1282 of 2044 pairs came out frozen with 47 µm of
        # iron standing inside the magnets, reported as a joint that had opened.
        # A pair whose gap swings by a tenth of a micron carries nothing on
        # either side of the line and can be held open honestly; one swinging by
        # forty microns is a moving part and keeps its vote.
        frozen |= uni & (flips > FREEZE_FLIPS) & (flip_amp <= freeze_margin)
        new_closed[frozen] = False
        if moved_now:
            # A part that has just MOVED carries a flip history about a place it
            # is no longer in; kept, it arrives at the new one already frozen
            # open.  Its landing pairs are then shut explicitly, because
            # `gap <= tol_g` on its own would rest a flat lip on the single
            # closest node instead of on the whole face.
            flips[moved_pairs] = 0
            frozen[moved_pairs] = False
            new_closed[land_now] = True

        # ── the Coulomb cone, as a secant stiffness ─────────────────────────
        # F_t = min(kc*|s|, mu*Fn) * sign(s)  ==>  k_t_eff = min(kc, mu*Fn/|s|).
        # Put in the MATRIX, not on the right-hand side: a positive, bounded
        # stiffness cannot flip sign between iterations, which is what made the
        # v2 return mapping diverge the moment mu stopped being zero.
        new_stick = stick.copy()
        fr = uni & new_closed & (muc > 0)
        if fr.any():
            fn = np.maximum(-lam_n, 0.0)
            cone = muc * fn                       # the largest force allowed
            kt_new = np.full(npair, kc)
            s_abs = np.abs(slip)
            on_cone = fr & (kc * s_abs > cone)
            kt_new[on_cone] = cone[on_cone] / np.maximum(s_abs[on_cone], 1e-18)
            kt_sec = np.where(fr, np.minimum(kt_new, kc), kc)
            new_stick = ~on_cone

        # ── has anything that MATTERS changed? (2026-09-09) ──────────────────
        # The test used to be "the closed set and the stick FLAGS both repeat",
        # and the second half of that is stricter than the physics.  The secant
        # law is continuous at the cone — a pair exactly on it transmits mu*F_n
        # whether it is called stuck or slipping — so a flag that flips there
        # changes the answer by nothing at all.  On the hot G2-L40 with the
        # torque alone that was the whole of the disagreement: every load step
        # after the first satisfied the normal complementarity EXACTLY
        # (residual 0.000 µm) and was still reported as "did not settle", which
        # cost the torque path its verdict for no reason a reader could see.
        #
        # So the flags get a second chance, and it is asked of the FORCE they
        # carry: if the largest tangential force on the joint has stopped moving
        # against the cone that bounds it, the tangential state has settled
        # whatever the labels say.  The NORMAL set still has to repeat exactly —
        # that one is not a label, it is which springs are in the matrix.
        _frm = uni & closed & (muc > 0)
        _cone_max = (float((muc * np.maximum(-lam_n, 0.0))[_frm].max())
                     if _frm.any() else 0.0)
        ft_rel = (float(np.abs(lam_t - lam_t_prev)[_frm].max()) / _cone_max
                  if _cone_max > 0.0 else 0.0)
        set_same = (np.array_equal(new_closed, closed)
                    and (np.array_equal(new_stick, stick)
                         or ft_rel <= FT_SET_TOL))
        du = float(np.linalg.norm(u - u_prev)) / max(float(np.linalg.norm(u)), 1e-30)
        if log.isEnabledFor(logging.DEBUG):
            # The whole state of the iteration on one line: it is what every
            # diagnosis of this loop has needed (2026-09-09), and reading it off
            # a debug log beats adding a print each time.
            _pen = _bad & ~closed
            log.debug("contact it %d (step %d/%d λ %.3f): closed %d (%+d), "
                      "regraded %d of which %d overlapping (worst %.3g µm), "
                      "viol %.4g µm, du %.3g, moved %s, frozen %d",
                      it, step, n_steps, lambdas[step - 1],
                      int(new_closed.sum()), int((new_closed != closed).sum()),
                      int(_bad.sum()), int(_pen.sum()),
                      (-gap[_pen].min() * 1e6) if _pen.any() else 0.0,
                      viol * 1e6, du, moved_now, int(frozen.sum()))
        closed, stick = new_closed, new_stick

        if moved_now:
            # The problem itself has changed, so neither "the set repeated" nor
            # "the solution stopped moving" may end the loop on this pass, and
            # the cycle memory is about a configuration that no longer exists.
            # Neither may the stall counter: an iteration that MOVED a part is
            # not an iteration that failed to improve, and counting it towards
            # ``SET_STALL`` ended the step while parts were still landing — on
            # the hot G2 with the torque alone that left 26 of 28 magnets
            # unseated (2026-09-09).
            set_same = False
            du = 1.0
            stall = 0
            seen.clear()

        # Converged when the active set repeats, or when the pairs that still
        # flip no longer move the solution — the pairs on the boundary carry
        # ~zero force, so which side of it they sit on is not a result.
        state = np.packbits(np.concatenate([closed, stick])).tobytes()
        if set_same or (it_step > 1 and du < 1e-9):
            converged = True
        elif state in seen:
            # Cycling between two configurations.  It happens when a part has
            # no load path at all — its displacement is whatever the rigid-body
            # border left, so the pairs it "penetrates" move every step.  Stop:
            # another twenty solves buys nothing, and ``free_parts`` below says
            # what the answer actually is.
            log.warning("contact active set cycled at iteration %d", it)
        elif it_step < max_iter and not (best["it"] and stall >= SET_STALL):
            seen.add(state)
            continue                     # …this load step is not finished

        # ── the load step is finished ───────────────────────────────────────
        # Its answer is the last iterate if it settled and the LEAST-VIOLATING
        # one it saw if it did not (see where ``best`` is filled): on a solve
        # that ran to its cap that is the difference between quoting the
        # algebra's worst moment and quoting its closest approach to a solution.
        # It matters twice over now — this state is also what the NEXT load step
        # starts from, so handing on the worst iterate would walk the error up
        # the ramp.
        if not converged and best["it"] and best["viol"] < viol:
            log.info("contact: iteration %d violated by %.3g µm against %.3g µm "
                     "at the cap — reporting iteration %d",
                     best["it"], best["viol"] * 1e6, viol * 1e6, best["it"])
            u, lam_n, lam_t = best["u"], best["lam_n"], best["lam_t"]
            gap, slip = best["gap"], best["slip"]
            closed, stick = best["closed"], best["stick"]
            g0, u_seat, hold_mult = best["g0"], best["u_seat"], best["hold_mult"]
            kt_sec = best["kt_sec"]
            viol = best["viol"]
        if not converged and viol <= freeze_margin:
            # The active set never repeated — but the iterate being RETURNED
            # satisfies the complementarity conditions to within the penalty
            # method's own penetration (``FREEZE_MARGIN_FRAC``, 73 nm on the G2,
            # against the 0.03 nm its springs deflect under load).  Every closed
            # pair is compressed and every open one is apart; there is nothing
            # left for another solve to find.  Reporting that as "did not
            # settle" cost the hot G2's torque path its verdict on an answer
            # whose residual was 0.000 µm (2026-09-09).
            converged = True
        step_res.append(0.0 if converged else float(viol))
        if step >= n_steps:
            break
        # ── on to the next increment ────────────────────────────────────────
        # What carries: the contact state (``closed``, ``stick``), the Coulomb
        # secant stiffnesses ``kt_sec``, the seating offsets ``g0`` / ``u_seat``
        # and the travel each part has already used.  That IS the load path —
        # the next increment starts where this one physically ended.
        # What does not: the flip history, which is a judgement about a
        # configuration the load has just changed — a pair frozen open because it
        # was marginal at 2/6 of the torque would otherwise arrive at 3/6 with
        # its vote already taken away.
        #
        # ``seat_steps`` is deliberately NOT reset.  ``SEAT_MAX_STEPS`` is the
        # guard against a part that keeps needing to be moved (it is oscillating,
        # and the travel cap should not be the only thing that stops it), and a
        # budget refreshed at every increment is no guard at all: on the hot G2
        # with the torque alone — no centrifugal clamp, so nothing holds the
        # magnet down at all — the magnets took eight moves per increment and
        # walked 145 µm through a pocket with 15 µm of clearance.
        step += 1
        it_step = 0
        f_step = f + lambdas[step - 1] * f_ramp
        converged = False
        viol = 0.0
        seen.clear()
        best = {"viol": math.inf, "it": 0}
        stall = 0
        flips[:] = 0
        flip_amp[:] = 0.0
        frozen[:] = False

    # ── what each pair ended up transmitting TANGENTIALLY ────────────────────
    # Written 2026-09-07 with the torque load: "is this joint holding" needs a
    # per-pair state, not just open/closed.  `lam_t` already comes out of the
    # secant spring, so it is on or inside the cone by construction — clamped
    # anyway, because a loop that stopped at its iteration cap must never report
    # a friction force Coulomb would not allow.
    fn_final = np.maximum(-lam_n, 0.0)
    slipping = uni & closed & (muc > 0) & ~stick
    cone_final = muc * fn_final
    clamp = uni & closed & (muc > 0)
    lam_t[clamp] = np.clip(lam_t[clamp], -cone_final[clamp], cone_final[clamp])

    slip_state = np.zeros(npair, dtype=np.int8)          # ST_OPEN
    slip_state[closed] = ST_STICK
    slip_state[slipping] = ST_SLIP
    # A frictionless separation pair is held tangentially by TANGENT_REG only —
    # 1e-6 of the contact stiffness — so it transmits nothing and is slipping by
    # construction.  Calling it "stuck" would read as a load path that is not
    # there, which is the whole question the torque load was added to answer.
    slip_state[uni & closed & (muc <= 0)] = ST_SLIP
    # `sliding` (Fusion's "no separation"): normal tied, tangent free.
    slip_state[(~uni) & (~cs.pair_tie_tangent)] = ST_SLIP

    hold_torque = float((hold_mult * hold.arm).sum()) if (hold is not None
                                                          and n_hold) else 0.0

    lab = _components(cs, closed, nv)
    main = np.bincount(lab).argmax()
    free_parts: List[str] = []
    for pid, name in PART_NAMES.items():
        m = cs.part_tri == pid
        if not m.any():
            continue
        vs = np.unique(cs.mesh.t.T[m])
        if not (lab[vs] == main).any():
            free_parts.append(name)
    # v1's number, deliberately: |R^T f| / |f| for the THREE rigid modes of the
    # whole rotor.  It answers "is the load self-equilibrated", which is a
    # geometry sanity check and is ~1e-15 on any real rotor whatever the contact
    # does.  Whether a PART has a load path is a different question and is
    # answered by ``free_parts`` — mixing the two into one number made a
    # perfectly healthy mesh read 0.21 the moment a magnet was let go.
    # On a SECTOR the rigid modes are not three but one: a wedge's centrifugal
    # load is deliberately NOT self-equilibrated in translation — the missing
    # 27/28 of the rotor is what balances it, and the cyclic ties are what say
    # so.  Asking the three-mode question there returns ~0.5 on a perfectly
    # healthy model, so the question asked is the one the sector can answer:
    # is the load self-equilibrated in the modes the ties LEAVE alive
    # (2026-09-09).
    R_all, _ = _rigid_basis(basis, np.zeros(nv, dtype=np.int64), cs.mesh,
                            cyclic_vertices=cyc_vertices)
    # With a held boundary the load is DELIBERATELY not self-equilibrated (a
    # torque has to be reacted somewhere), so the residual is only a geometry
    # check when nothing is held.  Reported as 0 rather than as a large number
    # that would read as a broken mesh.
    # …and it is asked of the FULL load, not of the last increment's: the walk
    # up the ramp is a solution technique, the question "is this load
    # self-equilibrated" is about the machine (2026-09-09).
    f_full = f + f_ramp if _ramp else f
    rigid_res = (0.0 if (hold is not None and n_hold) else
                 float(np.abs(R_all.T @ f_full).max()
                       / max(float(np.linalg.norm(f_full)), 1e-30)))

    # The RETURNED displacement carries the seating travel: the map has to show
    # the magnet where it now is, and a rigid translation of a whole component
    # adds no strain anywhere (every element sits inside exactly one component),
    # so nothing downstream of `recover_stress` sees it as a deformation.  The
    # solve's own `u` is kept out of it above, or the gaps would double-count.
    return ContactSolution(
        u=u + u_seat, lam_n=lam_n, force_n=-lam_n, force_t=lam_t, gap=gap,
        slip=slip,
        closed=closed, stick=stick, iterations=it, converged=converged,
        n_components=int(np.unique(lab).size), free_parts=free_parts,
        rigid_residual=rigid_res,
        max_penetration=float(max(-gap[uni].min(), 0.0)) if uni.any() else 0.0,
        n_frozen=int(frozen.sum()),
        residual=(0.0 if converged else float(viol)),
        slip_state=slip_state,
        seated=[seated[k] for k in sorted(seated)],
        seating_capped=seat_capped,
        u_seat=u_seat, pair_g0=g0,
        load_steps=n_steps, step_residuals=[float(x) for x in step_res],
        hold_mult=(hold_mult if n_hold else None),
        cyclic_mult=(cyc_mult if n_cyc else None),
        hold_torque_n_m_per_m=hold_torque)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def facet_report(cs: ContactSystem, sol: ContactSolution
                 ) -> Dict[str, Dict[str, np.ndarray]]:
    """Per-facet contact pressure (Pa), open fraction and gap (m), per interface.

    The pressure is SIGNED: positive is compression, negative is the tension a
    ``bonded`` or ``sliding`` tie is holding (a ``separation`` pair can never
    report a negative pressure — that is the whole point of it).
    """
    F = np.asarray(cs.W_force @ sol.force_n).reshape(-1)  # N/m per facet
    L = np.maximum(cs.facet_length, 1e-30)
    press = F / L
    openw = np.asarray(cs.W_gap @ (~sol.closed).astype(float)).reshape(-1)
    gapf = np.asarray(cs.W_gap @ np.maximum(sol.gap, 0.0)).reshape(-1)
    # 2026-09-07: the tangential state, averaged onto the facets the same way
    # `open` is, so "how much of this joint is sliding" is a length fraction and
    # not a node count.  open + slip + stick = 1 on every facet.
    st = (sol.slip_state if sol.slip_state.size == sol.closed.size
          else np.where(sol.closed, ST_STICK, ST_OPEN).astype(np.int8))
    slipw = np.asarray(cs.W_gap @ (st == ST_SLIP).astype(float)).reshape(-1)
    stickw = np.asarray(cs.W_gap @ (st == ST_STICK).astype(float)).reshape(-1)
    # tangential force per facet, N/m -> Pa; signed along the facet tangent
    Ft = np.asarray(cs.W_force @ sol.force_t).reshape(-1)
    out: Dict[str, Dict[str, np.ndarray]] = {}
    for it in cs.interfaces:
        if it.n_facets == 0:
            continue
        sl = slice(it.facet0, it.facet0 + it.n_facets)
        out[it.label] = {"pressure": press[sl], "open": openw[sl],
                         "gap": gapf[sl], "length": cs.facet_length[sl],
                         "slip": slipw[sl], "stick": stickw[sl],
                         "shear": (Ft / L)[sl]}
    return out


def interface_forces(cs: ContactSystem, sol: ContactSolution
                     ) -> Dict[str, Dict[str, float]]:
    """Net force each interface transmits, in polar components (N/m of stack).

    Sign convention: the force ON THE A SIDE.  A magnet held down by the bridge
    above it therefore reports a NEGATIVE (inward) radial force — that inward
    reaction is exactly what balances its centrifugal load, and its magnitude is
    the share of the retention this surface carries.
    """
    n = cs.pair_n
    tang = np.stack([-n[:, 1], n[:, 0]], axis=1)
    # force on the A side = lam*n (normal) + friction (already opposing slip)
    fa = sol.lam_n[:, None] * n
    if np.any(sol.force_t):
        fa = fa + sol.force_t[:, None] * tang
    r = cs.pair_pos
    rn = np.maximum(np.linalg.norm(r, axis=1), 1e-30)
    er = r / rn[:, None]
    et = np.stack([-er[:, 1], er[:, 0]], axis=1)
    fr = np.einsum("ij,ij->i", fa, er)
    ft = np.einsum("ij,ij->i", fa, et)
    # Moment about z of the force on the A side, N·m per metre of stack: this is
    # the torque this interface actually carries, and on a spoke rotor whose
    # bridges have yielded it is the whole question (user 2026-09-07).
    mz = r[:, 0] * fa[:, 1] - r[:, 1] * fa[:, 0]
    out: Dict[str, Dict[str, float]] = {}
    for k, it in enumerate(cs.interfaces):
        if it.n_pairs == 0:
            continue
        m = cs.pair_iface == k
        radial_face = np.abs(np.einsum("ij,ij->i", n, er)) > 0.7
        out[it.label] = {
            "radial_n_per_m": float(fr[m].sum()),
            "tangential_n_per_m": float(ft[m].sum()),
            "torque_n_m_per_m": float(mz[m].sum()),
            "radial_on_radial_faces_n_per_m": float(fr[m & radial_face].sum()),
            "radial_on_side_faces_n_per_m": float(fr[m & ~radial_face].sum()),
        }
    return out
