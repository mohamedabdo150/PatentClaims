"""Separator ("marker") boundary mode, as used by the customer documents ({start} / {next})."""

import pytest

from patent_converter.analyzer.docx_analyzer import DocxAnalyzer
from patent_converter.extraction.claim_detector import BlockInfo, ClaimBoundaryDetector, Role
from patent_converter.extraction.claim_extractor import extract_claims
from patent_converter.extraction.model_builder import ContentPolicy
from patent_converter.validation.integrity import check_extraction
from tests.fixtures.docx_builder import DocxBuilder, cell, para, run, table


@pytest.fixture(scope="module")
def marker(registry):
    return registry.get("marker")


def feed(det, items):
    roles = []
    for i, it in enumerate(items):
        kind, text, label = it if isinstance(it, tuple) else ("paragraph", it, None)
        roles.append(det.feed(BlockInfo(i, kind, text, label)))
    return roles, det.finish(500)


def codes(r):
    return {i.code for i in r.issues if i.severity == "error"}


def test_english_markers_labels_and_inline(marker):
    det = ClaimBoundaryDetector(marker.english, marker.validation)
    roles, r = feed(det, [
        "CLAIMS", "{start}", "",
        ("paragraph", "A compound of formula (I):", "1.\t"), "", "wherein R is H. {next}",
        ("paragraph", "The compound of claim 1.", "2.\t"), "{next}",
        "3-The compound of claim 1 or 2.", "{next}",
        ("paragraph", "The compound of claim 3.", "4.\t"),
        ("paragraph", "The compound of claim 4.", "5.\t"),   # no {next}: numbered start splits
    ])
    assert roles[0] is Role.OUTSIDE and roles[1] is Role.SECTION_START
    assert [c.number for c in r.claims] == ["1", "2", "3", "4", "5"]
    assert r.claims[0].closed_by == "separator" and r.claims[3].closed_by == "next_start"
    assert r.claims[0].number_from_numbering and not codes(r)


def test_arabic_number_anywhere_and_no_split(marker):
    det = ClaimBoundaryDetector(marker.arabic, marker.validation)
    _, r = feed(det, [
        "{start}", "-1   مركب جديد له هيكل:", "{next}",
        "المركب وفقا لعنصر الحماية 1، حيث", "-2", "{next}",
        "١٠- المركب وفقا لعنصر الحماية 2", "10-3 أضعاف", "{next}",   # "10-3" is a range, not a number
    ])
    assert [c.number for c in r.claims] == ["1", "2", "10"] and not codes(r)


def test_missing_and_ambiguous_numbers_are_errors(marker):
    det = ClaimBoundaryDetector(marker.arabic, marker.validation)
    _, r = feed(det, ["{start}", "نص بلا رقم", "{next}", "-2", "نص", "-3", "{next}"])
    assert {"MISSING_CLAIM_NUMBER", "AMBIGUOUS_CLAIM_NUMBER"} <= codes(r)
    det2 = ClaimBoundaryDetector(marker.english, marker.validation)
    _, r2 = feed(det2, ["{start}", "1. A claim {next} 2. Another"])
    assert "AMBIGUOUS_MARKER" in codes(r2)


def build_marker_doc(path, inline_split=False, layout=True):
    b = DocxBuilder()
    body = [para("CLAIMS"), para(run("{start}")), para(run("1. A compound of formula (I):")), para(run("wherein R is H."))]
    if inline_split:  # Word splits the marker across runs
        body.append(para(run("and salts thereof. "), run("{ne"), run("xt}")))
    else:
        body += [para(run("and salts thereof.")), para(run("{next}"))]
    if layout:  # PDF-style layout table: text cell + number cell, marker at the end of the text cell
        rows = [[cell(para(run("The compound of claim 1, wherein")), para(run("R is methyl.")), para(run("{next}"))),
                 cell(para(run("-2")))],
                [cell(para(run("The compound of claim 2.")), para(run("{next}"))), cell(para(run("-3")))]]
        body.append(table(rows, cols=2))
    body.append(para(run("4. The compound of claim 3.")))
    b.add(*body)
    return b.build(path)


def test_end_to_end_layout_table_and_split_marker(tmp_path, settings, marker):
    path = build_marker_doc(tmp_path / "m.docx", inline_split=True)
    rep = DocxAnalyzer(settings, marker).analyze(path, "en")
    assert rep["claims"]["numbers"] == ["1", "2", "3", "4"] and not rep["claims"]["issues"]
    assert rep["claims"]["layoutTablesLinearized"] == 1
    doc = extract_claims(path, "en", marker, settings, ContentPolicy())
    assert [c.number for c in doc.claims] == ["1", "2", "3", "4"]
    assert check_extraction(rep, doc.claims) == []
    text = "".join(i.text for b in doc.claims[0].blocks for i in getattr(b, "inlines", []) if hasattr(i, "text"))
    assert "{" not in text and "next" not in text and "salts thereof." in text
    assert doc.claims[0].counters.text_chars_removed == len("{next}")
