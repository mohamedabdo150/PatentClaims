import json

import pytest

from patent_converter.analyzer.bilingual import analyze_pair
from patent_converter.analyzer.capability_report import render_text
from patent_converter.analyzer.docx_analyzer import DocxAnalyzer


def risk_codes(rep):
    return {r["code"] for r in rep["risks"]}


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_full_fixture_structures(settings, synthetic_profile, docs, lang):
    rep = DocxAnalyzer(settings, synthetic_profile).analyze(docs[lang], lang)
    assert rep["status"] == "accepted"
    c = rep["structure"]["counts"]
    assert c["tables"] == 3 and c["nested_tables"] == 1
    assert c["merged_cells_horizontal"] == 1 and c["merged_cells_vertical"] == 2
    assert c["drawing_inline"] == 8 and c["drawing_anchor"] == 3
    assert c["drawing_groups"] == 1 and c["textboxes"] == 2
    assert c["equations"] == 4 and c["equation_paragraphs"] == 1
    assert c["ole_objects"] == 1 and c["vml_imagedata"] == 2
    assert c["hyperlinks"] == 1 and c["svg_blips"] == 1
    assert rep["structure"]["unknownElements"] == {}
    assert rep["media"]["formats"] == {"png": 3, "jpeg": 1, "gif": 1, "tiff": 1, "emf": 2, "wmf": 1, "svg": 1}
    assert all(not i["issues"] for i in rep["media"]["images"])
    cl = rep["claims"]
    assert cl["numbers"] == ["1", "2", "3", "4", "5", "6"] and not cl["issues"]
    assert rep["media"]["referencedInClaims"] == 10  # every image belongs to a claim
    per = {p["number"]: p for p in cl["perClaim"]}
    assert per["2"]["tables"] == 1 and per["4"]["tables"] == 2 and per["5"]["equations"] == 2
    assert per["3"]["oleObjects"] == 1 and per["6"]["imageReferences"] == 8
    if lang == "ar":
        assert c["rtl_paragraphs"] > 30 and c["rtl_tables"] == 3
        assert rep["unicode"]["document"]["arabic_chars"] > 300
    assert rep["unicode"]["document"]["warning_count"] == 0
    assert {"VECTOR_METAFILES", "OMML_EQUATIONS", "OLE_OBJECTS", "FLOATING_DRAWINGS"} <= risk_codes(rep)
    assert "TIFF_IMAGES" in risk_codes(rep)
    json.dumps(rep, ensure_ascii=False)  # serializable
    assert "DOCX CAPABILITY REPORT" in render_text(rep)


def test_report_contains_no_patent_text(settings, synthetic_profile, docs):
    rep = DocxAnalyzer(settings, synthetic_profile).analyze(docs["en"], "en")
    blob = json.dumps(rep, ensure_ascii=False)
    assert "pharmaceutically acceptable" not in blob and "compound of formula" not in blob


def test_pair_matches_by_number(settings, synthetic_profile, docs):
    r = analyze_pair(settings, synthetic_profile, english=docs["en"], arabic=docs["ar_reversed"])
    assert r["arabic"]["claims"]["numbers"] == ["6", "5", "4", "3", "2", "1"]
    p = r["pairing"]
    assert p["pairable"] and p["matched"] == 6  # reversed order still pairs correctly
    assert any(i["code"] == "NON_SEQUENTIAL_CLAIM_NUMBERS" for i in r["arabic"]["claims"]["issues"])


def test_pair_mismatch_details(settings, synthetic_profile, docs):
    r = analyze_pair(settings, synthetic_profile, english=docs["en"], arabic=docs["ar_missing5"])
    assert r["pairing"]["missingArabicClaims"] == ["5"] and not r["pairing"]["pairable"]


@pytest.mark.parametrize("key,code", [
    ("en_dup3", "DUPLICATE_CLAIM_NUMBERS"),
    ("en_unclosed2", "UNCLOSED_CLAIM"),
    ("en_orphan", "ORPHAN_CONTENT"),
])
def test_extraction_errors(settings, synthetic_profile, docs, key, code):
    rep = DocxAnalyzer(settings, synthetic_profile).analyze(docs[key], "en")
    assert code in {i["code"] for i in rep["claims"]["issues"] if i["severity"] == "error"}


@pytest.mark.parametrize("key,lang", [("en_auto", "en"), ("ar_auto", "ar")])
def test_auto_numbered_claims(settings, example_profile, docs, key, lang):
    rep = DocxAnalyzer(settings, example_profile).analyze(docs[key], lang)
    assert rep["claims"]["numbers"] == ["1", "2", "3", "4", "5", "6"]
    assert rep["numbering"]["claimsNumberedByWordNumbering"] == 6
    assert "CLAIMS_AUTO_NUMBERED" in risk_codes(rep)


def test_example_profile_next_start_mode(settings, example_profile, docs):
    rep = DocxAnalyzer(settings, example_profile).analyze(docs["en_extra"], "en")
    cl = rep["claims"]
    assert cl["numbers"] == [str(n) for n in range(1, 11)] and cl["endMode"] == "next_start"
    assert cl["perClaim"][-1]["closedBy"] == "section_end"


def test_edge_case_risks(settings, synthetic_profile, docs):
    rep = DocxAnalyzer(settings, synthetic_profile).analyze(docs["edge"], "en")
    codes = risk_codes(rep)
    assert {"TRACKED_CHANGES", "LINKED_EXTERNAL_IMAGES", "SYMBOL_FONT_TEXT", "SYMBOL_CHARACTERS", "ALTCHUNK_PRESENT",
            "REPLACEMENT_CHARACTERS", "SUSPECTED_MOJIBAKE", "PRIVATE_USE_CHARACTERS", "ARABIC_PRESENTATION_FORMS",
            "FOOTNOTE_REFERENCES", "COMMENTS_PRESENT", "MISSING_RELATIONSHIP_TARGETS"} <= codes
    # Claim 1 is inside a content control; claim 2's number is a SEQ field result.
    assert rep["claims"]["numbers"] == ["1", "2"]
    assert rep["structure"]["fieldTypes"].get("SEQ") == 1


def test_language_mismatch_detected(settings, synthetic_profile, docs):
    rep = DocxAnalyzer(settings, synthetic_profile).analyze(docs["ar"], "en")
    assert "LANGUAGE_MISMATCH" in risk_codes(rep)


def test_response_estimate_limit(tmp_path, synthetic_profile, docs):
    from tests.conftest import make_settings
    s = make_settings(tmp_path, output={"max_response_size_mb": 0.01})
    rep = DocxAnalyzer(s, synthetic_profile).analyze(docs["en"], "en")
    assert "ESTIMATED_RESPONSE_EXCEEDS_LIMIT" in risk_codes(rep)
