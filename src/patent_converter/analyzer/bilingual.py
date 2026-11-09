"""Analysis of an English/Arabic document pair, including claim-set comparison."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config.claim_patterns import CompiledProfile
from ..config.settings import MB, Settings
from ..diagnostics.resources import ResourceSampler
from ..extraction.claim_number_parser import claim_sort_key
from ..security.resource_limits import Deadline
from .docx_analyzer import DocxAnalyzer


def compare_claim_sets(en: dict[str, Any] | None, ar: dict[str, Any] | None) -> dict[str, Any]:
    """Number-keyed comparison (never positional)."""
    if not en or not ar or "claims" not in en or "claims" not in ar:
        return {"comparable": False}
    en_nums, ar_nums = en["claims"]["numbers"], ar["claims"]["numbers"]
    en_set, ar_set = set(en_nums), set(ar_nums)
    return {
        "comparable": True,
        "englishCount": len(en_nums),
        "arabicCount": len(ar_nums),
        "matched": len(en_set & ar_set),
        "missingArabicClaims": sorted(en_set - ar_set, key=claim_sort_key),
        "missingEnglishClaims": sorted(ar_set - en_set, key=claim_sort_key),
        "duplicateArabicClaims": ar["claims"]["duplicates"],
        "duplicateEnglishClaims": en["claims"]["duplicates"],
        "pairable": en_set == ar_set and not en["claims"]["duplicates"] and not ar["claims"]["duplicates"]
        and bool(en_set),
    }


def analyze_pair(
    settings: Settings, profile: CompiledProfile, english: Path | None = None, arabic: Path | None = None,
    english_name: str | None = None, arabic_name: str | None = None, deadline: Deadline | None = None,
) -> dict[str, Any]:
    analyzer = DocxAnalyzer(settings, profile, deadline)
    result: dict[str, Any] = {"profile": profile.name, "profileVerified": profile.verified}
    for key, path, lang, name in (("english", english, "en", english_name), ("arabic", arabic, "ar", arabic_name)):
        if path is None:
            result[key] = None
            continue
        with ResourceSampler() as sampler:
            rep = analyzer.analyze(path, lang, display_name=name)
        rep["resources"] = sampler.usage.to_dict()
        result[key] = rep
    result["pairing"] = compare_claim_sets(result.get("english"), result.get("arabic"))
    est = [r.get("estimates", {}).get("estimatedJsonContributionBytes", 0) for r in (result["english"], result["arabic"]) if r]
    total = sum(est)
    limit = int(settings.output.max_response_size_mb * MB)
    result["combinedEstimate"] = {
        "estimatedResponseBytes": total,
        "maxResponseBytes": limit,
        "withinLimit": total <= limit,
    }
    return result


def render_pair_summary(result: dict[str, Any]) -> str:
    p = result["pairing"]
    ce = result["combinedEstimate"]
    lines = ["=" * 78, "BILINGUAL SUMMARY", "=" * 78,
             f"Profile                : {result['profile']} (verified={result['profileVerified']})"]
    if p.get("comparable"):
        lines += [
            f"Claims EN / AR         : {p['englishCount']} / {p['arabicCount']} (matched by number: {p['matched']})",
            f"Missing in Arabic      : {p['missingArabicClaims'] or 'none'}",
            f"Missing in English     : {p['missingEnglishClaims'] or 'none'}",
            f"Duplicates EN / AR     : {p['duplicateEnglishClaims'] or 'none'} / {p['duplicateArabicClaims'] or 'none'}",
            f"Pairable               : {p['pairable']}",
        ]
    else:
        lines.append("Pairing                : not comparable (one document missing or rejected)")
    lines.append(f"Estimated response     : {ce['estimatedResponseBytes'] / MB:.2f} MB "
                 f"(limit {ce['maxResponseBytes'] / MB:.0f} MB, within={ce['withinLimit']})")
    return "\n".join(lines)
