from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from reporting.ncrb_reporter import ComplaintDraft

logger = logging.getLogger(__name__)

MODULE_DIR = Path(__file__).parent
PDF_OUTPUT_DIR = MODULE_DIR / "generated_pdfs"
PDF_OUTPUT_DIR.mkdir(exist_ok=True)


# -----------------------------------------------------
# FONT REGISTRATION & UNICODE HANDLING
# -----------------------------------------------------
#
# Candidate locations for the Tamil/regional TTF files. Using a path relative
# to the *current working directory* (as the original code did) is fragile:
# it only works if the process happens to be launched from this exact
# folder. Resolving relative to this module's own location makes font
# discovery independent of how/where the script is invoked.

FONT_DIR_CANDIDATES = [
    MODULE_DIR / "fonts",
    MODULE_DIR.parent / "fonts",
    Path("fonts"),  # last resort: still honor an explicit relative override
]

REGULAR_CANDIDATES = ["NotoSansTamil-Regular.ttf", "NotoSansTamil[wdth,wght].ttf"]
BOLD_CANDIDATES = ["NotoSansTamil-Bold.ttf"]

# Candidate regular-weight filenames per script, used when rasterizing the
# statement-of-fact text as an image (see RASTERIZED TEXT RENDERING below).
# Only Tamil is wired into the ReportLab-native font path above, but any of
# these scripts can go through the image path if a matching file exists.
SCRIPT_FONT_CANDIDATES: dict[str, list[str]] = {
    "ta": ["NotoSansTamil-Regular.ttf", "NotoSansTamil[wdth,wght].ttf"],
    "hi": ["NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari[wdth,wght].ttf"],
    "te": ["NotoSansTelugu-Regular.ttf", "NotoSansTelugu[wdth,wght].ttf"],
    "kn": ["NotoSansKannada-Regular.ttf", "NotoSansKannada[wdth,wght].ttf"],
    "ml": ["NotoSansMalayalam-Regular.ttf", "NotoSansMalayalam[wdth,wght].ttf"],
    "bn": ["NotoSansBengali-Regular.ttf", "NotoSansBengali[wdth,wght].ttf"],
}

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"
HAS_CUSTOM_FONT = False
HAS_CUSTOM_BOLD = False


def _find_font_file(filenames: list[str]) -> Path | None:
    for directory in FONT_DIR_CANDIDATES:
        for name in filenames:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def _font_path_for_language(lang: str) -> Path | None:
    """Locate a regular-weight font file capable of rendering the given
    language's script, for use by the image-rasterization path."""
    candidates = SCRIPT_FONT_CANDIDATES.get(lang)
    if not candidates:
        return None
    found = _find_font_file(candidates)
    if found is None:
        logger.warning(
            "No font file found for language '%s'. Looked for %s in %s. "
            "Rasterized rendering for this language will not be available.",
            lang, candidates, [str(d) for d in FONT_DIR_CANDIDATES],
        )
    return found


# Register regular and bold independently so a missing/corrupt bold file
# doesn't silently disable Unicode support altogether.
_regular_path = _find_font_file(REGULAR_CANDIDATES)
if _regular_path is not None:
    try:
        pdfmetrics.registerFont(TTFont("NotoTamil", str(_regular_path)))
        BODY_FONT = "NotoTamil"
        HAS_CUSTOM_FONT = True
        logger.info("Registered Tamil font from %s", _regular_path)
    except Exception:
        HAS_CUSTOM_FONT = False
        logger.exception("Found Tamil font file at %s but failed to register it with ReportLab.", _regular_path)
else:
    logger.warning(
        "No Tamil font file found. Looked for %s in %s. "
        "Falling back to Helvetica -- Tamil/Unicode text will be stripped to ASCII.",
        REGULAR_CANDIDATES, [str(d) for d in FONT_DIR_CANDIDATES],
    )

