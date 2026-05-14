# import pdfplumber
# from typing import Dict, List
# from reportlab.lib.pagesizes import letter
# from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
# from reportlab.lib.units import inch
# from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
# from reportlab.lib.enums import TA_LEFT, TA_CENTER

# class PDFService:
#     def extract_text_from_pdf(self, pdf_path: str) -> Dict[str, any]:
#         """Extract text content from PDF file."""
#         try:
#             text_content = []
#             total_pages = 0

#             with pdfplumber.open(pdf_path) as pdf:
#                 total_pages = len(pdf.pages)
#                 for page_num, page in enumerate(pdf.pages, start=1):
#                     text = page.extract_text()
#                     if text:
#                         text_content.append({
#                             "page": page_num,
#                             "content": text.strip()
#                         })

#             full_text = "\n\n".join([page["content"] for page in text_content])

#             return {
#                 "total_pages": total_pages,
#                 "pages": text_content,
#                 "full_text": full_text
#             }
#         except Exception as e:
#             raise Exception(f"Error extracting PDF text: {str(e)}")

#     def create_simplified_pdf(self, content: Dict[str, any], output_path: str) -> str:
#         """Create a simplified PDF from structured content."""
#         try:
#             doc = SimpleDocTemplate(
#                 output_path,
#                 pagesize=letter,
#                 rightMargin=72,
#                 leftMargin=72,
#                 topMargin=72,
#                 bottomMargin=18
#             )

#             styles = getSampleStyleSheet()

#             # Custom styles
#             title_style = ParagraphStyle(
#                 'CustomTitle',
#                 parent=styles['Heading1'],
#                 fontSize=24,
#                 textColor='#1a1a1a',
#                 spaceAfter=30,
#                 alignment=TA_CENTER
#             )

#             heading_style = ParagraphStyle(
#                 'CustomHeading',
#                 parent=styles['Heading2'],
#                 fontSize=16,
#                 textColor='#2c5aa0',
#                 spaceAfter=12,
#                 spaceBefore=12
#             )

#             body_style = ParagraphStyle(
#                 'CustomBody',
#                 parent=styles['BodyText'],
#                 fontSize=12,
#                 textColor='#333333',
#                 spaceAfter=12,
#                 alignment=TA_LEFT,
#                 leading=16
#             )

#             story = []

#             # Add title
#             if "title" in content:
#                 story.append(Paragraph(content["title"], title_style))
#                 story.append(Spacer(1, 0.3 * inch))

#             # Add introduction
#             if "introduction" in content:
#                 story.append(Paragraph("<b>Introduction</b>", heading_style))
#                 story.append(Paragraph(content["introduction"], body_style))
#                 story.append(Spacer(1, 0.2 * inch))

#             # Add main sections
#             if "sections" in content:
#                 for section in content["sections"]:
#                     story.append(Paragraph(f"<b>{section['heading']}</b>", heading_style))
#                     story.append(Paragraph(section["content"], body_style))

#                     # Add bullet points if available
#                     if "points" in section:
#                         for point in section["points"]:
#                             story.append(Paragraph(f"• {point}", body_style))

#                     story.append(Spacer(1, 0.2 * inch))

#             # Add summary
#             if "summary" in content:
#                 story.append(PageBreak())
#                 story.append(Paragraph("<b>Summary</b>", heading_style))
#                 story.append(Paragraph(content["summary"], body_style))

#             # Add key takeaways
#             if "key_takeaways" in content:
#                 story.append(Spacer(1, 0.2 * inch))
#                 story.append(Paragraph("<b>Key Takeaways</b>", heading_style))
#                 for takeaway in content["key_takeaways"]:
#                     story.append(Paragraph(f"✓ {takeaway}", body_style))

#             doc.build(story)
#             return output_path

#         except Exception as e:
#             raise Exception(f"Error creating PDF: {str(e)}")

import pdfplumber
from typing import Dict, List, Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, HRFlowable, KeepTogether,
)
from reportlab.platypus.flowables import Flowable


# ---------------------------------------------------------------------------
# Color palette — warm, student-friendly
# ---------------------------------------------------------------------------
C_PRIMARY    = colors.HexColor("#1E3A5F")   # Deep navy  — titles, headings
C_ACCENT     = colors.HexColor("#F4A200")   # Warm amber — highlights
C_SECTION_BG = colors.HexColor("#EBF3FB")   # Light blue — section background
C_EXAMPLE_BG = colors.HexColor("#FFF8E7")   # Warm cream — example boxes
C_REMEMBER_BG= colors.HexColor("#E8F5E9")   # Mint green — "Yaad Rakho" boxes
C_TAKEWAY_BG = colors.HexColor("#FFF3E0")   # Peach      — takeaway boxes
C_WHITE      = colors.white
C_DARK_TEXT  = colors.HexColor("#1A1A1A")
C_MUTED_TEXT = colors.HexColor("#555555")
C_BORDER     = colors.HexColor("#C8D8E8")


