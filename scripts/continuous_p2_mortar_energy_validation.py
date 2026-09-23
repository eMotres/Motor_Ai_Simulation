"""Conservative algebra oracle for the isolated continuous P2 mortar.

This is not a FEM model.  The full stiffness and source stay fixed as theta
changes, so the exact envelope derivative tests P' without other moving terms.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.sparse import diags

from motor_ai_sim.simulation.p2_projection import SlipProjection
from scripts.continuous_p2_mortar_trace_prototype import TraceMortarProjection


def small_ring(*, full_ring: bool, sign: int, n_ring: int = 6):
    """Independent algebraic rotor/stator P2 traces, with optional signed cuts."""
    n_edges = n_ring if full_ring else n_ring - 1
    pairs = ([(k, (k+1) % n_ring) for k in range(n_edges)]
             if full_ring else [(k, k+1) for k in range(n_edges)])
    facets = np.asarray(
        pairs + [(n_ring+a, n_ring+b) for a, b in pairs], dtype=int).T
    n_vertices = 2*n_ring
    n_dof = n_vertices + 2*n_edges + 4
    if full_ring:
        master = slave = np.array([], dtype=int)
    else:
        master = np.array([0, n_ring])
        slave = np.array([n_ring-1, 2*n_ring-1])
    discrete = SlipProjection(
        n_dof=n_dof, facets=facets, vdof=np.arange(n_vertices),
        fdof=np.arange(n_vertices, n_vertices+2*n_edges),
        rring=np.arange(n_ring), sring=np.arange(n_ring), nsn=n_ring,
        n_ring=n_ring, full_ring=full_ring, bc_sign=sign,
        Mn=master, Sn=slave, dirichlet_dofs=np.array([n_dof-1]))
    spacing_rad = (2*math.pi if full_ring else math.pi/2) / n_edges
    return TraceMortarProjection(discrete, spacing_rad)


def solve_quadratic(mortar, theta_rad, stiffness_diag, source):
    """Minimise 0.5 A^T K A - f^T A over A=P(theta)a."""
    p, dp, _ = mortar.build(theta_rad)
    reduced_k = (p.T @ diags(stiffness_diag) @ p).toarray()
    a = np.linalg.solve(reduced_k, np.asarray(p.T @ source))
    field = np.asarray(p @ a)
    residual = stiffness_diag*field-source
    phi = .5*float(np.dot(field, stiffness_diag*field))-float(source @ field)
    envelope = float(residual @ (dp @ a))
    return dict(phi=phi, coenergy=-phi, torque=-envelope,
                envelope=envelope, a=a, field=field, residual=residual,
                projected_residual=float(np.linalg.norm(p.T @ residual)),
                full_residual=float(np.linalg.norm(residual)))


def solve_quartic(mortar, theta_rad, stiffness_diag, beta_diag, source):
    """Convex diagonal nonlinear energy, solely an algebraic residual oracle."""
    p, dp, _ = mortar.build(theta_rad)
    reduced_k = (p.T @ diags(stiffness_diag) @ p).toarray()
    a = np.linalg.solve(reduced_k, np.asarray(p.T @ source))
    for _ in range(20):
        field = np.asarray(p @ a)
        residual = stiffness_diag*field + beta_diag*field**3-source
        projected = np.asarray(p.T @ residual)
        if np.linalg.norm(projected) < 2e-12:
            break
        hessian = (p.T @ diags(stiffness_diag+3*beta_diag*field**2) @ p).toarray()
        a -= np.linalg.solve(hessian, projected)
    else:
        raise RuntimeError("the convex algebraic oracle did not converge")
    field = np.asarray(p @ a)
    residual = stiffness_diag*field + beta_diag*field**3-source
    projected_norm = float(np.linalg.norm(p.T @ residual))
    if projected_norm >= 2e-12:
        raise RuntimeError("the nonlinear reduced residual did not converge")
    phi = float(np.sum(.5*stiffness_diag*field**2 +
                       .25*beta_diag*field**4) - source @ field)
    envelope = float(residual @ (dp @ a))
    return dict(phi=phi, coenergy=-phi, torque=-envelope,
                envelope=envelope, a=a, field=field, residual=residual,
                projected_residual=projected_norm,
                full_residual=float(np.linalg.norm(residual)))
