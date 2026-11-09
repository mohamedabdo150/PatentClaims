"""Size estimates, risk evaluation and human-readable rendering of analysis reports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config.settings import MB, Settings

if TYPE_CHECKING:
    from ..config.claim_patterns import CompiledProfile

SEVERITY_ORDER = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


# --------------------------------------------------------------------------- estimates
def _image_output_bytes(info: Any, settings: Settings) -> int:
    """Estimated embedded bytes (before Base64) of one image occurrence."""
    a = settings.analysis
    if info is None:
        return 0
    if not info.requires_conversion:
        return info.size_bytes
    if info.detected_format in ("emf", "wmf") and info.width_px and info.height_px:
        scale = a.vector_render_dpi / 72.0
        return int(info.width_px * scale * info.height_px * scale * a.converted_vector_bytes_per_pixel)
    if info.pixels:
        return int(info.pixels * a.converted_vector_bytes_per_pixel)
    return info.size_bytes


def build_estimates(report: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Rough output-size estimate. Base64 HTML is usually much larger than the DOCX text."""
    index = report.pop("_imageIndex", {})
    a = settings.analysis
    total_text_chars = total_text_utf8 = total_media = total_html = equations = 0
    for claim in report.get("claims", {}).get("perClaim", []):
        media = sum(_image_output_bytes(index.get(p), settings) for p in claim["mediaParts"])
        b64 = (media + 2) // 3 * 4
        html = int(claim["textUtf8Bytes"] * a.markup_overhead_factor) + b64 \
            + claim["equations"] * a.equation_html_bytes_estimate + 64
        claim["estimatedHtmlBytes"] = html
        total_text_chars += claim["textChars"]
        total_text_utf8 += claim["textUtf8Bytes"]
        total_media += media
        total_html += html
        equations += claim["equations"]
    json_bytes = int(total_html * 1.03)  # quote escaping, keys, claimNumber
    # NVARCHAR stores UTF-16: markup/Base64 chars cost 2 bytes; Arabic chars also 2 bytes.
    nvarchar = 2 * (total_html - total_text_utf8 + total_text_chars)
    return {
        "method": "heuristic: text*markup_factor + base64(embedded/converted images) + equations*constant",
        "claimsTextChars": total_text_chars,
        "claimsTextUtf8Bytes": total_text_utf8,
        "claimsEmbeddedMediaBytes": total_media,
        "claimsEmbeddedMediaBase64Bytes": (total_media + 2) // 3 * 4,
        "claimsEquations": equations,
        "estimatedHtmlBytes": total_html,
        "estimatedJsonContributionBytes": json_bytes,
        "estimatedSqlNvarcharBytes": nvarchar,
        "maxResponseBytes": int(settings.output.max_response_size_mb * MB),
    }


# --------------------------------------------------------------------------- risks
def _risk(out: list, code: str, severity: str, message: str, count: int | None = None) -> None:
    item = {"code": code, "severity": severity, "message": message}
    if count is not None:
        item["count"] = count
    out.append(item)


