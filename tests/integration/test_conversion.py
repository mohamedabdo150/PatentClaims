import base64
import io
import json
import re
import shutil

import pytest
from PIL import Image

from patent_converter.analyzer.docx_analyzer import DocxAnalyzer
from patent_converter.api.errors import AppError
from patent_converter.html.sanitizer import sanitize
from patent_converter.pairing.bilingual_pairing import assemble_response, pair_claims
from patent_converter.pipeline.converter import convert_document
from patent_converter.security.resource_limits import Deadline
from tests.conftest import make_settings

pytestmark = pytest.mark.skipif(not shutil.which("soffice") or not shutil.which("pdftocairo"),
                                reason="LibreOffice/poppler not installed")
IMG = re.compile(r'<img [^>]*src="data:(image/[a-z+]+);base64,([A-Za-z0-9+/=]+)"')


def convert(settings, profile, path, lang, tmp, strict=False):
    rep = DocxAnalyzer(settings, profile).analyze(path, lang)
    return convert_document(path, lang, profile, settings, rep, tmp / f"w_{lang}_{path.stem}", Deadline(600), strict)


@pytest.fixture(scope="module")
def converted(tmp_path_factory, docs, registry):
    tmp = tmp_path_factory.mktemp("conv")
    s = make_settings(tmp)
    prof = registry.get("synthetic-test")
    en = convert(s, prof, docs["en"], "en", tmp)
    ar = convert(s, prof, docs["ar"], "ar", tmp)
    out = tmp / "response.json"
    size = assemble_response(pair_claims(en, ar), out, 100 * 1024 * 1024)
    return {"en": en, "ar": ar, "response": out, "size": size, "tmp": tmp}


def test_response_contract(converted):
    data = json.loads(converted["response"].read_text(encoding="utf-8"))
    assert list(data) == ["claims"]
    assert [list(c) for c in data["claims"]] == [["claimNumber", "claimAr", "claimEn"]] * 6
    assert [c["claimNumber"] for c in data["claims"]] == ["1", "2", "3", "4", "5", "6"]
    assert converted["size"] == converted["response"].stat().st_size


def test_every_image_embedded_and_decodable(converted):
    data = json.loads(converted["response"].read_text(encoding="utf-8"))
    total = 0
    for c in data["claims"]:
        for html in (c["claimAr"], c["claimEn"]):
            assert "@@A" not in html and "http://" not in html.replace("http://www.w3.org/1998/Math/MathML", "")
            for mime, b64 in IMG.findall(html):
                raw = base64.b64decode(b64, validate=True)
                total += 1
                if mime != "image/svg+xml":
                    Image.open(io.BytesIO(raw)).verify()
                else:
                    assert raw.startswith(b"<svg") and b"script" not in raw
    assert total == 24  # 12 per language
    d = converted["en"].diagnostics
    assert d["imagesEmbedded"] == 12 and d["assets"]["failed"] == 0 and d["drawingsRasterized"] == 1


def test_direction_math_tables(converted):
    data = json.loads(converted["response"].read_text(encoding="utf-8"))
    c5 = data["claims"][4]
    assert c5["claimAr"].startswith('<div class="pc-claim" dir="rtl" lang="ar">')
    assert c5["claimEn"].startswith('<div class="pc-claim" dir="ltr" lang="en">')
    assert c5["claimEn"].count("<math") == 2 and "<mtable>" in c5["claimEn"] and "<msqrt>" in c5["claimEn"]
    c2 = data["claims"][1]
    assert 'rowspan="2"' in c2["claimAr"] and 'colspan="2"' in c2["claimAr"] and "<table" in c2["claimAr"]
    assert "صيدلانياً" in c2["claimAr"] and "pharmaceutically" in c2["claimEn"]
    assert "ΔH" in data["claims"][2]["claimEn"] and "≥" in data["claims"][2]["claimEn"]


def test_final_html_passes_sanitizer(converted):
    for doc in (converted["en"], converted["ar"]):
        for path in doc.claims.values():
            html = path.read_text(encoding="utf-8")
            assert sanitize(html)[1].clean


def test_equation_image_mode(tmp_path, docs, registry):
    s = make_settings(tmp_path, content={"equation_mode": "image"})
    doc = convert(s, registry.get("synthetic-test"), docs["en"], "en", tmp_path)
    d = doc.diagnostics
    assert d["equationsImage"] == 4 and d["equationsMathml"] == 0
    html = doc.claims["5"].read_text(encoding="utf-8")
    assert "<math" not in html and 'class="pc-equation"' in html


def test_unsupported_content_fails_explicitly(tmp_path, docs, registry):
    s = make_settings(tmp_path)
    with pytest.raises(AppError) as exc:
        convert(s, registry.get("synthetic-test"), docs["edge"], "en", tmp_path)
    assert exc.value.code.value == "UNSUPPORTED_CONTENT"
    codes = {e["code"] for e in exc.value.details["english"]}
    assert {"TRACKED_CHANGES", "LINKED_IMAGE"} <= codes


def test_strict_validation_escalates_warnings(tmp_path, docs, registry, monkeypatch):
    from patent_converter.pipeline import converter as conv

    monkeypatch.setattr(conv, "STRICT_ESCALATE", conv.STRICT_ESCALATE | {"OLE_RENDERED_FROM_PREVIEW"})
    s = make_settings(tmp_path)
    prof = registry.get("synthetic-test")
    assert convert(s, prof, docs["en"], "en", tmp_path / "lenient").claims  # warning only
    with pytest.raises(AppError) as exc:
        convert(s, prof, docs["en"], "en", tmp_path / "strict", strict=True)
    assert exc.value.code.value == "UNSUPPORTED_CONTENT"
    assert exc.value.details["english"][0]["code"] == "OLE_RENDERED_FROM_PREVIEW"


def test_output_limit(converted):
    with pytest.raises(AppError) as exc:
        assemble_response(pair_claims(converted["en"], converted["ar"]), converted["tmp"] / "small.json", 10_000)
    assert exc.value.code.value == "OUTPUT_LIMIT_EXCEEDED"
