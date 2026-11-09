"""Smoke test of an installed service: all legacy routes on http://127.0.0.1:<port>.

    python deploy/windows/smoke_test.py --port 50013 --fixtures fx
Exit code 0 when every check passes. Uses only the standard library + PyMuPDF (for a test PDF).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import uuid
from pathlib import Path


def post(url: str, body: bytes, content_type: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def multipart(fields: dict[str, Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, path in fields.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
                      "Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=50013)
    ap.add_argument("--fixtures", type=Path, required=True)
    ap.add_argument("--expect-model", action="store_true")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    ok = True

    def check(name: str, cond: bool, info: str = "") -> None:
        nonlocal ok
        ok &= cond
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {info}")

    import time
    health = json.loads(urllib.request.urlopen(base + "/health", timeout=30).read())
    check("GET /health", health.get("status") == "OK", json.dumps(health))
    if a.expect_model:  # TensorFlow loads in the background after start-up
        for _ in range(90):
            if health.get("model_loaded"):
                break
            time.sleep(2)
            health = json.loads(urllib.request.urlopen(base + "/health", timeout=30).read())
        check("model loaded", health.get("model_loaded") is True)

    body, ct = multipart({"english_file": a.fixtures / "patent_marker_en.docx",
                          "arabic_file": a.fixtures / "patent_marker_ar.docx"})
    st, res = post(base + "/word_to_json", body, ct)
    claims = res.get("claims", [])
    check("POST /word_to_json (both)", st == 200 and [c["id"] for c in claims] == [1, 2, 3, 4, 5, 6]
          and all(list(c) == ["id", "claim_english", "claim_arabic"] for c in claims), f"status={st}")
    check("images embedded", all("data:image/" in c["claim_english"] for c in claims if c["id"] in (1, 6)))

    body, ct = multipart({"arabic_file": a.fixtures / "patent_marker_ar.docx"})
    st, res = post(base + "/word_to_json", body, ct)
    check("POST /word_to_json (Arabic only)", st == 200 and list(res["claims"][0]) == ["id", "claim_arabic"])

    st, res = post(base + "/convert_word_to_html", (a.fixtures / "patent_en.docx").read_bytes(),
                   "application/octet-stream")
    check("POST /convert_word_to_html", st == 200 and res.get("success") is True and "ABSTRACT" in res["html"])

    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "smoke test")
    page.insert_image(fitz.Rect(72, 100, 200, 200), stream=(a.fixtures / "probe.png").read_bytes())
    pdf = doc.tobytes()
    st, res = post(base + "/pdf-to-images", pdf, "application/pdf")
    check("POST /pdf-to-images", st == 200 and res.get("success") is True and len(res["images"]) == 1)
    st, res = post(base + "/drawing_detection", pdf, "application/pdf")
    if a.expect_model:
        check("POST /drawing_detection", st == 200 and res["results"][0]["page"] == 1, json.dumps(res)[:200])
    else:
        check("POST /drawing_detection (no model)", st == 500 and res.get("detail") == "Model not loaded")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
