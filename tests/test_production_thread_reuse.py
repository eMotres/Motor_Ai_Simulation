"""One general thread and one thread per part: asking for a thread that exists returns it, so no message is hidden in a second one."""
from __future__ import annotations

from motor_ai_sim.production import store
from tests.test_production_access import prod_env  # noqa: F401
from tests.test_production_zip import CONFIG, DIE, _create


def test_a_second_create_returns_the_same_thread(prod_env):  # noqa: F811
    _create(prod_env)
    c, h = prod_env["client"], prod_env["admin"]
    url = "/api/production/%s/%s/threads" % (DIE, CONFIG)
    first = c.post(url, json={}, headers=h).json()
    again = c.post(url, json={"part_id": None}, headers=h).json()
    part = c.post(url, json={"part_id": "pt_x"}, headers=h).json()
    part_again = c.post(url, json={"part_id": "pt_x"}, headers=h).json()
    assert first["id"] == again["id"] and part["id"] == part_again["id"] and first["id"] != part["id"]
    stored = store.load_file(DIE, CONFIG, "threads.json")["threads"]
    assert sorted(t["id"] for t in stored) == sorted([first["id"], part["id"]])
