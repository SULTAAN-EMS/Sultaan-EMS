"""Premium, self-contained PDF output for the attendance summary report."""

from base64 import b64decode
from html import escape
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import arabic_reshaper
from bidi.algorithm import get_display
from flask import current_app
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PAGE_SIZE = landscape(A4)
NAVY = colors.HexColor("#142D4E")
NAVY_DARK = colors.HexColor("#0B1E36")
BLUE = colors.HexColor("#2E67D1")
TEAL = colors.HexColor("#09866D")
CORAL = colors.HexColor("#C94E43")
AMBER = colors.HexColor("#B87515")
VIOLET = colors.HexColor("#7550B5")
CYAN = colors.HexColor("#197E91")
INK = colors.HexColor("#1D2B41")
MUTED = colors.HexColor("#66758B")
LINE = colors.HexColor("#DCE4EF")
PALE = colors.HexColor("#F5F8FC")
ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
WEEKDAYS = ("Isniin", "Talaado", "Arbaco", "Khamiis", "Jimco", "Sabti", "Axad")
MONTHS = (
    "Janaayo", "Febraayo", "Maarso", "Abriil", "Maajo", "Juun",
    "Luulyo", "Agoosto", "Sebteembar", "Oktoober", "Nofeembar", "Diseembar",
)

_font_dir = Path(__file__).parent / "static" / "fonts"
pdfmetrics.registerFont(TTFont("NotoArabic", str(_font_dir / "NotoSansArabic-Regular.ttf")))
pdfmetrics.registerFont(TTFont("NotoArabic-Bold", str(_font_dir / "NotoSansArabic-Bold.ttf")))
pdfmetrics.registerFontFamily(
    "NotoArabic", normal="NotoArabic", bold="NotoArabic-Bold",
    italic="NotoArabic", boldItalic="NotoArabic-Bold",
)


def _rtl_text(value):
    text = "" if value is None else str(value)
    if ARABIC_RE.search(text):
        text = get_display(arabic_reshaper.reshape(text))
        return f'<font name="NotoArabic">{escape(text)}</font>'
    return escape(text)


def _p(value, style):
    return Paragraph(_rtl_text(value), style)


def _date(value):
    return f"{WEEKDAYS[value.weekday()]}, {MONTHS[value.month - 1]} {value.day}, {value.year}"


def _styles():
    return {
        "school": ParagraphStyle("attendance_school", fontName="Helvetica-Bold", fontSize=12, leading=14, textColor=colors.white),
        "school_sub": ParagraphStyle("attendance_school_sub", fontName="Helvetica", fontSize=7, leading=8.5, textColor=colors.HexColor("#D2DEEF")),
        "eyebrow": ParagraphStyle("attendance_eyebrow", fontName="Helvetica-Bold", fontSize=6.5, leading=8, alignment=TA_RIGHT, textColor=colors.HexColor("#7CE2C4")),
        "title": ParagraphStyle("attendance_title", fontName="Helvetica-Bold", fontSize=11.2, leading=13, alignment=TA_RIGHT, textColor=colors.white),
        "title_sub": ParagraphStyle("attendance_title_sub", fontName="Helvetica", fontSize=7, leading=9, alignment=TA_RIGHT, textColor=colors.HexColor("#D2DEEF")),
        "meta_label": ParagraphStyle("attendance_meta_label", fontName="Helvetica-Bold", fontSize=6.3, leading=7.5, textColor=MUTED),
        "meta_value": ParagraphStyle("attendance_meta_value", fontName="Helvetica-Bold", fontSize=8.6, leading=10.5, textColor=INK),
        "kpi_label": ParagraphStyle("attendance_kpi_label", fontName="Helvetica-Bold", fontSize=6, leading=7.2, textColor=MUTED),
        "section": ParagraphStyle("attendance_section", fontName="Helvetica-Bold", fontSize=8.1, leading=9.5, textColor=NAVY),
        "section_note": ParagraphStyle("attendance_section_note", fontName="Helvetica", fontSize=6.4, leading=8, alignment=TA_RIGHT, textColor=MUTED),
        "th": ParagraphStyle("attendance_th", fontName="Helvetica-Bold", fontSize=5.8, leading=6.8, alignment=TA_CENTER, textColor=colors.white),
        "th_left": ParagraphStyle("attendance_th_left", fontName="Helvetica-Bold", fontSize=6, leading=7, alignment=TA_LEFT, textColor=colors.white),
        "cell": ParagraphStyle("attendance_cell", fontName="Helvetica", fontSize=6.6, leading=8, textColor=INK),
        "cell_num": ParagraphStyle("attendance_cell_num", fontName="Helvetica-Bold", fontSize=6.5, leading=7.7, alignment=TA_CENTER, textColor=INK),
        "absent_cell": ParagraphStyle("attendance_absent_cell", fontName="Helvetica", fontSize=7, leading=8.6, textColor=INK),
        "student": ParagraphStyle("attendance_student", fontName="Helvetica-Bold", fontSize=8.1, leading=9.8, textColor=NAVY),
        "subjects": ParagraphStyle("attendance_subjects", fontName="Helvetica", fontSize=7.4, leading=9.2, textColor=INK),
        "footnote": ParagraphStyle("attendance_footnote", fontName="Helvetica", fontSize=6.1, leading=7.4, textColor=MUTED),
        "empty": ParagraphStyle("attendance_empty", fontName="Helvetica", fontSize=7, leading=9, alignment=TA_LEFT, textColor=MUTED),
        "session": ParagraphStyle("attendance_session", fontName="Helvetica-Bold", fontSize=6.5, leading=7.6, textColor=BLUE),
    }


