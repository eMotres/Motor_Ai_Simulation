"""Mail links are one-time credentials: their ids never reach the access log (synthetic ids only)."""
import logging

import pytest

from motor_ai_sim.log_redaction import RedactingFilter, redact

FAKE = "AbCdEf123456_-xyzFAKEID"


@pytest.mark.parametrize("path", [
    f"/api/auth/link/r/{FAKE}",
    f"/api/auth/link/v/{FAKE}",
    f"/api/auth/link/reset?t={FAKE}&lang=en",
    f"/api/auth/link/verify?lang=en&t={FAKE}",
    f"/?reset={FAKE}",
    f"/?lang=en&verify={FAKE}",
])
def test_mail_link_ids_are_redacted(path):
    out = redact(f'GET {path} HTTP/1.1')
    assert FAKE not in out and "[REDACTED]" in out


def test_ordinary_paths_are_untouched():
    for p in ("/api/auth/link/resend", "/api/auth/link/verify", "/api/projects/abc/thread?lang=en"):
        assert redact(f"GET {p} HTTP/1.1") == f"GET {p} HTTP/1.1"


def test_uvicorn_access_record_args_are_redacted_in_place():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                            '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:1", "GET", f"/api/auth/link/r/{FAKE}", "1.1", 200), None)
    RedactingFilter().filter(rec)
    assert FAKE not in rec.getMessage() and len(rec.args) == 5
