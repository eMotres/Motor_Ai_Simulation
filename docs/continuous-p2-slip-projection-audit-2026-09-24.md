# Continuous-angle P2 slip coupling: isolated audit (2026-09-24)

The production `SlipProjection.build(m)` welds matched P2 vertices and edge
midpoints at an integer ring shift. It uses a signed union-find for the sector
radial cuts and slip ring. This audit did not change that production path.

An isolated collocation prototype uses one fixed set of reduced coordinates:
first impose the m-independent signed radial-cut constraints, then eliminate
every rotor ring vertex and ring edge-midpoint class. For a rotor trace dof at
slot coordinate `q`, let `x = q + theta / delta`, `j = floor(x mod L)`, and
`u = x mod L - j`, where `L=Nring` for a full ring and `L=Nring-1` for a
sector. The extended sector trace gains `bc_sign**floor(x/L)` on each wrap.
The three stator P2 weights on interval `j` are

```
L0=(1-u)(1-2u),  Lm=4u(1-u),  L1=u(2u-1).
```

The nonzero rows of the prototype `P(theta)` use these weights for **both**
rotor vertices and rotor edge midpoints. Its derivative replaces them by
`(4u-3, 4-8u, 4u-1)/delta`; all other rows have zero derivative. This is an
analytic derivative inside each overlap cell. At a knot, the underlying P2
trace is only C0, so the collocation derivative is generally discontinuous;
the implementation chooses its right-hand value. Its reduced dimension and
outer Dirichlet columns stay fixed. Sector cut vertex and midpoint equations
hold at fractional shifts as well.

Seven isolated tests passed in 0.91 s. At integer shifts across positive and
negative wraps, the prototype and current `build(m)` have the same constraint
space and reduced dimension for full ring and periodic/antiperiodic sectors.
Their column order/sign may differ. At fractional shifts, the analytic
derivative matches a centred finite difference away from knots.

**Collocation is not a production mortar.** When a rotor edge spans a stator
vertex, its three interpolated dofs define one quadratic polynomial while the
stator trace has two quadratic pieces. In the unit-basis test at 0.37 slot
shift, the interior trace jump is **0.0456** at rotor edge coordinate 0.8,
despite exact agreement at the three collocation points. Increasing precision
cannot remove this structural jump. Straight mesh chords also make angular
slot interpolation only a parameterised trace approximation between exact
integer alignments.

A conforming weak coupling needs a one-dimensional L2 mortar map on the
existing slip ring. With rotor trace basis `Nr` and shifted, signed stator
trace basis `Ns(theta)`, form `Mrr = integral Nr^T Nr ds` and
`Mrs(theta) = integral Nr^T Ns(theta) ds` over every rotor/stator edge
intersection, then set rotor trace coefficients to
`Mrr^{-1} Mrs(theta)` times the fixed stator trace coordinates. The same
radial-cut constraints must be included in these bases. `Mrr` is constant;
`Mrs` and its angle derivative must account for changing overlap intervals
and sector wrap signs. This is the next isolated numerical prototype needed
before any production solver change or speed claim.
