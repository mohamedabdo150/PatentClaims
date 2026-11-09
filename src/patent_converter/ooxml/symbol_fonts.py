"""Mapping of the legacy Adobe "Symbol" font encoding to Unicode.

Older chemical/mathematical patents type Greek letters and operators as Latin
characters formatted with the *Symbol* font ("a" displayed as α), or as
``w:sym w:font="Symbol" w:char="F061"``. Rendering those characters without the
font would silently change the meaning, so they are mapped to the Unicode
characters the font displays (1:1, same character count).

Other dingbat fonts (Wingdings, Webdings, ...) have no reliable Unicode
equivalent; they are kept as-is with their font family and reported.
"""

from __future__ import annotations

SYMBOL_FONT_NAMES = {"symbol"}
DINGBAT_FONT_NAMES = {"wingdings", "wingdings 2", "wingdings 3", "webdings", "mt extra", "zapf dingbats", "zapfdingbats"}

_SYMBOL = {
    0x20: " ", 0x21: "!", 0x22: "∀", 0x23: "#", 0x24: "∃", 0x25: "%", 0x26: "&", 0x27: "∋", 0x28: "(", 0x29: ")",
    0x2A: "∗", 0x2B: "+", 0x2C: ",", 0x2D: "−", 0x2E: ".", 0x2F: "/", 0x3A: ":", 0x3B: ";", 0x3C: "<", 0x3D: "=",
    0x3E: ">", 0x3F: "?", 0x40: "≅",
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε", 0x46: "Φ", 0x47: "Γ", 0x48: "Η", 0x49: "Ι", 0x4A: "ϑ",
    0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ", 0x52: "Ρ", 0x53: "Σ", 0x54: "Τ",
    0x55: "Υ", 0x56: "ς", 0x57: "Ω", 0x58: "Ξ", 0x59: "Ψ", 0x5A: "Ζ", 0x5B: "[", 0x5C: "∴", 0x5D: "]", 0x5E: "⊥",
    0x5F: "_", 0x60: "‾",
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι", 0x6A: "ϕ",
    0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x74: "τ",
    0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ", 0x7B: "{", 0x7C: "|", 0x7D: "}", 0x7E: "∼",
    0xA0: "€", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞", 0xA6: "ƒ", 0xA7: "♣", 0xA8: "♦", 0xA9: "♥",
    0xAA: "♠", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°", 0xB1: "±", 0xB2: "″", 0xB3: "≥",
    0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈", 0xBC: "…", 0xBD: "⏐",
    0xBE: "⎯", 0xBF: "↵", 0xC0: "ℵ", 0xC1: "ℑ", 0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗", 0xC5: "⊕", 0xC6: "∅", 0xC7: "∩",
    0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇", 0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉", 0xD0: "∠", 0xD1: "∇",
    0xD2: "®", 0xD3: "©", 0xD4: "™", 0xD5: "∏", 0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔",
    0xDC: "⇐", 0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓", 0xE0: "◊", 0xE1: "〈", 0xE2: "®", 0xE3: "©", 0xE4: "™", 0xE5: "∑",
    0xE6: "⎛", 0xE7: "⎜", 0xE8: "⎝", 0xE9: "⎡", 0xEA: "⎢", 0xEB: "⎣", 0xEC: "⎧", 0xED: "⎨", 0xEE: "⎩", 0xEF: "⎪",
    0xF1: "〉", 0xF2: "∫", 0xF3: "⌠", 0xF4: "⎮", 0xF5: "⌡", 0xF6: "⎞", 0xF7: "⎟", 0xF8: "⎠", 0xF9: "⎤", 0xFA: "⎥",
    0xFB: "⎦", 0xFC: "⎫", 0xFD: "⎬", 0xFE: "⎭",
}


def map_symbol_char(ch: str) -> str | None:
    """Unicode equivalent of one Symbol-font character (ASCII or F0xx PUA form); None if unmapped."""
    cp = ord(ch)
    if 0xF000 <= cp <= 0xF0FF:
        cp -= 0xF000
    if 0x30 <= cp <= 0x39:
        return chr(cp)
    return _SYMBOL.get(cp)


def map_symbol_text(text: str) -> tuple[str, int]:
    """Map a Symbol-font run; returns (mapped text, number of unmapped characters kept as-is)."""
    out = []
    unmapped = 0
    for ch in text:
        m = map_symbol_char(ch)
        if m is None:
            out.append(ch)
            unmapped += 1
        else:
            out.append(m)
    return "".join(out), unmapped


def font_kind(font: str | None) -> str | None:
    if not font:
        return None
    f = font.strip().lower()
    if f in SYMBOL_FONT_NAMES:
        return "symbol"
    if f in DINGBAT_FONT_NAMES:
        return "dingbat"
    return None
