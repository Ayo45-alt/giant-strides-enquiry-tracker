
# recruitment/doc_generator.py

import io
from datetime import date

from django.utils import timezone

from docx import Document
from docx.shared import Pt, RGBColor, Inches

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)
from xml.sax.saxutils import escape


BRAND_NAME = "GIANT STRIDES ONLINE SCHOOL"
REPORT_TITLE = "COMPREHENSIVE ACADEMIC ASSESSMENT REPORT"


def get_report_reference(enquiry):
    """Return the student's existing reference or a stable fallback."""
    return enquiry.reference or f"GS-{enquiry.pk:04d}"


def get_report_date(report_date=None):
    return report_date or timezone.localdate()


def get_report_applications(enquiry):
    """Retrieve approved applications and their submitted reports."""
    job_ad = getattr(enquiry, "job_ad", None)

    if job_ad is None:
        return []

    return list(
        job_ad.applications
        .filter(is_approved=True)
        .select_related("subject", "report")
        .order_by("subject__name")
    )


def _report_sections(enquiry):
    sections = []

    for application in get_report_applications(enquiry):
        report = getattr(application, "report", None)

        sections.append({
            "subject": application.subject.name,
            "tutor": application.full_name,
            "current_level": (
                report.current_level if report else
                "Assessment report pending submission from tutor."
            ),
            "academic_strengths": (
                report.academic_strengths if report else ""
            ),
            "areas_for_improvement": (
                report.areas_for_improvement if report else ""
            ),
            "recommendations": (
                report.recommendations if report else ""
            ),
            "submitted_at": (
                report.submitted_at if report else None
            ),
        })

    return sections


def _report_metadata(enquiry, report_date):
    family = enquiry.family

    return {
        "student_name": enquiry.child_name,
        "reference": get_report_reference(enquiry),
        "parent_name": family.parent_name or "Not provided",
        "country": str(family.country or "Not provided"),
        "generated_date": report_date.strftime("%d %B %Y"),
    }


def build_report_filename(enquiry, extension, report_date=None):
    """Build a safe filename containing the student, reference and date."""
    from django.utils.text import slugify

    report_date = get_report_date(report_date)
    metadata = _report_metadata(enquiry, report_date)

    student = slugify(metadata["student_name"]) or "student"
    reference = slugify(metadata["reference"]) or f"GS-{enquiry.pk:04d}"
    date_text = report_date.strftime("%Y-%m-%d")

    extension = extension.lstrip(".")

    return (
        f"{student}_{reference}_{date_text}_Assessment_Report."
        f"{extension}"
    )


def generate_student_report_docx(enquiry, report_date=None):
    """Generate the admin-downloadable Word report."""
    report_date = get_report_date(report_date)
    metadata = _report_metadata(enquiry, report_date)
    sections = _report_sections(enquiry)

    doc = Document()

    section = doc.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)

    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)

    brand = doc.add_paragraph()
    brand.alignment = 1
    run = brand.add_run(BRAND_NAME)
    run.bold = True
    run.font.size = Pt(15)
    run.font.color.rgb = RGBColor(26, 54, 93)

    title = doc.add_heading(REPORT_TITLE, level=1)
    title.alignment = 1
    title.runs[0].font.color.rgb = RGBColor(26, 54, 93)
    title.runs[0].font.size = Pt(15)

    metadata_table = doc.add_table(rows=4, cols=2)
    metadata_table.style = "Table Grid"

    metadata_rows = [
        ("Student Name", metadata["student_name"]),
        ("Reference Number", metadata["reference"]),
        ("Parent / Guardian", metadata["parent_name"]),
        ("Report Generated", metadata["generated_date"]),
    ]

    for index, (label, value) in enumerate(metadata_rows):
        metadata_table.cell(index, 0).text = label
        metadata_table.cell(index, 1).text = str(value)

        for run in metadata_table.cell(index, 0).paragraphs[0].runs:
            run.bold = True

    doc.add_paragraph()

    country = doc.add_paragraph()
    country.add_run("Country: ").bold = True
    country.add_run(metadata["country"])

    if not sections:
        doc.add_paragraph(
            "No approved tutor assessment reports are available."
        )

    for item in sections:
        heading = doc.add_heading(
            f"Subject: {item['subject']}",
            level=2,
        )
        heading.runs[0].font.color.rgb = RGBColor(43, 108, 176)

        tutor = doc.add_paragraph()
        tutor.add_run("Assessed by: ").bold = True
        tutor.add_run(item["tutor"])

        fields = [
            ("Current Learning Level", item["current_level"]),
            ("Academic Strengths", item["academic_strengths"]),
            (
                "Areas Requiring Improvement",
                item["areas_for_improvement"],
            ),
            ("Recommendations", item["recommendations"]),
        ]

        for label, value in fields:
            paragraph = doc.add_paragraph()
            paragraph.add_run(f"{label}:").bold = True
            doc.add_paragraph(value or "Not provided.")

        doc.add_paragraph()

    footer = section.footer.paragraphs[0]
    footer.alignment = 1
    footer.add_run(
        f"{metadata['student_name']} | "
        f"{metadata['reference']} | "
        f"Generated: {metadata['generated_date']}"
    ).font.size = Pt(8)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    return buffer


