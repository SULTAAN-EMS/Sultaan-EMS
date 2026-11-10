"""Reusable TIS PDF theme and five-page attendance report renderer.

The public entry point is ``build_attendance_report_pdf(context)``.  The
context is deliberately plain data so Flask routes can pass ORM-derived
report dictionaries without coupling the theme to database models.
"""

from datetime import date
from html import escape
from io import BytesIO
from pathlib import Path
import re

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


PAGE_SIZE = landscape(A4)
TOKENS = {
    "navy": "#0F2747",
    "header_left": "#0A1B36",
    "header_mid": "#12305C",
    "header_right": "#17407A",
    "page_bg": "#F3F6FB",
    "card": "#FFFFFF",
    "card_border": "#E1E8F2",
    "muted": "#6B7C95",
    "muted_light": "#7A8BA6",
    "muted_dark": "#44566F",
    "row_alt": "#F7F9FC",
    "row_line": "#EDF1F7",
    "total_bg": "#E8F0FE",
}

STATUS = {
    "present": {"label": "Joogid", "color": "#059669", "tint": "#ECFDF5", "icon": "check-circle"},
    "late": {"label": "Soo Daahid", "color": "#2563EB", "tint": "#EFF6FF", "icon": "clock"},
    "absent": {"label": "Maqan", "color": "#DC2626", "tint": "#FEF2F2", "icon": "x-circle"},
    "excused": {"label": "La Fasaxay", "color": "#7C3AED", "tint": "#F5F3FF", "icon": "user-check"},
    "sick": {"label": "Cudur-daar", "color": "#D97706", "tint": "#FFFBEB", "icon": "thermometer"},
    "emergency": {"label": "Xaalad deg-deg ah", "color": "#DB2777", "tint": "#FDF2F8", "icon": "alert-triangle"},
    "unmarked": {"label": "Aan la calaamadin", "color": "#0891B2", "tint": "#ECFEFF", "icon": "help-circle"},
}
STATUS_ORDER = tuple(STATUS)
WEEKDAYS = ("Isniin", "Talaado", "Arbaco", "Khamiis", "Jimco", "Sabti", "Axad")
MONTHS = (
    "Janaayo", "Febraayo", "Maarso", "Abriil", "Maajo", "Juun",
    "Luulyo", "Agoosto", "Sebteembar", "Oktoober", "Nofeembar", "Diseembar",
)
ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
FONT_DIR = Path(__file__).parent / "static" / "fonts"
STATIC_DIR = Path(__file__).parent / "static"


def _register_fonts():
    fonts = {
        "TISInter": FONT_DIR / "Inter-Regular.ttf",
        "TISInterSemi": FONT_DIR / "Inter-SemiBold.ttf",
        "TISInterBold": FONT_DIR / "Inter-Bold.ttf",
        "TISInterExtra": FONT_DIR / "Inter-ExtraBold.ttf",
        "TISDejaVu": FONT_DIR / "NotoSansArabic-Regular.ttf",
        "TISDejaVuBold": FONT_DIR / "NotoSansArabic-Bold.ttf",
    }
    for name, path in fonts.items():
        if path.is_file() and name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
    if "TISDejaVu" in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFontFamily(
            "TISDejaVu", normal="TISDejaVu", bold="TISDejaVuBold",
            italic="TISDejaVu", boldItalic="TISDejaVuBold",
        )


_register_fonts()


def _color(token):
    return colors.HexColor(token)


def _school_label(value):
    value = str(value or "").strip()
    return value if re.search(r"\bTIS\)?$", value, flags=re.IGNORECASE) else f"{value} (TIS)"


def _font_for(text, bold=False, extra=False):
    regular = "TISInterExtra" if extra else "TISInterBold" if bold else "TISInter"
    candidate = regular if regular in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold" if bold or extra else "Helvetica"
    font = pdfmetrics.getFont(candidate)
    cmap = getattr(getattr(font, "face", None), "charToGlyph", {})
    if cmap and any(ord(char) not in cmap for char in text if ord(char) > 127):
        return "TISDejaVuBold" if bold or extra else "TISDejaVu"
    return candidate


def _text(value):
    text = "" if value is None else str(value)
    if ARABIC_RE.search(text):
        shaped = get_display(arabic_reshaper.reshape(text))
        return f'<font name="TISDejaVu">{escape(shaped)}</font>'
    return escape(text)


def _paragraph(value, style):
    return Paragraph(_text(value), style)


def _date_label(value, long=True):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    if long:
        return f"{WEEKDAYS[value.weekday()]}, {MONTHS[value.month - 1]} {value.day}, {value.year}"
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


def _styles():
    common = {}
    return {
        "school": ParagraphStyle("tis_school", **common, fontName="TISInterExtra", fontSize=13.8, leading=16, textColor=colors.white),
        "school_sub": ParagraphStyle("tis_school_sub", **common, fontName="TISInter", fontSize=8.6, leading=10, textColor=_color("#9FB6D9")),
        "eyebrow": ParagraphStyle("tis_eyebrow", **common, fontName="TISInterBold", fontSize=7.1, leading=9, alignment=TA_RIGHT, textColor=_color("#6EE7B7")),
        "title": ParagraphStyle("tis_title", **common, fontName="TISInterExtra", fontSize=15, leading=18, alignment=TA_RIGHT, textColor=colors.white),
        "title_sub": ParagraphStyle("tis_title_sub", **common, fontName="TISInter", fontSize=8, leading=10, alignment=TA_RIGHT, textColor=_color("#9FB6D9")),
        "meta_label": ParagraphStyle("tis_meta_label", **common, fontName="TISInterBold", fontSize=6.6, leading=8, textColor=_color(TOKENS["muted"])),
        "meta_value": ParagraphStyle("tis_meta_value", **common, fontName="TISInterBold", fontSize=9.1, leading=11, textColor=_color(TOKENS["navy"])),
        "section": ParagraphStyle("tis_section", **common, fontName="TISInterExtra", fontSize=8.8, leading=10.5, textColor=_color(TOKENS["navy"])),
        "note": ParagraphStyle("tis_note", **common, fontName="TISInter", fontSize=6.5, leading=8, textColor=_color(TOKENS["muted"])),
        "th": ParagraphStyle("tis_th", **common, fontName="TISInterBold", fontSize=6.4, leading=7.4, alignment=TA_CENTER, textColor=colors.white),
        "th_left": ParagraphStyle("tis_th_left", **common, fontName="TISInterBold", fontSize=6.4, leading=7.4, alignment=TA_LEFT, textColor=colors.white),
        "cell": ParagraphStyle("tis_cell", **common, fontName="TISInter", fontSize=7.1, leading=8.5, textColor=_color(TOKENS["muted_dark"])),
        "cell_bold": ParagraphStyle("tis_cell_bold", **common, fontName="TISInterBold", fontSize=7.2, leading=8.6, textColor=_color(TOKENS["navy"])),
        "cell_num": ParagraphStyle("tis_cell_num", **common, fontName="TISInterBold", fontSize=7, leading=8, alignment=TA_CENTER, textColor=_color(TOKENS["navy"])),
        "student": ParagraphStyle("tis_student", **common, fontName="TISInterBold", fontSize=8.1, leading=9.4, textColor=_color(TOKENS["navy"])),
        "event": ParagraphStyle("tis_event", **common, fontName="TISInter", fontSize=6.8, leading=8, textColor=_color(TOKENS["muted_dark"])),
        "empty": ParagraphStyle("tis_empty", **common, fontName="TISInter", fontSize=8, leading=10, alignment=TA_CENTER, textColor=_color(TOKENS["muted"])),
    }


