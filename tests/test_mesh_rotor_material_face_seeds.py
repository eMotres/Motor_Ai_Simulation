# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Every rotor iron PSLG face receives its requested mesh-size region."""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Point, Polygon

from motor_ai_sim.simulation import geo_mesh as gm
from motor_ai_sim.simulation.geo_mesh_gmsh import _faces


def test_sector_builder_seeds_partitioned_iron_faces_only(monkeypatch):
    rotor = Polygon([(2.0, 2.0), (6.0, 2.0), (6.0, 6.0), (2.0, 6.0)])
    magnet = Polygon([(3.8, 2.0), (4.2, 2.0), (4.2, 6.0), (3.8, 6.0)])
    captured = {}

    def capture_regions(vertices, segments, area, quality=gm._Q, **kwargs):
        captured["vertices"] = np.asarray(vertices)
        captured["segments"] = np.asarray(segments)
        captured["regions"] = np.asarray(kwargs["regions"])
        return np.empty((0, 2)), np.empty((0, 3), dtype=np.int64)

    monkeypatch.setattr(gm, "_triangulate", capture_regions)
    gm._mesh_rotor_sector(
        {"rotor": rotor, "magnets": [(magnet, 1)], "shaft": None},
        r_od=9.0, r_shaft=0.5, n_slip=48, span=math.pi / 2,
        area=0.5, air_mm=1.0, quality=20,
        part_area={"rotor": 0.25, "magnet": 0.125},
    )

    faces = _faces(captured["vertices"], captured["segments"])
    regions = captured["regions"]
    iron_rows = regions[regions[:, 2] == 5]
    iron_points = [Point(float(row[0]), float(row[1])) for row in iron_rows]
    expected = [face for face in faces
                if rotor.covers(face.representative_point())
                and not magnet.covers(face.representative_point())]
    assert len(expected) == len(iron_rows) == 2
    for face in expected:
        assert sum(face.contains(point) for point in iron_points) == 1
    assert all(row[3] == 0.25 for row in iron_rows)
    assert all(rotor.contains(point) and not magnet.covers(point)
               for point in iron_points)
    assert np.count_nonzero(regions[:, 2] == 7) == 1  # shaft-air marker remains.