def evaluate_risks(report: dict[str, Any], settings: Settings, profile: "CompiledProfile") -> list[dict]:
    risks: list[dict] = []
    for f in report.get("package", {}).get("findings", []):
        _risk(risks, f["code"], "blocker" if f.get("blocking", True) else "medium", f["message"])
    if report.get("status") == "rejected" and "structure" not in report:
        return sorted(risks, key=lambda r: SEVERITY_ORDER[r["severity"]])

    c = report.get("structure", {}).get("counts", {})
    st = report.get("structure", {})
    claims = report.get("claims", {})
    media = report.get("media", {})
    uni = report.get("unicode", {}).get("document", {})
    ox = report.get("ooxml", {})
    lang = report.get("file", {}).get("language")

    if not profile.verified:
        _risk(risks, "UNVERIFIED_PATTERN_PROFILE", "medium",
              f"claim pattern profile '{profile.name}' has not been verified against real documents")
    for issue in claims.get("issues", []):
        sev = "high" if issue["severity"] == "error" else "medium"
        _risk(risks, f"CLAIMS_{issue['code']}", sev, issue["message"])
    if claims.get("count", 0) and report.get("numbering", {}).get("claimsNumberedByWordNumbering"):
        _risk(risks, "CLAIMS_AUTO_NUMBERED", "medium",
              "claim numbers come from Word list numbering (not text); numbering resolution must be verified",
              report["numbering"]["claimsNumberedByWordNumbering"])
    if report.get("numbering", {}).get("unsupportedFormats"):
        _risk(risks, "UNSUPPORTED_NUMBERING_FORMAT", "high",
              f"unsupported list number formats: {sorted(report['numbering']['unsupportedFormats'])}")

    def chk(key: str, code: str, sev: str, msg: str) -> None:
        if c.get(key):
            _risk(risks, code, sev, msg, c[key])

    chk("altchunks", "ALTCHUNK_PRESENT", "high", "imported foreign content (altChunk) cannot be rendered faithfully")
    chk("subdocuments", "SUBDOCUMENTS_PRESENT", "high", "master/sub-document references")
    tracked = sum(v for k, v in c.items() if k.startswith("tracked_"))
    if tracked:
        _risk(risks, "TRACKED_CHANGES", "high",
              "unaccepted tracked changes present; a policy (reject/accept view) is required before conversion",
              tracked)
    chk("comment_references", "COMMENTS_PRESENT", "low", "review comments present (not rendered)")
    chk("footnote_references", "FOOTNOTE_REFERENCES", "medium", "footnote references (note content is outside claims)")
    chk("endnote_references", "ENDNOTE_REFERENCES", "medium", "endnote references (note content is outside claims)")
    chk("linked_only_images", "LINKED_EXTERNAL_IMAGES", "high",
        "images linked to external files are not in the package and cannot be embedded")
    if ox.get("externalNonHyperlinkRelationships"):
        _risk(risks, "EXTERNAL_RELATIONSHIPS", "medium",
              "external non-hyperlink relationships (never fetched)", ox["externalNonHyperlinkRelationships"])
    missing = st.get("missingRelationshipTargets", {})
    if missing:
        _risk(risks, "MISSING_RELATIONSHIP_TARGETS", "high", f"references to missing parts: {missing}", sum(missing.values()))
    if ox.get("unsafeRelationships"):
        _risk(risks, "UNSAFE_RELATIONSHIPS", "blocker", "relationship targets escaping the package", ox["unsafeRelationships"])
    chk("alternate_content_unresolved", "UNRESOLVED_ALTERNATE_CONTENT", "high",
        "mc:AlternateContent with no understood branch")

    fmts = media.get("formats", {})
    vec = fmts.get("emf", 0) + fmts.get("wmf", 0)
    if vec:
        _risk(risks, "VECTOR_METAFILES", "medium", "EMF/WMF images require isolated conversion to PNG/SVG", vec)
    if fmts.get("tiff"):
        _risk(risks, "TIFF_IMAGES", "low", "TIFF images must be converted to PNG", fmts["tiff"])
    bad_fmt = sum(fmts.get(k, 0) for k in ("unknown", "jxr", "pdf", "ole-cfb"))
    if bad_fmt:
        _risk(risks, "UNSUPPORTED_IMAGE_FORMAT", "high", "image parts in unsupported/unknown formats", bad_fmt)
    issue_counts: dict[str, int] = {}
    for img in media.get("images", []):
        for iss in img["issues"]:
            issue_counts[iss.split(":")[0]] = issue_counts.get(iss.split(":")[0], 0) + 1
    sev_map = {"IMAGE_PIXEL_LIMIT_EXCEEDED": "high", "IMAGE_DIMENSION_LIMIT_EXCEEDED": "high",
               "SVG_ACTIVE_CONTENT": "medium", "SVG_EXTERNAL_REFERENCE": "medium", "SVG_UNSAFE_XML": "high",
               "EXTENSION_FORMAT_MISMATCH": "low", "MULTI_FRAME_IMAGE": "medium", "IMAGE_HEADER_UNREADABLE": "high",
               "SVG_TOO_LARGE": "high", "SVG_PARSE_ERROR": "high"}
    for iss, n in issue_counts.items():
        if iss != "UNSUPPORTED_IMAGE_FORMAT":
            _risk(risks, f"IMAGE_{iss}" if not iss.startswith(("IMAGE_", "SVG_")) else iss,
                  sev_map.get(iss, "medium"), f"{n} image(s) flagged {iss}", n)

    chk("ole_objects", "OLE_OBJECTS", "medium",
        "embedded OLE objects (e.g. ChemDraw/Equation Editor) are rendered from their preview image only")
    chk("objects_without_preview", "OLE_WITHOUT_PREVIEW", "high", "embedded objects without a preview image")
    chk("charts", "CHARTS", "high", "DrawingML charts require rasterization (no cached image in DOCX)")
    chk("smartart", "SMARTART", "high", "SmartArt diagrams require rasterization")
    chk("activex_controls", "ACTIVEX_CONTROLS", "medium", "ActiveX controls (never executed)")
    if c.get("equations"):
        _risk(risks, "OMML_EQUATIONS", "medium",
              f"OMML equations require conversion; constructs used: {sorted(st.get('ommlConstructs', {}))}",
              c["equations"])
    ft = st.get("fieldTypes", {})
    if ft.get("EQ"):
        _risk(risks, "LEGACY_EQ_FIELDS", "high", "legacy EQ field equations", ft["EQ"])
    if ft.get("INCLUDEPICTURE"):
        _risk(risks, "INCLUDEPICTURE_FIELDS", "high", "INCLUDEPICTURE fields reference external images", ft["INCLUDEPICTURE"])
    if ft.get("EMBED"):
        _risk(risks, "EMBED_FIELDS", "info", "EMBED fields (OLE objects)", ft["EMBED"])
    chk("symbol_font_runs", "SYMBOL_FONT_TEXT", "high",
        "runs formatted with symbol fonts (e.g. Greek via 'Symbol'): characters must be mapped to Unicode")
    chk("symbols", "SYMBOL_CHARACTERS", "medium", "w:sym symbol-font characters (often Private Use Area codes)")
    if c.get("drawing_anchor"):
        _risk(risks, "FLOATING_DRAWINGS", "medium",
              "floating/anchored drawings: position is approximated in flow layout", c["drawing_anchor"])
    shapes = c.get("drawing_groups", 0) + c.get("drawing_canvases", 0) + c.get("drawing_shapes", 0)
    if shapes:
        _risk(risks, "DRAWINGML_SHAPES", "medium",
              "DrawingML shapes/groups/canvases require rendering to an image to preserve grouping", shapes)
    vml_shapes = c.get("vml_shapes", 0) - c.get("vml_imagedata", 0)
    if vml_shapes > 0 or c.get("vml_groups"):
        _risk(risks, "VML_SHAPES", "medium", "VML shapes/groups beyond simple images", max(vml_shapes, 0) + c.get("vml_groups", 0))
    chk("textboxes", "TEXT_BOXES", "low", "text boxes (rendered as inline blocks)")
    unknown = st.get("unknownElements", {})
    if unknown:
        _risk(risks, "UNKNOWN_ELEMENTS", "medium", f"unrecognized elements: {sorted(unknown)[:15]}", sum(unknown.values()))

    if uni.get("replacement_chars"):
        _risk(risks, "REPLACEMENT_CHARACTERS", "high", "U+FFFD replacement characters already in source text", uni["replacement_chars"])
    if uni.get("suspicious_encoding_sequences"):
        _risk(risks, "SUSPECTED_MOJIBAKE", "high", "character sequences typical of mis-decoded UTF-8",
              uni["suspicious_encoding_sequences"])
    if uni.get("private_use_chars"):
        _risk(risks, "PRIVATE_USE_CHARACTERS", "medium", "Private Use Area characters (font-specific glyphs)", uni["private_use_chars"])
    if uni.get("control_chars") or uni.get("invalid_xml_chars"):
        _risk(risks, "CONTROL_CHARACTERS", "medium", "control/invalid XML characters",
              uni.get("control_chars", 0) + uni.get("invalid_xml_chars", 0))
    if uni.get("arabic_presentation_forms"):
        _risk(risks, "ARABIC_PRESENTATION_FORMS", "medium",
              "Arabic presentation-form characters (legacy shaped text; preserved, not normalized)",
              uni["arabic_presentation_forms"])
    if lang == "ar" and uni.get("arabic_chars") and not c.get("rtl_paragraphs"):
        _risk(risks, "ARABIC_WITHOUT_BIDI_PARAGRAPHS", "low", "Arabic text without w:bidi paragraphs; RTL applied by language")
    if lang == "en" and uni.get("arabic_chars", 0) > max(50, uni.get("latin_chars", 0)):
        _risk(risks, "LANGUAGE_MISMATCH", "high", "'English' document is predominantly Arabic text")
    if lang == "ar" and uni.get("latin_chars", 0) > max(50, 2 * uni.get("arabic_chars", 0)):
        _risk(risks, "LANGUAGE_MISMATCH", "high", "'Arabic' document is predominantly Latin text")

    est = report.get("estimates", {})
    if est and est.get("estimatedJsonContributionBytes", 0) > est.get("maxResponseBytes", 0):
        _risk(risks, "ESTIMATED_RESPONSE_EXCEEDS_LIMIT", "high",
              f"estimated JSON contribution {est['estimatedJsonContributionBytes'] / MB:.1f} MB exceeds "
              f"output.max_response_size_mb={settings.output.max_response_size_mb}")
    return sorted(risks, key=lambda r: SEVERITY_ORDER[r["severity"]])