class Icon(Flowable):
    """Small Feather-like vector icon drawn without an external icon font."""

    def __init__(self, name, color, size=8 * mm):
        super().__init__()
        self.name = name
        self.color = _color(color) if isinstance(color, str) else color
        self.width = self.height = size

    def wrap(self, avail_width, avail_height):
        return min(self.width, avail_width), min(self.height, avail_height)

    def draw(self):
        c = self.canv
        s = min(self.width, self.height) / 24
        ox, oy = (self.width - 24 * s) / 2, (self.height - 24 * s) / 2
        c.saveState()
        # The icon paths use top-origin coordinates; ReportLab's canvas is bottom-origin.
        c.translate(0, self.height)
        c.scale(1, -1)
        c.setStrokeColor(self.color)
        c.setLineWidth(max(.75, 1.5 * s))
        c.setLineCap(1)
        c.setLineJoin(1)

        def line(points):
            path = c.beginPath()
            path.moveTo(ox + points[0][0] * s, oy + points[0][1] * s)
            for x, y in points[1:]:
                path.lineTo(ox + x * s, oy + y * s)
            c.drawPath(path, stroke=1, fill=0)

        def circle(x, y, r):
            c.circle(ox + x * s, oy + y * s, r * s, stroke=1, fill=0)

        name = self.name
        if name in ("check-circle", "x-circle", "help-circle", "clock"):
            circle(12, 12, 9)
            if name == "check-circle": line([(7, 12), (10.5, 15.5), (17, 8.5)])
            elif name == "x-circle":
                line([(8.5, 8.5), (15.5, 15.5)])
                line([(15.5, 8.5), (8.5, 15.5)])
            elif name == "help-circle":
                circle(12, 17, .7)
                line([(9.5, 9), (10.5, 7.5), (13, 7.5), (14.5, 9), (14, 11), (12, 12.5), (12, 14)])
            else:
                line([(12, 6), (12, 12), (16, 14)])
        elif name == "user-check":
            circle(9, 8, 3.2)
            path = c.beginPath(); path.moveTo(3*s+ox, 20*s+oy); path.curveTo(3*s+ox, 15*s+oy, 15*s+ox, 15*s+oy, 15*s+ox, 20*s+oy); c.drawPath(path, stroke=1, fill=0)
            line([(15, 11), (17, 13), (21, 8)])
        elif name == "thermometer":
            c.roundRect(10*s+ox, 3*s+oy, 4*s, 13*s, 2*s, stroke=1, fill=0)
            circle(12, 18, 3)
            line([(12, 7), (12, 17)])
        elif name == "alert-triangle":
            line([(12, 3), (22, 20), (2, 20), (12, 3)])
            line([(12, 9), (12, 14)])
            circle(12, 17, .55)
        elif name == "calendar":
            c.roundRect(3*s+ox, 4*s+oy, 18*s, 16*s, 2*s, stroke=1, fill=0)
            line([(3, 9), (21, 9)]); line([(8, 2.5), (8, 6)]); line([(16, 2.5), (16, 6)])
        elif name == "users":
            circle(9, 8, 3)
            path = c.beginPath(); path.moveTo(2*s+ox, 20*s+oy); path.curveTo(2*s+ox, 14*s+oy, 16*s+ox, 14*s+oy, 16*s+ox, 20*s+oy); c.drawPath(path, stroke=1, fill=0)
            circle(17, 9, 2.2); line([(17, 14), (19, 14), (22, 17)])
        elif name == "book-open":
            line([(12, 6), (12, 20)]); line([(12, 8), (8, 5), (3, 5), (3, 18), (8, 18), (12, 20)])
            line([(12, 8), (16, 5), (21, 5), (21, 18), (16, 18), (12, 20)])
        elif name == "map-pin":
            circle(12, 9, 6); circle(12, 9, 2)
            line([(7.5, 13), (12, 21), (16.5, 13)])
        elif name == "bar-chart":
            line([(4, 20), (4, 12), (8, 12), (8, 20)])
            line([(10, 20), (10, 6), (14, 6), (14, 20)])
            line([(16, 20), (16, 3), (20, 3), (20, 20)])
        elif name == "list":
            for y in (6, 12, 18):
                circle(5, y, .6); line([(9, y), (20, y)])
        else:
            circle(12, 12, 9); circle(12, 12, .7); line([(12, 11), (12, 16)])
        c.restoreState()


class IconTile(Flowable):
    def __init__(self, name, color, size=9 * mm, icon_size=6 * mm, tint=None):
        super().__init__()
        self.name, self.color, self.size, self.icon_size = name, _color(color), size, icon_size
        self.tint = _color(tint) if tint else self.color
        self.width = self.height = size

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(self.color)
        c.roundRect(0, 0, self.width, self.height, 2.4 * mm, stroke=0, fill=1)
        Icon(self.name, colors.white, self.icon_size).drawOn(c, (self.width-self.icon_size)/2, (self.height-self.icon_size)/2)
        c.restoreState()