class IconBadge(Flowable):
    """Small vector icon on a color tile; keeps the PDF independent of icon fonts."""

    def __init__(self, name, color, size=8 * mm):
        super().__init__()
        self.name = name
        self.color = color
        self.width = size
        self.height = size

    def wrap(self, avail_width, avail_height):
        self.width = min(self.width, avail_width)
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        size = min(self.width, self.height)
        scale = size / 24
        x0 = (self.width - size) / 2
        y0 = (self.height - size) / 2
        canvas.saveState()
        canvas.setFillColor(self.color)
        canvas.roundRect(x0, y0, size, size, 2.2 * mm, stroke=0, fill=1)
        canvas.setStrokeColor(colors.white)
        canvas.setFillColor(colors.white)
        canvas.setLineWidth(max(0.7, 0.95 * scale))
        canvas.setLineCap(1)
        canvas.setLineJoin(1)
        ox, oy = x0 + 5.5 * scale, y0 + 5.5 * scale

        def line(points):
            path = canvas.beginPath()
            path.moveTo(ox + points[0][0] * scale, oy + points[0][1] * scale)
            for point in points[1:]:
                path.lineTo(ox + point[0] * scale, oy + point[1] * scale)
            canvas.drawPath(path, stroke=1, fill=0)

        if self.name == "calendar":
            canvas.roundRect(ox + 1 * scale, oy + 1 * scale, 12 * scale, 12 * scale, 1.6 * scale, stroke=1, fill=0)
            line([(1, 5), (13, 5)])
            line([(4, 0.5), (4, 3.2)])
            line([(10, 0.5), (10, 3.2)])
            canvas.circle(4 * scale + ox, 8 * scale + oy, 0.6 * scale, stroke=0, fill=1)
            canvas.circle(8 * scale + ox, 8 * scale + oy, 0.6 * scale, stroke=0, fill=1)
        elif self.name == "users":
            canvas.circle(5 * scale + ox, 10 * scale + oy, 2 * scale, stroke=1, fill=0)
            path = canvas.beginPath()
            path.moveTo(1 * scale + ox, 2 * scale + oy)
            path.curveTo(1 * scale + ox, 6 * scale + oy, 9 * scale + ox, 6 * scale + oy, 9 * scale + ox, 2 * scale + oy)
            canvas.drawPath(path, stroke=1, fill=0)
            canvas.circle(11 * scale + ox, 9 * scale + oy, 1.7 * scale, stroke=1, fill=0)
            path = canvas.beginPath()
            path.moveTo(9 * scale + ox, 2.2 * scale + oy)
            path.curveTo(10 * scale + ox, 5 * scale + oy, 14 * scale + ox, 5 * scale + oy, 14 * scale + ox, 3 * scale + oy)
            canvas.drawPath(path, stroke=1, fill=0)
        elif self.name == "check":
            canvas.circle(7 * scale + ox, 7 * scale + oy, 6 * scale, stroke=1, fill=0)
            line([(3.5, 7), (6, 4.5), (10.5, 10)])
        elif self.name == "cross":
            canvas.circle(7 * scale + ox, 7 * scale + oy, 6 * scale, stroke=1, fill=0)
            line([(4.5, 4.5), (9.5, 9.5)])
            line([(9.5, 4.5), (4.5, 9.5)])
        elif self.name == "shield":
            path = canvas.beginPath()
            path.moveTo(7 * scale + ox, 13 * scale + oy)
            path.lineTo(1.5 * scale + ox, 10 * scale + oy)
            path.lineTo(1.5 * scale + ox, 4 * scale + oy)
            path.lineTo(7 * scale + ox, 1 * scale + oy)
            path.lineTo(12.5 * scale + ox, 4 * scale + oy)
            path.lineTo(12.5 * scale + ox, 10 * scale + oy)
            path.close()
            canvas.drawPath(path, stroke=1, fill=0)
            line([(4, 7), (6.2, 4.8), (10, 8.7)])
        elif self.name == "clock":
            canvas.circle(7 * scale + ox, 7 * scale + oy, 6 * scale, stroke=1, fill=0)
            line([(7, 11), (7, 7), (10, 5)])
        else:
            canvas.roundRect(ox + 1 * scale, oy + 1 * scale, 12 * scale, 12 * scale, 1.5 * scale, stroke=1, fill=0)
            line([(4, 10), (10, 10)])
            line([(4, 7), (10, 7)])
            line([(4, 4), (8, 4)])
        canvas.restoreState()


