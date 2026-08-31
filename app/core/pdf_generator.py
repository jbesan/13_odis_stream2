import os
import re
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from fpdf import FPDF
from fpdf.fonts import FontFace
from fpdf.enums import TextEmphasis, XPos, YPos

import config as cfg
from core.models import (
    SearchCriterias,
    SearchResultsData,
    CommuneResult,
    CommuneScoreDetail,
)

logger = logging.getLogger(__name__)

# --- Brand Color Constants (RGB) ---
COLOR_PRIMARY_TEAL = (0, 98, 104)      # #006268 (Brand Teal)
COLOR_SECONDARY_TEAL = (22, 139, 141)  # #168B8D
COLOR_DARK_SLATE = (30, 41, 59)        # #1E293B (Body text)
COLOR_MUTED_GRAY = (100, 116, 139)     # #64748B (Captions & subtitles)
COLOR_LIGHT_BG = (248, 250, 252)       # #F8FAFC (Card background)
COLOR_ALT_ROW = (241, 245, 249)        # #F1F5F9 (Table zebra)
COLOR_BORDER_LIGHT = (226, 232, 240)   # #E2E8F0 (Subtle borders)
COLOR_TABLE_HEADER_BG = (235, 245, 245)# #EBF5F5 (Table header fill)
COLOR_ALERT_BG = (254, 249, 231)       # #FEF9E7 (Warning background)
COLOR_ALERT_BORDER = (217, 119, 6)     # #D97706 (Warning border)
COLOR_WHITE = (255, 255, 255)
COLOR_GOLD_ACCENT = (202, 138, 4)      # Pinned commune accent


def _setup_unicode_font(pdf: FPDF) -> None:
    """Adds the local Unicode DejaVu fonts to the FPDF instance."""
    font_dir = os.path.join(cfg.ASSETS_DIR, "fonts")
    try:
        pdf.add_font("DejaVu", "", os.path.join(font_dir, "DejaVuSans.ttf"))
        pdf.add_font("DejaVu", "B", os.path.join(font_dir, "DejaVuSans-Bold.ttf"))
        pdf.add_font("DejaVu", "I", os.path.join(font_dir, "DejaVuSans-Oblique.ttf"))
        pdf.add_font(
            "DejaVu", "BI", os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf")
        )
        pdf.set_font("DejaVu", size=9)
    except Exception:
        logger.warning(
            "Could not load local Unicode DejaVu font; falling back to Helvetica",
            exc_info=True,
        )
        pdf.set_font("Helvetica", size=9)


