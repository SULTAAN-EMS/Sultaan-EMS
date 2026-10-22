"""Purpose-built PDF output for the attendance summary report."""

from html import escape
from io import BytesIO
from pathlib import Path
import re

import arabic_reshaper
from bidi.algorithm import get_display
from flask import current_app
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PAGE_SIZE = landscape(A4)
CONTENT_WIDTH = PAGE_SIZE[0] - 24 * mm
NAVY = colors.HexColor("#173763")
BLUE = colors.HexColor("#2563EB")
TEAL = colors.HexColor("#0C8A68")
INK = colors.HexColor("#24334D")
MUTED = colors.HexColor("#64748B")
PALE = colors.HexColor("#F3F7FC")
LINE = colors.HexColor("#D9E2EF")
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


def _date(value, include_year=True):
    label = f"{WEEKDAYS[value.weekday()]}, {MONTHS[value.month - 1]} {value.day}"
    return f"{label}, {value.year}" if include_year else label


def _styles():
    return {
        "school": ParagraphStyle("school", fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=NAVY),
        "brand_sub": ParagraphStyle("brand_sub", fontName="Helvetica", fontSize=7.2, leading=9, textColor=MUTED),
        "report_title": ParagraphStyle("report_title", fontName="Helvetica-Bold", fontSize=10.5, leading=13, alignment=TA_RIGHT, textColor=INK),
        "report_sub": ParagraphStyle("report_sub", fontName="Helvetica", fontSize=7.2, leading=9, alignment=TA_RIGHT, textColor=MUTED),
        "meta_label": ParagraphStyle("meta_label", fontName="Helvetica-Bold", fontSize=7, leading=8.5, textColor=MUTED),
        "meta_value": ParagraphStyle("meta_value", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=INK),
        "kpi_label": ParagraphStyle("kpi_label", fontName="Helvetica-Bold", fontSize=6.7, leading=8, alignment=TA_CENTER, textColor=MUTED),
        "kpi_value": ParagraphStyle("kpi_value", fontName="Helvetica-Bold", fontSize=16, leading=18, alignment=TA_CENTER, textColor=BLUE),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=8.5, leading=10, textColor=NAVY),
        "section_note": ParagraphStyle("section_note", fontName="Helvetica", fontSize=6.5, leading=8, alignment=TA_RIGHT, textColor=MUTED),
        "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=6.3, leading=7.5, alignment=TA_CENTER, textColor=colors.white),
        "th_left": ParagraphStyle("th_left", fontName="Helvetica-Bold", fontSize=6.5, leading=7.7, alignment=TA_LEFT, textColor=colors.white),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=7, leading=8.5, textColor=INK),
        "cell_num": ParagraphStyle("cell_num", fontName="Helvetica-Bold", fontSize=6.8, leading=8, alignment=TA_CENTER, textColor=INK),
        "absent_cell": ParagraphStyle("absent_cell", fontName="Helvetica", fontSize=7.8, leading=10, textColor=INK),
        "student": ParagraphStyle("student", fontName="Helvetica-Bold", fontSize=8.7, leading=10.5, textColor=NAVY),
        "subjects": ParagraphStyle("subjects", fontName="Helvetica", fontSize=8, leading=10.5, textColor=INK),
        "footnote": ParagraphStyle("footnote", fontName="Helvetica", fontSize=6.2, leading=8, textColor=MUTED),
        "empty": ParagraphStyle("empty", fontName="Helvetica", fontSize=7, leading=9, alignment=TA_CENTER, textColor=MUTED),
        "brand_mark": ParagraphStyle("brand_mark", fontName="Helvetica-Bold", fontSize=10, leading=12, alignment=TA_CENTER, textColor=colors.white),
        "session": ParagraphStyle("session", fontName="Helvetica-Bold", fontSize=6.8, leading=8, textColor=BLUE),
    }


def _table_style(*commands):
    return TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        *commands,
    ])


