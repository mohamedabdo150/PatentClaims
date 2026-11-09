import pytest

from patent_converter.analyzer.docx_analyzer import DocxAnalyzer
from patent_converter.extraction.claim_extractor import extract_claims
from patent_converter.extraction.model_builder import ContentPolicy
from patent_converter.validation.integrity import check_extraction


@pytest.mark.parametrize("key,lang,profile", [
    ("en", "en", "synthetic_profile"), ("ar", "ar", "synthetic_profile"), ("ar_reversed", "ar", "synthetic_profile"),
    ("en_auto", "en", "example_profile"), ("ar_auto", "ar", "example_profile"), ("en_extra", "en", "example_profile"),
])
def test_extraction_matches_analyzer_exactly(settings, docs, key, lang, profile, request):
    prof = request.getfixturevalue(profile)
    rep = DocxAnalyzer(settings, prof).analyze(docs[key], lang)
    doc = extract_claims(docs[key], lang, prof, settings, ContentPolicy())
    assert [c.number for c in doc.claims] == rep["claims"]["numbers"]
    assert check_extraction(rep, doc.claims) == []
    assert not [i for c in doc.claims for i in c.issues if i.severity == "error"]


def test_edge_case_errors_are_classified(settings, synthetic_profile, docs):
    doc = extract_claims(docs["edge"], "en", synthetic_profile, settings, ContentPolicy())
    codes = {(c.number, i.code) for c in doc.claims for i in c.issues if i.severity == "error"}
    assert ("1", "TRACKED_CHANGES") in codes and ("2", "LINKED_IMAGE") in codes
    first = "".join(getattr(i, "text", "") for i in doc.claims[0].blocks[0].inlines)
    assert "α-pinene" in first and "β-carotene" in first  # Symbol font mapped
