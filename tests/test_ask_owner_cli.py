"""scripts/ask_owner.py against an in-process stub server and a temporary token file (throwaway token)."""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("ask_owner", Path(__file__).resolve().parents[1] / "scripts" / "ask_owner.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)

TOKEN = "oq_THROWAWAYTOKENforTESTSonly0123456789ab"


class Stub:
    def __init__(self):
        self.seen = []
        self.items = {}
        self.fail_next = 0           # answer this many requests with a 500 first
        self.echo_token_in_error = False
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                stub.seen.append((method, self.path, body, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    return self._send(401, {"detail": "nope"})
                if stub.fail_next:
                    stub.fail_next -= 1
                    return self._send(500, {"detail": "boom " + (self.headers.get("Authorization") if stub.echo_token_in_error else "")})
                if method == "POST" and self.path == "/api/owner-queue/agent/items":
                    if body.get("question") == "bad":
                        return self._send(422, {"detail": "question: bad " + (TOKEN if stub.echo_token_in_error else "")})
                    iid = "oq-%012x" % (len(stub.items) + 1)
                    stub.items[iid] = dict(body, id=iid, state="open", answer=None)
                    return self._send(200, {"id": iid})
                if method == "GET" and self.path.startswith("/api/owner-queue/agent/items?"):
                    return self._send(200, {"items": list(stub.items.values())})
                if self.path.startswith("/api/owner-queue/agent/items/"):
                    iid = self.path.rsplit("/", 1)[-1] if not self.path.endswith("/withdraw") else self.path.split("/")[-2]
                    it = stub.items.get(iid)
                    if it is None:
                        return self._send(404, {"detail": "no such item"})
                    if method == "POST":
                        it["state"] = "withdrawn"
                        return self._send(200, it)
                    return self._send(200, it)
                self._send(404, {})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    s = Stub()
    tf = tmp_path / "tok"
    tf.write_text(TOKEN + "\n")
    for k in ("MOTRES_OWNER_QUEUE_TOKEN_FILE", "MOTRES_API", "MOTRES_SESSION_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MOTRES_OWNER_QUEUE_MIN_INTERVAL_TEST", "0")
    yield s, ["--api", s.url, "--token-file", str(tf), "--session", "US · pump"]
    s.close()


def go(capsys, base, *rest, **kw):
    code = CLI.run(base + list(rest), **kw)
    out, err = capsys.readouterr()
    assert TOKEN not in out and TOKEN not in err and TOKEN[:12] not in out + err
    return code, out, err


def test_missing_token_file_exits_2_with_one_clear_line(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MOTRES_OWNER_QUEUE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    code = CLI.run(["--session", "s", "--token-file", str(tmp_path / "none"), "list"])
    out, err = capsys.readouterr()
    assert code == 2 and out == ""
    assert err.count("\n") == 1 and "no owner-queue token file (looked in:" in err and "the owner creates the token on the Decisions page" in err
    empty = tmp_path / "empty"
    empty.write_text("  \n")
    assert CLI.run(["--session", "s", "--token-file", str(empty), "list"]) == 2
    # default lookup order: env var first, then the per-OS path
    monkeypatch.setenv("MOTRES_OWNER_QUEUE_TOKEN_FILE", str(tmp_path / "viaenv"))
    assert str(tmp_path / "viaenv") in str(CLI.default_token_paths()[0])
    assert any(str(p).endswith("owner_queue.token") for p in CLI.default_token_paths())


def test_token_is_not_a_command_line_argument(env, capsys):
    s, base = env
    with pytest.raises(SystemExit):
        CLI.run(["--token", TOKEN, "--session", "s", "list"])
    capsys.readouterr()


def test_session_name_is_required(env, capsys):
    s, base = env
    code = CLI.run([a for a in base if a not in ("--session", "US · pump")] + ["list"])
    assert code == 2 and "session" in capsys.readouterr().err


def test_ask_options_recommended_and_say(env, capsys, tmp_path):
    s, base = env
    d = tmp_path / "d.md"
    d.write_text("| a | b |\n|---|---|\n| 1 | 2 |\n")
    code, out, _ = go(capsys, base, "ask", "--kind", "decision", "--question", "Which winding?", "--option", "A*", "--option", "B",
                      "--project", "p1", "--blocks", "next run", "--detail-file", str(d))
    assert code == 0 and out.strip().startswith("oq-")
    m, path, body, auth = s.seen[-1]
    assert auth == "Bearer " + TOKEN and path == "/api/owner-queue/agent/items"
    assert body["options"] == [{"label": "A", "recommended": True}, {"label": "B", "recommended": False}]
    assert body["asked_by"] == "US · pump" and body["project_id"] == "p1" and body["detail"].startswith("| a")
    code, out, _ = go(capsys, base, "say", "--title", "Report ready")
    assert code == 0 and s.seen[-1][2]["kind"] == "message" and s.seen[-1][2]["question"] == "Report ready"
    code, out, _ = go(capsys, base, "list")
    assert code == 0 and "Which winding?" in out and "Report ready" in out
    iid = list(s.items)[0]
    code, out, _ = go(capsys, base, "withdraw", iid)
    assert code == 0 and s.items[iid]["state"] == "withdrawn"


def test_detail_over_4000_refused_locally(env, capsys, tmp_path):
    s, base = env
    d = tmp_path / "big.md"
    d.write_text("x" * 4001)
    n = len(s.seen)
    code, out, err = go(capsys, base, "ask", "--kind", "question", "--question", "q", "--detail-file", str(d))
    assert code == 2 and "4000" in err and "do not split" in err and len(s.seen) == n


def test_http_error_never_shows_the_token(env, capsys):
    s, base = env
    s.echo_token_in_error = True
    code, out, err = go(capsys, base, "ask", "--kind", "question", "--question", "bad")
    assert code == 1 and "HTTP 422" in err and "***" in err
    s.fail_next = 1
    code, out, err = go(capsys, base, "list")
    assert code == 1 and "HTTP 500" in err
    bad = base[:]
    bad[bad.index("--api") + 1] = "http://127.0.0.1:9"          # nothing listens: a network error
    code, out, err = go(capsys, bad, "list")
    assert code == 1 and "network error" in err


def test_wait_answered_prints_verbatim_json(env, capsys):
    s, base = env
    iid = CLI.Client(s.url, TOKEN).call("POST", "/api/owner-queue/agent/items", {"kind": "question"})["id"]
    polls = []

    def sleep(sec):
        polls.append(sec)
        if len(polls) == 2:
            s.items[iid].update(state="answered", answer={"option": "B", "text": "because", "by": "o@x.com", "at": "2026-11-25T10:00:00Z"})
    t = [0.0]
    code, out, _ = go(capsys, base, "wait", iid, "--interval", "1", "--timeout", "100", sleep=sleep, clock=lambda: t[0])
    assert code == 0 and len(polls) == 2
    assert json.loads(out) == {"state": "answered", "option": "B", "text": "because", "by": "o@x.com", "at": "2026-11-25T10:00:00Z"}
    s.items[iid].update(state="read", answer={"option": "read", "text": None, "by": "o@x.com", "at": "t"})
    code, out, _ = go(capsys, base, "wait", iid)
    assert code == 0 and json.loads(out)["state"] == "read" and json.loads(out)["option"] == "read"


def test_wait_withdrawn_timeout_and_network_retry(env, capsys):
    s, base = env
    iid = CLI.Client(s.url, TOKEN).call("POST", "/api/owner-queue/agent/items", {"kind": "question"})["id"]
    clock = [0.0]

    def sleep(sec):
        clock[0] += sec
    code, out, err = go(capsys, base, "wait", iid, "--interval", "10", "--timeout", "35", sleep=sleep, clock=lambda: clock[0])
    assert code == 4 and "timeout" in err and clock[0] == 35.0
    s.fail_next = 2                                                  # two failed polls are retried at the next tick
    clock[0] = 0.0
    seq = []

    def sleep2(sec):
        seq.append(sec)
        clock[0] += sec
        if len(seq) == 3:
            s.items[iid]["state"] = "withdrawn"
    code, out, err = go(capsys, base, "wait", iid, "--interval", "5", "--timeout", "100", sleep=sleep2, clock=lambda: clock[0])
    assert code == 3 and err.count("will retry") == 2
    code, out, err = go(capsys, base, "wait", "oq-nonexistent0", sleep=sleep, clock=lambda: clock[0])
    assert code == 1 and "404" in err


def test_wait_interval_has_a_30s_floor(env, capsys, monkeypatch):
    s, base = env
    monkeypatch.delenv("MOTRES_OWNER_QUEUE_MIN_INTERVAL_TEST")
    iid = CLI.Client(s.url, TOKEN).call("POST", "/api/owner-queue/agent/items", {"kind": "question"})["id"]
    got = []
    t = [0.0]

    def sleep(sec):
        got.append(sec)
        t[0] += 1000
    go(capsys, base, "wait", iid, "--interval", "1", "--timeout", "10", sleep=sleep, clock=lambda: t[0])
    assert got == [] or min(got) >= 10                                # the sleep is capped by the time left, never by a faster poll
    got.clear()
    t[0] = 0.0
    go(capsys, base, "wait", iid, "--interval", "1", "--timeout", "100", sleep=sleep, clock=lambda: t[0])
    assert got and got[0] == 30.0


VEC_OUT = ("outside_message", "Hello Zoë, | a | b |", "p@example.test", "f995a1707dab63afe05c785ba308c78b8873c55708ebcf4b392805c25b86ca11")
VEC_CAT = ("catalog_write", "| old | new |", None, "f412c5546d97352f72093510ebe8552b0e6c96f6e424c0fba2127a2c6d1b3bb6")


def test_hash_command_matches_the_shared_vector_and_the_server(env, capsys, tmp_path):
    s, base = env
    for k, d, r, h in (VEC_OUT, VEC_CAT):
        assert CLI.approval_hash(k, d, r) == h
        f = tmp_path / "d.md"
        f.write_text(d, encoding="utf-8")
        args = ["hash", "--kind", k, "--detail-file", str(f)] + (["--recipient", r] if r else [])
        n = len(s.seen)
        code, out, _ = go(capsys, base, *args)
        assert code == 0 and out.strip() == h and len(s.seen) == n              # local only, no request
    from motor_ai_sim import owner_queue as OQ
    for k, d, r, h in (VEC_OUT, VEC_CAT):
        assert OQ.approval_hash(k, d, r) == h
    code, _, err = go(capsys, base, "hash", "--kind", "outside_message", "--detail-file", str(f))
    assert code == 2 and "recipient" in err


def test_ask_outside_message_needs_recipient_and_detail_and_wait_prints_the_hash(env, capsys, tmp_path):
    s, base = env
    f = tmp_path / "m.md"
    f.write_text("Final text")
    code, _, err = go(capsys, base, "ask", "--kind", "outside_message", "--question", "Send?", "--detail-file", str(f))
    assert code == 2 and "--recipient" in err
    code, _, err = go(capsys, base, "ask", "--kind", "catalog_write", "--question", "Write?")
    assert code == 2 and "--detail-file" in err
    code, out, _ = go(capsys, base, "ask", "--kind", "outside_message", "--question", "Send?", "--detail-file", str(f), "--recipient", "a@example.test")
    iid = out.strip()
    assert s.seen[-1][2]["recipient"] == "a@example.test" and s.seen[-1][2]["options"] == []
    s.items[iid].update(state="answered", answer={"option": "Approve", "text": None, "by": "o", "at": "t", "approved_hash": "abc"})
    code, out, _ = go(capsys, base, "wait", iid)
    assert code == 0 and json.loads(out)["approved_hash"] == "abc"
