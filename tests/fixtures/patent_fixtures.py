"""Synthetic bilingual patent fixtures.

The English and Arabic documents describe the same six claims with the same
structures (images, OMML, OLE, tables, drawings), but each in its own language
and direction. Claim text is synthetic, written for testing only.
"""

from __future__ import annotations

from pathlib import Path

from .docx_builder import (
    SVG_SAMPLE, DocxBuilder, anchored_image, cell, emf_bytes, grouped_drawing, hyperlink, inline_image, omath,
    omath_para, omml_fraction, omml_matrix, omml_nary, omml_rad, omml_sub, omml_sup, ole_object, para,
    png_bytes, raster_bytes, run, seq_field, sym, table, textbox, tracked_delete, tracked_insert, vml_image,
    wmf_bytes,
)

TEXT = {
    "en": {
        "field": "TECHNICAL FIELD",
        "desc_numbered": "In one embodiment, the compound is used as a catalyst.",
        "claims": "CLAIMS",
        "end": "END OF CLAIMS",
        "abstract": "ABSTRACT",
        "abstract_text": "A compound and a method of preparing it are disclosed.",
        "c1": "A compound of formula (I):",
        "c1_end": ["wherein R", ("1", "subscript"), " is selected from C", ("1", "subscript"), "-C", ("6", "subscript"),
                   " alkyl, and n is an integer from 1 to 3."],
        "c2": "The compound of claim 1, wherein the compound is represented by the structure:",
        "c2_end": "and pharmaceutically acceptable salts thereof.",
        "c3": "The compound of claim 1 or claim 2, prepared by the reaction scheme:",
        "c3_mid": ["A + 2B → C", ("2+", "superscript"), " (ΔH < 0, α-isomer, 25 °C ± 2 °C), wherein:"],
        "c3_end": "the catalyst is Pd/C, the yield being ≥ 95 %;",
        "c4": "A method comprising:",
        "c4_end": "heating the mixture to 80 °C for 2 h.",
        "c5": "The method of claim 4, wherein the matrix ",
        "c5_end": " is satisfied.",
        "c6": "The method of any one of claims 1 to 5, using the drawings:",
        "c6_link": ["see ", "LINK", " for details,"],
        "c6_end": "as shown in the figures.",
        "cell": ["Substituent", "R1", "R2", "Methyl", "Ethyl", "Values"],
        "box": "Text box: Markush group",
        "group": "Fig. 1",
        "dep": "The compound of claim {n}.",
    },
    "ar": {
        "field": "المجال التقني",
        "desc_numbered": "في أحد التجسيدات، يُستخدم المُرَكَّب كمحفز.",
        "claims": "عناصر الحماية",
        "end": "نهاية عناصر الحماية",
        "abstract": "الملخص",
        "abstract_text": "يتم الكشف عن مركب وطريقة لتحضيره.",
        "c1": "مُرَكَّب له الصيغة (I):",
        "c1_end": ["حيث R", ("1", "subscript"), " يتم اختياره من ألكيل C", ("1", "subscript"), "-C", ("6", "subscript"),
                   "، و n عدد صحيح من 1 إلى 3."],
        "c2": "المركب وفقاً لعنصر الحماية 1، حيث يتم تمثيل المركب بالبنية:",
        "c2_end": "والأملاح المقبولة صيدلانياً منه.",
        "c3": "المركب وفقاً لعنصر الحماية 1 أو 2، المحضر بمخطط التفاعل:",
        "c3_mid": ["A + 2B → C", ("2+", "superscript"), " (ΔH < 0، الأيزومر α، ‏25 °C ± 2 °C)، حيث:"],
        "c3_end": "يكون المحفز Pd/C، والمردود ≥ 95 ٪؛",
        "c4": "طريقة تشتمل على:",
        "c4_end": "تسخين الخليط إلى 80 °C لمدة ساعتين.",
        "c5": "الطريقة وفقاً لعنصر الحماية ٤، حيث المصفوفة ",
        "c5_end": " متحققة.",
        "c6": "الطريقة وفقاً لأي من عناصر الحماية ١ إلى ٥، باستخدام الرسومات:",
        "c6_link": ["انظر ", "LINK", " للتفاصيل،"],
        "c6_end": "كما هو موضح في الأشكال.",
        "cell": ["البديل", "R1", "R2", "ميثيل", "إيثيل", "القيم"],
        "box": "مربع نص: مجموعة ماركush",
        "group": "شكل ١",
        "dep": "المركب وفقاً لعنصر الحماية {n}.",
    },
}

# Claim-number rendering per language (Arabic mixes Western and Arabic-Indic digits).
_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _num(lang: str, n: int) -> str:
    if lang == "ar" and n >= 4:
        return f"{str(n).translate(_AR_DIGITS)}- "
    return f"{n}. "


