"""The CKEditor 4 test bench page is served only when test_page.enabled is set."""

from __future__ import annotations

from fastapi.testclient import TestClient

from patent_converter.main import create_app
from tests.conftest import make_settings


def test_test_page_is_off_by_default(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        assert c.get("/test/ckeditor4").status_code == 404


def test_test_page_served_when_enabled(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, test_page={"enabled": True}))) as c:
        r = c.get("/test/ckeditor4")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-store"
        body = r.text
        # Calls the same legacy routes OutSystems uses, from the same origin.
        assert "/word_to_json" in body and "/convert_word_to_html" in body
        assert "english_file" in body and "arabic_file" in body
        assert "cdn.ckeditor.com" in body
