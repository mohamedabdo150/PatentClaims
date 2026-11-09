"""Regression tests on the customer's sample documents (NOT committed: confidential).

Run with:  PATENT_SAMPLES_DIR=/path/to/samples python -m pytest tests/regression -q
Only counts, numbers and error codes are asserted; no patent text is stored here.
"""

import os
import shutil
from pathlib import Path

import pytest

from patent_converter.analyzer.docx_analyzer import DocxAnalyzer
from patent_converter.extraction.claim_extractor import extract_claims
from patent_converter.extraction.model_builder import ContentPolicy
from patent_converter.validation.integrity import check_extraction

SAMPLES = Path(os.environ.get("PATENT_SAMPLES_DIR", "/nonexistent"))
pytestmark = pytest.mark.skipif(not SAMPLES.is_dir(), reason="PATENT_SAMPLES_DIR not set")
SEQ = lambda n: [str(i) for i in range(1, n + 1)]  # noqa: E731

EXPECTED = {
    ("CLAIMS.docx", "en"): (SEQ(21), set()),
    ("CLAIMS-English Amendment.docx", "en"): (SEQ(21), set()),
    ("CLclaims.docx", "en"): (SEQ(50), set()),
    ("bigClimsEn.docx", "en"): (SEQ(17), set()),
    # Source defects (PDF conversion): claim 8/11 numbers after their {next}, claim 9 number missing.
    ("Claims - Arabic.docx", "ar"): (None, {"MISSING_CLAIM_NUMBER", "AMBIGUOUS_CLAIM_NUMBER"}),
    ("Spec_E.docx", "en"): ([], {"CLAIMS_SECTION_NOT_FOUND"}),
    ("high tension system Full Description.docx", "en"): ([], {"CLAIMS_SECTION_NOT_FOUND"}),
}


@pytest.mark.parametrize("name,lang", list(EXPECTED))
def test_sample_claims(name, lang, settings, registry):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    prof = registry.get("marker")
    rep = DocxAnalyzer(settings, prof).analyze(path, lang)
    numbers, errors = EXPECTED[(name, lang)]
    assert {i["code"] for i in rep["claims"]["issues"] if i["severity"] == "error"} == errors
    if numbers is not None:
        assert rep["claims"]["numbers"] == numbers
    if not errors:
        doc = extract_claims(path, lang, prof, settings, ContentPolicy())
        assert check_extraction(rep, doc.claims) == []
        assert not [i for c in doc.claims for i in c.issues if i.severity == "error"]


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice not installed")
def test_sample_full_conversion(tmp_path, settings, registry):
    from patent_converter.pipeline.converter import convert_document
    from patent_converter.security.resource_limits import Deadline

    path = SAMPLES / "CLclaims.docx"
    if not path.exists():
        pytest.skip("CLclaims.docx not present")
    prof = registry.get("marker")
    rep = DocxAnalyzer(settings, prof).analyze(path, "en")
    doc = convert_document(path, "en", prof, settings, rep, tmp_path / "w", Deadline(1800))
    d = doc.diagnostics
    assert d["claimsRendered"] == 50 and d["imagesEmbedded"] == d["imageReferencesDetected"] == 16
