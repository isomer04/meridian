"""Generate the permanent fictional PDFs used to exercise Meridian document intake."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf"
FONT_DIR = ROOT / "assets" / "fonts"
FIELDS = [
    ("Borrower Name", "Alex Morgan"),
    ("SSN", "900-12-3456"),
    ("Employer", "Northwind Logistics"),
    ("Annual Income", "$132,000"),
    ("Property Address", "1420 Alder Street, Boise, ID 83702"),
    ("Estimated Value", "$500,000"),
    ("Purchase Price", "$500,000"),
    ("Loan Amount", "$400,000"),
    ("Interest Rate", "6.5%"),
    ("Loan Term", "30 years"),
]


def _draw_native(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    pdf.setTitle("Meridian fictional native-text loan application")
    pdf.setFillColor(HexColor("#101C2C"))
    pdf.rect(0, 720, 612, 72, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(48, 753, "MERIDIAN DEMO APPLICATION")
    pdf.setFillColor(HexColor("#172033"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(48, 692, "FICTIONAL TEST DATA - NOT A REAL BORROWER")
    y = 646
    for label, value in FIELDS:
        pdf.setFillColor(HexColor("#667085"))
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(48, y, f"{label.upper()}:")
        pdf.setFillColor(HexColor("#172033"))
        pdf.setFont("Helvetica", 12)
        pdf.drawString(210, y, value)
        pdf.setStrokeColor(HexColor("#C8D4E0"))
        pdf.line(48, y - 9, 564, y - 9)
        y -= 47
    pdf.setFillColor(HexColor("#667085"))
    pdf.setFont("Helvetica", 8)
    pdf.drawString(48, 42, "The 900-series SSN is deliberately fictional and must never be used for a real person.")
    pdf.save()


def _draw_scan(path: Path) -> None:
    image = Image.new("RGB", (1275, 1650), "#F1F5F9")
    draw = ImageDraw.Draw(image)
    regular = ImageFont.truetype(FONT_DIR / "DejaVuSans.ttf", 25)
    bold = ImageFont.truetype(FONT_DIR / "DejaVuSans-Bold.ttf", 25)
    title = ImageFont.truetype(FONT_DIR / "DejaVuSans-Bold.ttf", 42)
    draw.rectangle((0, 0, 1275, 150), fill="#101C2C")
    draw.text((95, 52), "MERIDIAN SCANNED DEMO", fill="white", font=title)
    draw.text((95, 195), "FICTIONAL TEST DATA - IMAGE-ONLY PDF", fill="#172033", font=bold)
    y = 285
    for label, value in FIELDS:
        draw.text((95, y), f"{label}:", fill="#566276", font=bold)
        draw.text((430, y), value, fill="#172033", font=regular)
        draw.line((95, y + 42, 1180, y + 42), fill="#C8D4E0", width=2)
        y += 88
    draw.text((95, 1530), "Fictional 900-series SSN. Never use real customer data in this sample.", fill="#667085", font=regular)
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    pdf = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    pdf.setTitle("Meridian fictional scanned loan application")
    pdf.drawImage(ImageReader(BytesIO(encoded.getvalue())), 0, 0, width=612, height=792)
    pdf.save()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _draw_native(OUTPUT / "meridian-demo-native-application.pdf")
    _draw_scan(OUTPUT / "meridian-demo-scanned-application.pdf")


if __name__ == "__main__":
    main()