def html_escape(text: str) -> str:
    """Safely escape HTML special characters for fpdf2 write_html."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def clean_markdown_text_for_pdf(text: str) -> str:
    """Cleans emojis and unsupported unicode glyphs from markdown text for DejaVu."""
    if not text:
        return ""
    # Strip emojis cleanly without leaving ugly bracketed tags
    emojis = [
        "🧭", "🔬", "🏠", "🚆", "🏥", "🎓", "🤝", "💼", "⚖️", "⚖",
        "⚠️", "⚠", "❓", "📌", "👤", "💬", "✨", "🔍", "📊", "🎯", "🏷️"
    ]
    for emo in emojis:
        text = text.replace(emo, "")

    # Strip remaining high-plane emojis outside DejaVu range
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    # Clean redundant double spaces left after emoji removal
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


class ODISReportPDF(FPDF):
    """Custom FPDF document class with standardized ODIS branding, headers, and footers."""

    def __init__(self, search_ref: str = ""):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.search_ref = search_ref
        self.set_margins(12, 12, 12)
        self.set_auto_page_break(auto=True, margin=15)
        _setup_unicode_font(self)

    def header(self):
        """Header rendered on page 2 and onwards."""
        if self.page_no() > 1:
            self.set_font("DejaVu", "B", 7.5)
            self.set_text_color(*COLOR_PRIMARY_TEAL)
            self.cell(100, 5, "OD&IS  ·  SYNTHÈSE DE RECHERCHE TERRITORIALE", align="L")
            self.set_font("DejaVu", "", 7.5)
            self.set_text_color(*COLOR_MUTED_GRAY)
            self.cell(
                0,
                5,
                f"Dossier J'Accueille  |  {datetime.date.today().strftime('%d/%m/%Y')}",
                align="R",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            # Thin separator line
            self.set_draw_color(*COLOR_BORDER_LIGHT)
            self.set_line_width(0.3)
            self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
            self.ln(5)

    def footer(self):
        """Standardized footer on all pages."""
        self.set_y(-12)
        self.set_draw_color(*COLOR_BORDER_LIGHT)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_font("DejaVu", "", 7.5)
        self.set_text_color(*COLOR_MUTED_GRAY)
        self.cell(
            120,
            4,
            "Document confidentiel · Accompagnement à la mobilité · J'Accueille",
            align="L",
        )
        self.cell(0, 4, f"Page {self.page_no()} / {{nb}}", align="R")

    def draw_section_title(self, title: str, subtitle: Optional[str] = None):
        """Renders a primary section header with a brand teal accent."""
        self.ln(2)
        self.set_font("DejaVu", "B", 10)
        self.set_text_color(*COLOR_PRIMARY_TEAL)
        self.cell(self.epw, 5.5, title.upper(), 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if subtitle:
            self.set_font("DejaVu", "I", 7.5)
            self.set_text_color(*COLOR_MUTED_GRAY)
            self.cell(self.epw, 4, subtitle, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*COLOR_PRIMARY_TEAL)
        self.set_line_width(0.5)
        y = self.get_y() + 0.8
        self.line(self.l_margin, y, self.l_margin + 35, y)
        self.set_draw_color(*COLOR_BORDER_LIGHT)
        self.set_line_width(0.2)
        self.line(self.l_margin + 35, y, self.w - self.r_margin, y)
        self.ln(2.5)

    def draw_callout_box(
        self,
        text: str,
        title: Optional[str] = None,
        border_color: Tuple[int, int, int] = COLOR_PRIMARY_TEAL,
        bg_color: Tuple[int, int, int] = COLOR_LIGHT_BG,
        text_color: Tuple[int, int, int] = COLOR_DARK_SLATE,
        italic: bool = False,
    ):
        """Renders a stylized card box with a thick left accent border and inline markdown parsing."""
        if not text:
            return
        clean_text = clean_markdown_text_for_pdf(text.strip())
        clean_text = re.sub(r"^\s*[-*]\s+", "• ", clean_text, flags=re.MULTILINE)

        self.set_font("DejaVu", "I" if italic else "", 8)
        wrapped_lines = self.multi_cell(
            self.epw - 10, 4.2, clean_text, markdown=True, output="LINES", dry_run=True
        )

        title_extra = 5.5 if title else 0
        box_h = max(10, len(wrapped_lines) * 4.2 + 5.0 + title_extra)

        # Trigger page break if box won't fit
        if self.get_y() + box_h > self.h - self.b_margin:
            self.add_page()

        x = self.get_x()
        y = self.get_y()
        w = self.epw

        # Draw background and left border
        self.set_fill_color(*bg_color)
        self.set_draw_color(*COLOR_BORDER_LIGHT)
        self.set_line_width(0.2)
        self.rect(x, y, w, box_h, style="FD")
        self.set_fill_color(*border_color)
        self.rect(x, y, 2.5, box_h, style="F")

        # Render title if present
        if title:
            self.set_xy(x + 5, y + 2.5)
            self.set_font("DejaVu", "B", 8)
            self.set_text_color(*border_color)
            self.cell(w - 10, 4.5, title)

        # Render body text
        self.set_xy(x + 5, y + 2.5 + title_extra)
        self.set_font("DejaVu", "I" if italic else "", 8)
        self.set_text_color(*text_color)
        self.multi_cell(w - 10, 4.2, clean_text, markdown=True)

        self.set_y(y + box_h + 3.0)




class MarkdownReportRenderer:
    """Renders structured Markdown content without raw HTML colors, oversized fonts, or emoji bugs."""

    def __init__(self, pdf: ODISReportPDF):
        self.pdf = pdf

    def render(self, md_text: str):
        """Parses and renders Markdown blocks sequentially."""
        if not md_text:
            return

        cleaned_md = clean_markdown_text_for_pdf(md_text.replace("\r\n", "\n"))
        lines = cleaned_md.split("\n")

        table_buffer: List[str] = []
        in_table = False

        for line in lines:
            trimmed = line.strip()
            is_table_line = "|" in line

            if is_table_line:
                if not in_table:
                    in_table = True
                    table_buffer = [line]
                else:
                    table_buffer.append(line)
                continue

            if in_table:
                in_table = False
                self._render_table_block(table_buffer)
                table_buffer = []

            if not trimmed:
                self.pdf.ln(1.5)
                continue

            # Separator rule (---)
            if re.match(r"^(\-{3,}|\*{3,}|_{3,})$", trimmed):
                self.pdf.ln(2)
                self.pdf.set_draw_color(*COLOR_BORDER_LIGHT)
                self.pdf.set_line_width(0.3)
                self.pdf.line(
                    self.pdf.l_margin,
                    self.pdf.get_y(),
                    self.pdf.w - self.pdf.r_margin,
                    self.pdf.get_y(),
                )
                self.pdf.ln(3)
                continue

            # Headers (# to ######)
            header_match = re.match(r"^(#{1,6})\s+(.*)", trimmed)
            if header_match:
                hashes, title_text = header_match.groups()
                level = len(hashes)
                if level == 1:
                    self.pdf.ln(3)
                    self.pdf.set_font("DejaVu", "B", 11)
                    self.pdf.set_text_color(*COLOR_PRIMARY_TEAL)
                    self.pdf.cell(
                        self.pdf.epw, 6, title_text, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT
                    )
                    self.pdf.set_draw_color(*COLOR_PRIMARY_TEAL)
                    self.pdf.set_line_width(0.4)
                    self.pdf.line(
                        self.pdf.l_margin,
                        self.pdf.get_y(),
                        self.pdf.l_margin + 25,
                        self.pdf.get_y(),
                    )
                    self.pdf.ln(2)
                elif level == 2:
                    self.pdf.ln(2.5)
                    self.pdf.set_font("DejaVu", "B", 9.5)
                    self.pdf.set_text_color(*COLOR_PRIMARY_TEAL)
                    self.pdf.cell(
                        self.pdf.epw, 5.5, title_text, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT
                    )
                    self.pdf.ln(1)
                elif level == 3:
                    self.pdf.ln(2)
                    self.pdf.set_font("DejaVu", "B", 8.5)
                    self.pdf.set_text_color(*COLOR_DARK_SLATE)
                    self.pdf.cell(
                        self.pdf.epw, 5, title_text, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT
                    )
                    self.pdf.ln(0.5)
                elif level == 4:
                    self.pdf.ln(1.8)
                    self.pdf.set_font("DejaVu", "B", 8)
                    self.pdf.set_text_color(*COLOR_PRIMARY_TEAL)
                    self.pdf.cell(
                        self.pdf.epw, 4.5, title_text, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT
                    )
                    self.pdf.ln(0.5)
                else:  # level 5 or 6
                    self.pdf.ln(1.5)
                    self.pdf.set_font("DejaVu", "BI", 7.5)
                    self.pdf.set_text_color(*COLOR_DARK_SLATE)
                    self.pdf.cell(
                        self.pdf.epw, 4, title_text, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT
                    )
                    self.pdf.ln(0.5)
                continue

            # Blockquotes (> text)
            if trimmed.startswith(">"):
                quote_text = re.sub(r"^>\s*", "", trimmed)
                self.pdf.set_font("DejaVu", "I", 8)
                self.pdf.set_text_color(*COLOR_MUTED_GRAY)
                self.pdf.cell(4, 4.5, "▎", 0, align="R")
                self._render_inline_html_line(quote_text)
                self.pdf.ln(1)
                continue

            # List item (bullet or numbered)
            bullet_match = re.match(r"^(\s*)([-*+•]|\d+\.)\s+(.*)", line)
            if bullet_match:
                indent_str, marker, content = bullet_match.groups()
                indent_level = len(indent_str) // 2
                x_offset = indent_level * 4

                self.pdf.set_x(self.pdf.l_margin + x_offset)
                if marker[0].isdigit():
                    self.pdf.set_font("DejaVu", "B", 8)
                    self.pdf.set_text_color(*COLOR_PRIMARY_TEAL)
                    self.pdf.cell(5, 4.2, f"{marker} ")
                else:
                    self.pdf.set_font("DejaVu", "", 8)
                    self.pdf.set_text_color(*COLOR_PRIMARY_TEAL)
                    self.pdf.cell(4, 4.2, "• ")

                self.pdf.set_font("DejaVu", "", 8)
                self.pdf.set_text_color(*COLOR_DARK_SLATE)
                self._render_inline_html_line(content)
                self.pdf.ln(1)
                continue

            # Standard paragraph
            self.pdf.set_font("DejaVu", "", 8)
            self.pdf.set_text_color(*COLOR_DARK_SLATE)
            self._render_inline_html_line(line)
            self.pdf.ln(1.2)

        if in_table:
            self._render_table_block(table_buffer)

    def _render_inline_html_line(self, raw_line: str):
        """Renders inline text with bold/italic HTML parsing without altering outer styling."""
        html = html_escape(raw_line)
        html = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", html)
        html = re.sub(r"\*(.*?)\*", r"<i>\1</i>", html)
        self.pdf.write_html(html)

    def _render_table_block(self, table_lines: List[str]):
        """Renders Markdown tables using standardized ODIS table styling."""
        rows: List[List[str]] = []
        for line in table_lines:
            if re.match(r"^\s*\|?\s*[:\-]+\s*\|\s*[:\-\s|]+$", line):
                continue
            cells = [c.strip() for c in line.split("|")]
            if cells and not cells[0]:
                cells.pop(0)
            if cells and not cells[-1]:
                cells.pop(-1)
            if cells:
                rows.append(cells)

        if not rows:
            return

        num_cols = len(rows[0])
        col_w = self.pdf.epw / max(1, num_cols)

        header_style = FontFace(
            emphasis=TextEmphasis.B,
            color=COLOR_PRIMARY_TEAL,
            fill_color=COLOR_TABLE_HEADER_BG,
            size_pt=7.5,
        )
        row_even_style = FontFace(
            color=COLOR_DARK_SLATE,
            fill_color=COLOR_WHITE,
            size_pt=7.5,
        )
        row_odd_style = FontFace(
            color=COLOR_DARK_SLATE,
            fill_color=COLOR_ALT_ROW,
            size_pt=7.5,
        )

        with self.pdf.table(
            col_widths=col_w,
            text_align="LEFT",
            borders_layout="HORIZONTAL_LINES",
            line_height=4.8,
            padding=1.2,
        ) as table:
            for i, row_data in enumerate(rows):
                style = header_style if i == 0 else (row_even_style if i % 2 == 0 else row_odd_style)
                row = table.row(style=style)
                for cell_text in row_data:
                    clean_cell = clean_markdown_text_for_pdf(cell_text)
                    row.cell(clean_cell)

        self.pdf.ln(2.5)


def _render_executive_summary_page(
    pdf: ODISReportPDF,
    search_results: SearchResultsData,
    config: SearchCriterias,
):
    """
    Renders Page 1: Single-page Executive Summary.
    Header, Search Criteria (2 columns card), and Results Podium Table.
    """
    pdf.add_page()

    # --- 1. Header Banner ---
    logo_path = os.path.join(cfg.ASSETS_DIR, "logo_jaccueille_pdf.jpg")
    if os.path.exists(logo_path):
        pdf.image(logo_path, x=12, y=8, w=30)

    pdf.set_xy(48, 8)
    pdf.set_font("DejaVu", "B", 13.5)
    pdf.set_text_color(*COLOR_PRIMARY_TEAL)
    pdf.cell(
        pdf.epw - 36,
        6,
        "SYNTHÈSE DE RECHERCHE TERRITORIALE",
        0,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="R",
    )
    pdf.set_xy(48, 14.5)
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(*COLOR_DARK_SLATE)
    pdf.cell(
        pdf.epw - 36,
        5,
        "Projet de vie et d'orientation de la personne accompagnée",
        0,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="R",
    )
    pdf.set_xy(48, 20)
    pdf.set_font("DejaVu", "I", 7.5)
    pdf.set_text_color(*COLOR_MUTED_GRAY)
    date_str = datetime.date.today().strftime("%d/%m/%Y")
    pdf.cell(
        pdf.epw - 36,
        4,
        f"Rapport d'aide à la décision OD&IS  |  Édité le {date_str}",
        0,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="R",
    )

    pdf.set_y(26)
    pdf.set_draw_color(*COLOR_PRIMARY_TEAL)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)

    # --- 2. Search Criteria Card (2 Columns) ---
    pdf.draw_section_title("1. Vos Critères de Recherche")

    # Extract criteria details safely
    metier_names = [
        c.label for sublist in getattr(config, "codes_metiers", []) for c in sublist
    ]
    metiers_str = ", ".join(metier_names) if metier_names else "Non spécifié"

    formation_names = [
        c.label for sublist in getattr(config, "codes_formations", []) for c in sublist
    ]
    formations_str = ", ".join(formation_names) if formation_names else "Non spécifié"

    type_logement_str = "Non spécifié"
    if getattr(config, "type_logement", None):
        type_logement_str = config.type_logement.label
    elif getattr(config, "logement", None):
        type_logement_str = config.logement

    target_pop = getattr(config, "target_population", None)
    target_sigma = getattr(config, "target_population_sigma", None)
    if target_pop:
        pop_str = f"{target_pop:,} hab."
        if target_sigma:
            pop_str += f" (± {target_sigma:,})"
        pop_str = pop_str.replace(",", " ")
    else:
        pop_str = "Non restreinte"

    besoin_sante = getattr(config, "besoin_sante", None)
    besoin_sante_str = (
        ", ".join(besoin_sante)
        if isinstance(besoin_sante, list)
        else (besoin_sante if besoin_sante else "Standard")
    )

    col1_items = [
        ("Lieu de départ", config.commune_actuelle.label if getattr(config, "commune_actuelle", None) else "N/A"),
        ("Zone recherchée", cfg.LOC_SEARCH_AREA_OPTIONS.get(getattr(config, "loc_search_area", ""), str(getattr(config, "loc_search_area", "France entière")))),
        ("Type de logement", type_logement_str),
        ("Population cible", pop_str),
    ]
    if getattr(config, "commune_pressentie", None):
        col1_items.insert(1, ("Ville pressentie", config.commune_pressentie.label))

    scolarite_str = (
        ", ".join(config.classe_enfants)
        if getattr(config, "classe_enfants", None)
        else "Sans enfants scolarisés"
    )
    famille_str = f"{getattr(config, 'nb_adultes', 1)} adulte(s)"
    if getattr(config, "nb_enfants", 0) > 0:
        famille_str += f", {config.nb_enfants} enfant(s) ({scolarite_str})"

    # Category weights
    weight_map = {
        "Emp.": getattr(config, "poids_emploi", 1.0),
        "Log.": getattr(config, "poids_logement", 1.0),
        "Santé": getattr(config, "poids_sante", 1.0),
        "Éduc.": getattr(config, "poids_education", 1.0),
        "Mobil.": getattr(config, "poids_mobilite", 1.0),
        "Inclus.": getattr(config, "poids_inclusion", 0.5),
    }
    weight_summary = ", ".join(
        [f"{k} {int(v * 100)}%" for k, v in weight_map.items() if v > 0]
    )

    col2_items = [
        ("Famille", famille_str),
        ("Métiers", metiers_str),
        ("Formations", formations_str),
        ("Santé", besoin_sante_str),
        ("Pondérations", weight_summary if weight_summary else "Équilibré"),
    ]

    # Draw 2-column criteria box
    card_y = pdf.get_y()
    card_w = pdf.epw
    card_h = 36

    pdf.set_fill_color(*COLOR_LIGHT_BG)
    pdf.set_draw_color(*COLOR_BORDER_LIGHT)
    pdf.set_line_width(0.2)
    pdf.rect(pdf.l_margin, card_y, card_w, card_h, style="FD")
    pdf.set_fill_color(*COLOR_PRIMARY_TEAL)
    pdf.rect(pdf.l_margin, card_y, 2, card_h, style="F")

    # Render Col 1 (width = 46%)
    col1_w = (card_w - 8) * 0.46
    col2_w = (card_w - 8) * 0.54

    pdf.set_xy(pdf.l_margin + 4, card_y + 2)
    for label, val in col1_items:
        pdf.set_font("DejaVu", "B", 7)
        pdf.set_text_color(*COLOR_PRIMARY_TEAL)
        pdf.cell(28, 5.5, f"{label} :", align="L")
        pdf.set_font("DejaVu", "", 7)
        pdf.set_text_color(*COLOR_DARK_SLATE)
        pdf.cell(col1_w - 28, 5.5, str(val)[:40], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(pdf.l_margin + 4)

    # Render Col 2 (width = 54%)
    pdf.set_xy(pdf.l_margin + col1_w + 6, card_y + 2)
    for label, val in col2_items:
        pdf.set_font("DejaVu", "B", 7)
        pdf.set_text_color(*COLOR_PRIMARY_TEAL)
        pdf.cell(22, 5.5, f"{label} :", align="L")
        pdf.set_font("DejaVu", "", 7)
        pdf.set_text_color(*COLOR_DARK_SLATE)
        pdf.cell(col2_w - 22, 5.5, str(val)[:58], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(pdf.l_margin + col1_w + 6)

    pdf.set_y(card_y + card_h + 3.5)

    # --- 3. Results Podium Table ---
    pdf.draw_section_title("2. Synthèse des Résultats (Podium & Adéquation)")

    table_rows: List[List[str]] = [
        [
            "Rang",
            "Commune",
            "Bassin de vie / Dép.",
            "Population",
            "Adéquation",
            "Emploi",
            "Logem.",
            "Santé",
            "Éduc.",
            "Mobil.",
        ]
    ]

    # Combine Top 5 + Commune Pressentie
    results_list = list(search_results.results[:5])
    if search_results.commune_pressentie:
        pressentie_codgeo = search_results.commune_pressentie.codgeo
        if not any(c.codgeo == pressentie_codgeo for c in results_list):
            results_list.append(search_results.commune_pressentie)

    for i, c in enumerate(results_list, start=1):
        is_pressentie = (
            search_results.commune_pressentie
            and c.codgeo == search_results.commune_pressentie.codgeo
        )
        rank_label = f"#{i}"
        if is_pressentie:
            rank_label = "📌 Pressentie" if i > 5 else f"#{i} (Press.)"

        pop_val = f"{c.population:,} hab.".replace(",", " ")
        score_val = f"{c.global_score * 100:.1f}%"
        emp_score = f"{c.employment.cat_score * 100:.0f}%"
        log_score = f"{c.housing.cat_score * 100:.0f}%"
        san_score = f"{c.health.cat_score * 100:.0f}%"
        edu_score = f"{c.education.cat_score * 100:.0f}%"
        mob_score = f"{c.mobility.cat_score * 100:.0f}%"

        table_rows.append(
            [
                rank_label,
                c.name,
                c.name_bdv or "N/A",
                pop_val,
                score_val,
                emp_score,
                log_score,
                san_score,
                edu_score,
                mob_score,
            ]
        )

    # Total = 186mm
    col_widths = (22, 28, 30, 24, 22, 12, 12, 12, 12, 12)

    header_style = FontFace(
        emphasis=TextEmphasis.B,
        color=COLOR_PRIMARY_TEAL,
        fill_color=COLOR_TABLE_HEADER_BG,
        size_pt=7,
    )
    row_even_style = FontFace(
        color=COLOR_DARK_SLATE,
        fill_color=COLOR_WHITE,
        size_pt=7,
    )
    row_odd_style = FontFace(
        color=COLOR_DARK_SLATE,
        fill_color=COLOR_ALT_ROW,
        size_pt=7,
    )

    with pdf.table(
        col_widths=col_widths,
        text_align="LEFT",
        borders_layout="HORIZONTAL_LINES",
        line_height=4.8,
        padding=1.2,
    ) as table:
        for r_idx, r_data in enumerate(table_rows):
            style = (
                header_style
                if r_idx == 0
                else (row_even_style if r_idx % 2 == 0 else row_odd_style)
            )
            row = table.row(style=style)
            for c_idx, cell_value in enumerate(r_data):
                if c_idx == 4 and r_idx > 0:
                    row.cell(cell_value, style=FontFace(emphasis=TextEmphasis.B, color=COLOR_PRIMARY_TEAL, size_pt=7))
                elif c_idx >= 5 and r_idx > 0:
                    row.cell(cell_value, align="CENTER")
                elif c_idx >= 5 and r_idx == 0:
                    row.cell(cell_value, align="CENTER")
                else:
                    row.cell(cell_value)

    pdf.ln(3)
    pdf.set_font("DejaVu", "I", 7)
    pdf.set_text_color(*COLOR_MUTED_GRAY)
    pdf.cell(
        pdf.epw,
        4,
        "Consultez les fiches détaillées ci-après pour les opportunités professionnelles, associatives et l'analyse IA.",
        align="C",
    )


def _render_commune_sheet(
    pdf: ODISReportPDF,
    commune: CommuneResult,
    rank_prefix: str,
    config: SearchCriterias,
):
    """
    Renders dedicated sheets for a specific commune.
    Identity banner, Pitch card, Indicators Table, Local Network (2-col), and Advanced AI Synthesis.
    """
    pdf.add_page()

    # --- 1. Identity Banner ---
    pop_str = f"{commune.population:,} hab.".replace(",", " ")
    bdv_str = f"Bassin de vie : {commune.name_bdv or 'N/A'}"
    score_pct = f"{commune.global_score * 100:.1f}%"
    score_besoins_val = getattr(commune, "score_besoins", commune.global_score) or 0.0
    pop_coeff_val = getattr(commune, "coeff_population_gauss", 1.0) or 1.0
    sub_score_str = f"Besoins : {score_besoins_val * 100:.1f}% | Démographie : {pop_coeff_val * 100:.0f}%"

    banner_y = pdf.get_y()
    banner_w = pdf.epw
    banner_h = 16

    pdf.set_fill_color(*COLOR_LIGHT_BG)
    pdf.set_draw_color(*COLOR_PRIMARY_TEAL)
    pdf.set_line_width(0.3)
    pdf.rect(pdf.l_margin, banner_y, banner_w, banner_h, style="FD")
    pdf.set_fill_color(*COLOR_PRIMARY_TEAL)
    pdf.rect(pdf.l_margin, banner_y, 3, banner_h, style="F")

    # City Title & Details
    pdf.set_xy(pdf.l_margin + 6, banner_y + 2)
    pdf.set_font("DejaVu", "B", 12)
    pdf.set_text_color(*COLOR_PRIMARY_TEAL)
    pdf.cell(110, 6, f"{rank_prefix} · {commune.name}", align="L")

    # Score Pill (Right side)
    pdf.set_xy(pdf.w - pdf.r_margin - 50, banner_y + 2)
    pdf.set_font("DejaVu", "B", 10)
    pdf.set_text_color(*COLOR_PRIMARY_TEAL)
    pdf.cell(46, 6, f"Adéquation : {score_pct}", align="R")

    # Subtitle
    pdf.set_xy(pdf.l_margin + 6, banner_y + 8)
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(*COLOR_MUTED_GRAY)
    pdf.cell(110, 5, f"{bdv_str}  ·  {pop_str}", align="L")

    pdf.set_xy(pdf.w - pdf.r_margin - 75, banner_y + 8)
    pdf.cell(71, 5, sub_score_str, align="R")

    pdf.set_y(banner_y + banner_h + 3)

    # --- 2. Pitch / Executive Highlights ---
    pitch = getattr(commune, "refiner_pitch", None)
    if pitch:
        pdf.draw_callout_box(
            text=pitch,
            title="POINTS SAILLANTS & ORIENTATION LOCALE",
            border_color=COLOR_PRIMARY_TEAL,
            bg_color=COLOR_LIGHT_BG,
            italic=True,
        )

    # --- 3. Detailed Indicators by Category ---
    pdf.draw_section_title("Indicateurs Thématiques Détaillés")

    cat_labels = {
        "emploi": "Emploi & Marché du travail",
        "logement": "Logement & Cadre de vie",
        "sante": "Santé & Soins de proximité",
        "education": "Éducation & Petite Enfance",
        "mobilite": "Mobilité & Transports",
        "inclusion": "Vie Sociale & Solidarité",
    }

    scores_dict: Dict[str, List[CommuneScoreDetail]] = getattr(commune, "scores", {}) or {}

    for cat_key, cat_name in cat_labels.items():
        cat_scores = scores_dict.get(cat_key, [])
        if not cat_scores:
            continue

        # Category small heading
        pdf.set_font("DejaVu", "B", 8.5)
        pdf.set_text_color(*COLOR_PRIMARY_TEAL)
        pdf.cell(pdf.epw, 5, f"▸ {cat_name}", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        table_rows = [["Indicateur clé", "Valeur locale", "Score"]]
        for s in sorted(cat_scores, key=lambda x: getattr(x, "score_normalise", 0.0), reverse=True):
            val_str = f"{s.valeur_kpi}" if s.valeur_kpi is not None else "N/A"
            if getattr(s, "unit", "") and s.unit != "None":
                val_str += f" {s.unit}"
            score_str = f"{s.score_normalise * 100:.1f}%"
            table_rows.append([s.label, val_str, score_str])

        header_style = FontFace(
            emphasis=TextEmphasis.B,
            color=COLOR_PRIMARY_TEAL,
            fill_color=COLOR_TABLE_HEADER_BG,
            size_pt=7.5,
        )
        row_even_style = FontFace(
            color=COLOR_DARK_SLATE,
            fill_color=COLOR_WHITE,
            size_pt=7.5,
        )
        row_odd_style = FontFace(
            color=COLOR_DARK_SLATE,
            fill_color=COLOR_ALT_ROW,
            size_pt=7.5,
        )

        with pdf.table(
            col_widths=(95, 55, 36),
            text_align="LEFT",
            borders_layout="HORIZONTAL_LINES",
            line_height=4.8,
            padding=1.2,
        ) as table:
            for r_idx, r_data in enumerate(table_rows):
                style = (
                    header_style
                    if r_idx == 0
                    else (row_even_style if r_idx % 2 == 0 else row_odd_style)
                )
                row = table.row(style=style)
                row.cell(r_data[0])
                row.cell(r_data[1])
                row.cell(r_data[2], align="RIGHT")

        pdf.ln(2.5)

    # --- 4. Local Network & Opportunities (Sequential flow) ---
    pdf.draw_section_title("Opportunités Professionnelles & Réseau Associatif")

    emp = getattr(commune, "employment", None)
    inc = getattr(commune, "inclusion", None)

    # 1. Métiers en tension
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_text_color(*COLOR_PRIMARY_TEAL)
    pdf.cell(pdf.epw, 4.5, "▸ Métiers les plus recherchés (Bassin de vie)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("DejaVu", "", 7.5)
    pdf.set_text_color(*COLOR_DARK_SLATE)
    top_profs = emp.top_professions if emp else []
    if top_profs:
        for p in top_profs[:4]:
            pdf.cell(pdf.epw, 4, f"  • {p}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(pdf.epw, 4, "  • Données de tension non disponibles", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1.5)

    # 2. Offres SIAE
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_text_color(*COLOR_PRIMARY_TEAL)
    pdf.cell(pdf.epw, 4.5, "▸ Offres d'emplois par les SIAE locales (Insertion Professionnelle)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("DejaVu", "", 7.5)
    pdf.set_text_color(*COLOR_DARK_SLATE)
    siae_summary = emp.inclusive_jobs_summary if emp else {}
    if siae_summary:
        for sector, count in sorted(siae_summary.items(), key=lambda x: x[1], reverse=True)[:4]:
            pdf.cell(pdf.epw, 4, f"  • {sector} : {count} offre(s)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(pdf.epw, 4, "  • Aucune offre inclusive active identifiée", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1.5)

    # 3. Associations Réfugiés & Inclusion
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_text_color(*COLOR_PRIMARY_TEAL)
    pdf.cell(pdf.epw, 4.5, "▸ Associations - Intégration des réfugiés", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("DejaVu", "", 7.5)
    pdf.set_text_color(*COLOR_DARK_SLATE)
    refugee_assos = inc.asso_refugee_list[:4] if inc else []
    if refugee_assos:
        for asso in refugee_assos:
            name = (asso.name or "Inconnu").strip()
            cat = (asso.waldec_label or "").strip()
            cat_str = f" ({cat})" if cat else ""
            desc = (asso.description or "").strip()
            desc_str = f" : {desc[:75]}..." if len(desc) > 75 else (f" : {desc}" if desc else "")
            pdf.cell(pdf.epw, 4, f"  • {name}{cat_str}{desc_str}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(pdf.epw, 4, "  • Aucune association spécifique identifiée", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1.5)

    # 4. Solidarité & Services Locaux
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_text_color(*COLOR_PRIMARY_TEAL)
    pdf.cell(pdf.epw, 4.5, "▸ Réseau solidarité & services locaux", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("DejaVu", "", 7.5)
    pdf.set_text_color(*COLOR_DARK_SLATE)
    inc_by_cat = inc.asso_inclusion_list_by_cat if inc else {}
    if inc_by_cat:
        rendered_count = 0
        for cat_name, assos in sorted(inc_by_cat.items()):
            for asso in assos[:2]:
                if rendered_count < 4:
                    name = (asso.name or "Inconnu").strip()
                    desc = (asso.description or "").strip()
                    desc_str = f" : {desc[:65]}..." if len(desc) > 65 else (f" : {desc}" if desc else "")
                    pdf.cell(pdf.epw, 4, f"  • {name} ({cat_name}){desc_str}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                    rendered_count += 1
    else:
        pdf.cell(pdf.epw, 4, "  • Aucun réseau spécifique répertorié", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    # --- 5. Advanced AI Synthesis (If present) ---
    synthesis_messages = getattr(commune, "odis_synthesis", None)
    if synthesis_messages:
        pdf.draw_section_title(
            "Analyse Avancée OD&IS (Agents Experts IA)",
            "Avis synthétique et pistes d'action générés pour le projet de mobilité.",
        )

        renderer = MarkdownReportRenderer(pdf)
        for msg in synthesis_messages:
            role = msg.get("role", "assistant")
            content = msg.get("content", "")
            if not content:
                continue

            if role == "user":
                pdf.draw_callout_box(
                    text=content,
                    title="QUESTION DU TRAVAILLEUR SOCIAL :",
                    border_color=COLOR_MUTED_GRAY,
                    bg_color=COLOR_ALT_ROW,
                )
            else:
                renderer.render(content)
                pdf.ln(3)


def generate_pdf_report(
    search_results: SearchResultsData,
    config: SearchCriterias,
    active_search_hash: Optional[str] = None,
    processed_gdf: Optional[Any] = None,
    generation_warnings: Optional[List[str]] = None,
) -> bytes:
    """
    Generates a clean, compact, professional PDF report for OD&IS territorial matching.
    Decoupled from Streamlit session state and rasterized image dependencies.
    """
    search_ref = active_search_hash or (search_results.search_hash if search_results else "")
    pdf = ODISReportPDF(search_ref=search_ref)
    pdf.alias_nb_pages()

    # --- Page 1: Executive Summary & Search Criteria ---
    _render_executive_summary_page(pdf, search_results, config)

    # --- Pages 2+: Individual Commune Sheets ---
    pages_to_render: List[Tuple[str, CommuneResult]] = []
    for rank, commune in enumerate(search_results.results[:5], start=1):
        pages_to_render.append((f"Top {rank}", commune))

    if search_results.commune_pressentie:
        p_codgeo = search_results.commune_pressentie.codgeo
        if not any(c.codgeo == p_codgeo for c in search_results.results[:5]):
            pages_to_render.append(("Ville Pressentie", search_results.commune_pressentie))

    for rank_prefix, commune in pages_to_render:
        _render_commune_sheet(pdf, commune, rank_prefix, config)

    return bytes(pdf.output())

