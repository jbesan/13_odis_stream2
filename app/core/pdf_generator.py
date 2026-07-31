import io
from fpdf import FPDF
from fpdf.fonts import FontFace
from fpdf.enums import TextEmphasis, XPos, YPos

import os
import config as cfg
from ui import components as ui
import streamlit as st
from core import maps
from core.models import SearchCriterias, SearchResultsData
import logging
from typing import List, Optional
import matplotlib.pyplot as plt
import contextily as ctx
import geopandas as gpd
import re

# Basic constants
PDF_TITLE = "Synthèse de votre recherche de territoire"


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
        pdf.set_font("DejaVu", size=12)
    except Exception as e:
        logging.warning(
            f"--- WARNING: Could not load local Unicode font. Falling back to Helvetica. Error: {e} ---"
        )
        logging.warning(
            "--- Please ensure you have downloaded the font files as per the instructions. ---"
        )
        # Fallback to Helvetica if font setup fails
        pdf.set_font("Helvetica", size=12)


def _render_markdown_as_blocks(pdf: FPDF, text: str):
    """
    Renders markdown text to PDF by splitting it into blocks (Text vs Table).
    Uses native fpdf2 tables for markdown tables to avoid HTML nesting issues.
    """
    if not text:
        return

    # Normalize newlines
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")

    table_buffer = []
    in_table = False

    for line in lines:
        is_table_line = "|" in line
        if is_table_line and not in_table:
            in_table = True
            table_buffer = [line]
        elif in_table:
            if is_table_line:
                table_buffer.append(line)
            else:
                in_table = False
                _render_table_block(pdf, table_buffer)
                table_buffer = []
                _render_text_block(pdf, line)
        else:
            _render_text_block(pdf, line)

    if in_table:
        _render_table_block(pdf, table_buffer)


def _render_text_block(pdf: FPDF, line: str):
    """Renders a single line of markdown text as HTML."""
    if not line.strip():
        pdf.ln(2)
        return
    html = html_escape(line)
    html = re.sub(r"###\s*(.*)", r"<h3>\1</h3>", html)
    html = re.sub(r"##\s*(.*)", r"<h2>\1</h2>", html)
    html = re.sub(r"#\s*(.*)", r"<h1>\1</h1>", html)
    html = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", html)
    html = re.sub(r"\*(.*?)\*", r"<i>\1</i>", html)
    html = re.sub(r"^\s*[-*+]\s+", "• ", html)
    pdf.write_html(html)
    pdf.ln(1)


def _render_table_block(pdf: FPDF, table_lines: List[str]):
    """Renders a markdown table using native fpdf2 Table API."""
    rows = []
    for line in table_lines:
        if re.match(r"^\s*\|?\s*[:\-]+\s*\|\s*[:\-\s|]+$", line):
            continue
        cells = [c.strip() for c in line.split("|")]
        if not cells[0]:
            cells.pop(0)
        if cells and not cells[-1]:
            cells.pop(-1)
        if cells:
            rows.append(cells)
    if not rows:
        return
    with pdf.table(col_widths=None, borders_layout="ALL", line_height=6) as table:
        for i, row_data in enumerate(rows):
            row = table.row()
            for cell_text in row_data:
                if i == 0:
                    pdf.set_font("DejaVu", "B", 8)
                else:
                    pdf.set_font("DejaVu", "", 8)
                row.cell(cell_text)
    pdf.ln(2)