# --------------------------------------------------------------------------- rendering
def _mb(n: int | float) -> str:
    return f"{n / MB:.2f} MB"


def render_text(report: dict[str, Any]) -> str:
    """Human-readable report (never includes patent text unless samples were requested)."""
    f = report["file"]
    pk = report.get("package", {})
    lines = [
        "=" * 78,
        f"DOCX CAPABILITY REPORT  [{f['language'].upper()}]  {f['name']}",
        "=" * 78,
        f"Status                 : {report['status'].upper()}",
        f"File size              : {_mb(f['sizeBytes'])}   sha256 {f['sha256'][:16]}…",
        f"Uncompressed package   : {_mb(pk.get('uncompressed_size', 0))}  (ratio {pk.get('overall_compression_ratio', 0)}:1)",
        f"ZIP entries            : {pk.get('entry_count', 0)}",
        f"Media entries          : {pk.get('media_entry_count', 0)} ({_mb(pk.get('media_total_size', 0))})",
        f"Embeddings             : {pk.get('embedding_entry_count', 0)} ({_mb(pk.get('embedding_total_size', 0))})",
    ]
    if "structure" in report:
        c = report["structure"]["counts"]
        ox = report["ooxml"]
        cl = report["claims"]
        md = report["media"]
        est = report["estimates"]
        g = c.get
        lines += [
            f"document.xml           : {_mb(ox['documentXmlBytes'])}",
            f"Relationships          : {ox['relationshipCount']} {ox['relationshipTypes']}",
            "-" * 78,
            f"Paragraphs (all/top)   : {g('paragraphs', 0)} / {report['structure']['topLevelBlocks'].get('paragraph', 0)}",
            f"Tables (nested)        : {g('tables', 0)} ({g('nested_tables', 0)}), merged cells h/v "
            f"{g('merged_cells_horizontal', 0)}/{g('merged_cells_vertical', 0)}",
            f"RTL paragraphs / runs  : {g('rtl_paragraphs', 0)} / {g('rtl_runs', 0)}",
            f"Drawings inline/anchor : {g('drawings', 0)} ({g('drawing_inline', 0)}/{g('drawing_anchor', 0)}), "
            f"pictures {g('pictures', 0)}, groups {g('drawing_groups', 0)}, shapes {g('drawing_shapes', 0)}",
            f"VML pict/shapes/images : {g('vml_pict', 0)} / {g('vml_shapes', 0)} / {g('vml_imagedata', 0)}",
            f"Text boxes             : {g('textboxes', 0)}",
            f"OMML equations         : {g('equations', 0)} (display paragraphs {g('equation_paragraphs', 0)}) "
            f"constructs {report['structure']['ommlConstructs']}",
            f"OLE objects            : {g('ole_objects', 0)} {report['structure']['oleProgIds']}",
            f"Hyperlinks             : {g('hyperlinks', 0)}   fields {report['structure']['fieldTypes']}",
            f"Symbols (w:sym)        : {g('symbols', 0)}  symbol-font runs {g('symbol_font_runs', 0)} "
            f"{report['structure']['symbolFonts']}",
            f"Charts / SmartArt      : {g('charts', 0)} / {g('smartart', 0)}",
            f"Unknown elements       : {report['structure']['unknownElements'] or 'none'}",
            "-" * 78,
            f"Images                 : {md['count']} ({_mb(md['totalBytes'])}) formats {md['formats']}",
            f"  referenced in claims : {md['referencedInClaims']} parts / {md['imageReferencesInClaims']} references",
            f"  requiring conversion : {md['requiringConversion']}   max pixels {md['maxPixels']:,}",
            "-" * 78,
            f"Claim profile          : {cl['profile']} (verified={cl['profileVerified']}, end mode {cl['endMode']})",
            f"Claims section found   : {cl['sectionFound']} (blocks {cl['sectionStartBlock']}..{cl['sectionEndBlock']})",
            f"Claims detected        : {cl['count']}  numbers {_range_summary(cl['numbers'])}",
            f"Duplicates / orphans   : {cl['duplicates'] or 'none'} / {cl['orphanBlocks']}",
        ]
        for issue in cl["issues"]:
            lines.append(f"  [{issue['severity'].upper():7}] {issue['code']}: {issue['message']}")
        lines += [
            "-" * 78,
            f"Estimated claims HTML  : {_mb(est['estimatedHtmlBytes'])} (JSON ~{_mb(est['estimatedJsonContributionBytes'])},"
            f" SQL NVARCHAR ~{_mb(est['estimatedSqlNvarcharBytes'])})",
            f"Unicode (document)     : {report['unicode']['document']}",
        ]
    lines.append("-" * 78)
    lines.append("RISKS")
    for r in report.get("risks", []):
        cnt = f" (x{r['count']})" if "count" in r else ""
        lines.append(f"  [{r['severity'].upper():7}] {r['code']}{cnt}: {r['message']}")
    if not report.get("risks"):
        lines.append("  none")
    t = report.get("timingsSeconds", {})
    lines.append(f"Timings (s)            : {t}")
    if "resources" in report:
        lines.append(f"Resources              : {report['resources']}")
    return "\n".join(lines)


def _range_summary(numbers: list[str]) -> str:
    if not numbers:
        return "[]"
    if len(numbers) <= 12:
        return "[" + ", ".join(numbers) + "]"
    return "[" + ", ".join(numbers[:5]) + ", …, " + ", ".join(numbers[-3:]) + "]"
