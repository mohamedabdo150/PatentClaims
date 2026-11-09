import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from patent_converter.main import create_app
from tests.conftest import TEST_API_KEY, make_settings

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
AUTH = {"X-API-Key": TEST_API_KEY}


def files(en: Path, ar: Path, en_name="en.docx", ar_name="ar.docx", ct=DOCX_CT):
    return {"englishFile": (en_name, en.read_bytes(), ct), "arabicFile": (ar_name, ar.read_bytes(), ct)}


@pytest.fixture()
def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        c.work = tmp_path / "work"
        yield c


def jobs_left(client) -> list:
    d = client.work / "jobs"
    return list(d.iterdir()) if d.exists() else []


def test_health_ready_metrics(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["checks"]["jobSlotAvailable"]
    m = client.get("/metrics")
    assert m.status_code == 200 and "patent_requests_total" in m.text


def test_auth_required(client, docs):
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]))
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]), headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_analyze_endpoint(client, docs):
    r = client.post("/api/v1/patent/claims/analyze", files=files(docs["en"], docs["ar"]),
                    data={"requestId": "req-123"}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requestId"] == "req-123" and r.headers["X-Request-ID"] == "req-123"
    assert body["pairing"]["pairable"] and body["english"]["claims"]["count"] == 6
    assert "pharmaceutically" not in r.text and "صيدلانياً" not in r.text  # no patent text
    assert not jobs_left(client)  # workspace cleaned


def test_convert_success_contract(client, docs):
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]),
                    data={"requestId": "ok-1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body) == ["claims"] and len(body["claims"]) == 6
    assert all(list(c) == ["claimNumber", "claimAr", "claimEn"] for c in body["claims"])
    assert body["claims"][0]["claimAr"].startswith('<div class="pc-claim" dir="rtl" lang="ar">')
    assert r.headers["X-Claims-Count"] == "6" and int(r.headers["X-Response-Bytes"]) == len(r.content)
    assert r.headers["X-Images-Embedded"] == "24" and r.headers["X-Request-ID"] == "ok-1"
    assert not jobs_left(client)
    assert not list((client.work / "responses").iterdir())  # streamed then deleted
    d = client.get("/api/v1/patent/claims/diagnostics/ok-1", headers=AUTH)
    assert d.status_code == 200 and d.json()["english"]["claimsRendered"] == 6
    assert "pharmaceutically" not in d.text
    assert client.get("/api/v1/patent/claims/diagnostics/nope", headers=AUTH).status_code == 404


def test_convert_unsupported_content(client, docs):
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["edge"], docs["ar"]),
                    data={"configProfile": "synthetic-test"}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("CLAIM_EXTRACTION_FAILED", "CLAIM_PAIRING_MISMATCH", "UNSUPPORTED_CONTENT")


def test_convert_output_limit(tmp_path, docs):
    app = create_app(make_settings(tmp_path, output={"max_response_size_mb": 0.01}))
    with TestClient(app) as c:
        r = c.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]), headers=AUTH)
        assert r.status_code == 422 and r.json()["error"]["code"] == "OUTPUT_LIMIT_EXCEEDED"
        assert not list((tmp_path / "work" / "jobs").iterdir())


def test_convert_pairing_mismatch_contract(client, docs):
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar_missing5"]),
                    data={"requestId": "abc"}, headers=AUTH)
    assert r.status_code == 422
    assert r.json() == {"error": {
        "code": "CLAIM_PAIRING_MISMATCH",
        "message": "Arabic and English claim numbers do not match.",
        "requestId": "abc",
        "details": {"missingArabicClaims": ["5"], "missingEnglishClaims": [],
                    "duplicateArabicClaims": [], "duplicateEnglishClaims": []},
    }}


def test_convert_extraction_failure(client, docs):
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en_unclosed2"], docs["ar"]), headers=AUTH)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "CLAIM_EXTRACTION_FAILED"
    assert err["details"]["issues"]["english"][0]["code"] == "UNCLOSED_CLAIM"


