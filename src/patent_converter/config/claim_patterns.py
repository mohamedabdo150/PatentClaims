"""Loading and validation of configurable claim-boundary pattern profiles.

Patterns are configuration, not code. A profile is validated and compiled once
at startup so a broken regex fails fast instead of at request time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Bidi control/marks stripped from the *match copy* only (never from content).
BIDI_MARKS = "‎‏؜‪‫‬‭‮⁦⁧⁨⁩﻿"
_BIDI_TABLE = {ord(c): None for c in BIDI_MARKS}


class EndMode(str, Enum):
    NEXT_START = "next_start"
    EXPLICIT_END = "explicit_end"
    SEPARATOR = "separator"


class LanguagePatternConfig(BaseModel):
    # start_end: claims open at claim_start_pattern (and close at claim_end_pattern / next start).
    # separator: claims are the content between section start / separator markers; the claim
    #            number is searched anywhere in the claim with number_patterns.
    boundary_mode: Literal["start_end", "separator"] = "start_end"
    section_start_pattern: str | None = None
    section_end_pattern: str | None = None
    claim_start_pattern: str | None = None
    claim_end_pattern: str | None = None
    separator_pattern: str | None = None
    number_patterns: list[str] = Field(default_factory=list)
    start_splits_claims: bool = False       # separator mode: a claim_start match closes a numbered claim
    strip_markers: bool = True              # remove marker text (section start / separator) from output
    linearize_marker_tables: bool = True    # tables containing markers are layout containers
    pattern_type: Literal["regex"] = "regex"
    case_sensitive: bool = False
    match_mode: Literal["match", "search", "fullmatch"] = "match"
    include_numbering_label: bool = True
    ignore_bidi_marks_for_matching: bool = True
    max_match_chars: int = Field(2000, ge=10, le=100_000)

    @field_validator("section_start_pattern", "section_end_pattern", "claim_start_pattern", "claim_end_pattern",
                     "separator_pattern")
    @classmethod
    def _compile_check(cls, value: str | None) -> str | None:
        if value is not None:
            if "CONFIGURE_" in value:
                raise ValueError(f"placeholder pattern not configured: {value!r}")
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex {value!r}: {exc}") from exc
        return value

    @field_validator("number_patterns")
    @classmethod
    def _number_patterns(cls, value: list[str]) -> list[str]:
        for pat in value:
            try:
                compiled = re.compile(pat)
            except re.error as exc:
                raise ValueError(f"invalid regex {pat!r}: {exc}") from exc
            if "number" not in compiled.groupindex:
                raise ValueError(f"number pattern {pat!r} must define a named group (?P<number>...)")
        return value

    @model_validator(mode="after")
    def _number_group(self) -> "LanguagePatternConfig":
        if self.claim_start_pattern and "number" not in re.compile(self.claim_start_pattern).groupindex:
            raise ValueError("claim_start_pattern must define a named group (?P<number>...)")
        if self.boundary_mode == "start_end" and not self.claim_start_pattern:
            raise ValueError("boundary_mode start_end requires claim_start_pattern")
        if self.boundary_mode == "separator":
            if not self.separator_pattern:
                raise ValueError("boundary_mode separator requires separator_pattern")
            if not self.number_patterns and not self.claim_start_pattern:
                raise ValueError("boundary_mode separator requires number_patterns or claim_start_pattern")
            if self.start_splits_claims and not self.claim_start_pattern:
                raise ValueError("start_splits_claims requires claim_start_pattern")
        return self


class ValidationConfig(BaseModel):
    require_section_start: bool = True
    require_start_marker: bool = True
    require_end_marker: bool = False
    reject_duplicate_claim_numbers: bool = True
    reject_unclosed_claims: bool = True
    reject_orphan_content: bool = True
    require_sequential_numbers: bool = False


class PatternProfileConfig(BaseModel):
    verified: bool = False
    description: str = ""
    english: LanguagePatternConfig
    arabic: LanguagePatternConfig
    validation: ValidationConfig = Field(default_factory=ValidationConfig)

    @model_validator(mode="after")
    def _end_marker_consistency(self) -> "PatternProfileConfig":
        if self.validation.require_end_marker:
            for lang, cfg in (("english", self.english), ("arabic", self.arabic)):
                if cfg.claim_end_pattern is None and cfg.boundary_mode != "separator":
                    raise ValueError(f"validation.require_end_marker is true but {lang}.claim_end_pattern is null")
        return self


class PatternFile(BaseModel):
    profiles: dict[str, PatternProfileConfig]


@dataclass(frozen=True)
class CompiledLanguagePatterns:
    """Compiled, ready-to-use patterns for one language."""

    language: str
    config: LanguagePatternConfig
    section_start: re.Pattern[str] | None
    section_end: re.Pattern[str] | None
    claim_start: re.Pattern[str] | None
    claim_end: re.Pattern[str] | None
    separator: re.Pattern[str] | None = None
    numbers: tuple[re.Pattern[str], ...] = ()

    @property
    def end_mode(self) -> EndMode:
        if self.separator is not None:
            return EndMode.SEPARATOR
        return EndMode.EXPLICIT_END if self.claim_end is not None else EndMode.NEXT_START

    def is_marker(self, text: str) -> bool:
        """True when the text contains a section-start or separator marker (used to find layout tables)."""
        t = self.prepare(text)
        return bool((self.separator is not None and self.separator.search(t))
                    or (self.section_start is not None and self.match_section_start(t)))

    def prepare(self, text: str) -> str:
        """Return the copy of paragraph text used for matching (content is never modified)."""
        if self.config.ignore_bidi_marks_for_matching:
            text = text.translate(_BIDI_TABLE)
        return text[: self.config.max_match_chars]

    def _apply(self, pattern: re.Pattern[str] | None, text: str) -> re.Match[str] | None:
        if pattern is None:
            return None
        mode = self.config.match_mode
        if mode == "match":
            return pattern.match(text)
        if mode == "search":
            return pattern.search(text)
        return pattern.fullmatch(text)

    def match_section_start(self, text: str) -> bool:
        return self._apply(self.section_start, text) is not None

    def match_section_end(self, text: str) -> bool:
        return self._apply(self.section_end, text) is not None

    def match_claim_start(self, text: str) -> re.Match[str] | None:
        return self._apply(self.claim_start, text) if self.claim_start is not None else None

    def match_claim_end(self, text: str) -> bool:
        # End markers are searched anywhere in the paragraph (typically anchored with $).
        return self.claim_end is not None and self.claim_end.search(text) is not None


@dataclass(frozen=True)
class CompiledProfile:
    name: str
    verified: bool
    english: CompiledLanguagePatterns
    arabic: CompiledLanguagePatterns
    validation: ValidationConfig

    def for_language(self, language: str) -> CompiledLanguagePatterns:
        if language == "en":
            return self.english
        if language == "ar":
            return self.arabic
        raise ValueError(f"unsupported language {language!r}")


def _compile_language(language: str, cfg: LanguagePatternConfig) -> CompiledLanguagePatterns:
    flags = re.UNICODE | (0 if cfg.case_sensitive else re.IGNORECASE)

    def c(p: str | None) -> re.Pattern[str] | None:
        return re.compile(p, flags) if p is not None else None

    numbers = [re.compile(p, flags) for p in cfg.number_patterns]
    if not numbers and cfg.claim_start_pattern:
        numbers = [re.compile(cfg.claim_start_pattern, flags)]
    return CompiledLanguagePatterns(
        language=language,
        config=cfg,
        section_start=c(cfg.section_start_pattern),
        section_end=c(cfg.section_end_pattern),
        claim_start=c(cfg.claim_start_pattern),
        claim_end=c(cfg.claim_end_pattern) if cfg.boundary_mode == "start_end" else None,
        separator=c(cfg.separator_pattern) if cfg.boundary_mode == "separator" else None,
        numbers=tuple(numbers),
    )


class PatternRegistry:
    """All configured profiles, compiled and validated."""

    def __init__(self, profiles: dict[str, CompiledProfile], source: Path | None = None):
        if not profiles:
            raise ValueError("no claim pattern profiles configured")
        self._profiles = profiles
        self.source = source

    @classmethod
    def from_file(cls, path: Path | str) -> "PatternRegistry":
        path = Path(path)
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        parsed = PatternFile.model_validate(raw)
        return cls(
            {
                name: CompiledProfile(
                    name=name,
                    verified=p.verified,
                    english=_compile_language("en", p.english),
                    arabic=_compile_language("ar", p.arabic),
                    validation=p.validation,
                )
                for name, p in parsed.profiles.items()
            },
            source=path,
        )

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def get(self, name: str) -> CompiledProfile:
        try:
            return self._profiles[name]
        except KeyError:
            raise KeyError(name) from None