def html_escape(text: str) -> str:
    """Safely escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _generate_static_map_image(
    search_results: SearchResultsData, processed_gdf: Optional[gpd.GeoDataFrame] = None
) -> bytes:
    """
    Generates a static map image using Matplotlib and Contextily.
    Leverages passed processed_gdf for performance and consistency.
    """
    if not search_results.results:
        return b""

    # 1. Get scored GeoDataFrame
    gdf = processed_gdf
    if gdf is None or gdf.empty:
        logging.warning(
            "⚠️ [PDF-MAP] processed_gdf is missing or empty. Map will be skipped."
        )
        return b""

    # Use native 4326
    inferred_crs = "EPSG:4326"

    # 2. Extract/Hydrate geometries for plotting
    gdf = gdf.copy()
    gdf["geometry"] = gdf.apply(lambda row: maps._get_geom(row, "polygon"), axis=1)
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=inferred_crs)

    # 3. Handle Current Location Geometry
    current_df = None
    if search_results.current_geo:
        poly = maps._get_geom(search_results.current_geo, "polygon")
        if poly:
            current_df = gpd.GeoDataFrame(
                [
                    {
                        "codgeo": search_results.current_geo.codgeo,
                        "libgeo": search_results.current_geo.name,
                        "weighted_score": search_results.current_geo.global_score,
                        "geometry": poly,
                    }
                ],
                crs=inferred_crs,
            )

    # Project to Web Mercator for Contextily
    gdf_results_plot = gdf.to_crs(epsg=3857)

    # Initialize figure
    fig, ax = plt.subplots(figsize=(8, 8))

    # Plot results (choropleth)
    gdf_results_plot.plot(
        column="weighted_score",
        cmap="YlGn",
        alpha=0.6,
        edgecolor="grey",
        linewidth=0.5,
        ax=ax,
        legend=True,
        vmin=0.0,
        vmax=1.0,
        legend_kwds={
            "label": "Indice global (0–100)",
            "orientation": "horizontal",
            "shrink": 0.5,
            "pad": 0.05,
        },
    )

    # Highlight Current Location (Blue dashed outline + light fill)
    if current_df is not None:
        gdf_curr_plot = current_df.to_crs(epsg=3857)
        gdf_curr_plot.plot(
            ax=ax,
            facecolor="#1f77b4",
            alpha=0.3,
            edgecolor="#1f77b4",
            linewidth=3,
            linestyle="--",
        )

    # Plot outlines for top results (Red)
    top_5 = gdf_results_plot.head(5)
    # We use codgeos from search_results to ensure consistency with the list
    top_codgeos = [c.codgeo for c in search_results.results[:5]]
    top_5_gdf = gdf_results_plot[gdf_results_plot.index.isin(top_codgeos)]

    top_5_gdf.plot(ax=ax, facecolor="none", edgecolor="red", linewidth=2)

    # Highlight Commune Pressentie if present (Yellow/Gold outline)
    if search_results.commune_pressentie:
        p_codgeo = search_results.commune_pressentie.codgeo
        if p_codgeo in gdf_results_plot.index:
            p_gdf = gdf_results_plot[gdf_results_plot.index == p_codgeo]
            p_gdf.plot(
                ax=ax,
                facecolor="none",
                edgecolor="#e0a800",  # Distinct gold/yellow color
                linewidth=3,
            )

            row = gdf_results_plot.loc[p_codgeo]
            centroid = row.geometry.centroid
            ax.annotate(
                "📌",
                xy=(centroid.x, centroid.y),
                xytext=(0, 0),
                textcoords="offset points",
                ha="center",
                va="center",
                fontsize=10,
                bbox=dict(boxstyle="circle,pad=0.2", fc="#e0a800", ec="none"),
            )

    # Add numbered markers for top results
    for i, codgeo in enumerate(top_codgeos):
        if codgeo in gdf_results_plot.index:
            row = gdf_results_plot.loc[codgeo]
            rank = i + 1
            centroid = row.geometry.centroid
            ax.annotate(
                str(rank),
                xy=(centroid.x, centroid.y),
                xytext=(0, 0),
                textcoords="offset points",
                ha="center",
                va="center",
                color="white",
                weight="bold",
                fontsize=10,
                bbox=dict(boxstyle="circle,pad=0.3", fc="#D63E2A", ec="none"),
            )

    # Add basemap
    try:
        ctx.add_basemap(ax, source=ctx.providers.CartoDB.Positron)
    except Exception as e:
        logging.error(f"Error adding basemap: {e}")

    # Remove axes
    ax.set_axis_off()

    # Save to buffer
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def generate_pdf_report(
    search_results: SearchResultsData,
    config: SearchCriterias,
    active_search_hash: Optional[str] = None,
    processed_gdf: Optional[gpd.GeoDataFrame] = None,
) -> bytes:
    """
    Generates a PDF report with the top 5 results and search criteria using a Unicode font.
    Decoupled from st.session_state for better testability and clean architecture.
    """
    pdf = FPDF()
    _setup_unicode_font(pdf)
    pdf.add_page()

    # --- PAGE 1: HEADER & CRITERIA ---
    # Header
    logo_path = os.path.join(cfg.ASSETS_DIR, "logo_jaccueille_pdf.jpg")
    if os.path.exists(logo_path):
        pdf.image(logo_path, x=10, y=8, w=40)
    pdf.ln(50)  # Add space for the logo
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(pdf.epw, 10, PDF_TITLE, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    # Subtitle with beneficiary's name
    subtitle = f"Pour le projet de vie {ui.get_person_accompanied_str()}"
    pdf.set_font("DejaVu", "", 12)
    pdf.cell(pdf.epw, 10, subtitle, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(10)

    # --- Search Criteria ---
    pdf.set_font("DejaVu", "B", 12)
    pdf.cell(
        pdf.epw,
        10,
        "Vos critères de recherche",
        0,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="L",
    )
    pdf.ln(2)

    if config:
        metier_names = [c.label for sublist in config.codes_metiers for c in sublist]
        metiers_str = ", ".join(metier_names) if metier_names else "Non spécifié"

        formation_names = [
            c.label for sublist in config.codes_formations for c in sublist
        ]
        formations_str = (
            ", ".join(formation_names) if formation_names else "Non spécifié"
        )

        # Dynamically build the full criteria list
        criteria = {
            "Lieu de départ": config.commune_actuelle.label
            if config.commune_actuelle
            else "N/A",
        }
        if getattr(config, "commune_pressentie", None):
            criteria["Commune pressentie"] = config.commune_pressentie.label

        criteria.update(
            {
                "Zone de recherche": cfg.LOC_SEARCH_AREA_OPTIONS.get(
                    config.loc_search_area, str(config.loc_search_area)
                ),
                "Métiers recherchés": metiers_str,
                "Formations recherchées": formations_str,
                "Nb. adultes": config.nb_adultes,
                "Nb. enfants": config.nb_enfants,
                "Niveaux scolaires": ", ".join(config.classe_enfants)
                if config.classe_enfants
                else "N/A",
                "Type de logement": config.type_logement.label
                if config.type_logement
                else (config.logement if config.logement else "N/A"),
                "Besoin de santé": config.besoin_sante,
                "Population cible": f"{config.target_population:,} hab. (+/- {config.target_population_sigma:,})".replace(
                    ",", " "
                ),
                "Fréquence retour": config.freq_retour if config.freq_retour else "N/A",
                "Autres besoins": ", ".join(
                    [c.label for c in config.inc_services_selection]
                )
                if config.inc_services_selection
                else "Aucun",
            }
        )

        # Add Associations Locales
        if config.inc_asso_add_selection:
            criteria["Associations Locales"] = ", ".join(
                [c.label for c in config.inc_asso_add_selection]
            )

        # Add Qualitative Notes
        if config.notes_qualitatives:
            criteria["Notes qualitatives"] = (
                ", ".join(config.notes_qualitatives)
                if isinstance(config.notes_qualitatives, list)
                else config.notes_qualitatives
            )

        # Profile & Weights (At the bottom)
        is_expert = st.session_state.get("ui_expert_weights", False)
        profile_name = (
            "Personnalisé"
            if is_expert
            else (config.weight_profile if config.weight_profile else "Équilibré")
        )
        criteria["Profil de poids"] = profile_name

        # Detail of category weights
        weight_details = []
        weight_map = {
            "Emploi": config.poids_emploi,
            "Logement": config.poids_logement,
            "Éducation": config.poids_education,
            "Santé": config.poids_sante,
            "Inclusion": config.poids_inclusion,
            "Mobilité": config.poids_mobilite,
        }
        for label, val in weight_map.items():
            if val > 0:
                # Convert back to percentage (e.g. 0.5 -> 50%)
                weight_details.append(f"{label}: {int(val * 100)}%")

        if weight_details:
            criteria["Détails des poids"] = ", ".join(weight_details)

        table_data = [[key, str(value)] for key, value in criteria.items() if value]

        if table_data:
            pdf.set_font("DejaVu", "", 9)  # Set base font for the table
            bold_style = FontFace(emphasis=TextEmphasis.B)
            with pdf.table(
                col_widths=(50, 130),
                text_align="LEFT",
                borders_layout="NONE",
                width=180,
            ) as table:
                for data_row in table_data:
                    row = table.row()
                    row.cell(f"{data_row[0]}:", style=bold_style)
                    row.cell(data_row[1])
    pdf.ln(5)

    # --- PAGE 2: MAP & SUMMARY ---
    pdf.add_page()

    # Page 2 Title
    pdf.set_font("DejaVu", "B", 14)
    pdf.cell(
        pdf.epw,
        10,
        "Résultats de la recherche",
        0,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="C",
    )
    pdf.ln(5)

    # Map Generation
    try:
        map_png = _generate_static_map_image(search_results, processed_gdf)
        if map_png:
            map_image_stream = io.BytesIO(map_png)
            # Center the image and limit width to avoid it being too big
            target_width = 150
            x_pos = (pdf.w - target_width) / 2
            pdf.image(map_image_stream, x=x_pos, w=target_width)
    except Exception as e:
        pdf.set_font("DejaVu", "I", 8)
        pdf.multi_cell(0, 6, f"Erreur lors de la generation de la carte: {e}")
    pdf.ln(5)

    # Top 5 Summary
    pdf.set_font("DejaVu", "B", 12)
    pdf.cell(
        pdf.epw,
        10,
        "Top 5 des résultats",
        0,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="L",
    )
    pdf.set_font("DejaVu", "", 9)
    for rank, commune in enumerate(search_results.results, start=1):
        score_percent = f"{commune.global_score * 100:.1f}/100"
        pdf.cell(
            pdf.epw,
            5,
            f"  {rank}. {commune.name} - {score_percent}",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    if search_results.commune_pressentie:
        p_commune = search_results.commune_pressentie
        score_percent = f"{p_commune.global_score * 100:.1f}/100"
        pdf.ln(2)
        pdf.set_font("DejaVu", "B", 9)
        pdf.cell(
            pdf.epw,
            5,
            f"  📌 Ville pressentie : {p_commune.name} - {score_percent}",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font("DejaVu", "", 9)

    pdf.ln(5)

    # --- INDIVIDUAL RESULT PAGES ---
    pages_to_render = []
    for rank, commune in enumerate(search_results.results, start=1):
        pages_to_render.append((f"Top {rank}", commune))
    if search_results.commune_pressentie:
        pages_to_render.append(
            ("📌 Ville Pressentie", search_results.commune_pressentie)
        )

    for prefix, commune in pages_to_render:
        pdf.add_page()

        # --- Header (Identity) ---
        population = f"{commune.population:,}".replace(",", " ")
        title = f"{prefix} | {commune.name} ({population} hab.)"
        pdf.set_font("DejaVu", "B", 14)
        pdf.cell(pdf.epw, 8, title, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font("DejaVu", "I", 10)
        bdv_text = f"Fait partie du bassin de vie de : {commune.name_bdv}"
        pdf.cell(pdf.epw, 6, bdv_text, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        score_percent = f"Indice global : {commune.global_score * 100:.1f}/100"
        pdf.set_font("DejaVu", "B", 10)
        pdf.cell(pdf.epw, 6, score_percent, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

        # --- Pitch (AI content as priority) ---
        pitch = commune.refiner_pitch
        if not pitch:
            # Fallback to simple pitch logic if refiner_pitch is missing
            pitch = f"{commune.name} se distingue particulièrement sur vos critères prioritaires."

        pdf.set_font("DejaVu", "", 10)
        _render_markdown_as_blocks(pdf, pitch)
        pdf.ln(5)

        # Radar Chart with Comparison
        try:
            # 1. Target Data
            categories = [
                "Emploi",
                "Logement",
                "Education",
                "Sante",
                "Inclusion",
                "Mobilite",
            ]
            raw_values = [
                commune.employment.cat_score * 100,
                commune.housing.cat_score * 100,
                commune.education.cat_score * 100,
                commune.health.cat_score * 100,
                commune.inclusion.cat_score * 100,
                commune.mobility.cat_score * 100,
            ]

            # filter out inactive categories based on config
            active_cats = (
                config.active_categories
                if config and hasattr(config, "active_categories")
                else []
            )
            if active_cats:
                cat_map = {
                    "emploi": "Emploi",
                    "logement": "Logement",
                    "education": "Education",
                    "sante": "Sante",
                    "inclusion": "Inclusion",
                    "mobilite": "Mobilite",
                }
                filtered_cats = []
                filtered_vals = []
                for i, cat in enumerate(
                    [
                        "emploi",
                        "logement",
                        "education",
                        "sante",
                        "inclusion",
                        "mobilite",
                    ]
                ):
                    if cat in active_cats:
                        filtered_cats.append(cat_map.get(cat, cat.capitalize()))
                        filtered_vals.append(raw_values[i])
                categories = filtered_cats
                values = filtered_vals
            else:
                values = raw_values

            # 2. Current City Data
            has_comparison = False
            values_current = []
            if search_results.current_geo:
                current_c = search_results.current_geo
                raw_values_cur = [
                    current_c.employment.cat_score * 100,
                    current_c.housing.cat_score * 100,
                    current_c.education.cat_score * 100,
                    current_c.health.cat_score * 100,
                    current_c.inclusion.cat_score * 100,
                    current_c.mobility.cat_score * 100,
                ]
                if active_cats:
                    filtered_vals_cur = []
                    for i, cat in enumerate(
                        [
                            "emploi",
                            "logement",
                            "education",
                            "sante",
                            "inclusion",
                            "mobilite",
                        ]
                    ):
                        if cat in active_cats:
                            filtered_vals_cur.append(raw_values_cur[i])
                    values_current = filtered_vals_cur
                else:
                    values_current = raw_values_cur
                has_comparison = True

            N = len(categories)
            if N > 0:
                # Compute angles
                angles = [n / float(N) * 2 * 3.14159 for n in range(N)]
                angles += angles[:1]  # Close the loop

                fig, ax = plt.subplots(figsize=(4, 4), subplot_kw=dict(polar=True))

                # Draw one axe per variable + add labels
                plt.xticks(angles[:-1], categories, size=8)

                # Draw ylabels
                ax.set_rlabel_position(0)  # type: ignore
                plt.yticks([25, 50, 75], ["25", "50", "75"], color="grey", size=7)
                plt.ylim(0, 100)

                # Plot target city (Green)
                vals_target = values + values[:1]
                ax.plot(
                    angles,
                    vals_target,
                    linewidth=2,
                    linestyle="solid",
                    color="#006268",
                    label=commune.name,
                )
                ax.fill(angles, vals_target, "#006268", alpha=0.3)

                # Plot current city (Blue)
                if has_comparison:
                    vals_current = values_current + values_current[:1]
                    label_cur = (
                        config.commune_actuelle.label
                        if config.commune_actuelle
                        else "Actuel"
                    )
                    ax.plot(
                        angles,
                        vals_current,
                        linewidth=2,
                        linestyle="solid",
                        color="#1f77b4",
                        label=f"Actuel ({label_cur})",
                    )
                    ax.fill(angles, vals_current, "#1f77b4", alpha=0.2)
                    plt.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=8)

                # Save to buffer
                buf = io.BytesIO()
                plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
                plt.close(fig)
                buf.seek(0)

                # Embed in PDF (Centered)
                chart_w = 100
                pdf.image(buf, x=(pdf.w - chart_w) / 2, w=chart_w)

                if has_comparison:
                    pdf.set_font("DejaVu", "I", 8)
                    label_cur = (
                        search_results.current_geo.name
                        if search_results.current_geo
                        else "votre commune"
                    )
                    pdf.cell(
                        pdf.epw,
                        5,
                        f"Comparaison entre {commune.name} (vert) et {label_cur} (bleu).",
                        0,
                        new_x=XPos.LMARGIN,
                        new_y=YPos.NEXT,
                        align="C",
                    )
                    pdf.ln(2)

        except Exception as e:
            pdf.set_font("DejaVu", "I", 8)
            pdf.multi_cell(0, 6, f"Erreur lors de la generation du graphique: {e}")
        pdf.ln(5)

        # --- Detailed Indicator Tables (Loop per category) ---
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(
            pdf.epw,
            10,
            "Indicateurs détaillés par catégorie",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.ln(2)

        cat_labels = {
            "emploi": "Emploi & Formation",
            "logement": "Logement",
            "education": "Éducation",
            "sante": "Santé",
            "inclusion": "Vie Sociale & Inclusion",
            "mobilite": "Mobilité",
        }

        for cat_key, cat_name in cat_labels.items():
            scores_list = commune.scores.get(cat_key, [])
            if not scores_list:
                continue

            pdf.set_font("DejaVu", "B", 10)
            pdf.cell(pdf.epw, 8, cat_name, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            # Category Table
            pdf.set_font("DejaVu", "", 8)
            with pdf.table(
                col_widths=(80, 60, 40),
                text_align="LEFT",
                borders_layout="SINGLE_TOP_LINE",
                width=180,
            ) as table:
                header = table.row()
                header.cell("Indicateur")
                header.cell("Données")
                header.cell("Score")

                for s in sorted(
                    scores_list, key=lambda x: x.score_normalise, reverse=True
                ):
                    row = table.row()
                    row.cell(s.label)
                    val_str = f"{s.valeur_kpi}" if s.valeur_kpi is not None else "N/A"
                    if s.unit and s.unit != "None":
                        val_str += f" {s.unit}"
                    row.cell(val_str)
                    row.cell(f"{s.score_normalise * 100:.1f}%")
            pdf.ln(4)

        # --- Focus Opportunités & Vie Sociale ---
        pdf.add_page()
        pdf.set_font("DejaVu", "B", 12)
        pdf.cell(
            pdf.epw,
            10,
            "Focus Opportunités & Vie Sociale",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.ln(2)

        # 1. Employment Details
        pdf.set_font("DejaVu", "B", 10)
        pdf.cell(
            pdf.epw,
            8,
            "Métiers les plus recherchés (Bassin de vie)",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font("DejaVu", "", 9)
        top_professions = commune.employment.top_professions
        if top_professions:
            pdf.multi_cell(pdf.epw, 5, "\n".join([f"• {m}" for m in top_professions]))
        else:
            pdf.multi_cell(
                pdf.epw, 5, "Aucune donnée de tension spécifique disponible."
            )
        pdf.ln(3)

        pdf.set_font("DejaVu", "B", 10)
        pdf.cell(
            pdf.epw,
            8,
            "Offres d'emplois par les SIAE locales",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font("DejaVu", "", 9)
        inclusive_summary = commune.employment.inclusive_jobs_summary
        if inclusive_summary:
            items = [
                f"• {cat}: {count} offre(s)"
                for cat, count in sorted(
                    inclusive_summary.items(), key=lambda x: x[1], reverse=True
                )
            ]
            pdf.multi_cell(pdf.epw, 5, "\n".join(items))
        else:
            pdf.multi_cell(pdf.epw, 5, "Aucune offre inclusive active identifiée.")
        pdf.ln(3)

        # 2. Association Details
        pdf.set_font("DejaVu", "B", 10)
        pdf.cell(
            pdf.epw,
            8,
            "Associations - Intégration des réfugiés (Top 10)",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font("DejaVu", "", 8)
        refugee_assos = commune.inclusion.asso_refugee_list[:10]
        if refugee_assos:
            for asso in refugee_assos:
                name = html_escape(asso.name or "Inconnu")
                cat = html_escape(asso.waldec_label or "")
                cat_str = f" ({cat})" if cat else ""
                desc = html_escape((asso.description or "").strip())
                line = f"• <b>{name}</b>{cat_str}"
                if desc:
                    line += f": {desc}"
                pdf.write_html(line)
                pdf.ln(1)
        else:
            pdf.multi_cell(pdf.epw, 5, "Aucune association spécifique identifiée.")
        pdf.ln(3)

        pdf.set_font("DejaVu", "B", 10)
        pdf.cell(
            pdf.epw,
            8,
            "Réseau inclusion & solidarité (Top 5 par catégorie)",
            0,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font("DejaVu", "", 8)
        inclusion_by_cat = commune.inclusion.asso_inclusion_list_by_cat
        if inclusion_by_cat:
            for cat, assos in sorted(inclusion_by_cat.items()):
                pdf.set_font("DejaVu", "B", 9)
                pdf.cell(pdf.epw, 6, cat, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_font("DejaVu", "", 8)
                for asso in assos[:5]:
                    name = html_escape(asso.name or "Inconnu")
                    desc = html_escape((asso.description or "").strip())
                    line = f"• <b>{name}</b>"
                    if desc:
                        line += f": {desc}"
                    pdf.write_html(line)
                    pdf.ln(1)
                pdf.ln(2)
        else:
            pdf.multi_cell(pdf.epw, 5, "Aucun réseau détaillé répertorié.")
        pdf.ln(5)

        # --- Synthesis ---
        if commune.odis_synthesis:
            pdf.set_font("DejaVu", "B", 12)
            pdf.cell(
                pdf.epw,
                10,
                "Synthèse de l'analyse OD&IS",
                0,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.set_font("DejaVu", "", 9)

            for msg in commune.odis_synthesis:
                role = "OD&IS" if msg.get("role") == "assistant" else "Projet"
                content = msg.get("content", "")

                pdf.set_font("DejaVu", "B", 10)
                pdf.cell(pdf.epw, 7, role, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_font("DejaVu", "", 9)
                _render_markdown_as_blocks(pdf, content)
                pdf.ln(5)

    return bytes(pdf.output())