class PageHeader(Flowable):
    def __init__(self, width, styles, school_name, title, subtitle, logo=None):
        super().__init__()
        self.width, self.height = width, 78 * mm / 3
        self.styles, self.school_name, self.title, self.subtitle = styles, school_name, title, subtitle
        self.logo = ImageReader(BytesIO(logo)) if logo else None
        self.height = 78

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        c = self.canv
        w, h = self.width, self.height
        c.saveState()
        c.setFillColor(_color(TOKENS["header_left"]))
        c.rect(0, 0, w, h, fill=1, stroke=0)
        c.setFillColor(_color(TOKENS["header_mid"]))
        path = c.beginPath(); path.moveTo(0, 0); path.lineTo(w*.64, 0); path.lineTo(w*.74, h); path.lineTo(0, h); path.close(); c.setFillColor(_color(TOKENS["header_mid"])); c.drawPath(path, fill=1, stroke=0)
        c.setFillColor(_color(TOKENS["header_right"]))
        path = c.beginPath(); path.moveTo(w*.64, 0); path.lineTo(w, 0); path.lineTo(w, h); path.lineTo(w*.74, h); path.close(); c.drawPath(path, fill=1, stroke=0)
        c.saveState()
        clip = c.beginPath(); clip.rect(0, 0, w, h); c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(_color("#1D477F"))
        c.circle(w*.82, h*.26, 20*mm, stroke=0, fill=1)
        c.setFillColor(_color("#183765"))
        c.circle(w*.73, h*.9, 14*mm, stroke=0, fill=1)
        c.restoreState()
        stripe = ["#059669", "#2563EB", "#7C3AED", "#DB2777", "#D97706"]
        sx = 0
        for color in stripe:
            sw = w / len(stripe)
            c.setFillColor(_color(color)); c.rect(sx, 0, sw, 3, fill=1, stroke=0); sx += sw
        logo_box = 51
        c.setFillColor(colors.white); c.roundRect(27, 13, logo_box, logo_box, 11, fill=1, stroke=0)
        if self.logo:
            c.drawImage(self.logo, 31, 17, width=43, height=43, preserveAspectRatio=True, anchor="c", mask="auto")
        school = Paragraph(_text(_school_label(self.school_name).upper()), self.styles["school"])
        school.wrapOn(c, w*.48, 22); school.drawOn(c, 92, 47)
        school_sub = Paragraph("Diiwaanka rasmiga ah ee xaadirinta", self.styles["school_sub"])
        school_sub.wrapOn(c, w*.48, 14); school_sub.drawOn(c, 92, 30)
        pill = Paragraph("SULTAAN EMS  |  WARBIXIN RASMI AH", self.styles["eyebrow"])
        pill.wrapOn(c, w*.42, 15); pill.drawOn(c, w*.56, 60)
        title = Paragraph(_text(self.title.upper()), self.styles["title"])
        title.wrapOn(c, w*.43, 20); title.drawOn(c, w*.55, 39)
        sub = Paragraph(_text(self.subtitle), self.styles["title_sub"])
        sub.wrapOn(c, w*.43, 15); sub.drawOn(c, w*.55, 24)
        c.restoreState()


class SectionHeading(Flowable):
    def __init__(self, width, title, icon="list", color="#2563EB", right=""):
        super().__init__()
        self.width, self.height, self.title, self.icon, self.color, self.right = width, 25, title, icon, _color(color), right

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(self.color); c.roundRect(0, 2, 22, 22, 5, fill=1, stroke=0)
        Icon(self.icon, colors.white, 14).drawOn(c, 4, 6)
        title_style = ParagraphStyle("section_heading_inline", fontName="TISInterExtra", fontSize=8.4, leading=10, textColor=_color(TOKENS["navy"]))
        p = Paragraph(_text(self.title.upper()), title_style); p.wrapOn(c, self.width-150, 20); p.drawOn(c, 30, 8)
        if self.right:
            right_style = ParagraphStyle("section_right_inline", fontName="TISInter", fontSize=6.5, leading=8, alignment=TA_RIGHT, textColor=_color(TOKENS["muted"]))
            p = Paragraph(_text(self.right), right_style); p.wrapOn(c, 115, 20); p.drawOn(c, self.width-115, 8)
        c.restoreState()


class KpiCard(Flowable):
    def __init__(self, width, key, value, compact=False):
        super().__init__()
        self.width, self.height, self.key, self.value = width, 50 if compact else 104, key, value
        self.compact = compact

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        spec = STATUS[self.key]
        c = self.canv
        c.saveState()
        c.setFillColor(_color(spec["tint"])); c.setStrokeColor(_color(TOKENS["card_border"]))
        c.roundRect(0, 0, self.width-4, self.height, 8, fill=1, stroke=1)
        c.setFillColor(_color(spec["color"]))
        if self.compact:
            c.roundRect(0, 0, 4, self.height, 2, fill=1, stroke=0)
            IconTile(spec["icon"], spec["color"], 34, 22).drawOn(c, 12, 8)
            c.setFont("TISInterBold", 6.7); c.drawString(55, self.height-19, spec["label"].upper())
            c.setFont("TISInterExtra", 20); c.drawRightString(self.width-13, 15, str(self.value))
        else:
            c.roundRect(0, self.height-4, self.width-4, 4, 2, fill=1, stroke=0)
            IconTile(spec["icon"], spec["color"], 27, 17).drawOn(c, 10, self.height-39)
            c.setFont("TISInterExtra", 27); c.drawString(10, 17, str(self.value))
            c.setFont("TISInterBold", 6.2); c.drawString(10, 7, spec["label"].upper())
        c.restoreState()


def _table_style(*extra):
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _color(TOKENS["navy"])),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), .35, _color(TOKENS["row_line"])),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _color(TOKENS["row_alt"])]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        *extra,
    ])


def _header_cells(labels, styles, first_left=False):
    return [Paragraph(_text(label), styles["th_left"] if first_left and index == 0 else styles["th"]) for index, label in enumerate(labels)]


def _kpi_strip(report, keys, styles, width, columns, compact=True):
    gap = 7
    card_width = (width - gap * (columns - 1)) / columns
    card_height = 52 if compact else 104
    cards = [KpiCard(card_width, key, report["totals"].get(key, 0), compact=compact) for key in keys]
    while len(cards) % columns:
        cards.append("")
    rows = [cards[index:index+columns] for index in range(0, len(cards), columns)]
    table = Table(rows, colWidths=[card_width] * columns, rowHeights=[card_height] * len(rows))
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),gap),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    return table


def _info_cards(report, styles, width):
    items = [
        ("SANAD-DUGSIYEEDKA", report["year"], "calendar", "#2563EB", "#EFF6FF", .22),
        ("NOOCA IMTIXAANKA", report["exam_name"], "book-open", "#7C3AED", "#F5F3FF", .24),
        ("MUDDADA IYO GOOBTAHA", f'{report["start_date"]}  -  {report["end_date"]}  |  {report.get("hall_label", "CUSUB")}', "map-pin", "#059669", "#ECFDF5", .54),
    ]
    cells = []
    for label, value, icon, accent, tint, _ in items:
        block = Table([
            [IconTile(icon, accent, 28, 17), Paragraph(_text(label), styles["meta_label"])],
            ["", Paragraph(_text(value), styles["meta_value"])],
        ], colWidths=[36, None], rowHeights=[12, 24])
        block.setStyle(TableStyle([
            ("SPAN", (0,0),(0,1)), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("BACKGROUND",(0,0),(-1,-1),_color(tint)), ("BOX",(0,0),(-1,-1),.6,_color(TOKENS["card_border"])),
            ("ROUNDEDCORNERS",[7,7,7,7]), ("LEFTPADDING",(0,0),(-1,-1),7),
            ("RIGHTPADDING",(0,0),(-1,-1),6), ("TOPPADDING",(0,0),(-1,-1),3),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ]))
        cells.append(block)
    widths = [width * item[5] for item in items]
    row = Table([cells], colWidths=widths)
    row.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),5)]))
    return row