def test_validation_errors(client, docs, tmp_path):
    r = client.post("/api/v1/patent/claims/convert", files={"englishFile": ("en.docx", b"x", DOCX_CT)}, headers=AUTH)
    assert r.status_code == 400 and r.json()["error"]["code"] == "MISSING_FILE"
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]),
                    data={"configProfile": "nope"}, headers=AUTH)
    assert r.status_code == 400 and r.json()["error"]["code"] == "UNKNOWN_CONFIG_PROFILE"
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]),
                    data={"allowPartialResult": "true"}, headers=AUTH)
    assert r.status_code == 400 and r.json()["error"]["code"] == "PARTIAL_RESULT_NOT_ENABLED"
    r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]),
                    data={"requestId": "bad id!"}, headers=AUTH)
    assert r.status_code == 400
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    r = client.post("/api/v1/patent/claims/convert", files=files(pdf, docs["ar"], en_name="x.pdf"), headers=AUTH)
    assert r.status_code == 415
    fake = tmp_path / "fake.docx"
    fake.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    r = client.post("/api/v1/patent/claims/convert", files=files(fake, docs["ar"]), headers=AUTH)
    assert r.status_code == 415 and r.json()["error"]["code"] == "ENCRYPTED_OR_LEGACY_DOCUMENT"
    assert not jobs_left(client)


def test_upload_limits(tmp_path, docs):
    app = create_app(make_settings(tmp_path, processing={"max_english_file_size_mb": 0.005,
                                                          "max_arabic_file_size_mb": 1, "max_combined_upload_mb": 1}))
    with TestClient(app) as c:
        r = c.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]), headers=AUTH)
        assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
        big = tmp_path / "big.docx"
        big.write_bytes(b"PK\x03\x04" + b"\0" * (3 * 1024 * 1024))
        r = c.post("/api/v1/patent/claims/convert", files=files(big, docs["ar"]), headers=AUTH)
        assert r.status_code == 413 and r.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_concurrency_limit(client, docs):
    state = client.app.state.app_state
    assert state.limiter.try_acquire()
    try:
        r = client.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]), headers=AUTH)
        assert r.status_code == 429 and r.json()["error"]["code"] == "CONCURRENCY_LIMIT"
        assert client.get("/ready").status_code == 503  # load balancer routes to the other node
    finally:
        state.limiter.release()


def test_rate_limit(tmp_path, docs):
    app = create_app(make_settings(tmp_path, security={"rate_limit_requests_per_minute": 1}))
    with TestClient(app) as c:
        c.post("/api/v1/patent/claims/analyze", files=files(docs["en"], docs["ar"]), headers=AUTH)
        r = c.post("/api/v1/patent/claims/analyze", files=files(docs["en"], docs["ar"]), headers=AUTH)
        assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_timeout_returns_504_and_cleans_up(tmp_path, docs, monkeypatch):
    import patent_converter.pipeline.jobs as jobs
    from patent_converter.security.resource_limits import DeadlineExceeded

    def slow(*a, **k):
        raise DeadlineExceeded()

    monkeypatch.setattr(jobs, "analyze_pair", slow)
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        r = c.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]), headers=AUTH)
        assert r.status_code == 504 and r.json()["error"]["code"] == "PROCESSING_TIMEOUT"
        assert c.app.state.app_state.limiter.active == 0
    assert not list((tmp_path / "work" / "jobs").iterdir())


def test_internal_errors_hide_details(tmp_path, docs, monkeypatch):
    import patent_converter.pipeline.jobs as jobs

    def boom(*a, **k):
        raise RuntimeError("/secret/path claim text leaked")

    monkeypatch.setattr(jobs, "analyze_pair", boom)
    app = create_app(make_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/v1/patent/claims/convert", files=files(docs["en"], docs["ar"]), headers=AUTH)
        assert r.status_code == 500 and "secret" not in r.text
        assert json.loads(r.text)["error"]["code"] == "INTERNAL_ERROR"
        assert c.app.state.app_state.limiter.active == 0
