"""GET /api/controller/last answers with the last solve of the CALLER's workspace, never another account's."""
from __future__ import annotations

import pytest

from motor_ai_sim import workspace as WS
from motor_ai_sim.routes import controller as C

A, B = "a@example.test", "b@example.test"


@pytest.fixture(autouse=True)
def _ws(monkeypatch, tmp_path):
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    yield


def _as(email):
    class _Both:
        def __enter__(self):
            self.cms = [WS.use_workspace(WS.workspace_for_identity(email)), WS.use_caller({"id": email, "is_admin": False})]
            for c in self.cms:
                c.__enter__()

        def __exit__(self, *a):
            for c in reversed(self.cms):
                c.__exit__(*a)
    return _Both()


def test_each_workspace_reads_only_its_own_last_solve():
    with _as(A):
        assert C.get_last() == {}
        C._LAST["last"] = {"v_dc": 400, "who": "a"}
    with _as(B):
        assert C.get_last() == {}                                    # A's solve is not B's
        C._LAST["last"] = {"v_dc": 800, "who": "b"}
    with _as(A):
        assert C.get_last() == {"v_dc": 400, "who": "a"}
    with _as(B):
        assert C.get_last() == {"v_dc": 800, "who": "b"}


def test_the_answer_is_a_copy():
    with _as(A):
        C._LAST["last"] = {"x": 1}
        got = C.get_last()
        got["x"] = 2
        assert C.get_last() == {"x": 1}


def test_no_workspace_layering_keeps_the_single_process_behaviour(monkeypatch):
    monkeypatch.delenv(WS.ENV_WORKSPACES_ROOT, raising=False)
    C._LAST["last"] = {"v_dc": 12}
    assert C.get_last() == {"v_dc": 12}
    C._LAST.pop("last", None)
    assert C.get_last() == {}