def _stacked_status(report, width, styles):
    counts = report["totals"]
    total = sum(counts.get(key, 0) for key in STATUS_ORDER)
    bar_cells, bar_widths = [], []
    for key in STATUS_ORDER:
        count = counts.get(key, 0)
        bar_cells.append(Paragraph(f'<font color="white"><b>{count}</b></font>' if total and count / total >= .045 else "", ParagraphStyle("bar_count", fontName="TISInterBold", fontSize=7, leading=8, alignment=TA_CENTER)))
        bar_widths.append(max(2, (width - 20) * count / max(total, 1)))
    bar = Table([bar_cells], colWidths=bar_widths, rowHeights=[23])
    commands = [("BACKGROUND", (i,0),(i,0),_color(STATUS[key]["color"])) for i,key in enumerate(STATUS_ORDER)]
    commands.extend([("LINEAFTER",(0,0),(-2,0),1.5,colors.white), ("VALIGN",(0,0),(-1,-1),"MIDDLE")])
    bar.setStyle(TableStyle(commands))
    legends = []
    for key in STATUS_ORDER:
        item = STATUS[key]
        legends.append(Paragraph(f'<font color="{item["color"]}">●</font> {_text(item["label"])} <b>{counts.get(key,0)}</b>', ParagraphStyle("legend", fontName="TISInter", fontSize=5.7, leading=7, textColor=_color(TOKENS["muted_dark"]))))
    legend = Table([legends], colWidths=[(width-20)/7]*7)
    legend.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"), ("LEFTPADDING",(0,0),(-1,-1),1), ("RIGHTPADDING",(0,0),(-1,-1),1), ("TOPPADDING",(0,0),(-1,-1),4)]))
    return Table([[bar],[legend]], colWidths=[width-20], style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),colors.white), ("BOX",(0,0),(-1,-1),.7,_color(TOKENS["card_border"])),
        ("ROUNDEDCORNERS",[8,8,8,8]), ("LEFTPADDING",(0,0),(-1,-1),10),
        ("RIGHTPADDING",(0,0),(-1,-1),10), ("TOPPADDING",(0,0),(-1,-1),5),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))


def _summary_table(rows, headers, widths, styles, *, total=False, color_cols=None, repeat=1):
    matrix = [_header_cells(headers, styles, first_left=True)]
    for row_index, row in enumerate(rows):
        is_total = total and row_index == len(rows) - 1
        cells = []
        for col, value in enumerate(row):
            if isinstance(value, Flowable):
                cells.append(value)
            else:
                style = styles["cell_bold"] if col == 0 or is_total else styles["cell_num"]
                if color_cols and col in color_cols and not is_total:
                    try:
                        color = "#B6C2D4" if int(value) == 0 else color_cols[col]
                    except (TypeError, ValueError):
                        color = color_cols[col]
                    style = ParagraphStyle(f"statuscell-{row_index}-{col}", parent=styles["cell_num"], textColor=_color(color))
                cells.append(_paragraph(value, style))
        matrix.append(cells)
    table = Table(matrix, colWidths=widths, repeatRows=repeat, hAlign="LEFT")
    commands = []
    if total and rows:
        commands.extend([
            ("BACKGROUND",(0,-1),(-1,-1),_color(TOKENS["total_bg"])),
            ("LINEABOVE",(0,-1),(-1,-1),1.5,_color(TOKENS["navy"])),
        ])
    table.setStyle(_table_style(*commands, ("BOX",(0,0),(-1,-1),.6,_color(TOKENS["card_border"])), ("ROUNDEDCORNERS",[8,8,8,8])))
    return table


def _hall_table(report, styles, width):
    headers = ["Hall-ka", "Fadhi", "Arday/fadhi", "Diiwaan maado", "La calaamadiyey", "Joogid", "Maqan (maado)", "Arday maqnaa", "La fasaxay", "Cudur-daar", "Xaalad deg-deg ah", "Soo daahid", "Aan la calaamadin"]
    fields = ("hall", "sessions", "students", "slots", "marked", "present", "absent", "absent_students", "excused", "sick", "emergency", "late", "unmarked")
    rows = [[item.get(field, "") for field in fields] for item in report.get("hall_totals", [])]
    if rows:
        t = report["totals"]
        rows.append(["WADARTA", t.get("sessions", report.get("session_count", 0)), t.get("students", 0), t.get("slots", 0), t.get("marked", 0), *[t.get(key, 0) for key in ("present", "absent", "absent_students", "excused", "sick", "emergency", "late", "unmarked")]])
    else:
        rows = [["Jadwal ku jira muddadan lama helin.", *([""]*12)]]
    widths_ratio = [.15] + [.85/12]*12
    colored = {5: STATUS["present"]["color"], 6: STATUS["absent"]["color"], 7: STATUS["absent"]["color"], 8: STATUS["excused"]["color"], 9: STATUS["sick"]["color"], 10: STATUS["emergency"]["color"], 11: STATUS["late"]["color"], 12: STATUS["unmarked"]["color"]}
    return _summary_table(rows, headers, [width*r for r in widths_ratio], styles, total=bool(report.get("hall_totals")), color_cols=colored)


def _day_table(report, styles, width):
    headers = ["Maalinta imtixaanka", "Hall/fadhi", "Arday/fadhi", "Diiwaan maado", "Joogid", "Maqan", "Arday maqnaa", "La fasaxay", "Cudur-daar", "Xaalad deg-deg", "Soo daahid", "Aan la calaamadin"]
    fields = ("sessions", "students", "slots", "present", "absent", "absent_students", "excused", "sick", "emergency", "late", "unmarked")
    rows = [[_date_label(item["date"], long=True), *[item.get(field, 0) for field in fields]] for item in report.get("day_totals", [])]
    if not rows:
        rows = [["Jadwal ku jira muddadan lama helin.", *([""]*11)]]
    ratios = [.17] + [.83/11]*11
    colored = {4: STATUS["present"]["color"], 5: STATUS["absent"]["color"], 6: STATUS["absent"]["color"], 7: STATUS["excused"]["color"], 8: STATUS["sick"]["color"], 9: STATUS["emergency"]["color"], 10: STATUS["late"]["color"], 11: STATUS["unmarked"]["color"]}
    if report.get("day_totals"):
        totals = report["totals"]
        rows.append(["WADARTA", *[totals.get(field, 0) for field in fields]])
    return _summary_table(rows, headers, [width*r for r in ratios], styles, total=bool(report.get("day_totals")), color_cols=colored)