class PremiumHeader(Flowable):
    def __init__(self, width, styles, school_name, title, subtitle, logo):
        super().__init__()
        self.width = width
        self.height = 25 * mm
        self.styles = styles
        self.school_name = school_name
        self.title = title
        self.subtitle = subtitle
        self.logo = logo

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        height = self.height
        canvas.saveState()
        canvas.setFillColor(NAVY_DARK)
        canvas.roundRect(0, 0, self.width, height, 3 * mm, stroke=0, fill=1)
        canvas.setFillColor(NAVY)
        canvas.roundRect(2 * mm, 1.5 * mm, self.width - 3.5 * mm, height - 3 * mm, 2.4 * mm, stroke=0, fill=1)
        canvas.setFillColor(TEAL)
        canvas.roundRect(0, 0, 2.2 * mm, height, 1 * mm, stroke=0, fill=1)

        logo_size = 20 * mm
        logo_x = 5 * mm
        logo_y = (height - logo_size) / 2
        canvas.setFillColor(colors.white)
        canvas.roundRect(logo_x, logo_y, logo_size, logo_size, 2.5 * mm, stroke=0, fill=1)
        if self.logo:
            logo_x_inner = logo_x + 1.2 * mm
            logo_y_inner = logo_y + 1.2 * mm
            target = logo_size - 2.4 * mm
            factor = min(target / self.logo.drawWidth, target / self.logo.drawHeight)
            draw_width = self.logo.drawWidth * factor
            draw_height = self.logo.drawHeight * factor
            self.logo.drawOn(canvas, logo_x_inner + (target - draw_width) / 2, logo_y_inner + (target - draw_height) / 2)
        else:
            canvas.setFillColor(BLUE)
            canvas.setFont("Helvetica-Bold", 10)
            canvas.drawCentredString(logo_x + logo_size / 2, logo_y + logo_size / 2 - 3, "TIS")

        left_x = 29 * mm
        right_x = self.width - 5 * mm
        right_width = 103 * mm
        left_width = self.width - left_x - right_width - 13 * mm
        school = _p(self.school_name, self.styles["school"])
        _, school_height = school.wrap(left_width, height)
        school.drawOn(canvas, left_x, height - 7 * mm - school_height)
        school_sub = _p("Diiwaanka rasmiga ah ee xaadirinta", self.styles["school_sub"])
        school_sub.wrapOn(canvas, left_width, height)
        school_sub.drawOn(canvas, left_x, 5 * mm)

        title_x = self.width - right_width - 8 * mm
        for value, style, y in (
            ("SULTAAN EMS  |  WARBIXIN RASMI AH", self.styles["eyebrow"], height - 6 * mm),
            (self.title, self.styles["title"], height - 12 * mm),
            (self.subtitle, self.styles["title_sub"], 5 * mm),
        ):
            paragraph = _p(value, style)
            paragraph.wrapOn(canvas, right_width, 8 * mm)
            paragraph.drawOn(canvas, title_x, y - paragraph.height + (2 * mm if y == 5 * mm else 0))
        canvas.restoreState()


