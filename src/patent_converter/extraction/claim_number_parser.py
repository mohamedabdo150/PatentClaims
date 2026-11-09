"""Claim-number normalization used only for pairing keys.

The raw number as written in the document is always kept; normalization only
produces a comparison key (Arabic-Indic / Extended Arabic-Indic digits are
mapped to ASCII, surrounding whitespace and bidi marks are removed).
"""

from __future__ import annotations

import re

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_STRIP = "‎‏؜‪‫‬‭‮⁦⁧⁨⁩ \t "


def normalize_claim_number(raw: str) -> str:
    key = raw.strip(_STRIP).translate(_DIGIT_MAP)
    # Remove leading zeros of a purely numeric key ("01" -> "1").
    if key.isdigit():
        key = str(int(key))
    return key


def claim_sort_key(key: str) -> tuple:
    """Natural sort key: '2' < '10', '1.1' < '1.2', '12a' after '12'."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.findall(r"\d+|\D+", key))