def _runs(parts, rtl: bool) -> list[str]:
    out = []
    for p in parts:
        if isinstance(p, tuple):
            out.append(run(p[0], vert=p[1], rtl=rtl))
        else:
            out.append(run(p, rtl=rtl))
    return out


def build_patent(
    path: Path | str,
    lang: str,
    *,
    missing: set[int] = frozenset(),
    duplicate: int | None = None,
    unclosed: int | None = None,
    orphan_after: int | None = None,
    extra_claims: int = 0,
    autonumber: bool = False,
    reverse_order: bool = False,
) -> Path:
    """Build the six-claim synthetic patent (plus optional simple dependent claims)."""
    T = TEXT[lang]
    rtl = lang == "ar"
    b = DocxBuilder()
    numid = b.use_numbering() if autonumber else None
    P = lambda *c, **k: para(*c, rtl=rtl, **k)  # noqa: E731

    png = b.add_media(png_bytes(160, 100), "png")
    png2 = b.add_media(png_bytes(80, 80), "png")
    jpg = b.add_media(raster_bytes("JPEG"), "jpeg")
    gif = b.add_media(raster_bytes("GIF"), "gif")
    tif = b.add_media(raster_bytes("TIFF"), "tiff")
    emf = b.add_media(emf_bytes(), "emf")
    wmf = b.add_media(wmf_bytes(), "wmf")
    svg_fallback = b.add_media(png_bytes(120, 80), "png")
    svg = b.add_media(SVG_SAMPLE, "svg")
    ole_preview = b.add_media(emf_bytes(300, 150), "emf")
    ole = b.add_ole()
    link = b.add_hyperlink("https://example.org/patent-reference")

    def start(n: int, text_parts: list[str]) -> str:
        if autonumber:
            return P(*text_parts, num=(numid, 0))
        return P(run(_num(lang, n), rtl=rtl), *text_parts)

    claims: dict[int, list[str]] = {}
    claims[1] = [
        start(1, [run(T["c1"], rtl=rtl)]),
        P(inline_image(png, 160, 100, b.pid()), jc="center"),
        omath_para(omml_fraction("dy", "dx"), mr_text("="), omml_sup("x", "2")),
        P(*_runs(T["c1_end"], rtl)),
    ]
    merged = table([
        [cell(T["cell"][0], vmerge="restart", rtl=rtl), cell(T["cell"][5], span=2, rtl=rtl)],
        [cell("", vmerge="continue", rtl=rtl), cell(T["cell"][1], rtl=rtl), cell(T["cell"][2], rtl=rtl)],
        [cell("1", rtl=rtl), cell(T["cell"][3], rtl=rtl), cell(T["cell"][4], rtl=rtl)],
    ], cols=3, rtl=rtl)
    claims[2] = [
        start(2, [run(T["c2"], rtl=rtl)]),
        P(grouped_drawing(png, png2, T["group"], b.pid())),
        merged,
        P(run(T["c2_end"], rtl=rtl)),
    ]
    claims[3] = [
        start(3, [run(T["c3"], rtl=rtl)]),
        P(*_runs(T["c3_mid"], rtl)),
        P(ole_object(ole_preview, ole)),
        P(run(T["c3_end"], rtl=rtl)),
    ]
    inner = table([[cell(f"<w:p>{omath(omml_sub('H', '2'), mr_text('O'))}</w:p>"),
                    cell(P(inline_image(png2, 40, 40, b.pid())))]], cols=2, rtl=rtl)
    nested = table([[cell(T["cell"][0], rtl=rtl), cell(inner)]], cols=2, rtl=rtl)
    claims[4] = [
        start(4, [run(T["c4"], rtl=rtl)]),
        nested,
        P(anchored_image(png, 160, 100, b.pid())),
        P(textbox(T["box"], b.pid(), rtl=rtl)),
        P(run(T["c4_end"], rtl=rtl)),
    ]
    claims[5] = [
        start(5, [run(T["c5"], rtl=rtl), omath(omml_matrix([["a", "b"], ["c", "d"]])), run(" ", rtl=rtl),
                  omath(omml_nary("∑", "i=1", "n", "x"), mr_text("="), omml_rad("y")), run(T["c5_end"], rtl=rtl)]),
    ]
    link_parts = [hyperlink(link, "example.org") if x == "LINK" else run(x, rtl=rtl) for x in T["c6_link"]]
    claims[6] = [
        start(6, [run(T["c6"], rtl=rtl)]),
        P(inline_image(jpg, 100, 60, b.pid()), inline_image(gif, 100, 60, b.pid()),
          inline_image(tif, 100, 60, b.pid())),
        P(inline_image(emf, 200, 100, b.pid()), inline_image(wmf, 96, 48, b.pid())),
        P(inline_image(svg_fallback, 120, 80, b.pid(), svg_rid=svg), vml_image(png2)),
        P(*link_parts),
        P(run(T["c6_end"], rtl=rtl)),
    ]
    for k in range(extra_claims):
        n = 7 + k
        claims[n] = [start(n, [run(T["dep"].format(n=(n - 1) if lang == "en" else str(n - 1)), rtl=rtl)])]

    if unclosed is not None:
        last = claims[unclosed][-1]
        claims[unclosed][-1] = last.replace(".</w:t>", "</w:t>").replace("؛</w:t>", "</w:t>").replace(";</w:t>", "</w:t>")

    b.add(P(run(T["field"], rtl=rtl)))
    b.add(P(run(_num(lang, 1), rtl=rtl), run(T["desc_numbered"], rtl=rtl)))
    b.add(P(run(T["claims"], rtl=rtl)))
    order = sorted(claims, reverse=reverse_order)
    for n in order:
        if n in missing:
            continue
        b.add(*claims[n])
        if duplicate == n:
            b.add(*claims[n])
        if orphan_after == n:
            b.add(P(run("Orphan paragraph outside any claim" if lang == "en" else "فقرة خارج أي عنصر حماية", rtl=rtl)))
        b.add(P())  # empty spacer paragraph between claims
    b.add(P(run(T["end"], rtl=rtl)))
    b.add(P(run(T["abstract"], rtl=rtl)), P(run(T["abstract_text"], rtl=rtl)))
    return b.build(path)


