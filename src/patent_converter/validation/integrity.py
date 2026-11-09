"""Cross-checks between the analyzer pass and the extraction/rendering passes.

The analyzer and the extractor are independent walks over the same XML. For
each claim the following must hold exactly, otherwise the conversion fails
(``CONTENT_INTEGRITY_FAILED``) instead of returning possibly incomplete HTML:

* claim numbers and order are identical,
* text characters: analyzer == as-text + rasterized + removed-by-policy,
* image relationship references: analyzer == accounted by the extractor,
* OMML equations: analyzer == MathML + image-rendered,
* tables: analyzer == HTML tables + tables inside rasterized drawings.
"""

from __future__ import annotations

from typing import Any

from ..models.document_model import Claim


def check_extraction(analysis: dict[str, Any], claims: list[Claim]) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    expected = analysis["claims"]["perClaim"]
    if [c["number"] for c in expected] != [c.number for c in claims]:
        problems.append({"code": "CLAIM_SET_DIFFERS", "expected": len(expected), "extracted": len(claims)})
        return problems
    for exp, claim in zip(expected, claims):
        k = claim.counters
        checks = {
            "TEXT_CHARS": (exp["textChars"], k.text_chars + k.text_chars_rasterized + k.text_chars_removed),
            "IMAGE_REFERENCES": (exp["imageReferences"], k.image_refs),
            "EQUATIONS": (exp["equations"], k.equations),
            "EQUATION_RENDERING": (k.equations, k.equations_mathml + k.equations_image),
            "TABLES": (exp["tables"], k.tables + k.tables_rasterized),
        }
        for code, (want, got) in checks.items():
            if want != got:
                problems.append({"code": code, "claimNumber": claim.number, "expected": want, "actual": got})
    return problems


def check_rendering(claim: Claim, rendered: dict[str, int]) -> list[dict[str, Any]]:
    """Rendered output must contain every model text character, image, equation and table."""
    k = claim.counters
    problems = []
    pairs = {
        "RENDERED_TEXT_CHARS": (k.text_chars, rendered["text_chars"]),
        "RENDERED_IMAGES": (k.images + k.equations_image, rendered["images"]),
        "RENDERED_EQUATIONS_MATHML": (k.equations_mathml, rendered["mathml"]),
        "RENDERED_TABLES": (k.tables, rendered["tables"]),
    }
    for code, (want, got) in pairs.items():
        if want != got:
            problems.append({"code": code, "claimNumber": claim.number, "expected": want, "actual": got})
    return problems


def check_document(analysis: dict[str, Any], model: Claim) -> list[dict[str, Any]]:
    """Whole-document mode: analyzer document totals == extracted model."""
    k = model.counters
    c = analysis["structure"]["counts"]
    checks = {
        "TEXT_CHARS": (analysis["unicode"]["document"]["total_chars"],
                       k.text_chars + k.text_chars_rasterized + k.text_chars_removed),
        "IMAGE_REFERENCES": (analysis["media"]["imageReferencesInMainDocument"], k.image_refs),
        "EQUATIONS": (c.get("equations", 0), k.equations),
        "TABLES": (c.get("tables", 0), k.tables + k.tables_rasterized),
    }
    return [{"code": code, "expected": want, "actual": got}
            for code, (want, got) in checks.items() if want != got]
