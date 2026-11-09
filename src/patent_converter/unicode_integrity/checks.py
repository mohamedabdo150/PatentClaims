"""Character-integrity diagnostics.

These checks only *observe* text. Nothing here modifies, normalizes or replaces
characters: patent wording must be preserved exactly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

# UTF-8 bytes mis-decoded as cp1252/latin-1 (e.g. "Ã©", Arabic "Ø§Ù„").
_MOJIBAKE = re.compile(
    "[ÂÃÅØÙÚÛâ][\u0080-¿ŒœŠšŸ"
    "Žžƒˆ˜–—‘-„†-•…‰‹›€™]"
)
_BIDI_CONTROLS = set("‎‏؜‪‫‬‭‮⁦⁧⁨⁩")


@dataclass
class UnicodeReport:
    total_chars: int = 0
    arabic_chars: int = 0
    latin_chars: int = 0
    digit_ascii: int = 0
    digit_arabic_indic: int = 0
    arabic_diacritics: int = 0
    arabic_presentation_forms: int = 0
    greek_chars: int = 0
    math_symbols: int = 0
    cjk_chars: int = 0
    replacement_chars: int = 0
    control_chars: int = 0
    invalid_xml_chars: int = 0
    private_use_chars: int = 0
    unassigned_chars: int = 0
    noncharacters: int = 0
    bidi_controls: int = 0
    suspicious_encoding_sequences: int = 0

    def merge(self, other: "UnicodeReport") -> None:
        for key, value in asdict(other).items():
            setattr(self, key, getattr(self, key) + value)

    def warning_count(self) -> int:
        return (
            self.replacement_chars + self.control_chars + self.invalid_xml_chars + self.unassigned_chars
            + self.noncharacters + self.suspicious_encoding_sequences
        )

    def to_dict(self) -> dict[str, int]:
        d = asdict(self)
        d["warning_count"] = self.warning_count()
        return d


def _is_invalid_xml(cp: int) -> bool:
    return not (
        cp in (0x9, 0xA, 0xD) or 0x20 <= cp <= 0xD7FF or 0xE000 <= cp <= 0xFFFD or 0x10000 <= cp <= 0x10FFFF
    )


def analyze_text(text: str) -> UnicodeReport:
    r = UnicodeReport(total_chars=len(text))
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            if ch.isascii() and ch.isalpha():
                r.latin_chars += 1
            elif ch.isdigit():
                r.digit_ascii += 1
            elif cp < 0x20 and ch not in "\t\n\r":
                r.control_chars += 1
                r.invalid_xml_chars += 1
            continue
        if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0x08A0 <= cp <= 0x08FF:
            if 0x0660 <= cp <= 0x0669 or 0x06F0 <= cp <= 0x06F9:
                r.digit_arabic_indic += 1
            elif 0x064B <= cp <= 0x065F or cp == 0x0670:
                r.arabic_diacritics += 1
            else:
                r.arabic_chars += 1
            continue
        if 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF:
            if cp == 0xFEFF:
                r.bidi_controls += 1
            else:
                r.arabic_presentation_forms += 1
            continue
        if ch == "�":
            r.replacement_chars += 1
            continue
        if ch in _BIDI_CONTROLS:
            r.bidi_controls += 1
            continue
        if 0x0370 <= cp <= 0x03FF:
            r.greek_chars += 1
            continue
        if 0x4E00 <= cp <= 0x9FFF or 0x3040 <= cp <= 0x30FF or 0xAC00 <= cp <= 0xD7AF:
            r.cjk_chars += 1
            continue
        if 0xE000 <= cp <= 0xF8FF or cp >= 0xF0000:
            r.private_use_chars += 1
            continue
        if (0xFDD0 <= cp <= 0xFDEF) or (cp & 0xFFFE) == 0xFFFE:
            r.noncharacters += 1
            continue
        if _is_invalid_xml(cp):
            r.invalid_xml_chars += 1
            continue
        cat = unicodedata.category(ch)
        if cat == "Cn":
            r.unassigned_chars += 1
        elif cat == "Cc":
            r.control_chars += 1
        elif cat == "Sm":
            r.math_symbols += 1
        elif cat.startswith("L") and ch.isalpha() and cp < 0x0250:
            r.latin_chars += 1
    r.suspicious_encoding_sequences = len(_MOJIBAKE.findall(text))
    return r