_bold_path = _find_font_file(BOLD_CANDIDATES)
if _bold_path is not None:
    try:
        pdfmetrics.registerFont(TTFont("NotoTamilBold", str(_bold_path)))
        BOLD_FONT = "NotoTamilBold"
        HAS_CUSTOM_BOLD = True
    except Exception:
        HAS_CUSTOM_BOLD = False

# If we got a regular Unicode font but not a bold variant, keep body text
# in Unicode and just fall back to the standard bold for headings rather
# than dropping Unicode support everywhere.
if HAS_CUSTOM_FONT and not HAS_CUSTOM_BOLD:
    BOLD_FONT = "Helvetica-Bold"


def _sanitize_text(text) -> str:
    """Return text safe to render in the currently active body font.

    When a Unicode-capable font (Tamil, etc.) is registered, characters are
    passed through untouched. When only the base-14 Helvetica fonts are
    available, non-Latin-1 characters (including symbols like the Rupee
    sign, which base-14 fonts do not contain glyphs for) are stripped so
    ReportLab doesn't raise an encoding error or render a broken glyph.
    """
    if text is None:
        return ""
    text = str(text).strip()
    if not text:
        return ""
    if HAS_CUSTOM_FONT:
        return text
    return text.encode("ascii", "ignore").decode("ascii").strip()


_AMOUNT_STRIP_RE = re.compile(r"[^\d.\-]")


def _parse_amount(amount_raw) -> float | None:
    """Safely coerce a user-supplied amount into a float.

    Handles values that arrive as already-numeric types, but also common
    real-world string forms such as "₹5,000", "Rs. 5,000.50", "INR 5000",
    or values with stray whitespace — any of which would raise ValueError
    if passed straight to float(). Returns None if no usable number is
    present (as opposed to silently treating an unparsed string as "0").
    """
    if amount_raw is None:
        return None
    if isinstance(amount_raw, (int, float)):
        return float(amount_raw)

    text = str(amount_raw).strip()
    if not text:
        return None

    # Strip currency symbols/words, thousands separators, and whitespace,
    # keeping digits, a single decimal point, and a leading minus sign.
    cleaned = text.replace(",", "")
    cleaned = re.sub(r"(?i)\b(rs|inr|rupees?)\b\.?", "", cleaned)
    cleaned = _AMOUNT_STRIP_RE.sub("", cleaned).strip()

    if not cleaned or cleaned in {"-", "."}:
        return None

    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


# -----------------------------------------------------
# LANGUAGE AUTO-DETECTION
# -----------------------------------------------------
#
# The declared draft.language field can go stale or simply be wrong (e.g.
# the citizen switched from Tamil to English mid-conversation but the form
# field wasn't updated). Rather than trust that field blindly, detect the
# script actually used in the incident narrative and let that take
# precedence. This is a lightweight Unicode-block check rather than a
# statistical language-id library, since we only need to distinguish
# between a handful of known Indic scripts and Latin/English text -- a
# character-range check is both sufficient and dependency-free.

_SCRIPT_RANGES: list[tuple[str, tuple[int, int]]] = [
    ("ta", (0x0B80, 0x0BFF)),  # Tamil
    ("hi", (0x0900, 0x097F)),  # Devanagari (Hindi)
    ("te", (0x0C00, 0x0C7F)),  # Telugu
    ("kn", (0x0C80, 0x0CFF)),  # Kannada
    ("ml", (0x0D00, 0x0D7F)),  # Malayalam
    ("bn", (0x0980, 0x09FF)),  # Bengali
]


def _detect_language(*texts: str) -> str | None:
    """Inspect the given text fields and return the language code for the
    predominant non-Latin script found, or None if the text is (or looks
    like) plain English/Latin script.

    When multiple scripts are present, the one with the most characters
    wins, so a message that's mostly English with a couple of stray Tamil
    words won't get mis-tagged.
    """
    counts: dict[str, int] = {}
    for text in texts:
        if not text:
            continue
        for ch in str(text):
            code = ord(ch)
            for lang_code, (lo, hi) in _SCRIPT_RANGES:
                if lo <= code <= hi:
                    counts[lang_code] = counts.get(lang_code, 0) + 1
                    break

    if not counts:
        return None
    return max(counts, key=counts.get)