class StudentAvatar(Flowable):
    def __init__(self, initials, color, size=27):
        super().__init__()
        self.initials, self.color, self.width, self.height = initials[:2], _color(color), size, size

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(self.color)
        c.circle(self.width / 2, self.height / 2, self.width / 2, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("TISInterBold", 7)
        c.drawCentredString(self.width / 2, self.height / 2 - 2.4, self.initials)
        c.restoreState()


def _status_badge(key, styles):
    spec = STATUS[key]
    label = Paragraph(
        f'<font color="{spec["color"]}"><b>{_text(spec["label"])}</b></font>',
        ParagraphStyle(f"badge-{key}", parent=styles["event"], fontName="TISInterBold", fontSize=5.7, leading=6.4, alignment=TA_CENTER),
    )
    badge_width = pdfmetrics.stringWidth(spec["label"], "TISInterBold", 5.7) + 8
    badge = Table([[label]], colWidths=[badge_width], style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),_color(spec["tint"])),
        ("BOX",(0,0),(-1,-1),.45,_color(spec["color"])), ("ROUNDEDCORNERS",[5,5,5,5]),
        ("LEFTPADDING",(0,0),(-1,-1),3), ("RIGHTPADDING",(0,0),(-1,-1),3),
        ("TOPPADDING",(0,0),(-1,-1),1), ("BOTTOMPADDING",(0,0),(-1,-1),1),
    ]))
    return badge


def _date_block(event, category, styles, width, compact_subjects=False):
    accent = STATUS[category]["color"]
    content_width = width - 8
    date_text = _date_label(event["date"], long=False)
    session = event.get("session") or "Fadhi lama sheegin"
    if isinstance(session, int):
        session = f"Fadhiga {session}aad"
    event_time = event.get("time")
    if event_time:
        time_label = event_time.strftime("%I:%M %p").lstrip("0") if hasattr(event_time, "strftime") else str(event_time).strip()
        if time_label and time_label.casefold() not in str(session).casefold():
            session = f"{session} · {time_label}"
    title = Paragraph(
        f'<font color="{accent}"><b>{date_text} · {_text(session)}</b></font>',
        ParagraphStyle("date-title", parent=styles["event"], fontName="TISInterBold", fontSize=6.2, leading=7.2),
    )
    meta = f'{_text(event.get("hall", "-"))} · {_text(event.get("level_name", "-"))} / {_text(event.get("class_name", "-"))}'
    meta_line = Table([[Icon("map-pin", TOKENS["muted"], 7), Paragraph(meta, ParagraphStyle("date-meta", parent=styles["event"], fontSize=5.8, leading=6.6, textColor=_color(TOKENS["muted"]))) ]], colWidths=[9, content_width-9])
    meta_line.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0)]))

    subject_rows = []
    for subject in event.get("subjects", []):
        label = Paragraph(_text(subject.get("name") or "Maado aan la aqoon"), ParagraphStyle("subject", parent=styles["event"], fontSize=6.1, leading=7))
        badge = _status_badge(subject.get("status", "unmarked"), styles)
        if compact_subjects:
            chip = Table([[label], [badge]], colWidths=[max(20, (width-8)/max(1, len(event.get("subjects", []))))], style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),colors.white), ("BOX",(0,0),(-1,-1),.4,_color(TOKENS["card_border"])),
                ("ROUNDEDCORNERS",[5,5,5,5]), ("ALIGN",(0,0),(-1,-1),"CENTER"),
                ("LEFTPADDING",(0,0),(-1,-1),3), ("RIGHTPADDING",(0,0),(-1,-1),3),
                ("TOPPADDING",(0,0),(-1,-1),2), ("BOTTOMPADDING",(0,0),(-1,-1),2),
            ]))
            subject_rows.append(chip)
        else:
            subject_rows.append(Table([[label, badge]], colWidths=[content_width*.58, content_width*.42], style=TableStyle([
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("ALIGN",(1,0),(1,0),"RIGHT"),
                ("LEFTPADDING",(0,0),(-1,-1),0),
                ("RIGHTPADDING",(0,0),(-1,-1),1), ("TOPPADDING",(0,0),(-1,-1),.5),
                ("BOTTOMPADDING",(0,0),(-1,-1),.5),
            ])))

    content = [[title], [meta_line]]
    if compact_subjects and subject_rows:
        content.append([Table([subject_rows], colWidths=[(width-8)/len(subject_rows)]*len(subject_rows), style=TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"), ("LEFTPADDING",(0,0),(-1,-1),1),
            ("RIGHTPADDING",(0,0),(-1,-1),1), ("TOPPADDING",(0,0),(-1,-1),1),
            ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ]))])
    else:
        content.extend([[row] for row in subject_rows])
    for subject in event.get("subjects", []):
        if subject.get("note"):
            content.append([Paragraph(f'<font color="{TOKENS["muted"]}">Faallo: {_text(subject["note"])}</font>', ParagraphStyle("event-note", parent=styles["event"], fontSize=5.5, leading=6.2))])
    inner = Table(content, colWidths=[width-8], style=TableStyle([
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),.5), ("BOTTOMPADDING",(0,0),(-1,-1),.5),
    ]))
    return Table([[inner]], colWidths=[width], style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),_color(TOKENS["row_alt"])),
        ("BOX",(0,0),(-1,-1),.5,_color(TOKENS["card_border"])),
        ("LINEBEFORE",(0,0),(0,0),2.2,_color(accent)),
        ("ROUNDEDCORNERS",[6,6,6,6]), ("LEFTPADDING",(0,0),(-1,-1),4),
        ("RIGHTPADDING",(0,0),(-1,-1),3), ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))


