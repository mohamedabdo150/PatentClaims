"""Contract tests for the routes of the previous service (patent_claims / AI_API_v2.py)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from patent_converter.main import create_app
from tests.conftest import make_settings

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture()
def client(tmp_path):
    app = create_app(make_settings(tmp_path))  # auth is enabled for /api/v1, not for legacy routes
    with TestClient(app) as c:
        c.work = tmp_path / "work"
        yield c


def legacy_files(**paths):
    return {k: (p.name, p.read_bytes(), DOCX) for k, p in paths.items()}


def assert_utf8_json(r):
    assert r.headers["content-type"] == "application/json; charset=utf-8"


def test_word_to_json_both_files(client, docs):
    r = client.post("/word_to_json", files=legacy_files(english_file=docs["en"], arabic_file=docs["ar"]))
    assert r.status_code == 200, r.text
    assert_utf8_json(r)
    body = r.json()
    assert list(body) == ["claims"]
    assert [list(c) for c in body["claims"]] == [["id", "claim_english", "claim_arabic"]] * 6
    assert [c["id"] for c in body["claims"]] == [1, 2, 3, 4, 5, 6]
    assert body["claims"][0]["claim_arabic"].startswith('<div class="pc-claim" dir="rtl" lang="ar">')
    assert "pharmaceutically" in body["claims"][1]["claim_english"]
    assert not list((client.work / "responses").iterdir())


@pytest.mark.parametrize("field,key", [("english_file", "claim_english"), ("arabic_file", "claim_arabic")])
def test_word_to_json_single_file(client, docs, field, key):
    path = docs["en"] if field == "english_file" else docs["ar"]
    r = client.post("/word_to_json", files=legacy_files(**{field: path}))
    assert r.status_code == 200, r.text
    claims = r.json()["claims"]
    assert [list(c) for c in claims] == [["id", key]] * 6  # the missing language key is omitted, as before


def test_word_to_json_no_file(client, docs):
    r = client.post("/word_to_json", files={"other": ("x.txt", b"x", "text/plain")})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "At least one file must be provided" and body["error"] is True
    empty = client.post("/word_to_json", files={"english_file": ("e.docx", b"", DOCX)})
    assert empty.status_code == 400


def test_word_to_json_pairing_mismatch_legacy_shape(client, docs):
    r = client.post("/word_to_json", files=legacy_files(english_file=docs["en"], arabic_file=docs["ar_missing5"]))
    assert r.status_code == 422
    assert_utf8_json(r)
    body = r.json()
    assert body["error"] is True and body["detail"] == body["message"]
    assert body["code"] == "CLAIM_PAIRING_MISMATCH" and body["details"]["missingArabicClaims"] == ["5"]


def test_word_to_json_timeout_legacy_detail(tmp_path, docs, monkeypatch):
    import patent_converter.pipeline.jobs as jobs
    from patent_converter.security.resource_limits import DeadlineExceeded

    def slow(*a, **k):
        raise DeadlineExceeded()

    monkeypatch.setattr(jobs, "analyze_pair", slow)
    s = make_settings(tmp_path, processing={"max_processing_seconds": 600})
    with TestClient(create_app(s)) as c:
        r = c.post("/word_to_json", files=legacy_files(english_file=docs["en"]))
    assert r.status_code == 504 and r.json()["detail"] == "Timeout after 600s"


def test_word_to_json_waits_for_free_worker(tmp_path, docs):
    s = make_settings(tmp_path, processing={"queue_wait_seconds": 30})
    with TestClient(create_app(s)) as c:
        state = c.app.state.app_state
        assert state.limiter.try_acquire()  # another conversion is running
        threading.Thread(target=lambda: (time.sleep(1.0), state.limiter.release()), daemon=True).start()
        t0 = time.perf_counter()
        r = c.post("/word_to_json", files=legacy_files(english_file=docs["en"]))
        assert r.status_code == 200 and time.perf_counter() - t0 >= 0.9  # queued, not rejected with 429
        # The new API keeps answering 429 immediately.
        assert state.limiter.try_acquire()
        try:
            r2 = c.post("/api/v1/patent/claims/analyze",
                        files={"englishFile": ("e.docx", docs["en"].read_bytes(), DOCX),
                               "arabicFile": ("a.docx", docs["ar"].read_bytes(), DOCX)},
                        headers={"X-API-Key": "test-key-123"})
            assert r2.status_code == 429
        finally:
            state.limiter.release()


def test_legacy_routes_can_require_api_key(tmp_path, docs):
    s = make_settings(tmp_path, legacy={"require_api_key": True})
    with TestClient(create_app(s)) as c:
        assert c.post("/word_to_json", files=legacy_files(english_file=docs["en"])).status_code == 401
        ok = c.post("/word_to_json", files=legacy_files(english_file=docs["en"]), headers={"X-API-Key": "test-key-123"})
        assert ok.status_code == 200


def test_convert_word_to_html_whole_document(client, docs):
    r = client.post("/convert_word_to_html", content=docs["en"].read_bytes(),
                    headers={"content-type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    assert_utf8_json(r)
    body = r.json()
    assert list(body) == ["success", "html"] and body["success"] is True
    html = body["html"]
    assert html.startswith('<div class="pc-document" dir="ltr" lang="en">')
    # Content outside the claims section is included (whole document).
    for text in ("TECHNICAL FIELD", "ABSTRACT", "pharmaceutically", "END OF CLAIMS"):
        assert text in html
    assert html.count("<img") >= 12 and "<table" in html and "<math" in html
    ar = client.post("/convert_word_to_html", content=docs["ar"].read_bytes()).json()["html"]
    assert ar.startswith('<div class="pc-document" dir="rtl" lang="ar">')


def test_convert_word_to_html_errors(client, tmp_path):
    r = client.post("/convert_word_to_html", content=b"%PDF-1.4 not a docx")
    assert r.status_code == 415 and r.json()["error"] is True and "detail" in r.json()
    assert client.post("/convert_word_to_html", content=b"").status_code == 400


def test_health_legacy_format(client):
    r = client.get("/health")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "OK"
    assert {"message", "extractor", "model_loaded", "workers", "timeout", "port"} <= set(body)
    assert body["port"] == 50013
