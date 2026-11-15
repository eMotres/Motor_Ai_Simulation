"""PUT /api/geometry then GET /api/geometry/mesh2d with the EXACT request of the production session (2026-11-15, 12_40 live test)."""
import os

os.environ["AUTH_ENFORCE"] = "0"
for _k in ("ADMIN_EMAILS", "MOTRES_ADMIN_EMAILS"):
    os.environ.pop(_k, None)

from fastapi.testclient import TestClient

from motor_ai_sim.api import app

client = TestClient(app)

REQ = dict(num_wires_per_slot=2, wire_split=3, wire_width=0.2, wire_height=0.8, wire_spacing_x=0.025, wire_spacing_y=0.05,
           wire_parallel=1, wire_omit=1, wire_bore_margin=0.042, k_end_by_turns=1, magnet_flat=1, magnet_pocket_clearance=0.025,
           rotor_house_height=0.258, allow_die_transition=False)


def _put(**ov):
    return client.put("/api/geometry", json=dict(REQ, **ov))


def test_exact_request_builds_the_two_by_three_grid():
    try:
        for om in (0, 1, 2):
            r = _put(wire_omit=om)
            assert r.status_code == 200, r.text
            g = client.get("/api/geometry").json()
            assert (g["num_wires_per_slot"], g["wire_split"], g["wire_omit"]) == (2, 3, om)
            m = client.get("/api/geometry/mesh2d")
            assert m.status_code == 200, m.text[:300]
    finally:
        _put(wire_omit=0)


def test_a_position_outside_the_grid_is_refused_at_the_put_with_the_numbering():
    r = _put(wire_omit=8)
    assert r.status_code == 422
    txt = r.text
    assert "(0, 3)" in txt and "2 row(s) x 3 strip(s)" in txt and "2**(row*8 + col)" in txt


def test_a_list_is_refused_at_the_put_with_a_named_error():
    r = _put(wire_omit=[[0, 0]])
    assert r.status_code == 422 and "wire_omit" in r.text and "integer" in r.text


def test_mesh_routes_answer_422_not_500_for_an_undrawable_wire_omit():
    import json
    try:
        assert _put(wire_omit=0).status_code == 200
        for bad in (8, 7, 1 + 2 + 4, 256 * 7):          # (0,3) outside; row 0 emptied (1+2+4); row 1 emptied; ...
            geo = json.dumps({"wire_omit": bad})
            m = client.get("/api/geometry/mesh2d", params={"geo": geo})
            if bad == 7 or bad == 256 * 7 or bad == 8:
                assert m.status_code == 422, (bad, m.status_code, m.text[:200])
                assert "wire_omit" in m.text and "grid" in m.text
    finally:
        _put(wire_omit=0)