class InfoCard(Flowable):
    def __init__(self, width, label, value, icon, accent, pale, styles):
        super().__init__()
        self.width = width
        self.height = 18 * mm
        self.label = label
        self.value = value
        self.icon = icon
        self.accent = accent
        self.pale = pale
        self.styles = styles

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(self.pale)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.55)
        canvas.roundRect(0, 0, self.width, self.height, 2 * mm, stroke=1, fill=1)
        canvas.setFillColor(self.accent)
        canvas.roundRect(0, 0, 1.5 * mm, self.height, 0.7 * mm, stroke=0, fill=1)
        icon = IconBadge(self.icon, self.accent, 8 * mm)
        icon.wrapOn(canvas, 8 * mm, 8 * mm)
        icon.drawOn(canvas, 3 * mm, (self.height - 8 * mm) / 2)
        text_x = 13 * mm
        text_width = self.width - text_x - 3 * mm
        label = _p(self.label, self.styles["meta_label"])
        label.wrapOn(canvas, text_width, self.height)
        label.drawOn(canvas, text_x, self.height - 6.5 * mm)
        value = _p(self.value, self.styles["meta_value"])
        value.wrapOn(canvas, text_width, 10 * mm)
        value.drawOn(canvas, text_x, 3.2 * mm)
        canvas.restoreState()


class KpiCard(Flowable):
    def __init__(self, width, label, value, icon, accent, pale, styles):
        super().__init__()
        self.width = width
        self.height = 19 * mm
        self.label = label
        self.value = value
        self.icon = icon
        self.accent = accent
        self.pale = pale
        self.styles = styles

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(self.pale)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.roundRect(0, 0, self.width, self.height, 2 * mm, stroke=1, fill=1)
        canvas.setFillColor(self.accent)
        canvas.roundRect(0, self.height - 1.2 * mm, self.width, 1.2 * mm, 0.7 * mm, stroke=0, fill=1)
        icon = IconBadge(self.icon, self.accent, 7.2 * mm)
        icon.wrapOn(canvas, 7.2 * mm, 7.2 * mm)
        icon.drawOn(canvas, 2.6 * mm, (self.height - 7.2 * mm) / 2)
        text_x = 12 * mm
        text_width = self.width - text_x - 2 * mm
        label = _p(self.label, self.styles["kpi_label"])
        label.wrapOn(canvas, text_width, 8 * mm)
        label.drawOn(canvas, text_x, self.height - 6.4 * mm)
        canvas.setFillColor(self.accent)
        canvas.setFont("Helvetica-Bold", 14.5)
        canvas.drawString(text_x, 3 * mm, str(self.value))
        canvas.restoreState()


class SectionBand(Flowable):
    def __init__(self, width, title, note, icon, accent, styles):
        super().__init__()
        self.width = width
        self.height = 10 * mm
        self.title = title
        self.note = note
        self.icon = icon
        self.accent = accent
        self.styles = styles

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(PALE)
        canvas.roundRect(0, 0, self.width, self.height, 2 * mm, stroke=0, fill=1)
        icon = IconBadge(self.icon, self.accent, 7 * mm)
        icon.wrapOn(canvas, 7 * mm, 7 * mm)
        icon.drawOn(canvas, 2 * mm, 1.5 * mm)
        title = _p(self.title, self.styles["section"])
        title.wrapOn(canvas, self.width * 0.62, 7 * mm)
        title.drawOn(canvas, 11 * mm, 5.2 * mm)
        note = _p(self.note, self.styles["section_note"])
        note.wrapOn(canvas, self.width * 0.32, 7 * mm)
        note.drawOn(canvas, self.width - note.width - 3 * mm, 4.8 * mm)
        canvas.restoreState()


def _make_styles_table(*commands):
    return TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        *commands,
    ])