def _resolve_language(draft) -> tuple[str, bool]:
    """Determine the language to use for this report.

    Returns (language_code, was_auto_detected). Detection is based on the
    incident narrative (and a couple of other free-text fields the citizen
    typed themselves); the declared draft.language is used only as a
    fallback when no recognizable script is found in the text.
    """
    detected = _detect_language(
        getattr(draft, "incident_text", None),
        getattr(draft, "citizen_name", None),
    )
    if detected is not None:
        return detected, True

    declared = getattr(draft, "language", None) or "en"
    return declared, False


# -----------------------------------------------------
# RASTERIZED TEXT RENDERING (for complex/Indic scripts)
# -----------------------------------------------------
#
# ReportLab's TTFont support maps Unicode codepoints straight to glyphs in
# logical order -- it has no text-shaping engine. For scripts like Tamil,
# Devanagari, Malayalam, etc. that require reordering (e.g. a vowel sign
# that's typed after its consonant but must be *drawn* before it) or glyph
# substitution (conjuncts/ligatures), that means text can come out with
# visible characters in the wrong position, even with a correct font
# registered. Registering the font fixes "missing glyph" errors, but not
# this.
#
# The reliable fix is to shape and rasterize the text ourselves using
# Pillow's RAQM layout engine (HarfBuzz + FriBidi under the hood), which
# performs real script shaping, then embed the result as an image in the
# PDF instead of asking ReportLab to lay the text out as native text.
#
# Trade-off worth knowing: rasterized text is not selectable/searchable/
# copy-pasteable in the resulting PDF, unlike native ReportLab text. For
# that reason this path is only used for scripts ReportLab can't shape
# correctly; plain English content still renders as normal (searchable)
# Paragraph text.

try:
    from PIL import Image as PILImage, ImageDraw, ImageFont

    _PIL_AVAILABLE = True
except Exception as _pil_import_error:
    _PIL_AVAILABLE = False
    logger.warning(
        "Pillow could not be imported (%s). Rasterized rendering for complex "
        "scripts (Tamil, Devanagari, etc.) will be unavailable; affected "
        "sections will fall back to ASCII-stripped text.",
        _pil_import_error,
    )

try:
    from reportlab.platypus import Image as RLImage
except Exception as _rlimage_import_error:
    RLImage = None
    logger.warning("reportlab.platypus.Image could not be imported (%s).", _rlimage_import_error)

_RASTER_DPI = 200
_RASTER_FONT_SIZE_PX = 26  # ~13pt body text at 200dpi
_RASTER_LINE_SPACING = 1.35