def _student_table(rows, title, empty_message, category, styles, width, max_rows=5, continuation=()):
    headers = ["#", "Ardeyga / aqoonsiga", "Heerka / fasalka", "Taariikhaha, hall-ka, fadhiga iyo maaddooyinka"]
    widths = [width*.03, width*.235, width*.09, width*.645]
    if not rows:
        table = Table([
            _header_cells(headers, styles),
            [Paragraph(_text(empty_message), styles["empty"]), "", "", ""],
        ], colWidths=widths, repeatRows=1)
        table.setStyle(_table_style(("SPAN",(0,1),(-1,1)), ("ALIGN",(0,1),(-1,1),"CENTER")))
        return [SectionHeading(width, title, "users", STATUS[category]["color"], "0 arday"), Spacer(1,3), table]

    event_columns = 1 if category == "unmarked" else min(3, max(1, max(len(row.get("events", [])) for row in rows)))
    pages = []
    avatar_colors = ("#2563EB", "#7C3AED", "#059669", "#D97706", "#DB2777")
    for offset in range(0, len(rows), max_rows):
        chunk = rows[offset:offset+max_rows]
        table_rows = [_header_cells(headers, styles)]
        for index, student in enumerate(chunk, offset+1):
            full_name = student.get("full_name") or "Arday aan la aqoon"
            code = student.get("student_code") or "-"
            initials = student.get("initials") or "".join(word[0] for word in full_name.split()[:2]).upper()
            id_style = ParagraphStyle("student-id", parent=styles["event"], fontName="TISInterBold", fontSize=5.8, leading=6.4, textColor=_color("#2563EB"), alignment=TA_CENTER)
            id_width = min(widths[1] - 38, max(32, pdfmetrics.stringWidth(code, "TISInterBold", 5.8) + 10))
            id_chip = Table([[Paragraph(_text(code), id_style)]], colWidths=[id_width], style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),_color("#E8F0FE")), ("ROUNDEDCORNERS",[5,5,5,5]),
                ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4),
                ("TOPPADDING",(0,0),(-1,-1),1), ("BOTTOMPADDING",(0,0),(-1,-1),1),
            ]))
            name_cell = Table([[Paragraph(_text(full_name), styles["student"])], [id_chip]], style=TableStyle([
                ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
                ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),1),
            ]))
            person = Table([[StudentAvatar(initials, avatar_colors[(index-1) % len(avatar_colors)]), name_cell]], colWidths=[31, widths[1]-31], style=TableStyle([
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LEFTPADDING",(0,0),(-1,-1),3),
                ("RIGHTPADDING",(0,0),(-1,-1),3), ("TOPPADDING",(0,0),(-1,-1),3),
                ("BOTTOMPADDING",(0,0),(-1,-1),3),
            ]))
            class_chip = Table([[Paragraph(_text(student.get("class_name") or "-"), ParagraphStyle("class-chip", parent=styles["event"], fontName="TISInterBold", fontSize=5.8, leading=6.4, textColor=_color(TOKENS["muted_dark"]))) ]], style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),_color("#E8F0FE")), ("ROUNDEDCORNERS",[5,5,5,5]),
                ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4),
                ("TOPPADDING",(0,0),(-1,-1),2), ("BOTTOMPADDING",(0,0),(-1,-1),2),
            ]))
            level_cell = Table([[Paragraph(_text(student.get("level_name") or "-"), styles["cell"])], [class_chip]], style=TableStyle([
                ("LEFTPADDING",(0,0),(-1,-1),3), ("RIGHTPADDING",(0,0),(-1,-1),3),
                ("TOPPADDING",(0,0),(-1,-1),1), ("BOTTOMPADDING",(0,0),(-1,-1),1),
            ]))

            event_area = widths[3]
            event_width = (event_area - 4*(event_columns-1)) / event_columns
            events = student.get("events", [])
            date_rows = []
            for event_offset in range(0, max(1, len(events)), event_columns):
                row_events = events[event_offset:event_offset+event_columns]
                blocks = [_date_block(event, category, styles, event_width, compact_subjects=category == "unmarked") for event in row_events]
                blocks.extend([""] * (event_columns-len(blocks)))
                date_rows.append(blocks)
            event_grid = Table(date_rows or [["-" for _ in range(event_columns)]], colWidths=[event_width]*event_columns, style=TableStyle([
                ("VALIGN",(0,0),(-1,-1),"TOP"), ("LEFTPADDING",(0,0),(-1,-1),0),
                ("RIGHTPADDING",(0,0),(-1,-1),4), ("TOPPADDING",(0,0),(-1,-1),2),
                ("BOTTOMPADDING",(0,0),(-1,-1),2),
            ]))
            table_rows.append([Paragraph(str(index), styles["cell_num"]), person, level_cell, event_grid])
        table = Table(table_rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(_table_style(("BOX",(0,0),(-1,-1),.6,_color(TOKENS["card_border"])), ("ROUNDEDCORNERS",[8,8,8,8])))
        pages.append((chunk, table))

    output = []
    for page_index, (chunk, table) in enumerate(pages):
        if page_index:
            output.extend([PageBreak(), *continuation])
        note = f'{len(rows)} arday | muddada oo dhan' if page_index == 0 else f'{len(rows)} arday | sii socota'
        output.extend([SectionHeading(width, title, "users", STATUS[category]["color"], note), Spacer(1,3), table, Spacer(1,4)])
        if category == "unmarked":
            output.append(_callout("Fiiro gaar ah: Aan la calaamadin macnaheedu ma aha in ardaygu maqnaa; waa xaadirin aan weli nidaamka lagu diiwaangelin.", styles, width))
        elif category == "present":
            output.append(Paragraph("Arday kasta hal saf ayuu ku leeyahay qaybtan; dhammaan taariikhaha Joogid ama Soo Daahid ee muddada la doortay ayaa la isu geeyey.", styles["note"]))
        elif category == "absent":
            output.append(Paragraph("Arday kasta hal saf ayuu ku leeyahay qaybtan; taariikhaha iyo maaddooyinka xaaladaha kale ayaa la isu geeyey. Joogid/Soo Daahid halkan kuma jiraan.", styles["note"]))
    return output


def _callout(text, styles, width):
    row = Table([[IconTile("info", STATUS["unmarked"]["color"], 26, 16), Paragraph(_text(text), styles["cell"]) ]], colWidths=[36, width-36])
    row.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),_color("#ECFEFF")),
        ("BOX",(0,0),(-1,-1),.8,_color("#A5E3EE")),
        ("ROUNDEDCORNERS",[7,7,7,7]), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),7), ("RIGHTPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    return row


def _logo(settings):
    configured = str(settings.get("logo_path") or "").strip().replace("\\", "/")
    candidates = []
    if configured and not configured.startswith(("https://", "http://", "data:")):
        relative = configured.removeprefix("/static/").removeprefix("static/").lstrip("/")
        configured_path = Path(configured)
        candidates.append(configured_path)
        if not configured_path.is_absolute():
            candidates.append(STATIC_DIR / relative)
    candidates.extend([STATIC_DIR / "images" / "taysiir-school-icon-512.png", STATIC_DIR / "images" / "qr-landing-school-logo.jpg"])
    for candidate in candidates:
        try:
            with PILImage.open(candidate) as image:
                image.thumbnail((300,300))
                stream = BytesIO()
                image.convert("RGBA").save(stream, format="PNG")
                return stream.getvalue()
        except (OSError, ValueError, TypeError):
            continue
    return None


def _validate_report(report):
    totals = report["totals"]
    status_total = sum(int(totals.get(key, 0)) for key in STATUS_ORDER)
    slots = int(totals.get("slots", status_total))
    if status_total != slots:
        raise ValueError(f"Attendance status total {status_total} does not equal {slots} subject records")
    additive_fields = ("sessions", "students", "slots", "marked", *STATUS_ORDER)
    for source_name, source_rows in (("daily", report.get("day_totals", [])), ("hall", report.get("hall_totals", []))):
        for field in additive_fields:
            if source_rows and field in totals:
                value = sum(int(row.get(field, 0)) for row in source_rows)
                if value != int(totals[field]):
                    raise ValueError(f"{source_name.title()} {field} total {value} does not equal report total {totals[field]}")
    for key in STATUS_ORDER:
        daily = sum(int(day.get(key, 0)) for day in report.get("day_totals", []))
        if report.get("day_totals") and daily != int(totals.get(key, 0)):
            raise ValueError(f"Daily {key} total {daily} does not equal report total {totals.get(key, 0)}")
    if report.get("hall_totals"):
        for key in STATUS_ORDER:
            hall_sum = sum(int(hall.get(key, 0)) for hall in report["hall_totals"])
            if hall_sum != int(totals.get(key, 0)):
                raise ValueError(f"Hall {key} total {hall_sum} does not equal report total {totals.get(key, 0)}")
    for key, group_name in (("routine", "routine_students"), ("exception", "exception_students"), ("unmarked", "unmarked_students")):
        actual = sum(1 for student in report.get(group_name, []) for event in student.get("events", []) for subject in event.get("subjects", []) if subject.get("status") in ( {"present", "late"} if key == "routine" else {"absent", "excused", "sick", "emergency"} if key == "exception" else {"unmarked"} ))
        expected = sum(int(totals.get(status, 0)) for status in (("present", "late") if key == "routine" else ("absent", "excused", "sick", "emergency") if key == "exception" else ("unmarked",)))
        if actual != expected:
            raise ValueError(f"Detailed {key} records {actual} do not reconcile with KPI totals {expected}")


def _draw_background(canvas, document):
    canvas.saveState()
    canvas.setFillColor(_color(TOKENS["page_bg"]))
    canvas.rect(0, 0, PAGE_SIZE[0], PAGE_SIZE[1], fill=1, stroke=0)
    canvas.restoreState()


def _draw_page(canvas, school_name, prepared_date, page_number, total_pages):
    canvas.saveState()
    canvas.setStrokeColor(_color("#D5DEEB")); canvas.setLineWidth(.7)
    canvas.line(27, 22, PAGE_SIZE[0]-27, 22)
    canvas.setFillColor(_color("#059669")); canvas.roundRect(27, 21, 45, 2, 1, fill=1, stroke=0)
    canvas.setFont("TISInter", 6.4); canvas.setFillColor(_color(TOKENS["muted_light"]))
    canvas.drawString(27, 10, f"{_school_label(school_name).upper()}  |  La diyaariyey {prepared_date}")
    canvas.setFont("TISInterBold", 6.5); canvas.setFillColor(_color(TOKENS["navy"]))
    canvas.drawRightString(PAGE_SIZE[0]-27, 10, f"BOGGA {page_number} / {total_pages}")
    canvas.restoreState()


class _NumberedCanvas:
    """Late-bound footer page counter implemented as a ReportLab canvas mixin."""
    pass


def _numbered_canvas_factory(school_name, prepared_date):
    from reportlab.pdfgen.canvas import Canvas

    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for page_number, state in enumerate(self._saved_page_states, 1):
                self.__dict__.update(state)
                _draw_page(self, school_name, prepared_date, page_number, total_pages)
                Canvas.showPage(self)
            Canvas.save(self)

    return NumberedCanvas


def build_attendance_report_pdf(context):
    """Build the designed attendance PDF from a normalized context mapping.

    Required context keys: ``report``; optional keys: ``settings`` and ``today``.
    The report expects totals, per-day/per-hall totals, and grouped student events.
    """
    report = context["report"]
    _validate_report(report)
    settings = context.get("settings") or {}
    school_name = settings.get("school_name_so") or settings.get("school_name") or "DUGSIYADA CAALAMIGA AH EE TAYSIIR"
    today = context.get("today") or date.today()
    prepared_date = _date_label(today, long=False)
    styles = _styles()
    logo = context.get("logo_bytes") or _logo(settings)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE_SIZE, leftMargin=27, rightMargin=27,
        topMargin=8, bottomMargin=37, title=f"Warbixinta Xaadirinta - {report.get('year', '')}",
        author=school_name, pageCompression=1,
    )
    width = doc.width
    subtitle = f'{report.get("year", "")} | {report.get("exam_name", "")}'
    story = [
        PageHeader(width, styles, school_name, "WARBIXINTA XAADIRINTA IMTIXAANKA", "Tirokoobka goobaha iyo fadhiyada la doortay", logo),
        Spacer(1, 10), _info_cards(report, styles, width), Spacer(1, 8),
        _kpi_strip(report, STATUS_ORDER, styles, width, 7, compact=False), Spacer(1, 10),
        SectionHeading(width, "QAYBINTA XAALADAHA", "bar-chart", "#0F2747", f'{report["totals"].get("slots", 0)} diiwaan maado | la calaamadiyey {report["totals"].get("marked", 0)}'),
        Spacer(1, 2), _stacked_status(report, width, styles), Spacer(1, 9),
        SectionHeading(width, "TIROKOOBKA HALL-KA", "users", "#2563EB", f'{report.get("hall_count", 0)} hall | {report.get("session_count", 0)} fadhi'),
        Spacer(1, 3), _hall_table(report, styles, width), Spacer(1, 4),
        Paragraph("Tirooyinka cards-ku waa maado/fadhi. Safka WADARTA wuxuu la mid yahay tirokoobka guud ee hall-ka.", styles["note"]),
        PageBreak(), PageHeader(width, styles, school_name, "ISU-GEYNTA MAALIN KASTA", subtitle, logo), Spacer(1, 10),
        SectionHeading(width, "XAALADAHA XAADIRINTA MAALIN WALBA", "calendar", "#2563EB", "Xaaladaha waxaa lagu tiriyey maado/fadhi"),
        Spacer(1, 5), _day_cards(report, styles, width), Spacer(1, 7),
        SectionHeading(width, "JADWALKA FAAHFAAHSAN", "list", "#0F2747", f'Hal-ka: {report.get("hall_label", "CUSUB")}'),
        Spacer(1, 3), _day_table(report, styles, width), Spacer(1, 4),
        Paragraph("Tirooyinka waa maado/fadhi. Wadartu waa inay la mid noqotaa tirokoobka guud ee hall-ka.", styles["note"]),
        PageBreak(), PageHeader(width, styles, school_name, "JOOGID IYO SOO DAAHID", subtitle, logo), Spacer(1, 9),
        _kpi_strip(report, ("present", "late"), styles, width, 2), Spacer(1, 5),
    ]
    routine_rows = report.get("routine_students", [])
    story.extend(_student_table(routine_rows, "ARDAYDA JOOGIDDA AMA SOO DAAHIDDA LEH", "Muddadan Joogid ama Soo Daahid laguma diiwaangelin.", "present", styles, width, continuation=(PageHeader(width, styles, school_name, "JOOGID IYO SOO DAAHID", subtitle, logo), Spacer(1, 8), _kpi_strip(report, ("present", "late"), styles, width, 2))))
    story.append(PageBreak())
    story.extend([PageHeader(width, styles, school_name, "XAALADAHA KALE EE U BAAHAN DABAGAL", subtitle, logo), Spacer(1, 9), _kpi_strip(report, ("absent", "excused", "sick", "emergency"), styles, width, 4), Spacer(1, 5)])
    story.extend(_student_table(report.get("exception_students", []), "MAQAN, CUDUR-DAAR, LA FASAXAY IYO XAALAD DEG-DEG AH", "Muddadan xaalado kale oo dabagal u baahan lagama helin.", "absent", styles, width, continuation=(PageHeader(width, styles, school_name, "XAALADAHA KALE EE U BAAHAN DABAGAL", subtitle, logo), Spacer(1, 8), _kpi_strip(report, ("absent", "excused", "sick", "emergency"), styles, width, 4))))
    story.append(PageBreak())
    story.extend([PageHeader(width, styles, school_name, "DIIWAANNADA AAN LA CALAAMADIN", subtitle, logo), Spacer(1, 9), _kpi_strip(report, ("unmarked",), styles, width, 1), Spacer(1, 5)])
    story.extend(_student_table(report.get("unmarked_students", []), "XAADIRIN AAN WELI LA GELIN", "Diiwaanno aan la calaamadin lagama helin muddadan.", "unmarked", styles, width, continuation=(PageHeader(width, styles, school_name, "DIIWAANNADA AAN LA CALAAMADIN", subtitle, logo), Spacer(1, 8), _kpi_strip(report, ("unmarked",), styles, width, 1))))

    doc.build(
        story,
        onFirstPage=_draw_background,
        onLaterPages=_draw_background,
        canvasmaker=_numbered_canvas_factory(school_name, prepared_date),
    )
    return buffer.getvalue()


