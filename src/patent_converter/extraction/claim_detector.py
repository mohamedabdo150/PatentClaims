"""Pattern-based claim boundary detection.

A deterministic state machine fed with the top-level body blocks of a document
in order. No AI and no generic paragraph heuristics are used: boundaries come
exclusively from the configured patterns.

States::

    BEFORE_SECTION --section_start--> IN_SECTION --claim_start--> IN_CLAIM
    IN_CLAIM --claim_end (explicit mode) / next claim_start / section_end--> ...
    IN_SECTION/IN_CLAIM --section_end--> AFTER_SECTION

Every block inside the claims section is either part of a claim, a section
marker, or reported as orphan content. Nothing is skipped silently.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from ..config.claim_patterns import CompiledLanguagePatterns, EndMode, ValidationConfig
from .claim_number_parser import claim_sort_key, normalize_claim_number


class State(str, Enum):
    BEFORE_SECTION = "before_section"
    IN_SECTION = "in_section"
    IN_CLAIM = "in_claim"
    AFTER_SECTION = "after_section"


class Role(str, Enum):
    OUTSIDE = "outside"
    SECTION_START = "section_start"
    SECTION_END = "section_end"
    CLAIM_START = "claim_start"
    CLAIM_BODY = "claim_body"
    ORPHAN = "orphan"
    EMPTY_GAP = "empty_gap"


@dataclass(frozen=True)
class BlockInfo:
    index: int
    kind: str  # paragraph | table | altchunk | other
    text: str = ""
    numbering_label: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.kind == "paragraph" and not self.text.strip() and not self.numbering_label


@dataclass
class DetectedClaim:
    number_raw: str
    number: str
    start_block: int
    end_block: int
    block_count: int = 1
    closed_by: str | None = None  # end_marker | next_start | section_end | end_of_document
    number_from_numbering: bool = False

    @property
    def closed(self) -> bool:
        return self.closed_by is not None


@dataclass
class BoundaryIssue:
    code: str
    severity: str  # error | warning
    message: str
    block_index: int | None = None
    claim_number: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class DetectionResult:
    language: str
    end_mode: EndMode
    section_found: bool
    section_start_block: int | None
    section_end_block: int | None
    claims: list[DetectedClaim]
    issues: list[BoundaryIssue]
    orphan_blocks: list[int]

    @property
    def errors(self) -> list[BoundaryIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def claim_numbers(self) -> list[str]:
        return [c.number for c in self.claims]

    @property
    def duplicate_numbers(self) -> list[str]:
        counts = Counter(self.claim_numbers)
        return sorted((n for n, c in counts.items() if c > 1), key=claim_sort_key)


class ClaimBoundaryDetector:
    def __init__(self, patterns: CompiledLanguagePatterns, validation: ValidationConfig):
        self.patterns = patterns
        self.validation = validation
        self.state = State.IN_SECTION if patterns.section_start is None else State.BEFORE_SECTION
        self.section_found = patterns.section_start is None
        self.section_start_block: int | None = None
        self.section_end_block: int | None = None
        self.claims: list[DetectedClaim] = []
        self.issues: list[BoundaryIssue] = []
        self.orphan_blocks: list[int] = []
        self._current: DetectedClaim | None = None
        self._finished = False

    @property
    def current_claim(self) -> DetectedClaim | None:
        return self._current

    def _close(self, reason: str) -> None:
        if self._current is not None:
            self._current.closed_by = reason
            self._current = None

    def _unclosed(self, block_index: int, reason: str) -> None:
        """Explicit-end mode: a claim was interrupted before its end marker."""
        claim = self._current
        assert claim is not None
        self.issues.append(BoundaryIssue(
            code="UNCLOSED_CLAIM",
            severity="error" if self.validation.reject_unclosed_claims else "warning",
            message=f"claim {claim.number_raw} has no end marker before {reason}",
            block_index=block_index,
            claim_number=claim.number,
        ))
        self._current = None

    def feed(self, block: BlockInfo) -> Role:
        if self._finished:
            raise RuntimeError("detector already finished")
        p = self.patterns
        text = ""
        if block.kind == "paragraph":
            raw = block.text
            if p.config.include_numbering_label and block.numbering_label:
                raw = block.numbering_label + raw
            text = p.prepare(raw)

        if self.state is State.BEFORE_SECTION:
            if block.kind == "paragraph" and p.match_section_start(text):
                self.state = State.IN_SECTION
                self.section_found = True
                self.section_start_block = block.index
                return Role.SECTION_START
            return Role.OUTSIDE

        if self.state is State.AFTER_SECTION:
            return Role.OUTSIDE

        # IN_SECTION or IN_CLAIM
        if block.kind == "paragraph":
            if p.section_end is not None and p.match_section_end(text):
                if self._current is not None:
                    if p.end_mode is EndMode.EXPLICIT_END:
                        self._unclosed(block.index, "the claims section end")
                    else:
                        self._close("section_end")
                self.state = State.AFTER_SECTION
                self.section_end_block = block.index
                return Role.SECTION_END
            if p.section_start is not None and p.match_section_start(text):
                self.issues.append(BoundaryIssue(
                    code="REPEATED_SECTION_START", severity="warning",
                    message="claims section start marker found again inside the claims section",
                    block_index=block.index,
                ))
                return Role.SECTION_START
            m = p.match_claim_start(text)
            if m is not None:
                if self._current is not None:
                    if p.end_mode is EndMode.EXPLICIT_END:
                        self._unclosed(block.index, f"the start of the next claim ({m.group('number')})")
                    else:
                        self._close("next_start")
                number_raw = m.group("number")
                from_numbering = bool(
                    p.config.include_numbering_label and block.numbering_label
                    and m.start("number") < len(p.prepare(block.numbering_label))
                )
                claim = DetectedClaim(
                    number_raw=number_raw,
                    number=normalize_claim_number(number_raw),
                    start_block=block.index,
                    end_block=block.index,
                    number_from_numbering=from_numbering,
                )
                self.claims.append(claim)
                self._current = claim
                self.state = State.IN_CLAIM
                if p.end_mode is EndMode.EXPLICIT_END and p.match_claim_end(text):
                    self._close("end_marker")
                    self.state = State.IN_SECTION
                return Role.CLAIM_START

        if self._current is not None:
            self._current.end_block = block.index
            self._current.block_count += 1
            if block.kind == "paragraph" and p.end_mode is EndMode.EXPLICIT_END and p.match_claim_end(text):
                self._close("end_marker")
                self.state = State.IN_SECTION
            return Role.CLAIM_BODY

        if block.is_empty:
            return Role.EMPTY_GAP
        self.orphan_blocks.append(block.index)
        return Role.ORPHAN

    def finish(self, max_claims: int | None = None) -> DetectionResult:
        if not self._finished:
            self._finished = True
            self._validate(max_claims)
        return DetectionResult(
            language=self.patterns.language,
            end_mode=self.patterns.end_mode,
            section_found=self.section_found,
            section_start_block=self.section_start_block,
            section_end_block=self.section_end_block,
            claims=self.claims,
            issues=self.issues,
            orphan_blocks=self.orphan_blocks,
        )

    def _validate(self, max_claims: int | None) -> None:
        v = self.validation
        p = self.patterns
        if self._current is not None:
            if p.end_mode is EndMode.EXPLICIT_END:
                self._unclosed(self._current.end_block, "the end of the document")
            else:
                if p.section_end is not None:
                    self.issues.append(BoundaryIssue(
                        code="SECTION_END_NOT_FOUND", severity="warning",
                        message="last claim closed by end of document; claims section end marker not found",
                        claim_number=self._current.number,
                    ))
                self._close("end_of_document")
        if p.section_start is not None and not self.section_found:
            self.issues.append(BoundaryIssue(
                code="CLAIMS_SECTION_NOT_FOUND",
                severity="error" if v.require_section_start else "warning",
                message="claims section start marker not found",
            ))
        if not self.claims and (self.section_found or p.section_start is None):
            self.issues.append(BoundaryIssue(
                code="NO_CLAIMS_DETECTED",
                severity="error" if v.require_start_marker else "warning",
                message="no claim start markers matched inside the claims section",
            ))
        if self.orphan_blocks:
            self.issues.append(BoundaryIssue(
                code="ORPHAN_CONTENT",
                severity="error" if v.reject_orphan_content else "warning",
                message=f"{len(self.orphan_blocks)} non-empty block(s) inside the claims section belong to no claim",
                details={"blockIndexes": self.orphan_blocks[:50]},
            ))
        counts = Counter(c.number for c in self.claims)
        dups = sorted((n for n, c in counts.items() if c > 1), key=claim_sort_key)
        if dups:
            self.issues.append(BoundaryIssue(
                code="DUPLICATE_CLAIM_NUMBERS",
                severity="error" if v.reject_duplicate_claim_numbers else "warning",
                message=f"duplicate claim numbers: {', '.join(dups[:20])}",
                details={"duplicates": dups},
            ))
        numeric = [int(n) for n in counts if n.isdigit()]
        if numeric:
            expected = set(range(1, max(numeric) + 1))
            gaps = sorted(expected - set(numeric))
            order = [int(c.number) for c in self.claims if c.number.isdigit()]
            out_of_order = any(b < a for a, b in zip(order, order[1:]))
            if gaps or out_of_order:
                self.issues.append(BoundaryIssue(
                    code="NON_SEQUENTIAL_CLAIM_NUMBERS",
                    severity="error" if v.require_sequential_numbers else "warning",
                    message="claim numbers are not a contiguous ascending sequence starting at 1",
                    details={"missingNumbers": [str(g) for g in gaps[:100]], "outOfOrder": out_of_order},
                ))
        if max_claims is not None and len(self.claims) > max_claims:
            self.issues.append(BoundaryIssue(
                code="TOO_MANY_CLAIMS", severity="error",
                message=f"{len(self.claims)} claims detected; configured maximum is {max_claims}",
            ))
