from patent_converter.unicode_integrity.checks import analyze_text


def test_arabic_text_is_clean_and_counted():
    text = "مُرَكَّب له الصيغة (I) وفقاً لعنصر الحماية ١٢، C₆H₆ → α"
    r = analyze_text(text)
    assert r.warning_count() == 0
    assert r.arabic_chars > 10 and r.arabic_diacritics == 5 and r.digit_arabic_indic == 2
    assert r.greek_chars == 1 and r.math_symbols >= 1


def test_problem_characters_detected():
    r = analyze_text("bad � mojibake Ã© and Ø§Ù„ and \x07 bell  pua ﻻ")
    assert r.replacement_chars == 1
    assert r.suspicious_encoding_sequences >= 3
    assert r.control_chars == 1
    assert r.private_use_chars == 1
    assert r.arabic_presentation_forms == 1


def test_analysis_does_not_modify_text():
    s = "‏نص‫ mixed"
    before = s
    analyze_text(s)
    assert s == before