def _raqm_font(font_path: Path, size_px: int):
    """Load a font with the RAQM (HarfBuzz-based) layout engine so complex
    scripts get properly shaped. Falls back to Pillow's basic layout if the
    installed Pillow build lacks libraqm support -- shaping won't be
    correct in that case, but text will still render rather than error."""
    try:
        return ImageFont.truetype(str(font_path), size_px, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        return ImageFont.truetype(str(font_path), size_px)


def _wrap_text_to_width(draw, text: str, font, max_width_px: int) -> list[str]:
    """Word-wrap text to fit max_width_px, measuring with the actual shaped
    font metrics rather than a fixed character count (character counts are
    meaningless across scripts with variable glyph widths)."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split(" ")
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            bbox = draw.textbbox((0, 0), trial, font=font)
            if (bbox[2] - bbox[0]) <= max_width_px:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def render_text_as_image(text: str, lang: str, max_width_pt: float = 522) -> "RLImage | None":
    """Rasterize `text` (shaped for `lang`'s script) into a ReportLab Image
    flowable sized to fit within max_width_pt (points). Returns None if
    Pillow, a matching font file, or the text itself isn't available --
    callers should fall back to a normal Paragraph in that case."""
    if not text or not text.strip():
        return None
    if not _PIL_AVAILABLE:
        logger.warning("Pillow is not installed/importable -- cannot rasterize text for language '%s'.", lang)
        return None
    if RLImage is None:
        logger.warning("reportlab.platypus.Image is unavailable -- cannot embed rasterized text.")
        return None

    font_path = _font_path_for_language(lang)
    if font_path is None:
        # _font_path_for_language already logged the specific paths searched.
        return None

    try:
        max_width_px = int(max_width_pt / 72 * _RASTER_DPI)
        font = _raqm_font(font_path, _RASTER_FONT_SIZE_PX)

        # Measure on a throwaway canvas first to determine wrapped line
        # count and required image height before allocating the real one.
        probe_img = PILImage.new("RGB", (max_width_px, 10), "white")
        probe_draw = ImageDraw.Draw(probe_img)
        lines = _wrap_text_to_width(probe_draw, text.strip(), font, max_width_px)

        line_height_px = int(_RASTER_FONT_SIZE_PX * _RASTER_LINE_SPACING)
        padding_px = 16
        img_height = padding_px * 2 + line_height_px * max(len(lines), 1)

        img = PILImage.new("RGB", (max_width_px, img_height), "#F8F9FA")
        draw = ImageDraw.Draw(img)
        y = padding_px
        for line in lines:
            draw.text((padding_px, y), line, font=font, fill="#111111")
            y += line_height_px

        from io import BytesIO

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        width_pt = max_width_pt
        height_pt = img_height / _RASTER_DPI * 72
        return RLImage(buf, width=width_pt, height=height_pt)
    except Exception:
        logger.exception(
            "Failed to rasterize text for language '%s' using font %s. "
            "Falling back to ASCII-stripped text for this section.",
            lang, font_path,
        )
        return None


def _format_amount(amount_raw) -> str:
    """Render the amount-lost field, using the currency symbol only when
    the active font can actually display it."""
    value = _parse_amount(amount_raw)
    if value is None or value <= 0:
        return "No Direct Monetary Loss Reported"

    currency_prefix = "₹" if HAS_CUSTOM_FONT else "Rs."
    return f"{currency_prefix} {value:,.2f}"


# -----------------------------------------------------
# COLOUR PALETTE
# -----------------------------------------------------

PRIMARY = colors.HexColor("#083735")
SECONDARY = colors.HexColor("#0B4F4A")
GOLD = colors.HexColor("#C97A2B")
LINE = colors.HexColor("#DAD4C4")
BG_LIGHT = colors.HexColor("#F8F9FA")

RISK_COLORS = {
    "LOW": colors.HexColor("#2E7D4F"),
    "MEDIUM": colors.HexColor("#C98A1A"),
    "HIGH": colors.HexColor("#C15A24"),
    "CRITICAL": colors.HexColor("#B33A2E"),
}


# -----------------------------------------------------
# STYLES
# -----------------------------------------------------

def _styles():
    styles = getSampleStyleSheet()

    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            fontName=BOLD_FONT,
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            textColor=PRIMARY,
            spaceAfter=4,
        )
    )

    styles.add(
        ParagraphStyle(
            name="ReportSubTitle",
            fontName=BODY_FONT,
            fontSize=9.5,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#555555"),
            spaceAfter=10,
        )
    )

    styles.add(
        ParagraphStyle(
            name="ReportHeading",
            fontName=BOLD_FONT,
            fontSize=11,
            leading=14,
            textColor=SECONDARY,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
        )
    )

    styles.add(
        ParagraphStyle(
            name="ReportBody",
            fontName=BODY_FONT,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#222222"),
        )
    )

    styles.add(
        ParagraphStyle(
            name="ComplaintText",
            fontName=BODY_FONT,
            fontSize=9,
            leading=13.5,
            textColor=colors.HexColor("#111111"),
            backColor=BG_LIGHT,
            borderColor=LINE,
            borderWidth=0.5,
            borderPadding=8,
            spaceBefore=4,
            spaceAfter=6,
        )
    )

    styles.add(
        ParagraphStyle(
            name="ReportSmall",
            fontName=BODY_FONT,
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#666666"),
        )
    )

    return styles


# -----------------------------------------------------
# CANVAS DECORATIONS (HEADER / FOOTER)
# -----------------------------------------------------

def draw_header_footer(canvas, doc):
    canvas.saveState()
    page_width, page_height = A4

    # Top Banner
    banner_height = 28
    canvas.setFillColor(PRIMARY)
    canvas.rect(0, page_height - banner_height, page_width, banner_height, fill=1, stroke=0)

    canvas.setFont(BOLD_FONT, 10)
    canvas.setFillColor(colors.white)
    canvas.drawString(18 * mm, page_height - 18, "CITIZEN FRAUD SHIELD | OFFICIAL INCIDENT REPORT")

    canvas.setFont(BODY_FONT, 8)
    canvas.drawRightString(
        page_width - (18 * mm),
        page_height - 18,
        f"Generated: {datetime.now():%d-%b-%Y %H:%M IST}",
    )

    # Bottom Footer
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 12 * mm, page_width - (18 * mm), 12 * mm)

    canvas.setFont(BODY_FONT, 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(18 * mm, 7 * mm, "CONFIDENTIAL — FOR LAW ENFORCEMENT & BANKING PURPOSES")
    canvas.drawRightString(page_width - (18 * mm), 7 * mm, f"Page {doc.page}")

    canvas.restoreState()


# -----------------------------------------------------
# HELPER COMPONENTS
# -----------------------------------------------------

def field_table(rows, body_style):
    data = []
    for k, v in rows:
        value_text = _sanitize_text(v) if v is not None and str(v).strip() != "" else "—"
        data.append([
            Paragraph(f"<b>{_sanitize_text(k)}</b>", body_style),
            Paragraph(value_text, body_style),
        ])

    table = Table(data, colWidths=[54 * mm, 120 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def risk_banner(score, band):
    band_str = str(band).upper() if band else "UNKNOWN"
    colour = RISK_COLORS.get(band_str, RISK_COLORS["HIGH"])

    banner_style = ParagraphStyle(
        "BannerText",
        alignment=TA_CENTER,
        fontName=BOLD_FONT,
        fontSize=13,
        leading=16,
        textColor=colors.white,
    )

    table = Table(
        [[
            Paragraph(
                f"SEVERITY ASSESSMENT: {band_str} RISK (Score: {score}/100)",
                banner_style,
            )
        ]],
        colWidths=[174 * mm],
    )

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colour),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


# -----------------------------------------------------
# MAIN PDF GENERATION
# -----------------------------------------------------

def generate_complaint_pdf(draft: ComplaintDraft, result: dict) -> Path:
    """Build the PDF and return its filesystem path."""
    styles = _styles()
    ref_id = getattr(draft, "ref_id", "CFS-UNKNOWN")
    out_path = PDF_OUTPUT_DIR / f"{ref_id}.pdf"

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
    )

    story = []

    # Title Section
    story.append(Spacer(1, 2))
    story.append(Paragraph("CYBER FRAUD INCIDENT REPORT", styles["ReportTitle"]))
    story.append(
        Paragraph(
            "Prepared for National Cyber Crime Reporting Portal (cybercrime.gov.in) &amp; Bank Nodal Officers",
            styles["ReportSubTitle"],
        )
    )

    # Risk Banner
    risk_score = getattr(draft, "risk_score", 0)
    risk_band = getattr(draft, "risk_band", "UNKNOWN")
    story.append(risk_banner(risk_score, risk_band))
    story.append(Spacer(1, 6))

    # Incident Overview
    lang, lang_auto_detected = _resolve_language(draft)
    language_display = f"{lang} (auto-detected from message)" if lang_auto_detected else lang

    story.append(
        KeepTogether([
            Paragraph("1. Incident Summary Overview", styles["ReportHeading"]),
            field_table([
                ("Reference ID", ref_id),
                ("Filing Date / Time", getattr(draft, "created_at", datetime.now().strftime("%Y-%m-%d %H:%M"))),
                ("Complaint Category", getattr(draft, "category", "N/A")),
                ("Subcategory", getattr(draft, "subcategory", "N/A")),
                ("Primary Language", language_display),
                ("Reporting Channel", getattr(draft, "channel", "Mobile App")),
            ], styles["ReportBody"]),
        ])
    )
    story.append(Spacer(1, 6))

    # Complainant Details
    story.append(
        KeepTogether([
            Paragraph("2. Complainant Details", styles["ReportHeading"]),
            field_table([
                ("Full Name", getattr(draft, "citizen_name", "N/A")),
                ("Contact Phone Number", getattr(draft, "citizen_phone", "N/A")),
                ("State / UT Jurisdiction", getattr(draft, "citizen_state", "N/A")),
            ], styles["ReportBody"]),
        ])
    )
    story.append(Spacer(1, 6))

    # Financial & Suspect Data — amount parsing/formatting is now robust to
    # strings like "₹5,000", "Rs. 5,000.50", stray whitespace, etc., and the
    # currency glyph used matches whatever font is actually active.
    amount_text = _format_amount(getattr(draft, "amount_lost", None))
    suspect_id = getattr(draft, "suspect_number_or_id", "Not Provided")

    story.append(
        KeepTogether([
            Paragraph("3. Financial &amp; Suspect Information", styles["ReportHeading"]),
            field_table([
                ("Total Amount Lost", amount_text),
                ("Suspect ID / Phone / UPI / Account", suspect_id),
            ], styles["ReportBody"]),
        ])
    )
    story.append(Spacer(1, 6))

    # Statement of Fact
    #
    # Plain-text statements go through the normal (searchable) Paragraph
    # path. Statements in scripts ReportLab can't shape correctly (Tamil,
    # Devanagari, etc. -- see RASTERIZED TEXT RENDERING above) are instead
    # rasterized as a properly-shaped image and embedded directly, so the
    # text reads correctly regardless of ReportLab's native font/shaping
    # limitations. If rasterization isn't possible (no matching font file,
    # Pillow unavailable), it falls back to the sanitized text as before
    # rather than failing the whole report.
    raw_incident_text = getattr(draft, "incident_text", "No detailed statement provided.")
    statement_block = [Paragraph("4. Detailed Statement of Fact", styles["ReportHeading"])]

    statement_image = None
    if lang != "en":
        statement_image = render_text_as_image(str(raw_incident_text), lang)

    if statement_image is not None:
        statement_block.append(statement_image)
    else:
        incident_desc = _sanitize_text(raw_incident_text)
        statement_block.append(Paragraph(incident_desc, styles["ReportBody"]))

    story.append(KeepTogether(statement_block))
    story.append(Spacer(1, 6))

    # Keep a sanitized plain-text copy available for the draft complaint
    # text block further down, regardless of which path was used above.
    incident_desc = _sanitize_text(raw_incident_text)

    # Detected Indicators
    signals = getattr(draft, "signals", [])
    if signals:
        signal_block = [Paragraph("5. AI-Detected Fraud Indicators", styles["ReportHeading"])]
        for signal in signals:
            signal_block.append(Paragraph(f"• {_sanitize_text(signal)}", styles["ReportBody"]))
        story.append(KeepTogether(signal_block))
        story.append(Spacer(1, 6))

    # Draft Complaint Text (Formal Copy Block)
    created_at = _sanitize_text(getattr(draft, "created_at", "recently"))
    category = _sanitize_text(getattr(draft, "category", "Cyber Crime"))
    subcategory = _sanitize_text(getattr(draft, "subcategory", "General"))
    suspect_id_safe = _sanitize_text(suspect_id)

    formal_block = []
    formal_block.append(Paragraph("6. Draft Complaint Text (For Police/NCRB Copying)", styles["ReportHeading"]))

    complaint_body = (
        f"<b>TO THE CONCERNED POLICE / CYBER CRIME CELL OFFICER,</b><br/><br/>"
        f"I wish to formally report an instance of cyber fraud. "
        f"The incident occurred on or around <b>{created_at}</b> under the category "
        f"<b>{category}</b> ({subcategory}).<br/><br/>"
        f"<b>Brief Summary:</b> {incident_desc}<br/><br/>"
        f"<b>Suspect Identifiers:</b> {suspect_id_safe}<br/>"
        f"<b>Financial Loss:</b> {amount_text}<br/><br/>"
        f"I request you to register this complaint, take immediate action to block the suspect's accounts/numbers, "
        f"and initiate proceedings to recover the funds if applicable."
    )
    formal_block.append(Paragraph(complaint_body, styles["ComplaintText"]))
    story.append(KeepTogether(formal_block))

    # Regional Language Section (Tamil & Others)
    # `lang` here is the auto-detected value resolved earlier (falling back
    # to the declared draft.language only if no script could be detected),
    # so a citizen who switched from Tamil to English mid-conversation (or
    # vice versa) still gets the correct section rendered.
    translations = {
        "hi": "यह शिकायत साइबर धोखाधड़ी से संबंधित है। कृपया इसे साइबर क्राइम पोर्टल पर दर्ज करें।",
        "ta": "இந்த அறிக்கை இணைய மோசடி தொடர்பானது. தயவுசெய்து இதை Cyber Crime Portal-ல் பதிவு செய்யவும்.",
        "te": "ఈ నివేదిక సైబర్ మోసానికి సంబంధించినది. దయచేసి దీనిని Cyber Crime Portal లో ఫిర్యాదు చేయండి.",
        "kn": "ಈ ವರದಿ ಸೈಬರ್ ವಂಚನೆಗೆ ಸಂಬಂಧಿಸಿದೆ. ದಯವಿಟ್ಟು Cyber Crime Portal ನಲ್ಲಿ ದೂರು ಸಲ್ಲಿಸಿ.",
        "ml": "ഈ റിപ്പോർട്ട് സൈബർ തട്ടിപ്പുമായി ബന്ധപ്പെട്ടതാണ്. ദയവായി Cyber Crime Portal ൽ പരാതി നൽകുക.",
        "bn": "এই প্রতিবেদনটি সাইবার জালিয়াতির সাথে সম্পর্কিত। অনুগ্রহ করে Cyber Crime Portal-এ অভিযোগ করুন.",
    }

    # Only attempt the regional-language block when a font that can actually
    # render that script is available. Rendering it through Helvetica would
    # silently produce blank/garbled text since _sanitize_text would strip
    # nearly everything.
    if lang in translations and HAS_CUSTOM_FONT:
        lang_block = [
            Paragraph("7. Regional Language Translation", styles["ReportHeading"]),
            Paragraph(translations[lang], styles["ReportBody"]),
        ]
        story.append(KeepTogether(lang_block))
        story.append(Spacer(1, 6))

    # Evidence Checklist
    checklist_block = [
        Paragraph("8. Required Evidence Checklist for Submission", styles["ReportHeading"]),
        Paragraph("☐ Screenshots of chat conversations (WhatsApp/Telegram/SMS)", styles["ReportBody"]),
        Paragraph("☐ Payment/Bank transfer receipts (showing UTR / Transaction Reference ID)", styles["ReportBody"]),
        Paragraph("☐ Bank statement highlighting unauthorized debits", styles["ReportBody"]),
        Paragraph("☐ Fraudulent links or domain URLs", styles["ReportBody"]),
    ]
    story.append(KeepTogether(checklist_block))
    story.append(Spacer(1, 6))

    # Immediate Actions
    action_block = [
        Paragraph("9. Immediate Mandatory Actions", styles["ReportHeading"]),
        Paragraph("1. <b>Report Immediately on CyberCrime Portal:</b> Visit <b>cybercrime.gov.in</b> or call <b>1930</b>.", styles["ReportBody"]),
        Paragraph("2. <b>Freeze Bank Account/Cards:</b> Contact your bank's fraud unit immediately to freeze compromised accounts.", styles["ReportBody"]),
        Paragraph("3. <b>Email Notice:</b> Attach this PDF and email it directly to your bank's official grievance officer.", styles["ReportBody"]),
    ]
    story.append(KeepTogether(action_block))
    story.append(Spacer(1, 8))

    # Footer Notes / Legal Disclaimer
    story.append(HRFlowable(width="100%", color=LINE, spaceAfter=4))
    portal = result.get("portal_url", "https://cybercrime.gov.in")
    helpline = result.get("helpline", "1930")

    story.append(
        KeepTogether([
            Paragraph(
                f"<b>Official Reporting Portal:</b> {_sanitize_text(portal)} | <b>National Helpline:</b> {_sanitize_text(helpline)}",
                styles["ReportBody"],
            ),
            Paragraph(
                "<b>Disclaimer:</b> Citizen Fraud Shield is an automated guidance framework. "
                "This document compiles user-submitted information and AI metrics for formal submission "
                "and does not replace an officially filed FIR or police station acknowledgement.",
                styles["ReportSmall"],
            ),
        ])
    )

    # Build PDF
    doc.build(
        story,
        onFirstPage=draw_header_footer,
        onLaterPages=draw_header_footer,
    )

    return out_path


# -----------------------------------------------------
# ENVIRONMENT DIAGNOSTICS
# -----------------------------------------------------
#
# Run this directly on the server where PDFs are actually generated:
#
#     python -m reporting.pdf_generator --diagnose
#
# It reports, in plain terms, exactly which fonts were found/missing and
# whether Pillow/RAQM shaping is available -- the two things that silently
# determine whether Tamil (and other Indic-script) text renders correctly.

def diagnose_environment() -> str:
    lines = []
    lines.append(f"Module directory: {MODULE_DIR}")
    lines.append("Font search directories (in order checked):")
    for d in FONT_DIR_CANDIDATES:
        exists = "exists" if d.is_dir() else "MISSING"
        lines.append(f"  - {d}  [{exists}]")

    lines.append("")
    lines.append(f"Tamil font registered with ReportLab: {HAS_CUSTOM_FONT}")
    if HAS_CUSTOM_FONT:
        lines.append(f"  -> using: {_regular_path}")
    lines.append(f"Tamil bold font registered: {HAS_CUSTOM_BOLD}")

    lines.append("")
    lines.append("Per-script font availability (for rasterized rendering):")
    for lang_code, candidates in SCRIPT_FONT_CANDIDATES.items():
        path = _find_font_file(candidates)
        status = f"FOUND at {path}" if path else f"NOT FOUND (looked for {candidates})"
        lines.append(f"  - {lang_code}: {status}")

    lines.append("")
    lines.append(f"Pillow available: {_PIL_AVAILABLE}")
    if _PIL_AVAILABLE:
        try:
            test_font_path = next(
                (p for p in (_find_font_file(c) for c in SCRIPT_FONT_CANDIDATES.values()) if p),
                None,
            )
            if test_font_path:
                ImageFont.truetype(str(test_font_path), 20, layout_engine=ImageFont.Layout.RAQM)
                lines.append("RAQM (HarfBuzz) shaping engine: available")
            else:
                lines.append("RAQM shaping engine: could not test (no font file found to test with)")
        except Exception as e:
            lines.append(f"RAQM (HarfBuzz) shaping engine: NOT available ({e}) -- "
                          f"install libraqm for correct complex-script shaping.")
    lines.append(f"reportlab.platypus.Image available: {RLImage is not None}")

    report = "\n".join(lines)
    print(report)
    return report


if __name__ == "__main__":
    import sys

    if "--diagnose" in sys.argv:
        logging.basicConfig(level=logging.INFO)
        diagnose_environment()