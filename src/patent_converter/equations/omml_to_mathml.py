"""Office Math (OMML) -> MathML Core converter.

Covers the OMML constructs Word produces for patent claims: fractions, scripts
(sup/sub/pre), radicals, n-ary operators, delimiters, matrices, accents, bars,
boxes, equation arrays, functions, group characters, limits and phantoms.

Any construct this converter does not understand is reported in
``MathResult.unsupported``; the caller then renders the *original OMML* to an
image instead (never a partial MathML rendering). Every ``m:t`` character is
emitted exactly once (``MathResult.text_chars``) so integrity can be verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape

from lxml import etree

from ..ooxml.namespaces import M, MC, W, qn

MATHML_NS = "http://www.w3.org/1998/Math/MathML"
_VAL = qn(M, "val")

# Combining accents -> spacing forms recommended in MathML.
_ACCENTS = {"̀": "`", "́": "´", "̂": "^", "̃": "~", "̄": "¯", "̅": "¯",
            "̆": "˘", "̇": "˙", "̈": "¨", "̌": "ˇ", "⃖": "←", "⃗": "→", "⃡": "↔"}
_TOKEN = re.compile(r"(?P<num>[0-9]+(?:[.,][0-9]+)*)|(?P<alpha>[^\W\d_])|(?P<space>\s)|(?P<other>.)", re.UNICODE | re.DOTALL)
_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LARGE_OPS_SUBSUP = {"∫", "∬", "∭", "∮", "∯", "∰", "∱", "∲", "∳"}


@dataclass
class MathResult:
    mathml: str
    linear: str
    text_chars: int = 0
    unsupported: set[str] = field(default_factory=set)

    @property
    def faithful(self) -> bool:
        return not self.unsupported


def _local(el: etree._Element) -> tuple[str, str]:
    tag = el.tag
    if not isinstance(tag, str) or not tag.startswith("{"):
        return "", str(tag)
    ns, local = tag[1:].split("}", 1)
    return ns, local


def _prop(parent: etree._Element, pr_name: str, name: str, default: str | None = None) -> str | None:
    pr = parent.find(qn(M, pr_name))
    if pr is None:
        return default
    el = pr.find(qn(M, name))
    if el is None:
        return default
    return el.get(_VAL, "1" if default is None else default)


def _flag(parent: etree._Element, pr_name: str, name: str) -> bool:
    v = _prop(parent, pr_name, name)
    return v is not None and v not in ("0", "off", "false")


class _Converter:
    def __init__(self) -> None:
        self.unsupported: set[str] = set()
        self.text_chars = 0
        self.linear: list[str] = []

    # -- helpers ----------------------------------------------------------------
    def children(self, el: etree._Element | None) -> str:
        if el is None:
            return "<mrow></mrow>"
        parts = [self.node(c) for c in el]
        parts = [p for p in parts if p]
        if len(parts) == 1:
            return parts[0]
        return "<mrow>" + "".join(parts) + "</mrow>"

    def arg(self, el: etree._Element, name: str) -> str:
        return self.children(el.find(qn(M, name)))

    @staticmethod
    def mo(ch: str, **attrs: str) -> str:
        a = "".join(f' {k}="{escape(v)}"' for k, v in attrs.items())
        return f"<mo{a}>{escape(ch)}</mo>"

    # -- text runs --------------------------------------------------------------
    def run(self, r: etree._Element) -> str:
        text = "".join(t.text or "" for t in r.iter(qn(M, "t"), qn(W, "t")))
        self.text_chars += len(text)
        self.linear.append(text)
        rpr = r.find(qn(M, "rPr"))
        normal = False
        variant = None
        if rpr is not None:
            if rpr.find(qn(M, "nor")) is not None and rpr.find(qn(M, "nor")).get(_VAL, "1") not in ("0", "off"):
                normal = True
            sty = rpr.find(qn(M, "sty"))
            if sty is not None:
                variant = {"p": "normal", "b": "bold", "i": "italic", "bi": "bold-italic"}.get(sty.get(_VAL, ""))
            scr = rpr.find(qn(M, "scr"))
            if scr is not None:
                variant = {"double-struck": "double-struck", "fraktur": "fraktur", "script": "script",
                           "sans-serif": "sans-serif", "monospace": "monospace"}.get(scr.get(_VAL, ""), variant)
        if not text:
            return ""
        if normal or _ARABIC.search(text):
            return f"<mtext>{escape(text)}</mtext>"
        out = []
        for m in _TOKEN.finditer(text):
            if m.group("num"):
                out.append(f"<mn>{escape(m.group('num'))}</mn>")
            elif m.group("alpha"):
                ch = m.group("alpha")
                attr = f' mathvariant="{variant}"' if variant and not (variant == "italic") else ""
                if variant == "normal" or variant is None:
                    attr = ' mathvariant="normal"' if variant == "normal" else ""
                out.append(f"<mi{attr}>{escape(ch)}</mi>")
            elif m.group("space"):
                out.append(f"<mtext>{escape(m.group('space'))}</mtext>")
            else:
                out.append(self.mo(m.group("other")))
        return "".join(out) if len(out) == 1 else "<mrow>" + "".join(out) + "</mrow>"

    # -- dispatcher ---------------------------------------------------------------
    def node(self, el: etree._Element) -> str:
        ns, local = _local(el)
        if not ns:
            return ""
        if ns == W:
            if local == "r":  # ordinary text run inside math
                text = "".join(t.text or "" for t in el.iter(qn(W, "t")))
                self.text_chars += len(text)
                self.linear.append(text)
                return f"<mtext>{escape(text)}</mtext>" if text else ""
            if local in ("bookmarkStart", "bookmarkEnd", "proofErr", "rPr", "permStart", "permEnd"):
                return ""
            self.unsupported.add(f"w:{local}")
            return ""
        if ns == MC and local == "AlternateContent":
            self.unsupported.add("mc:AlternateContent")
            return ""
        if ns != M:
            self.unsupported.add(f"{{{ns}}}{local}")
            return ""
        if local.endswith("Pr"):
            return ""
        handler = getattr(self, "m_" + local, None)
        if handler is None:
            self.unsupported.add(f"m:{local}")
            return ""
        return handler(el)

    # -- constructs -----------------------------------------------------------------
    def m_r(self, el):
        return self.run(el)

    def m_oMath(self, el):
        return self.children(el)

    def m_e(self, el):
        return self.children(el)

    m_num = m_den = m_sub = m_sup = m_deg = m_lim = m_fName = m_e

    def m_f(self, el):
        ftype = _prop(el, "fPr", "type", "bar")
        self.linear.append("(")
        num = self.arg(el, "num")
        self.linear.append(")/(")
        den = self.arg(el, "den")
        self.linear.append(")")
        if ftype == "lin":
            return f"<mrow>{num}{self.mo('/')}{den}</mrow>"
        if ftype == "skw":
            return f'<mfrac bevelled="true">{num}{den}</mfrac>'
        if ftype == "noBar":
            return f'<mfrac linethickness="0">{num}{den}</mfrac>'
        return f"<mfrac>{num}{den}</mfrac>"

    def m_sSup(self, el):
        base = self.arg(el, "e")
        self.linear.append("^")
        return f"<msup>{base}{self.arg(el, 'sup')}</msup>"

    def m_sSub(self, el):
        base = self.arg(el, "e")
        self.linear.append("_")
        return f"<msub>{base}{self.arg(el, 'sub')}</msub>"

    def m_sSubSup(self, el):
        base = self.arg(el, "e")
        self.linear.append("_")
        sub = self.arg(el, "sub")
        self.linear.append("^")
        return f"<msubsup>{base}{sub}{self.arg(el, 'sup')}</msubsup>"

    def m_sPre(self, el):
        sub = self.arg(el, "sub")
        sup = self.arg(el, "sup")
        base = self.arg(el, "e")
        return f"<mmultiscripts>{base}<mprescripts/>{sub}{sup}</mmultiscripts>"

    def m_rad(self, el):
        deg_el = el.find(qn(M, "deg"))
        hide = _flag(el, "radPr", "degHide")
        self.linear.append("√(")
        if hide or deg_el is None or len([c for c in deg_el if _local(c)[1] != "ctrlPr"]) == 0:
            body = self.arg(el, "e")
            self.linear.append(")")
            return f"<msqrt>{body}</msqrt>"
        deg = self.children(deg_el)
        body = self.arg(el, "e")
        self.linear.append(")")
        return f"<mroot>{body}{deg}</mroot>"

    def m_nary(self, el):
        ch = _prop(el, "naryPr", "chr", "∫") or "∫"
        loc = _prop(el, "naryPr", "limLoc", None) or ("subSup" if ch in _LARGE_OPS_SUBSUP else "undOvr")
        sub_hide = _flag(el, "naryPr", "subHide")
        sup_hide = _flag(el, "naryPr", "supHide")
        self.linear.append(ch)
        op = self.mo(ch, largeop="true", movablelimits="false")
        sub = None if sub_hide else self.arg(el, "sub")
        sup = None if sup_hide else self.arg(el, "sup")
        body = self.arg(el, "e")
        if loc == "undOvr":
            if sub and sup:
                core = f"<munderover>{op}{sub}{sup}</munderover>"
            elif sub:
                core = f"<munder>{op}{sub}</munder>"
            elif sup:
                core = f"<mover>{op}{sup}</mover>"
            else:
                core = op
        else:
            if sub and sup:
                core = f"<msubsup>{op}{sub}{sup}</msubsup>"
            elif sub:
                core = f"<msub>{op}{sub}</msub>"
            elif sup:
                core = f"<msup>{op}{sup}</msup>"
            else:
                core = op
        return f"<mrow>{core}{body}</mrow>"

    def m_d(self, el):
        beg = _prop(el, "dPr", "begChr", "(")
        end = _prop(el, "dPr", "endChr", ")")
        sep = _prop(el, "dPr", "sepChr", "|")
        parts = []
        if beg:
            parts.append(self.mo(beg, fence="true", stretchy="true"))
            self.linear.append(beg)
        es = el.findall(qn(M, "e"))
        for i, e in enumerate(es):
            if i:
                parts.append(self.mo(sep, separator="true"))
                self.linear.append(sep)
            parts.append(self.children(e))
        if end:
            parts.append(self.mo(end, fence="true", stretchy="true"))
            self.linear.append(end)
        return "<mrow>" + "".join(parts) + "</mrow>"

    def m_m(self, el):
        rows = []
        for mr in el.findall(qn(M, "mr")):
            cells = "".join(f"<mtd>{self.children(e)}</mtd>" for e in mr.findall(qn(M, "e")))
            rows.append(f"<mtr>{cells}</mtr>")
        return "<mtable>" + "".join(rows) + "</mtable>"

    def m_acc(self, el):
        ch = _prop(el, "accPr", "chr", "̂") or "̂"
        body = self.arg(el, "e")
        return f'<mover accent="true">{body}{self.mo(_ACCENTS.get(ch, ch), stretchy="false")}</mover>'

    def m_bar(self, el):
        pos = _prop(el, "barPr", "pos", "bot")
        body = self.arg(el, "e")
        if pos == "top":
            return f'<mover accent="true">{body}{self.mo("¯", stretchy="true")}</mover>'
        return f'<munder accentunder="true">{body}{self.mo("_", stretchy="true")}</munder>'

    def m_box(self, el):
        return self.arg(el, "e")

    def m_borderBox(self, el):
        return f'<menclose notation="box">{self.arg(el, "e")}</menclose>'

    def m_eqArr(self, el):
        rows = "".join(f"<mtr><mtd>{self.children(e)}</mtd></mtr>" for e in el.findall(qn(M, "e")))
        return f'<mtable columnalign="left">{rows}</mtable>'

    def m_func(self, el):
        name = self.arg(el, "fName")
        name = re.sub(r"<mi>([^<]*)</mi>", r'<mi mathvariant="normal">\1</mi>', name)
        return f"<mrow>{name}{self.mo(chr(0x2061))}{self.arg(el, 'e')}</mrow>"

    def m_groupChr(self, el):
        ch = _prop(el, "groupChrPr", "chr", "⏟") or "⏟"
        pos = _prop(el, "groupChrPr", "pos", "bot")
        body = self.arg(el, "e")
        op = self.mo(ch, stretchy="true")
        return f"<mover>{body}{op}</mover>" if pos == "top" else f"<munder>{body}{op}</munder>"

    def m_limLow(self, el):
        return f"<munder>{self.arg(el, 'e')}{self.arg(el, 'lim')}</munder>"

    def m_limUpp(self, el):
        return f"<mover>{self.arg(el, 'e')}{self.arg(el, 'lim')}</mover>"

    def m_phant(self, el):
        body = self.arg(el, "e")
        if _flag(el, "phantPr", "show"):
            return body
        return f"<mphantom>{body}</mphantom>"


def omml_to_mathml(omath: etree._Element, display: bool) -> MathResult:
    """Convert one ``m:oMath`` element."""
    conv = _Converter()
    body = conv.children(omath)
    attr = ' display="block"' if display else ""
    return MathResult(
        mathml=f'<math xmlns="{MATHML_NS}"{attr}>{body}</math>',
        linear="".join(conv.linear),
        text_chars=conv.text_chars,
        unsupported=conv.unsupported,
    )