def build_attendance_summary_pdf(context):
    """Adapt the existing attendance-summary route data to the TIS PDF theme."""
    source = context["report"]
    settings = context.get("settings") or {}
    year = source.get("year")
    year_name = getattr(year, "name", None) or str(year or "")
    hall_names = source.get("hall_names") or []
    if context.get("selected_all_halls"):
        hall_label = "Dhammaan halls-ka"
    elif len(hall_names) == 1:
        hall_label = hall_names[0]
    elif hall_names:
        hall_label = f'{len(hall_names)} hall'
    else:
        hall_label = "—"

    report = {
        "year": year_name,
        "exam_name": source.get("exam_name") or "Imtixaan lama sheegin",
        "start_date": _date_label(source["start_date"], long=True),
        "end_date": _date_label(source["end_date"], long=True),
        "hall_label": hall_label,
        "hall_count": source.get("hall_count", len(hall_names)),
        "session_count": source.get("session_count", 0),
        "totals": source["totals"],
        "hall_totals": source.get("hall_totals", []),
        "day_totals": source.get("day_totals", []),
        "routine_students": source.get("routine_students", []),
        "exception_students": source.get("exception_students", []),
        "unmarked_students": source.get("unmarked_students", []),
    }
    logo_bytes = None
    try:
        from flask import current_app
        from .attendance_summary_pdf import _read_logo_bytes

        logo_bytes = _read_logo_bytes(settings.get("logo_path"), Path(current_app.static_folder).resolve())
    except RuntimeError:
        pass

    return build_attendance_report_pdf({
        "report": report,
        "settings": settings,
        "today": context.get("today"),
        "logo_bytes": logo_bytes,
    })


