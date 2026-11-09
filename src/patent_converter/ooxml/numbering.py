"""Resolution of Word list-numbering labels (w:numPr).

Patent claims are frequently numbered with Word automatic numbering, in which
case the claim number never appears in ``w:t`` text. This module reproduces the
label Word would display so that claim-start patterns can match it.

Known approximations (reported, never hidden):
* Counters are kept per abstract numbering definition; ``w:startOverride`` on a
  ``w:num`` restarts the list on first use of that num instance.
* ``w:lvlRestart`` and legal-style numbering (``w:isLgl``) are honoured;
  number formats outside ``_FORMATTERS`` are recorded as unsupported and
  rendered as decimal.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

from .namespaces import W, qn

_W_VAL = qn(W, "val")
_ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_ARABIC_ALPHA = "أبتثجحخدذرزسشصضطظعغفقكلمنهوي"
_ARABIC_ABJAD = "أبجدهوزحطيكلمنسعفصقرشتثخذضظغ"


def _roman(n: int) -> str:
    if n <= 0:
        return str(n)
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
            (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = []
    for v, s in vals:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


def _letters(n: int, alphabet: str) -> str:
    # Word repeats the letter: a..z, aa..zz, aaa...
    if n <= 0:
        return str(n)
    size = len(alphabet)
    return alphabet[(n - 1) % size] * ((n - 1) // size + 1)


_FORMATTERS = {
    "decimal": str,
    "decimalZero": lambda n: f"{n:02d}",
    "upperRoman": _roman,
    "lowerRoman": lambda n: _roman(n).lower(),
    "upperLetter": lambda n: _letters(n, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    "lowerLetter": lambda n: _letters(n, "abcdefghijklmnopqrstuvwxyz"),
    "hindiNumbers": lambda n: str(n).translate(_ARABIC_INDIC),
    "arabicAlpha": lambda n: _letters(n, _ARABIC_ALPHA),
    "arabicAbjad": lambda n: _letters(n, _ARABIC_ABJAD),
    "none": lambda n: "",
}


@dataclass
class Level:
    start: int = 1
    num_fmt: str = "decimal"
    lvl_text: str = "%1."
    suffix: str = "tab"
    restart: int | None = None
    is_legal: bool = False


@dataclass
class NumberingResolver:
    abstract_levels: dict[str, dict[int, Level]] = field(default_factory=dict)
    num_to_abstract: dict[str, str] = field(default_factory=dict)
    num_level_overrides: dict[str, dict[int, Level]] = field(default_factory=dict)
    num_start_overrides: dict[str, dict[int, int]] = field(default_factory=dict)
    style_numpr: dict[str, tuple[str | None, int | None]] = field(default_factory=dict)
    style_based_on: dict[str, str] = field(default_factory=dict)
    unsupported_formats: Counter = field(default_factory=Counter)
    _counters: dict[str, dict[int, int]] = field(default_factory=dict)
    _started_nums: set[str] = field(default_factory=set)

    # -- parsing ---------------------------------------------------------------
    @staticmethod
    def _parse_level(lvl: etree._Element, base: Level | None = None) -> Level:
        level = Level(**vars(base)) if base else Level()
        for child in lvl:
            local = etree.QName(child).localname
            val = child.get(_W_VAL)
            if local == "start" and val is not None:
                level.start = int(val)
            elif local == "numFmt" and val is not None:
                level.num_fmt = val
            elif local == "lvlText" and val is not None:
                level.lvl_text = val
            elif local == "suff" and val is not None:
                level.suffix = val
            elif local == "lvlRestart" and val is not None:
                level.restart = int(val)
            elif local == "isLgl":
                level.is_legal = val not in ("0", "false", "off")
        return level

    def load_numbering(self, root: etree._Element) -> None:
        for absn in root.iter(qn(W, "abstractNum")):
            aid = absn.get(qn(W, "abstractNumId"), "")
            self.abstract_levels[aid] = {
                int(lvl.get(qn(W, "ilvl"), "0")): self._parse_level(lvl) for lvl in absn.iter(qn(W, "lvl"))
            }
        for num in root.iter(qn(W, "num")):
            nid = num.get(qn(W, "numId"), "")
            abs_el = num.find(qn(W, "abstractNumId"))
            if abs_el is not None:
                self.num_to_abstract[nid] = abs_el.get(_W_VAL, "")
            for ov in num.iter(qn(W, "lvlOverride")):
                ilvl = int(ov.get(qn(W, "ilvl"), "0"))
                so = ov.find(qn(W, "startOverride"))
                if so is not None and so.get(_W_VAL) is not None:
                    self.num_start_overrides.setdefault(nid, {})[ilvl] = int(so.get(_W_VAL))
                lvl = ov.find(qn(W, "lvl"))
                if lvl is not None:
                    self.num_level_overrides.setdefault(nid, {})[ilvl] = self._parse_level(lvl)

    def load_styles(self, root: etree._Element) -> None:
        for style in root.iter(qn(W, "style")):
            sid = style.get(qn(W, "styleId"), "")
            based = style.find(qn(W, "basedOn"))
            if based is not None:
                self.style_based_on[sid] = based.get(_W_VAL, "")
            numpr = style.find(f"{qn(W, 'pPr')}/{qn(W, 'numPr')}")
            if numpr is not None:
                num_id_el = numpr.find(qn(W, "numId"))
                ilvl_el = numpr.find(qn(W, "ilvl"))
                self.style_numpr[sid] = (
                    num_id_el.get(_W_VAL) if num_id_el is not None else None,
                    int(ilvl_el.get(_W_VAL, "0")) if ilvl_el is not None else None,
                )

    # -- resolution -------------------------------------------------------------
    def paragraph_numpr(self, ppr: etree._Element | None) -> tuple[str, int] | None:
        """Effective (numId, ilvl) for a paragraph, honouring style inheritance."""
        num_id: str | None = None
        ilvl: int | None = None
        style_id: str | None = None
        if ppr is not None:
            numpr = ppr.find(qn(W, "numPr"))
            if numpr is not None:
                n = numpr.find(qn(W, "numId"))
                i = numpr.find(qn(W, "ilvl"))
                num_id = n.get(_W_VAL) if n is not None else None
                ilvl = int(i.get(_W_VAL, "0")) if i is not None else None
            ps = ppr.find(qn(W, "pStyle"))
            style_id = ps.get(_W_VAL) if ps is not None else None
        seen: set[str] = set()
        while (num_id is None or ilvl is None) and style_id and style_id not in seen:
            seen.add(style_id)
            s_num, s_ilvl = self.style_numpr.get(style_id, (None, None))
            num_id = num_id if num_id is not None else s_num
            ilvl = ilvl if ilvl is not None else s_ilvl
            style_id = self.style_based_on.get(style_id)
        if not num_id or num_id == "0":
            return None
        return num_id, ilvl or 0

    def _level(self, num_id: str, ilvl: int) -> Level | None:
        override = self.num_level_overrides.get(num_id, {}).get(ilvl)
        if override:
            return override
        aid = self.num_to_abstract.get(num_id)
        if aid is None:
            return None
        return self.abstract_levels.get(aid, {}).get(ilvl)

    def _format(self, level: Level, value: int, force_decimal: bool) -> str:
        fmt = "decimal" if force_decimal else level.num_fmt
        if fmt == "bullet":
            return ""
        formatter = _FORMATTERS.get(fmt)
        if formatter is None:
            self.unsupported_formats[fmt] += 1
            formatter = str
        return formatter(value)

    def next_label(self, num_id: str, ilvl: int) -> str | None:
        """Advance counters and return the displayed label, e.g. ``"3."``."""
        aid = self.num_to_abstract.get(num_id)
        level = self._level(num_id, ilvl)
        if aid is None or level is None:
            return None
        counters = self._counters.setdefault(aid, {})
        if num_id not in self._started_nums:
            self._started_nums.add(num_id)
            for lvl_idx, start in self.num_start_overrides.get(num_id, {}).items():
                counters[lvl_idx] = start - 1
                for deeper in [k for k in counters if k > lvl_idx]:
                    del counters[deeper]
        counters[ilvl] = counters.get(ilvl, level.start - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            lvl_def = self._level(num_id, deeper)
            if lvl_def is None or lvl_def.restart is None or lvl_def.restart > ilvl:
                del counters[deeper]
        if level.num_fmt == "bullet":
            return level.lvl_text

        def repl(m: re.Match[str]) -> str:
            ref = int(m.group(1)) - 1
            ref_level = self._level(num_id, ref) or Level()
            value = counters.get(ref, ref_level.start)
            return self._format(ref_level, value, force_decimal=level.is_legal and ref != ilvl)

        label = re.sub(r"%([1-9])", repl, level.lvl_text)
        suffix = {"tab": "\t", "space": " "}.get(level.suffix, "")
        return label + suffix
