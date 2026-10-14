"""Render docs/SUBMISSION_SUMMARY.md to docs/SUBMISSION_SUMMARY.pdf.

Handles the small Markdown subset used in that file (headings, paragraphs, bullets,
tables, one image, fenced code) with fpdf2's native API.
Dev-only dependency:  pip install fpdf2
"""

import re
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

DOCS = Path(__file__).resolve().parents[1] / "docs"
SRC = DOCS / "SUBMISSION_SUMMARY.md"
OUT = DOCS / "SUBMISSION_SUMMARY.pdf"

# Core PDF fonts are Latin-1 only; map the few non-Latin-1 symbols used in the summary.
REPLACE = {"→": "->", "–": "-", "—": "-", "≥": ">=", "’": "'", "“": '"', "”": '"', "×": "x"}


def inline(text: str) -> str:
    """Markdown inline -> fpdf2 'markdown=True' syntax (**bold**, __italic__)."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # links -> text
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"__\1__", text)  # *italic*
    return text.replace("`", "")


def render(pdf: FPDF, lines: list[str]) -> None:
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if line.startswith("```"):
            j = i + 1
            pdf.set_font("Courier", size=8.5)
            while j < len(lines) and not lines[j].startswith("```"):
                pdf.cell(0, 4.2, lines[j].rstrip(), new_x="LMARGIN", new_y="NEXT")
                j += 1
            pdf.set_font("Helvetica", size=9.5)
            pdf.ln(2)
            i = j + 1
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            pdf.ln(2 if level > 1 else 0)
            pdf.set_font("Helvetica", "B", {1: 15, 2: 12}.get(level, 10.5))
            pdf.multi_cell(0, 6.5, inline(line.lstrip("# ")), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", size=9.5)
            pdf.ln(1)
            i += 1
            continue
        m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", line)
        if m:
            pdf.image(str(DOCS / m.group(1)), w=pdf.epw * 0.92, x=pdf.l_margin + pdf.epw * 0.04)
            pdf.ln(2)
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [inline(c.strip()).replace("**", "") for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            with pdf.table(col_widths=(38, 62), line_height=5, text_align="LEFT",
                           headings_style=FontFace(emphasis="BOLD", fill_color=(235, 240, 248))) as table:
                for r in rows:
                    row = table.row()
                    for c in r:
                        row.cell(c)
            pdf.ln(2)
            continue
        if line.startswith("- "):
            pdf.set_x(pdf.l_margin + 3)
            pdf.multi_cell(pdf.epw - 3, 5, "- " + inline(line[2:]), markdown=True,
                           new_x="LMARGIN", new_y="NEXT")
            i += 1
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"[#|`!-]", lines[i]):
            para.append(lines[i].strip())
            i += 1
        pdf.multi_cell(0, 5, inline(" ".join(para)), markdown=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    for k, v in REPLACE.items():
        text = text.replace(k, v)
    pdf = FPDF(format="A4")
    pdf.set_margins(16, 14, 16)
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.set_font("Helvetica", size=9.5)
    render(pdf, text.splitlines())
    pdf.output(str(OUT))
    print(f"wrote {OUT} ({pdf.page_no()} pages)")


if __name__ == "__main__":
    main()
