import pytest
import yaml
from pydantic import ValidationError

from patent_converter.config.claim_patterns import EndMode, PatternRegistry


def test_profiles_load_and_compile(registry):
    assert {"example", "synthetic-test"} <= set(registry.names())
    assert registry.get("synthetic-test").english.end_mode is EndMode.EXPLICIT_END
    assert registry.get("example").arabic.end_mode is EndMode.NEXT_START
    assert not registry.get("example").verified


def _write(tmp_path, english_start, **validation):
    lang = {"claim_start_pattern": english_start}
    data = {"profiles": {"p": {"english": lang, "arabic": {"claim_start_pattern": r"(?P<number>\d+)\."},
                                 "validation": validation}}}
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_placeholder_patterns_rejected(tmp_path):
    with pytest.raises(ValidationError, match="placeholder"):
        PatternRegistry.from_file(_write(tmp_path, "CONFIGURE_ENGLISH_START_PATTERN"))


def test_number_group_required(tmp_path):
    with pytest.raises(ValidationError, match="number"):
        PatternRegistry.from_file(_write(tmp_path, r"^\d+\."))


def test_invalid_regex_rejected(tmp_path):
    with pytest.raises(ValidationError, match="invalid regex"):
        PatternRegistry.from_file(_write(tmp_path, r"(?P<number>\d+"))


def test_require_end_marker_needs_pattern(tmp_path):
    with pytest.raises(ValidationError, match="require_end_marker"):
        PatternRegistry.from_file(_write(tmp_path, r"(?P<number>\d+)\.", require_end_marker=True))


def test_bidi_marks_ignored_only_in_match_copy(registry):
    ar = registry.get("example").arabic
    text = "‏١٢- مركب"
    assert ar.match_claim_start(ar.prepare(text)).group("number") == "١٢"
    assert text.startswith("‏")  # content untouched