def _metadata(styles, report, width):
    cards = [
        ("SANAD-DUGSIYEEDKA", report["year"].name, "calendar", BLUE, colors.HexColor("#F1F6FF"), 0.17),
        ("NOOCA IMTIXAANKA", report["exam_name"], "check", VIOLET, colors.HexColor("#F7F3FF"), 0.25),
        (
            "MUDDADA IYO GOOBTAHA",
            f'{_date(report["start_date"])} - {_date(report["end_date"])} | {", ".join(report["hall_names"])}',
            "clock", TEAL, colors.HexColor("#EFFAF6"), 0.58,
        ),
    ]
    widths = [width * item[-1] for item in cards]
    table = Table(
        [[InfoCard(card_width, label, value, icon, accent, pale, styles) for card_width, (label, value, icon, accent, pale, _) in zip(widths, cards)]],
        colWidths=widths,
    )
    table.setStyle(_make_styles_table(("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)))
    return table


def _kpis(styles, totals, width):
    items = [
        ("ARDEY / FADHI", totals["students"], "users", BLUE, colors.HexColor("#F1F6FF")),
        ("JOOGID (MAADO)", totals["present"], "check", TEAL, colors.HexColor("#EFFAF6")),
        ("MAQAN (MAADO)", totals["absent"], "cross", CORAL, colors.HexColor("#FFF3F1")),
        ("ARDEY MAQNAA / FADHI", totals["absent_students"], "users", AMBER, colors.HexColor("#FFF8ED")),
        ("LA FASAXAY", totals["excused"], "shield", VIOLET, colors.HexColor("#F7F3FF")),
        ("AAN LA CALAAMADIN", totals["unmarked"], "clock", CYAN, colors.HexColor("#EFF9FB")),
    ]
    gap = 3
    card_width = (width - gap * (len(items) - 1)) / len(items)
    cells = [KpiCard(card_width, *item[:3], item[3], item[4], styles) for item in items]
    table = Table([cells], colWidths=[card_width] * len(items))
    table.setStyle(_make_styles_table(
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), gap),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ))
    return table


def _summary_table(styles, rows, headers, widths, *, total_row=False, color_columns=None):
    color_columns = color_columns or {}
    matrix = [[_p(header, styles["th_left"] if index == 0 else styles["th"]) for index, header in enumerate(headers)]]
    is_empty = bool(rows and len(rows[0]) > 1 and not any(rows[0][1:]))
    for row_index, row in enumerate(rows):
        is_total = total_row and row_index == len(rows) - 1
        values = []
        for column, value in enumerate(row):
            base_style = styles["cell"] if column == 0 else styles["cell_num"]
            if is_total:
                base_style = ParagraphStyle(
                    f"attendance_total_{row_index}_{column}", parent=base_style,
                    fontName="Helvetica-Bold", textColor=color_columns.get(column, NAVY),
                )
            elif column in color_columns:
                base_style = ParagraphStyle(
                    f"attendance_color_{row_index}_{column}", parent=base_style,
                    textColor=color_columns[column],
                )
            values.append(_p(value, base_style))
        matrix.append(values)
    table = Table(matrix, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.3, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 1), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3.2),
    ]
    if is_empty:
        commands.extend([
            ("SPAN", (0, 1), (-1, 1)),
            ("ALIGN", (0, 1), (-1, 1), "LEFT"),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F8FAFD")),
        ])
    if total_row and len(matrix) > 1:
        commands.extend([
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EAF2FC")),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, BLUE),
        ])
    table.setStyle(_make_styles_table(*commands))
    return table


def _hall_table(report, styles, width):
    headers = ["Hall-ka", "Fadhi", "Arday/fadhi", "Diiwaan maado", "La calaamadiyey", "Joogid", "Maqan (maado)", "Arday maqnaa", "La fasaxay", "Cudur daar", "Xaalad degdeg", "Soo daahid", "Aan la calaamadin"]
    keys = ("hall", "sessions", "students", "slots", "marked", "present", "absent", "absent_students", "excused", "sick", "emergency", "late", "unmarked")
    rows = [[row.get(key, 0) for key in keys] for row in report["hall_totals"]]
    totals = report["totals"]
    if rows:
        rows.append(["WADARTA", totals["sessions"], totals["students"], totals["slots"], totals["marked"], totals["present"], totals["absent"], totals["absent_students"], totals["excused"], totals["sick"], totals["emergency"], totals["late"], totals["unmarked"]])
    else:
        rows.append(["Jadwal ku jira muddadan lama helin.", *([""] * 12)])
    weights = [0.15] + [0.85 / 12] * 12
    color_columns = {5: TEAL, 6: CORAL, 7: AMBER, 8: VIOLET, 11: CYAN}
    return _summary_table(styles, rows, headers, [width * weight for weight in weights], total_row=bool(report["hall_totals"]), color_columns=color_columns)