def mr_text(text: str) -> str:
    return f"<m:r><m:t>{text}</m:t></m:r>"


def build_edge_cases(path: Path | str) -> Path:
    """English document exercising risky/rare constructs."""
    b = DocxBuilder()
    linked = b.add_linked_image("file:///C:/drawings/fig1.png")
    b.add(
        para("CLAIMS"),
        "<w:sdt><w:sdtPr><w:alias w:val=\"claim\"/></w:sdtPr><w:sdtContent>"
        + para(run("1. A composition comprising "), sym("Symbol", "F061"), run("-pinene and "),
               run("b", font="Symbol"), run("-carotene, "), tracked_insert("newly inserted "),
               tracked_delete("removed "), run("characterised by \ufffd and \ue001 and ﻻ."))
        + "</w:sdtContent></w:sdt>",
        para(seq_field("2"), run(". The composition of claim 1, wherein "),
             '<w:r><w:footnoteReference w:id="1"/></w:r>', '<w:r><w:commentReference w:id="0"/></w:r>',
             run("the image is linked:")),
        para(f'<w:r><w:drawing><wp:inline><wp:extent cx="952500" cy="952500"/><wp:docPr id="77" name="L"/>'
             '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
             f'<pic:nvPicPr><pic:cNvPr id="77" name="L"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:link="{linked}"/>'
             '</pic:blipFill><pic:spPr/></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'),
        para(run("and mojibake Ã© text.")),
        '<w:altChunk r:id="rIdMissing"/>',
        para(run("END OF CLAIMS")),
    )
    return b.build(path)


def build_large(path: Path | str, lang: str, claims: int, images: int, image_px: int = 1200, seed: int = 1,
                vector_every: int = 0, group_every: int = 0, equation_every: int = 10) -> Path:
    """Large document: ``claims`` claims, ``images`` incompressible PNGs spread across claims, plus optional
    EMF figures every ``vector_every`` claims and drawing groups every ``group_every`` claims."""
    T = TEXT[lang]
    rtl = lang == "ar"
    b = DocxBuilder()
    rids = [b.add_media(png_bytes(image_px, image_px, noise=True, seed=seed + i), "png") for i in range(images)]
    emf = b.add_media(emf_bytes(300, 150), "emf") if vector_every else None
    small = b.add_media(png_bytes(160, 100), "png") if group_every else None
    b.add(para(run(T["claims"], rtl=rtl), rtl=rtl))
    per = max(1, claims // max(images, 1))
    img_iter = iter(rids)
    filler = (T["c3"] + " ") * 6
    for n in range(1, claims + 1):
        b.add(para(run(_num(lang, n), rtl=rtl), run(filler, rtl=rtl), run(" :", rtl=rtl), rtl=rtl))
        if (n - 1) % per == 0:
            rid = next(img_iter, None)
            if rid:
                b.add(para(inline_image(rid, 400, 400, b.pid()), rtl=rtl))
        if vector_every and n % vector_every == 0:
            b.add(para(inline_image(emf, 300, 150, b.pid()), rtl=rtl))
        if group_every and n % group_every == 0:
            b.add(para(grouped_drawing(small, small, T["group"], b.pid()), rtl=rtl))
        if equation_every and n % equation_every == 0:
            b.add(omath_para(omml_fraction("a", "b"), omml_sup("x", "2")))
        b.add(para(run(T["c4_end"], rtl=rtl), rtl=rtl))
    b.add(para(run(T["end"], rtl=rtl), rtl=rtl))
    return b.build(path)