# ---------------------------------------------------------------------------
# Custom Flowable: colored rounded box (simulated with Table)
# ---------------------------------------------------------------------------

def _make_callout_table(content_para, bg_color, border_color, left_bar_color=None):
    """
    Wrap a Paragraph in a single-cell Table to simulate a callout box.
    Optional left_bar_color draws a thick left border stripe.
    """
    if left_bar_color:
        # Two-column table: narrow color bar | content
        data = [["", content_para]]
        col_widths = [8, 430]
        style = TableStyle([
            ("BACKGROUND",   (0, 0), (0, 0), left_bar_color),
            ("BACKGROUND",   (1, 0), (1, 0), bg_color),
            ("BOX",          (0, 0), (-1, -1), 1, border_color),
            ("TOPPADDING",   (1, 0), (1, 0), 10),
            ("BOTTOMPADDING",(1, 0), (1, 0), 10),
            ("LEFTPADDING",  (1, 0), (1, 0), 12),
            ("RIGHTPADDING", (1, 0), (1, 0), 10),
            ("TOPPADDING",   (0, 0), (0, 0), 0),
            ("BOTTOMPADDING",(0, 0), (0, 0), 0),
            ("LEFTPADDING",  (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ])
        t = Table(data, colWidths=col_widths)
    else:
        data = [[content_para]]
        col_widths = [438]
        style = TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), bg_color),
            ("BOX",          (0, 0), (-1, -1), 1, border_color),
            ("TOPPADDING",   (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
            ("LEFTPADDING",  (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ])
        t = Table(data, colWidths=col_widths)
    t.setStyle(style)
    return t


# ---------------------------------------------------------------------------
# PDFService
# ---------------------------------------------------------------------------

class PDFService:

    # ------------------------------------------------------------------
    # Text extraction (unchanged)
    # ------------------------------------------------------------------

    def extract_text_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """Extract text content from PDF file."""
        try:
            text_content = []
            total_pages = 0

            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                for page_num, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text()
                    if text:
                        text_content.append({
                            "page": page_num,
                            "content": text.strip(),
                        })

            full_text = "\n\n".join([p["content"] for p in text_content])
            return {"total_pages": total_pages, "pages": text_content, "full_text": full_text}

        except Exception as e:
            raise Exception(f"Error extracting PDF text: {str(e)}")

    # ------------------------------------------------------------------
    # PDF creation — redesigned
    # ------------------------------------------------------------------

    def create_simplified_pdf(self, content: Dict[str, Any], output_path: str) -> str:
        """
        Create a beautifully designed, student-friendly simplified PDF.

        Layout features:
          • Full-width colored title banner
          • Section cards with left color bar
          • "Udaharan" (Example) cream callout boxes
          • "Yaad Rakho" (Remember) green callout boxes
          • Numbered takeaway cards
          • Running footer with page numbers
        """
        try:
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=1.8 * cm,
                leftMargin=1.8 * cm,
                topMargin=2.0 * cm,
                bottomMargin=2.5 * cm,
                title=content.get("title", "CA Study Notes"),
                author="CA Content Processor",
            )

            styles = self._build_styles()
            story  = []

            # ---- Cover / Title banner ----
            story += self._build_title_banner(content, styles)

            # ---- Introduction ----
            if content.get("introduction"):
                story += self._build_intro(content["introduction"], styles)

            # ---- Main sections ----
            for idx, section in enumerate(content.get("sections", []), 1):
                story += self._build_section(idx, section, styles)

            # ---- Summary ----
            if content.get("summary"):
                story.append(PageBreak())
                story += self._build_summary(content["summary"], styles)

            # ---- Key Takeaways ----
            if content.get("key_takeaways"):
                story += self._build_takeaways(content["key_takeaways"], styles)

            # ---- Footer via onLaterPages ----
            doc.build(
                story,
                onFirstPage=self._draw_page_footer,
                onLaterPages=self._draw_page_footer,
            )
            return output_path

        except Exception as e:
            raise Exception(f"Error creating PDF: {str(e)}")

    # ------------------------------------------------------------------
    # Style definitions
    # ------------------------------------------------------------------

    def _build_styles(self) -> dict:
        base = getSampleStyleSheet()

        def ps(name, **kwargs):
            return ParagraphStyle(name, **kwargs)

        return {
            "title": ps(
                "PDFTitle",
                fontName="Helvetica-Bold", fontSize=26,
                textColor=C_WHITE, alignment=TA_CENTER,
                spaceAfter=4, leading=32,
            ),
            "subtitle": ps(
                "PDFSubtitle",
                fontName="Helvetica", fontSize=12,
                textColor=colors.HexColor("#D0E8FF"), alignment=TA_CENTER,
                spaceAfter=0,
            ),
            "section_heading": ps(
                "SecHeading",
                fontName="Helvetica-Bold", fontSize=14,
                textColor=C_PRIMARY, spaceAfter=6, spaceBefore=2,
            ),
            "body": ps(
                "Body",
                fontName="Helvetica", fontSize=11,
                textColor=C_DARK_TEXT, spaceAfter=6,
                leading=17, alignment=TA_JUSTIFY,
            ),
            "body_box": ps(
                "BodyBox",
                fontName="Helvetica", fontSize=11,
                textColor=C_DARK_TEXT, spaceAfter=4,
                leading=16,
            ),
            "bullet": ps(
                "Bullet",
                fontName="Helvetica", fontSize=11,
                textColor=C_DARK_TEXT, spaceAfter=4,
                leading=16, leftIndent=12,
            ),
            "intro": ps(
                "Intro",
                fontName="Helvetica-Oblique", fontSize=12,
                textColor=colors.HexColor("#2C4E80"),
                leading=19, spaceAfter=6, alignment=TA_JUSTIFY,
            ),
            "callout_label": ps(
                "CalloutLabel",
                fontName="Helvetica-Bold", fontSize=10,
                textColor=C_PRIMARY, spaceAfter=3,
            ),
            "callout_body": ps(
                "CalloutBody",
                fontName="Helvetica", fontSize=11,
                textColor=C_DARK_TEXT, leading=16, spaceAfter=0,
            ),
            "takeaway_num": ps(
                "TakeawayNum",
                fontName="Helvetica-Bold", fontSize=22,
                textColor=C_ACCENT, alignment=TA_CENTER,
            ),
            "takeaway_text": ps(
                "TakeawayText",
                fontName="Helvetica", fontSize=11,
                textColor=C_DARK_TEXT, leading=16,
            ),
            "summary_heading": ps(
                "SummaryHeading",
                fontName="Helvetica-Bold", fontSize=16,
                textColor=C_PRIMARY, spaceAfter=8,
            ),
            "footer": ps(
                "Footer",
                fontName="Helvetica", fontSize=8,
                textColor=C_MUTED_TEXT, alignment=TA_CENTER,
            ),
        }

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_title_banner(self, content: dict, s: dict) -> list:
        """Full-width dark navy title block with amber underline."""
        title_text = content.get("title", "CA Study Notes")
        items = []

        # Draw title table spanning full usable width
        title_para    = Paragraph(title_text, s["title"])
        subtitle_para = Paragraph("📚 Simple Notes for CA Students", s["subtitle"])

        banner_data  = [[title_para], [subtitle_para]]
        banner_table = Table(banner_data, colWidths=[475])
        banner_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), C_PRIMARY),
            ("TOPPADDING",    (0, 0), (-1, -1), 16),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
            ("LEFTPADDING",   (0, 0), (-1, -1), 20),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 20),
            ("ROUNDEDCORNERS",(0, 0), (-1, -1), [8, 8, 8, 8]),
        ]))
        items.append(banner_table)

        # Amber accent line
        items.append(Spacer(1, 4))
        items.append(HRFlowable(
            width="100%", thickness=4,
            color=C_ACCENT, spaceAfter=16,
        ))
        return items

    def _build_intro(self, intro_text: str, s: dict) -> list:
        items = []
        label = Paragraph("📖  Yeh Topic Kya Hai?", s["callout_label"])
        body  = Paragraph(intro_text, s["intro"])

        combined = Paragraph(
            f"<b>📖 Yeh Topic Kya Hai?</b><br/>{intro_text}",
            ParagraphStyle(
                "IntroBox",
                fontName="Helvetica", fontSize=11,
                textColor=colors.HexColor("#2C4E80"),
                leading=18, spaceAfter=0,
            ),
        )
        box = _make_callout_table(combined, C_SECTION_BG, C_BORDER, left_bar_color=C_PRIMARY)
        items.append(box)
        items.append(Spacer(1, 14))
        return items

    def _build_section(self, idx: int, section: dict, s: dict) -> list:
        """One section: heading card + content + bullet points + optional example."""
        items = []

        heading  = section.get("heading", f"Section {idx}")
        content  = section.get("content", "")
        points   = section.get("points", [])
        example  = section.get("example", "")

        # Section heading with number badge
        heading_para = Paragraph(
            f"<font color='#F4A200'><b>{idx}.</b></font>  {heading}",
            s["section_heading"],
        )
        heading_table = Table([[heading_para]], colWidths=[475])
        heading_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), C_SECTION_BG),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING",   (0, 0), (-1, -1), 14),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
            ("LINEBELOW",     (0, 0), (-1, -1), 2, C_ACCENT),
        ]))

        section_items = [heading_table, Spacer(1, 6)]

        # Content paragraph
        if content:
            section_items.append(Paragraph(content, s["body"]))
            section_items.append(Spacer(1, 4))

        # Bullet points
        if points:
            for pt in points:
                section_items.append(
                    Paragraph(f"✅  {pt}", s["bullet"])
                )
            section_items.append(Spacer(1, 4))

        # Example callout box
        if example:
            ex_content = Paragraph(
                f"<b>💡 Udaharan (Example):</b><br/>{example}",
                s["callout_body"],
            )
            ex_box = _make_callout_table(
                ex_content, C_EXAMPLE_BG,
                colors.HexColor("#F4A200"),
                left_bar_color=C_ACCENT,
            )
            section_items.append(ex_box)
            section_items.append(Spacer(1, 4))

        items.append(KeepTogether(section_items))
        items.append(Spacer(1, 12))
        return items

    def _build_summary(self, summary_text: str, s: dict) -> list:
        items = []
        items.append(Paragraph("📝  Summary / Saar", s["summary_heading"]))
        items.append(HRFlowable(width="100%", thickness=2, color=C_ACCENT, spaceAfter=10))

        summary_para = Paragraph(summary_text, s["body"])
        box = _make_callout_table(
            summary_para, C_SECTION_BG, C_BORDER, left_bar_color=C_PRIMARY
        )
        items.append(box)
        items.append(Spacer(1, 18))
        return items

    def _build_takeaways(self, takeaways: list, s: dict) -> list:
        """'Yaad Rakho' section with numbered cards in a 2-column grid."""
        items = []
        items.append(
            Paragraph("⭐  Yaad Rakho — Key Takeaways", s["summary_heading"])
        )
        items.append(HRFlowable(width="100%", thickness=2, color=C_ACCENT, spaceAfter=10))

        # Build pairs for 2-column layout
        card_w = 225
        pairs  = [takeaways[i:i+2] for i in range(0, len(takeaways), 2)]

        for pair_idx, pair in enumerate(pairs):
            row_cells = []
            for card_idx, takeaway in enumerate(pair):
                abs_idx = pair_idx * 2 + card_idx + 1
                card_content = Paragraph(
                    f"<font size='18' color='#F4A200'><b>{abs_idx}</b></font><br/>{takeaway}",
                    s["takeaway_text"],
                )
                row_cells.append(card_content)

            # Pad to 2 columns if odd number
            if len(row_cells) == 1:
                row_cells.append(Paragraph("", s["body"]))

            row_table = Table([row_cells], colWidths=[card_w, card_w])
            row_table.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), C_REMEMBER_BG),
                ("BOX",           (0, 0), (0, 0), 1, C_BORDER),
                ("BOX",           (1, 0), (1, 0), 1, C_BORDER),
                ("TOPPADDING",    (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING",   (0, 0), (-1, -1), 12),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ]))
            items.append(row_table)
            items.append(Spacer(1, 6))

        return items

    # ------------------------------------------------------------------
    # Page footer callback
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_page_footer(canvas, doc):
        """Draw a subtle footer with page number on every page."""
        canvas.saveState()
        page_width, page_height = A4
        footer_y = 1.2 * cm

        # Thin separator line
        canvas.setStrokeColor(C_BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(1.8 * cm, footer_y + 10, page_width - 1.8 * cm, footer_y + 10)

        # Footer text
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(C_MUTED_TEXT)
        canvas.drawCentredString(
            page_width / 2,
            footer_y,
            f"CA Study Notes  •  Page {doc.page}  •  Simplified for Easy Understanding",
        )
        canvas.restoreState()