def _day_table(report, styles, width):
    headers = ["Maalinta imtixaanka", "Hall/fadhi", "Arday/fadhi", "Diiwaan maado", "Joogid", "Maqan", "Arday maqnaa", "La fasaxay", "Cudur daar", "Xaalad degdeg", "Soo daahid", "Aan la calaamadin"]
    keys = ("sessions", "students", "slots", "present", "absent", "absent_students", "excused", "sick", "emergency", "late", "unmarked")
    rows = [[_date(row["date"]), *[row.get(key, 0) for key in keys]] for row in report["day_totals"]]
    if not rows:
        rows.append(["Ma jiraan fadhiyo jadwalaysan muddadan.", *([""] * 11)])
    weights = [0.17] + [0.83 / 11] * 11
    color_columns = {4: TEAL, 5: CORAL, 6: AMBER, 7: VIOLET, 10: CYAN}
    return _summary_table(styles, rows, headers, [width * weight for weight in weights], color_columns=color_columns)


def _absent_table(report, styles, width):
    headers = ["#", "Taariikh / fadhi", "Hall-ka", "Ardeyga / aqoonsiga", "Heerka / fasalka", "Maadooyinka maqnaanshaha"]
    matrix = [[_p(header, styles["th"]) for header in headers]]
    for index, row in enumerate(report["absent_students"], 1):
        when = _date(row["date"])
        time = row.get("time")
        session = escape(str(row.get("session") or "Fadhi lama sheegin"))
        if time:
            session += f'<br/><font name="Helvetica" color="#66758B">{escape(time.strftime("%I:%M %p").lstrip("0"))}</font>'
        student = f'{_rtl_text(row.get("full_name") or "Arday aan la aqoon")}<br/><font name="Helvetica-Bold" color="#2E67D1">{escape(str(row.get("student_code") or "-"))}</font>'
        class_name = f'{_rtl_text(row.get("level_name") or "-")}<br/><font name="Helvetica-Bold">{_rtl_text(row.get("class_name") or "-")}</font>'
        subject_names = "<br/>".join(_rtl_text(subject) for subject in row.get("subjects", [])) or "-"
        matrix.append([
            _p(index, styles["cell_num"]),
            Paragraph(f'{escape(when)}<br/><font name="Helvetica-Bold" color="#2E67D1">{session}</font>', styles["absent_cell"]),
            _p(row.get("hall") or "-", styles["absent_cell"]),
            Paragraph(student, styles["student"]),
            Paragraph(class_name, styles["absent_cell"]),
            Paragraph(subject_names, styles["subjects"]),
        ])
    if len(matrix) == 1:
        matrix.append([_p("Muddadan arday maqnaansho leh lagama helin.", styles["empty"]), "", "", "", "", ""])
    widths = [width * fraction for fraction in (0.04, 0.19, 0.13, 0.24, 0.17, 0.23)]
    table = Table(matrix, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
    ]
    if len(matrix) == 2 and not report["absent_students"]:
        commands.extend([("SPAN", (0, 1), (-1, 1)), ("ALIGN", (0, 1), (-1, 1), "LEFT")])
    table.setStyle(_make_styles_table(*commands))
    return table


