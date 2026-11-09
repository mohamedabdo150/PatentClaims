import zipfile

import pytest

from patent_converter.analyzer.docx_analyzer import DocxAnalyzer
from patent_converter.analyzer.package_analyzer import analyze_package
from tests.conftest import make_settings
from tests.fixtures.docx_builder import DocxBuilder, inline_image, para, png_bytes, run


def finding_codes(rep):
    return {f["code"] for f in rep["package"]["findings"]}


def base_doc(path, **kw):
    b = DocxBuilder()
    b.add(para("CLAIMS"), para(run("1. A claim.")), para("END OF CLAIMS"))
    return b, b.build(path, **kw)


def test_zip_bomb_rejected_before_parsing(tmp_path, synthetic_profile):
    s = make_settings(tmp_path)
    b, path = base_doc(tmp_path / "bomb.docx")
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/media/huge.bin", b"\0" * (50 * 1024 * 1024))
    rep = DocxAnalyzer(s, synthetic_profile).analyze(path, "en")
    assert rep["status"] == "rejected"
    assert "SUSPICIOUS_COMPRESSION_RATIO" in finding_codes(rep)
    assert "structure" not in rep  # nothing parsed


def test_uncompressed_and_entry_limits(tmp_path):
    s = make_settings(tmp_path, security={"max_uncompressed_size_mb": 0.001, "max_zip_entries": 3})
    _, path = base_doc(tmp_path / "d.docx")
    codes = {f.code for f in analyze_package(path, s.security).findings}
    assert {"UNCOMPRESSED_SIZE_EXCEEDED", "TOO_MANY_ZIP_ENTRIES"} <= codes


def test_path_traversal_and_symlink_entries(tmp_path, synthetic_profile):
    s = make_settings(tmp_path)
    _, path = base_doc(tmp_path / "t.docx")
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("../../etc/evil.xml", b"x")
        info = zipfile.ZipInfo("word/link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    rep = DocxAnalyzer(s, synthetic_profile).analyze(path, "en")
    assert rep["status"] == "rejected"
    assert {"UNSAFE_ENTRY_NAME", "SYMLINK_ENTRY"} <= finding_codes(rep)


@pytest.mark.parametrize("payload", [
    b'<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>',
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
    b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
])
def test_xml_entity_attacks_rejected(tmp_path, synthetic_profile, payload):
    s = make_settings(tmp_path)
    b = DocxBuilder()
    path = b.build(tmp_path / "xxe.docx", document_xml=payload)
    rep = DocxAnalyzer(s, synthetic_profile).analyze(path, "en")
    assert rep["status"] == "rejected" and "UNSAFE_XML" in finding_codes(rep)
    assert "root:" not in str(rep)


def test_macro_project_rejected(tmp_path, synthetic_profile):
    s = make_settings(tmp_path)
    b = DocxBuilder()
    b.extra_parts["word/vbaProject.bin"] = b"\xd0\xcf\x11\xe0" + b"\0" * 64
    path = b.build(tmp_path / "m.docx")
    rep = DocxAnalyzer(s, synthetic_profile).analyze(path, "en")
    assert rep["status"] == "rejected" and "MACRO_PROJECT_PRESENT" in finding_codes(rep)


def test_unsafe_relationship_target(tmp_path, synthetic_profile):
    s = make_settings(tmp_path)
    b = DocxBuilder()
    rid = b._rel("http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", "../../../etc/passwd")
    b.add(para("CLAIMS"), para(run("1. A claim."), inline_image(rid, 10, 10, 2)), para("END OF CLAIMS"))
    rep = DocxAnalyzer(s, synthetic_profile).analyze(b.build(tmp_path / "r.docx"), "en")
    assert rep["status"] == "rejected" and "UNSAFE_RELATIONSHIP_TARGET" in finding_codes(rep)


def test_not_a_zip(tmp_path, synthetic_profile):
    s = make_settings(tmp_path)
    p = tmp_path / "x.docx"
    p.write_bytes(b"PK\x03\x04garbage")
    rep = DocxAnalyzer(s, synthetic_profile).analyze(p, "en")
    assert rep["status"] == "rejected" and "INVALID_ZIP" in finding_codes(rep)


def test_missing_main_document(tmp_path, synthetic_profile):
    s = make_settings(tmp_path)
    p = tmp_path / "x.docx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
    rep = DocxAnalyzer(s, synthetic_profile).analyze(p, "en")
    assert rep["status"] == "rejected" and "INVALID_DOCX_STRUCTURE" in finding_codes(rep)


def test_oversized_image_flagged(tmp_path, synthetic_profile):
    s = make_settings(tmp_path, security={"max_image_pixels": 1000})
    b = DocxBuilder()
    rid = b.add_media(png_bytes(100, 100), "png")
    b.add(para("CLAIMS"), para(run("1. A claim:"), inline_image(rid, 10, 10, 2)), para(run("end.")), para("END OF CLAIMS"))
    rep = DocxAnalyzer(s, synthetic_profile).analyze(b.build(tmp_path / "i.docx"), "en")
    assert "IMAGE_PIXEL_LIMIT_EXCEEDED" in {r["code"] for r in rep["risks"]}


def test_external_relationships_never_fetched(tmp_path, synthetic_profile, monkeypatch):
    import socket

    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    s = make_settings(tmp_path)
    b = DocxBuilder()
    rid = b.add_linked_image("http://attacker.example/track.png")
    b.add(para("CLAIMS"), para(run("1. A claim.")), para("END OF CLAIMS"))
    b._rels.append(("rIdX", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate",
                    "http://attacker.example/t.dotm", True))
    rep = DocxAnalyzer(s, synthetic_profile).analyze(b.build(tmp_path / "e.docx"), "en")
    assert rep["ooxml"]["externalNonHyperlinkRelationships"] == 2
    assert rid


def test_excessive_xml_depth_rejected(tmp_path, synthetic_profile):
    s = make_settings(tmp_path)
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    deep = "<w:smartTag>" * 20000 + "</w:smartTag>" * 20000
    doc = f'<w:document {ns}><w:body><w:p>{deep}</w:p></w:body></w:document>'.encode()
    rep = DocxAnalyzer(s, synthetic_profile).analyze(DocxBuilder().build(tmp_path / "deep.docx", document_xml=doc), "en")
    assert rep["status"] == "rejected" and "INVALID_DOCX_STRUCTURE" in finding_codes(rep)