class DayProgressBar(Flowable):
    def __init__(self, value, total, color, height=4):
        super().__init__()
        self.value, self.total, self.color, self.height = max(0, value), max(0, total), _color(color), height

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(_color("#E9EFF7"))
        c.roundRect(0, 0, self.width, self.height, self.height/2, fill=1, stroke=0)
        if self.total and self.value:
            fill_width = min(self.width, self.width * self.value / self.total)
            c.setFillColor(self.color)
            c.roundRect(0, 0, fill_width, self.height, self.height/2, fill=1, stroke=0)
        c.restoreState()


def _day_cards(report, styles, width):
    days = report.get("day_totals", [])
    if not days:
        return Paragraph("Muddadan jadwal ama xog xaadirin lagama helin.", styles["empty"])
    gap = 8
    card_width = (width-gap*(len(days)-1))/len(days)
    cards = []
    for day in days:
        date_value = date.fromisoformat(day["date"]) if isinstance(day["date"], str) else day["date"]
        top = Table([[Paragraph(f'<font color="white"><b>{date_value.day:02d}</b></font>', ParagraphStyle("daynumber", fontName="TISInterExtra", fontSize=13, leading=15, alignment=TA_CENTER)), Paragraph(f'<font color="white"><b>{WEEKDAYS[date_value.weekday()]}</b></font><br/><font color="#D5E2F3">{MONTHS[date_value.month-1]} {date_value.day}, {date_value.year}</font>', ParagraphStyle("daytitle", fontName="TISInterBold", fontSize=7.2, leading=9, textColor=colors.white))]], colWidths=[32, card_width-40], rowHeights=[43])
        top.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_color("#174B85")), ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4), ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3)]))
        metrics = Table([[f'{day.get("sessions",0)}\nHALL/FADHI', f'{day.get("students",0)}\nARDAY/FADHI', f'{day.get("slots",0)}\nDIIWAAN MAADO']], colWidths=[(card_width-4)/3]*3, rowHeights=[34])
        metrics.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_color("#F7F9FC")), ("GRID",(0,0),(-1,-1),.4,_color(TOKENS["card_border"])), ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("FONTNAME",(0,0),(-1,-1),"TISInterBold"), ("FONTSIZE",(0,0),(-1,-1),6.4), ("TEXTCOLOR",(0,0),(-1,-1),_color(TOKENS["navy"]))]))
        state_rows = []
        state_label_width = 69
        state_icon_width = 18
        state_value_width = 22
        state_bar_width = card_width - state_label_width - state_icon_width - state_value_width
        for key in STATUS_ORDER:
            spec = STATUS[key]
            state_rows.append([
                IconTile(spec["icon"], spec["color"], 14, 9),
                Paragraph(_text(spec["label"]), ParagraphStyle("day-status", parent=styles["event"], fontSize=5.7, leading=6.2)),
                DayProgressBar(day.get(key, 0), day.get("slots", 0), spec["color"]),
                Paragraph(f'<font color="{spec["color"]}"><b>{day.get(key,0)}</b></font>', styles["cell_num"]),
            ])
        states = Table(state_rows, colWidths=[state_icon_width, state_label_width, state_bar_width, state_value_width], rowHeights=[14]*len(state_rows))
        states.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LEFTPADDING",(0,0),(-1,-1),2), ("RIGHTPADDING",(0,0),(-1,-1),2), ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0)]))
        card = Table([[top],[metrics],[states]], colWidths=[card_width], style=TableStyle([("BOX",(0,0),(-1,-1),.6,_color(TOKENS["card_border"])), ("ROUNDEDCORNERS",[8,8,8,8]), ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2)]))
        cards.append(card)
    row = Table([cards], colWidths=[card_width]*len(cards))
    row.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"), ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),gap)]))
    return row