def _image_bytes(raw):
    with PILImage.open(BytesIO(raw)) as image:
        image.thumbnail((900, 900))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def _read_logo_bytes(value, static_root):
    value = str(value or "").strip().replace("\\", "/")
    if not value:
        return None
    if value.startswith("data:image/"):
        try:
            header, payload = value.split(",", 1)
            if ";base64" in header:
                raw = b64decode(payload, validate=True)
                return _image_bytes(raw) if len(raw) <= 3 * 1024 * 1024 else None
        except (ValueError, OSError):
            return None
        return None
    if value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "res.cloudinary.com":
            current_app.logger.warning("Skipping unsupported remote school logo host: %s", parsed.hostname)
            return None
        try:
            request = Request(value, headers={"User-Agent": "SULTAAN-EMS attendance PDF"})
            with urlopen(request, timeout=5) as response:
                raw = response.read(3 * 1024 * 1024 + 1)
            return _image_bytes(raw) if len(raw) <= 3 * 1024 * 1024 else None
        except Exception as exc:
            current_app.logger.warning("Unable to load configured school logo from Cloudinary: %s", exc)
            return None

    relative = value
    if relative.startswith("/static/"):
        relative = relative[len("/static/"):]
    elif relative.startswith("static/"):
        relative = relative[len("static/"):]
    else:
        relative = relative.lstrip("/")
    try:
        image_path = (static_root / relative).resolve()
        if static_root not in image_path.parents or not image_path.is_file():
            return None
        raw = image_path.read_bytes()
        return _image_bytes(raw) if len(raw) <= 3 * 1024 * 1024 else None
    except (OSError, ValueError):
        return None


def _school_logo(settings, static_root):
    configured = _read_logo_bytes(settings.get("logo_path"), static_root)
    if configured:
        return Image(BytesIO(configured), width=18 * mm, height=18 * mm, kind="proportional")
    fallback = static_root / "images" / "qr-landing-school-logo.jpg"
    try:
        raw = fallback.read_bytes()
        return Image(BytesIO(_image_bytes(raw)), width=18 * mm, height=18 * mm, kind="proportional")
    except (OSError, ValueError):
        return None


def build_attendance_summary_pdf(context):
    """Render the summary and absence register as a premium, printable PDF."""
    report = context["report"]
    settings = context.get("settings") or {}
    school_name = settings.get("school_name_so") or settings.get("school_name") or "Dugsiyada Caalamiga ah ee Taysiir"
    static_root = Path(current_app.static_folder).resolve()
    logo = _school_logo(settings, static_root)
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=8 * mm,
        bottomMargin=12 * mm,
        title=f"Warbixinta Guud ee Xaadirinta - {school_name}",
        author=school_name,
    )
    width = doc.width
    subtitle = f'{report["year"].name} | {report["exam_name"]}'
    story = [
        PremiumHeader(width, styles, school_name, "WARBIXINTA XAADIRINTA IMTIXAANKA", "Tirokoobka goobaha iyo fadhiyada la doortay", logo),
        Spacer(1, 4),
        _metadata(styles, report, width),
        Spacer(1, 4),
        _kpis(styles, report["totals"], width),
        Spacer(1, 5),
        SectionBand(width, "TIROKOOBKA HALL-KA", f'{report["hall_count"]} hall | {report["session_count"]} fadhi', "users", BLUE, styles),
        Spacer(1, 2),
        _hall_table(report, styles, width),
        Spacer(1, 4),
        SectionBand(width, "ISU-GEYNTA MAALIN KASTA", "Xaaladaha xaadirinta waxaa lagu tiriyey maado/fadhi", "calendar", TEAL, styles),
        Spacer(1, 2),
        _day_table(report, styles, width),
        Spacer(1, 3),
        _p("Arday/fadhi wuxuu qofka mar kale tiriyaa haddii uu ku jiro fadhi ama hall kale. Xaaladaha maqan, joogid iyo fasax waa tirada maadooyinka. Liiska magacyadu wuxuu ku yaal bogga xiga.", styles["footnote"]),
        PageBreak(),
        PremiumHeader(width, styles, school_name, "LIISKA ARDAYDA MAQNAANSHAHA LEH", subtitle, logo),
        Spacer(1, 5),
        SectionBand(width, "ARDAYDA IYO MAADOYINKA LOO CALAAMADEEY MAQAN", f'{len(report["absent_students"])} arday/fadhi', "cross", CORAL, styles),
        Spacer(1, 3),
        _absent_table(report, styles, width),
    ]

    def draw_footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.line(document.leftMargin, 8 * mm, PAGE_SIZE[0] - document.rightMargin, 8 * mm)
        canvas.setFillColor(TEAL)
        canvas.roundRect(document.leftMargin, 7.5 * mm, 18 * mm, 1 * mm, 0.5 * mm, stroke=0, fill=1)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(document.leftMargin, 4.5 * mm, f'{school_name} | La diyaariyey {context["today"].strftime("%d/%m/%Y")}')
        canvas.drawRightString(PAGE_SIZE[0] - document.rightMargin, 4.5 * mm, f"BOGGA {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
