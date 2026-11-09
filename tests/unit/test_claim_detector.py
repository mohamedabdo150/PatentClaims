from patent_converter.config.claim_patterns import ValidationConfig
from patent_converter.extraction.claim_detector import BlockInfo, ClaimBoundaryDetector, Role
from patent_converter.extraction.claim_number_parser import claim_sort_key, normalize_claim_number


def feed_all(detector, items):
    roles = []
    for i, item in enumerate(items):
        kind, text, label = (item + (None,))[:3] if isinstance(item, tuple) else ("paragraph", item, None)
        roles.append(detector.feed(BlockInfo(i, kind, text, label)))
    return roles, detector.finish(max_claims=500)


def codes(result):
    return {i.code: i.severity for i in result.issues}


def test_next_start_mode_section_gating(example_profile):
    d = ClaimBoundaryDetector(example_profile.english, example_profile.validation)
    roles, r = feed_all(d, [
        "1. A numbered description paragraph.",  # before section -> outside
        "What is claimed is:",
        "1. A compound comprising:",
        "a first part; and",
        ("table", ""),
        "2. The compound of claim 1.",
        "",
        "3. The compound of claim 2.",
        "ABSTRACT",
        "4. not a claim",
    ])
    assert roles[0] is Role.OUTSIDE and roles[1] is Role.SECTION_START
    assert [c.number for c in r.claims] == ["1", "2", "3"]
    assert r.claims[0].block_count == 3 and r.claims[0].closed_by == "next_start"
    assert r.claims[1].block_count == 2  # empty spacer paragraph belongs to claim 2
    assert r.claims[2].closed_by == "section_end"
    assert not r.errors


def test_explicit_end_mode(synthetic_profile):
    d = ClaimBoundaryDetector(synthetic_profile.english, synthetic_profile.validation)
    _, r = feed_all(d, ["CLAIMS", "1. A method comprising:", "step one,", "step two.", "", "2. The method of claim 1.",
                        "END OF CLAIMS"])
    assert [(c.number, c.block_count, c.closed_by) for c in r.claims] == [("1", 3, "end_marker"), ("2", 1, "end_marker")]
    assert not r.issues


def test_unclosed_claim_is_error(synthetic_profile):
    d = ClaimBoundaryDetector(synthetic_profile.english, synthetic_profile.validation)
    _, r = feed_all(d, ["CLAIMS", "1. A method comprising:", "step one,", "2. The method of claim 1.", "END OF CLAIMS"])
    assert codes(r).get("UNCLOSED_CLAIM") == "error"


def test_orphan_content_is_reported(synthetic_profile):
    d = ClaimBoundaryDetector(synthetic_profile.english, synthetic_profile.validation)
    _, r = feed_all(d, ["CLAIMS", "1. A method.", "stray text", ("table", ""), "2. Another.", "END OF CLAIMS"])
    assert codes(r).get("ORPHAN_CONTENT") == "error"
    assert r.orphan_blocks == [2, 3]


def test_duplicates_gaps_and_missing_section(example_profile):
    d = ClaimBoundaryDetector(example_profile.english, example_profile.validation)
    _, r = feed_all(d, ["CLAIMS", "1. a", "3. b", "3. c"])
    c = codes(r)
    assert c["DUPLICATE_CLAIM_NUMBERS"] == "error"
    assert c["NON_SEQUENTIAL_CLAIM_NUMBERS"] == "warning"
    assert r.duplicate_numbers == ["3"]

    d2 = ClaimBoundaryDetector(example_profile.english, example_profile.validation)
    _, r2 = feed_all(d2, ["1. a", "2. b"])
    assert codes(r2)["CLAIMS_SECTION_NOT_FOUND"] == "error"
    assert not r2.claims


def test_numbering_label_and_arabic_digits(example_profile):
    d = ClaimBoundaryDetector(example_profile.arabic, example_profile.validation)
    _, r = feed_all(d, [
        "عناصر الحماية",
        ("paragraph", "مركب يشتمل على:", "١.\t"),
        ("paragraph", "‏٢- المركب وفقاً لعنصر الحماية ١.", None),
        ("paragraph", "المركب.", "3.\t"),
    ])
    assert [(c.number_raw, c.number, c.number_from_numbering) for c in r.claims] == [
        ("١", "1", True), ("٢", "2", False), ("3", "3", True)]


def test_too_many_claims(example_profile):
    d = ClaimBoundaryDetector(example_profile.english, example_profile.validation)
    for i, text in enumerate(["CLAIMS"] + [f"{n}. claim" for n in range(1, 6)]):
        d.feed(BlockInfo(i, "paragraph", text))
    assert "TOO_MANY_CLAIMS" in codes(d.finish(max_claims=3))


def test_lenient_validation_downgrades(example_profile):
    lenient = ValidationConfig(reject_duplicate_claim_numbers=False, reject_orphan_content=False)
    d = ClaimBoundaryDetector(example_profile.english, lenient)
    _, r = feed_all(d, ["CLAIMS", "1. a", "1. b"])
    assert codes(r)["DUPLICATE_CLAIM_NUMBERS"] == "warning"


def test_claim_number_normalization():
    assert normalize_claim_number("١٢") == "12"
    assert normalize_claim_number("۰۷") == "7"
    assert normalize_claim_number("‏3 ") == "3"
    assert normalize_claim_number("12a") == "12a"
    assert sorted(["10", "2", "1", "1.2", "1.10"], key=claim_sort_key) == ["1", "1.2", "1.10", "2", "10"]