def generate_student_report_pdf(enquiry, report_date=None):
    """Generate the parent/guardian PDF report."""
    report_date = get_report_date(report_date)
    metadata = _report_metadata(enquiry, report_date)
    sections = _report_sections(enquiry)

    buffer = io.BytesIO()

    pdf = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=REPORT_TITLE,
        author=BRAND_NAME,
    )

    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="Brand",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#1A365D"),
        alignment=TA_CENTER,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name="ReportTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#1A365D"),
        alignment=TA_CENTER,
        spaceAfter=16,
    ))

    styles.add(ParagraphStyle(
        name="SubjectHeading",
        parent=styles["Heading2"],
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=14,
        spaceAfter=6,
    ))

    styles.add(ParagraphStyle(
        name="ReportBody",
        parent=styles["BodyText"],
        fontSize=9.5,
        leading=14,
        spaceAfter=7,
    ))

    def safe_text(value):
        return escape(str(value or "Not provided")).replace(
            "\n", "<br/>"
        )

    story = [
        Paragraph(BRAND_NAME, styles["Brand"]),
        Paragraph(REPORT_TITLE, styles["ReportTitle"]),
    ]

    metadata_rows = [
        ["Student Name", metadata["student_name"]],
        ["Reference Number", metadata["reference"]],
        ["Parent / Guardian", metadata["parent_name"]],
        ["Country", metadata["country"]],
        ["Report Generated", metadata["generated_date"]],
    ]

    metadata_table = Table(
        [
            [
                Paragraph(f"<b>{safe_text(label)}</b>", styles["ReportBody"]),
                Paragraph(safe_text(value), styles["ReportBody"]),
            ]
            for label, value in metadata_rows
        ],
        colWidths=[48 * mm, 115 * mm],
    )

    metadata_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EDF2F7")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    story.extend([metadata_table, Spacer(1, 12)])

    if not sections:
        story.append(Paragraph(
            "No approved tutor assessment reports are available.",
            styles["ReportBody"],
        ))

    for item in sections:
        subject_content = [
            Paragraph(
                f"Subject: {safe_text(item['subject'])}",
                styles["SubjectHeading"],
            ),
            Paragraph(
                f"<b>Assessed by:</b> {safe_text(item['tutor'])}",
                styles["ReportBody"],
            ),
        ]

        fields = [
            ("Current Learning Level", item["current_level"]),
            ("Academic Strengths", item["academic_strengths"]),
            (
                "Areas Requiring Improvement",
                item["areas_for_improvement"],
            ),
            ("Recommendations", item["recommendations"]),
        ]

        for label, value in fields:
            subject_content.append(Paragraph(
                f"<b>{escape(label)}:</b><br/>{safe_text(value)}",
                styles["ReportBody"],
            ))

        story.append(KeepTogether(subject_content))
        story.append(Spacer(1, 8))

    def draw_page_footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#CBD5E0"))
        canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)

        footer_text = (
            f"{metadata['student_name']} | "
            f"{metadata['reference']} | "
            f"Generated: {metadata['generated_date']}"
        )

        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#4A5568"))
        canvas.drawCentredString(
            A4[0] / 2,
            10 * mm,
            footer_text,
        )
        canvas.restoreState()

    pdf.build(
        story,
        onFirstPage=draw_page_footer,
        onLaterPages=draw_page_footer,
    )

    buffer.seek(0)
    return buffer