def _masthead(styles, report, school_name, logo=None, absent=False):
    mark = logo or Table([[_p("TIS", styles["brand_mark"])]], colWidths=[11 * mm], rowHeights=[11 * mm])
    if not logo:
        mark.setStyle(_table_style(("BACKGROUND", (0, 0), (-1, -1), NAVY), ("BOX", (0, 0), (-1, -1), 0, NAVY)))
    brand = Table([
        [mark, [_p(school_name, styles["school"]), _p("Warbixinta xaadirinta imtixaanka", styles["brand_sub"])]],
    ], colWidths=[14 * mm, 92 * mm])
    brand.setStyle(_table_style(("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4)))
    title = "LIISKA ARDAYDA MAQNAANSHAHA LEH" if absent else "WARBIXINTA XAADIRINTA IMTIXAANKA"
    subtitle = f'{report["year"].name} · {report["exam_name"]}' if absent else "Tirokoobka goobaha iyo fadhiyada la doortay"
    heading = Table([
        [_p("DIIWAAN RASMI AH" if absent else "WARBIXIN RASMI AH", styles["section_note"])],
        [_p(title, styles["report_title"])],
        [_p(subtitle, styles["report_sub"])],
    ], colWidths=[CONTENT_WIDTH - 106 * mm])
    heading.setStyle(_table_style(("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)))
    row = Table([[brand, heading]], colWidths=[106 * mm, CONTENT_WIDTH - 106 * mm])
    row.setStyle(_table_style(("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.8, LINE), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)))
    return row


def _metadata(styles, report):
    data = [
        [_p("SANAD-DUGSIYEEDKA", styles["meta_label"]), _p("NOOCA IMTIXAANKA", styles["meta_label"]), _p("MUDDADA IYO GOOBTAHA", styles["meta_label"])],
        [_p(report["year"].name, styles["meta_value"]), _p(report["exam_name"], styles["meta_value"]), _p(f'{_date(report["start_date"])} – {_date(report["end_date"])} · {", ".join(report["hall_names"])}', styles["meta_value"])],
    ]
    table = Table(data, colWidths=[42 * mm, 60 * mm, CONTENT_WIDTH - 102 * mm])
    table.setStyle(_table_style(
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 1),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
    ))
    return table


def _section(styles, title, note, width):
    table = Table([[_p(title, styles["section"]), _p(note, styles["section_note"])]], colWidths=[width * 0.63, width * 0.37])
    table.setStyle(_table_style(("LINEBELOW", (0, 0), (-1, -1), 0.6, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)))
    return table


def _summary_table(styles, rows, headers, widths, *, total_row=False):
    matrix = [[_p(header, styles["th_left"] if index == 0 else styles["th"]) for index, header in enumerate(headers)]]
    matrix.extend([[_p(value, styles["cell"] if index == 0 else styles["cell_num"]) for index, value in enumerate(row)] for row in rows])
    empty_message = bool(rows and len(rows[0]) > 1 and not any(rows[0][1:]))
    table = Table(matrix, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]
    if empty_message:
        commands.extend([
            ("SPAN", (0, 1), (-1, 1)),
            ("ALIGN", (0, 1), (-1, 1), "LEFT"),
            ("TEXTCOLOR", (0, 1), (-1, 1), MUTED),
        ])
    if total_row and len(matrix) > 1:
        commands.extend([
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EAF1FA")),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, BLUE),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ])
    table.setStyle(_table_style(*commands))
    return table


def _kpis(styles, totals, width):
    items = [
        ("ARDEY/FADHI", totals["students"], BLUE),
        ("JOOGID (MAADO)", totals["present"], TEAL),
        ("MAQAN (MAADO)", totals["absent"], colors.HexColor("#B53A2E")),
        ("ARDEY MAQNAA/FADHI", totals["absent_students"], colors.HexColor("#A96A12")),
        ("LA FASAXAY", totals["excused"], colors.HexColor("#7650B5")),
        ("AAN LA CALAAMADIN", totals["unmarked"], colors.HexColor("#16778D")),
    ]
    columns = width / len(items)
    cells = []
    for label, value, color in items:
        value_style = ParagraphStyle(f"kpi_{label}", parent=styles["kpi_value"], textColor=color)
        cells.append([_p(label, styles["kpi_label"]), _p(value, value_style)])
    table = Table([cells], colWidths=[columns] * len(items))
    table.setStyle(_table_style(
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ))
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
    weights = [0.16] + [0.07] * 12
    return _summary_table(styles, rows, headers, [width * weight for weight in weights], total_row=bool(report["hall_totals"]))


def _day_table(report, styles, width):
    headers = ["Maalinta imtixaanka", "Hall/fadhi", "Arday/fadhi", "Diiwaan maado", "Joogid", "Maqan", "Arday maqnaa", "La fasaxay", "Cudur daar", "Xaalad degdeg", "Soo daahid", "Aan la calaamadin"]
    keys = ("sessions", "students", "slots", "present", "absent", "absent_students", "excused", "sick", "emergency", "late", "unmarked")
    rows = [[_date(row["date"]), *[row.get(key, 0) for key in keys]] for row in report["day_totals"]]
    if not rows:
        rows.append(["Ma jiraan fadhiyo jadwalaysan muddadan.", *([""] * 11)])
    weights = [0.18] + [0.82 / 11] * 11
    return _summary_table(styles, rows, headers, [width * weight for weight in weights])


def _absent_table(report, styles, width):
    headers = ["#", "Taariikh / fadhi", "Hall-ka", "Ardeyga / aqoonsiga", "Heerka / fasalka", "Maadooyinka maqnaanshaha"]
    header = [_p(item, styles["th"]) for item in headers]
    matrix = [header]
    for index, row in enumerate(report["absent_students"], 1):
        when = _date(row["date"])
        if row.get("time"):
            when += f'<br/><font name="Helvetica-Bold" color="#2563EB">{escape(row["time"].strftime("%I:%M %p").lstrip("0"))}</font>'
        student = f'{_rtl_text(row.get("full_name") or "Arday aan la aqoon")}<br/><font name="Helvetica-Bold" color="#2563EB">{escape(str(row.get("student_code") or "—"))}</font>'
        class_name = f'{_rtl_text(row.get("level_name") or "—")}<br/><font name="Helvetica-Bold">{_rtl_text(row.get("class_name") or "—")}</font>'
        subject_names = "<br/>".join(_rtl_text(subject) for subject in row.get("subjects", [])) or "—"
        matrix.append([
            _p(index, styles["cell_num"]),
            Paragraph(f'{escape(when.split("<br/>")[0])}<br/><font name="Helvetica-Bold" color="#2563EB">{escape(row["session"])}</font>' + (f'<br/><font name="Helvetica" color="#64748B">{row["time"].strftime("%I:%M %p").lstrip("0")}</font>' if row.get("time") else ""), styles["absent_cell"]),
            _p(row.get("hall") or "—", styles["absent_cell"]),
            Paragraph(student, styles["student"]),
            Paragraph(class_name, styles["absent_cell"]),
            Paragraph(subject_names, styles["subjects"]),
        ])
    if len(matrix) == 1:
        matrix.append([_p("Muddadan arday maqnaansho leh lagama helin.", styles["empty"]), "", "", "", "", ""])
    widths = [width * fraction for fraction in (0.04, 0.17, 0.13, 0.25, 0.17, 0.24)]
    table = Table(matrix, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(_table_style(
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("SPAN", (0, 1), (-1, 1)) if len(matrix) == 2 and not report["absent_students"] else ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
    ))
    return table


def build_attendance_summary_pdf(context):
    """Render the summary and absence register as a clean, printable PDF."""
    report = context["report"]
    settings = context.get("settings") or {}
    school_name = settings.get("school_name_so") or settings.get("school_name") or "Dugsiyada Caalamiga ah ee Taysiir"
    logo_path = str(settings.get("logo_path") or "").strip().replace("\\", "/")
    if logo_path.startswith(("http://", "https://", "data:")):
        logo_path = ""
    logo_path = logo_path.removeprefix("/static/").removeprefix("static/").lstrip("/")
    static_root = Path(current_app.static_folder).resolve()
    logo_file = (static_root / logo_path).resolve() if logo_path else None
    logo = None
    if logo_file and logo_file.is_file() and static_root in logo_file.parents:
        try:
            logo = Image(str(logo_file), width=11 * mm, height=11 * mm, kind="proportional")
        except Exception:
            logo = None
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=13 * mm,
        title=f"Warbixinta Guud ee Xaadirinta - {school_name}",
        author=school_name,
    )
    width = doc.width
    story = [
        _masthead(styles, report, school_name, logo=logo),
        Spacer(1, 5),
        _metadata(styles, report),
        Spacer(1, 5),
        _kpis(styles, report["totals"], width),
        Spacer(1, 5),
        _section(styles, "TIROKOOBKA HALL-KA · MUDDADA OO DHAN", f'{report["hall_count"]} hall · {report["session_count"]} fadhi', width),
        Spacer(1, 3),
        _hall_table(report, styles, width),
        Spacer(1, 5),
        _section(styles, "ISU-GEYNTA MAALIN KASTA", "Tirooyinka xaadirintu waa maado/fadhi", width),
        Spacer(1, 3),
        _day_table(report, styles, width),
        Spacer(1, 5),
        _p("* Arday/fadhi wuxuu qofka mar kale tiriyaa haddii uu ku jiro fadhi ama hall kale. Xaaladaha xaadirintu waa tirada maadooyinka; arday kasta oo maqnaa waxaa lagu taxay warqadda xigta.", styles["footnote"]),
        PageBreak(),
        _masthead(styles, report, school_name, logo=logo, absent=True),
        Spacer(1, 7),
        _section(styles, "ARDAYDA IYO MAADOYINKA LOO CALAAMADEEY MAQAN", f'{len(report["absent_students"])} arday/fadhi', width),
        Spacer(1, 4),
        _absent_table(report, styles, width),
    ]

    def draw_footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.line(document.leftMargin, 8 * mm, PAGE_SIZE[0] - document.rightMargin, 8 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(document.leftMargin, 5 * mm, f'{school_name} · Warbixinta xaadirinta imtixaanka · {context["today"].strftime("%d/%m/%Y")}')
        canvas.drawRightString(PAGE_SIZE[0] - document.rightMargin, 5 * mm, f"Bogga {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
