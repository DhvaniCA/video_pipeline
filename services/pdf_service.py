import pdfplumber
from typing import Dict, List
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER

class PDFService:
    def extract_text_from_pdf(self, pdf_path: str) -> Dict[str, any]:
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
                            "content": text.strip()
                        })

            full_text = "\n\n".join([page["content"] for page in text_content])

            return {
                "total_pages": total_pages,
                "pages": text_content,
                "full_text": full_text
            }
        except Exception as e:
            raise Exception(f"Error extracting PDF text: {str(e)}")

    def create_simplified_pdf(self, content: Dict[str, any], output_path: str) -> str:
        """Create a simplified PDF from structured content."""
        try:
            doc = SimpleDocTemplate(
                output_path,
                pagesize=letter,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=18
            )

            styles = getSampleStyleSheet()

            # Custom styles
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=24,
                textColor='#1a1a1a',
                spaceAfter=30,
                alignment=TA_CENTER
            )

            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=16,
                textColor='#2c5aa0',
                spaceAfter=12,
                spaceBefore=12
            )

            body_style = ParagraphStyle(
                'CustomBody',
                parent=styles['BodyText'],
                fontSize=12,
                textColor='#333333',
                spaceAfter=12,
                alignment=TA_LEFT,
                leading=16
            )

            story = []

            # Add title
            if "title" in content:
                story.append(Paragraph(content["title"], title_style))
                story.append(Spacer(1, 0.3 * inch))

            # Add introduction
            if "introduction" in content:
                story.append(Paragraph("<b>Introduction</b>", heading_style))
                story.append(Paragraph(content["introduction"], body_style))
                story.append(Spacer(1, 0.2 * inch))

            # Add main sections
            if "sections" in content:
                for section in content["sections"]:
                    story.append(Paragraph(f"<b>{section['heading']}</b>", heading_style))
                    story.append(Paragraph(section["content"], body_style))

                    # Add bullet points if available
                    if "points" in section:
                        for point in section["points"]:
                            story.append(Paragraph(f"• {point}", body_style))

                    story.append(Spacer(1, 0.2 * inch))

            # Add summary
            if "summary" in content:
                story.append(PageBreak())
                story.append(Paragraph("<b>Summary</b>", heading_style))
                story.append(Paragraph(content["summary"], body_style))

            # Add key takeaways
            if "key_takeaways" in content:
                story.append(Spacer(1, 0.2 * inch))
                story.append(Paragraph("<b>Key Takeaways</b>", heading_style))
                for takeaway in content["key_takeaways"]:
                    story.append(Paragraph(f"✓ {takeaway}", body_style))

            doc.build(story)
            return output_path

        except Exception as e:
            raise Exception(f"Error creating PDF: {str(e)}")