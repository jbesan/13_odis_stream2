import io
import pandas as pd
from fpdf import FPDF
from fpdf.fonts import FontFace
from fpdf.enums import TextEmphasis, XPos, YPos

import tempfile
import os
import config as cfg
from ui import components as ui
from core.models import ScoringConfig
import base64
import logging
from typing import Dict, Any, List, Optional
import matplotlib.pyplot as plt
import contextily as ctx
import geopandas as gpd

# Basic constants
PDF_TITLE = "Synthèse de votre recherche de territoire"


def _setup_unicode_font(pdf: FPDF) -> None:
    """Adds the local Unicode DejaVu fonts to the FPDF instance."""
    font_dir = os.path.join(cfg.ASSETS_DIR, "fonts")
    try:
        pdf.add_font("DejaVu", "", os.path.join(font_dir, "DejaVuSans.ttf"))
        pdf.add_font("DejaVu", "B", os.path.join(font_dir, "DejaVuSans-Bold.ttf"))
        pdf.add_font("DejaVu", "I", os.path.join(font_dir, "DejaVuSans-Oblique.ttf"))
        pdf.add_font("DejaVu", "BI", os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf"))
        pdf.set_font("DejaVu", size=12)
    except Exception as e:
        logging.warning(f"--- WARNING: Could not load local Unicode font. Falling back to Arial. Error: {e} ---")
        logging.warning("--- Please ensure you have downloaded the font files as per the instructions. ---")
        # Fallback to Arial if font setup fails
        pdf.set_font("Arial", size=12)


def _generate_static_map_image(results_df: pd.DataFrame) -> bytes:
    """
    Generates a static map image using Matplotlib and Contextily.
    Highlights the top 5 results.
    """
    if results_df.empty:
        return b""

    # Project to Web Mercator for Contextily
    gdf_plot = results_df.to_crs(epsg=3857)
    
    # Initialize figure
    # Use a square aspect ratio or slightly landscape
    fig, ax = plt.subplots(figsize=(8, 8))
    
    # Plot all scores (choropleth)
    gdf_plot.plot(
        column='weighted_score',
        cmap='YlGn',
        alpha=0.6,
        edgecolor='grey',
        linewidth=0.5,
        ax=ax,
        legend=True,
        legend_kwds={'label': "Score Global", 'orientation': "horizontal", 'shrink': 0.5, 'pad': 0.05}
    )
    
    # Highlight Top 5 results
    top_5 = gdf_plot.head(5)
    
    # Plot outlines for Top 5
    top_5.plot(
        ax=ax,
        facecolor='none',
        edgecolor='red',
        linewidth=2
    )
    
    # Add numbered markers for Top 5
    # Get the name of the geometry column (it might be 'geometry', 'polygon', etc.)
    geom_col = gdf_plot.geometry.name
    
    for idx, row in top_5.iterrows():
        # Find the rank (0-based index in the dataframe)
        rank = results_df.index.get_loc(idx) + 1
        
        # Access geometry using the column name
        centroid = row[geom_col].centroid
        ax.annotate(
            str(rank),
            xy=(centroid.x, centroid.y),
            xytext=(0, 0),
            textcoords="offset points",
            ha='center',
            va='center',
            color='white',
            weight='bold',
            fontsize=10,
            bbox=dict(boxstyle="circle,pad=0.3", fc="#D63E2A", ec="none")
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
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def generate_pdf_report(st_session_state: Dict[str, Any], results_df: pd.DataFrame, folium_map: Any = None) -> bytes:
    """
    Generates a PDF report with the top 5 results and search criteria using a Unicode font.
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
    pdf.set_font("DejaVu", 'B', 16)
    pdf.cell(0, 10, PDF_TITLE, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

    # Subtitle with beneficiary's name
    subtitle = f"Pour le projet de vie {ui.get_person_accompanied_str()}"
    pdf.set_font("DejaVu", '', 12)
    pdf.cell(0, 10, subtitle, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(10)

    # --- Search Criteria ---
    pdf.set_font("DejaVu", 'B', 12)
    pdf.cell(0, 10, "Vos critères de recherche", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
    pdf.ln(2)
    
    config = st_session_state.get('config')
    if config:
        # --- Correctly look up names for Jobs & Formations ---
        app_data = st_session_state.get('app_data', {})
        metiers_df = app_data.get('rome_index')

        formations_df = app_data.get('codformations_index')

        # Get job names from codes
        selected_metier_codes = [code for sublist in config.codes_metiers for code in sublist]
        if selected_metier_codes and metiers_df is not None:
            # metiers_df is already indexed by code (from data_loader)
            valid_codes = [c for c in selected_metier_codes if c in metiers_df.index]
            metier_names = metiers_df.loc[valid_codes, 'label'].tolist()
            metiers_str = ", ".join(metier_names)
        else:
            metiers_str = "Non spécifié"

        # Get formation names from codes
        selected_formation_codes = [code for sublist in config.codes_formations for code in sublist]
        if selected_formation_codes and formations_df is not None:
            # formations_df is indexed by code (from data_loader)
            valid_codes = [c for c in selected_formation_codes if c in formations_df.index]
            formation_names = formations_df.loc[valid_codes, 'label'].tolist()
            formations_str = ", ".join(formation_names)
        else:
            formations_str = "Non spécifié"

        # Dynamically build the full criteria list
        criteria = {
            "Lieu de départ": st_session_state.get('ui_commune'),
            "Zone de recherche": cfg.LOC_SEARCH_AREA_OPTIONS.get(config.loc_search_area,
                                                               str(config.loc_search_area)),
            "Métiers recherchés": metiers_str,
            "Formations recherchées": formations_str,
            "Nb. adultes": config.nb_adultes,
            "Nb. enfants": config.nb_enfants,
            "Niveaux scolaires": ", ".join(config.classe_enfants) if config.classe_enfants else "N/A",
            "Hébergement": config.hebergement,
            "Logement à long terme": config.logement,
            "Besoin de santé": config.besoin_sante,
            "Autres besoins": (lambda: 
                ", ".join([
                    {v: k for k, v in st_session_state.get('ui_inc_services_add_selection_map', {}).items()}.get(slug, slug)
                    for slug in config.inc_services_add_selection
                ]) if config.inc_services_add_selection else "Aucun"
            )(),
        }
        
        table_data = [[key, str(value)] for key, value in criteria.items() if value]

        if table_data:
            pdf.set_font("DejaVu", '', 9)  # Set base font for the table
            bold_style = FontFace(emphasis=TextEmphasis.B)
            with pdf.table(
                col_widths=(50, 130),
                text_align="LEFT",
                borders_layout="NONE"
            ) as table:
                for data_row in table_data:
                    row = table.row()
                    row.cell(f"{data_row[0]}:", style=bold_style)
                    row.cell(data_row[1])
    pdf.ln(5)

    # --- PAGE 2: MAP & SUMMARY ---
    pdf.add_page()

    # Page 2 Title
    pdf.set_font("DejaVu", 'B', 14)
    pdf.cell(0, 10, "Résultats de la recherche", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(5)

    # Map Generation
    try:
        map_png = _generate_static_map_image(results_df)
        if map_png:
            map_image_stream = io.BytesIO(map_png)
            # Center the image and limit width to avoid it being too big
            target_width = 150
            x_pos = (pdf.w - target_width) / 2
            pdf.image(map_image_stream, x=x_pos, w=target_width)
    except Exception as e:
        pdf.set_font("DejaVu", 'I', 8)
        pdf.multi_cell(0, 6, f"Erreur lors de la generation de la carte: {e}")
    pdf.ln(5)

    # Top 5 Summary
    pdf.set_font("DejaVu", 'B', 12)
    pdf.cell(0, 10, "Top 5 des résultats", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
    pdf.set_font("DejaVu", '', 9)
    for rank, (index, row) in enumerate(results_df.head(5).iterrows(), start=1):
        score_percent = f"{row['weighted_score'] * 100:.0f}%"
        name = row.libgeo
        pdf.cell(0, 5, f"  {rank}. {name} - {score_percent}", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    # --- INDIVIDUAL RESULT PAGES ---
    for rank, (index, row) in enumerate(results_df.head(5).iterrows(), start=1):
        pdf.add_page()
        # Result Title
        title = f"Top {rank} | {row.libgeo}"
        pdf.set_font("DejaVu", 'B', 12)
        pdf.cell(0, 8, title, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

        # Pitch
        pitch = ui._produce_pitch_markdown(row, st_session_state['config'], st_session_state['app_data']['scores_cat'])
        pdf.set_font("DejaVu", '', 9)
        pdf.multi_cell(0, 5, pitch, markdown=True)
        pdf.ln(5)

        # Radar Chart
        try:
            cat_scores = row[[col for col in row.index if col.endswith('_cat_score')]]
            cat_scores.rename(lambda x: x.split('_')[0].capitalize(), inplace=True)
            
            # Matplotlib Radar Chart
            categories = cat_scores.index.tolist()
            values = (cat_scores.values * 100).tolist()
            N = len(categories)
            
            if N > 0:
                # Compute angles
                angles = [n / float(N) * 2 * 3.14159 for n in range(N)]
                angles += angles[:1] # Close the loop
                values += values[:1] # Close the loop
                
                fig, ax = plt.subplots(figsize=(4, 4), subplot_kw=dict(polar=True))
                
                # Draw one axe per variable + add labels
                plt.xticks(angles[:-1], categories, size=8)
                
                # Draw ylabels
                ax.set_rlabel_position(0) # type: ignore
                plt.yticks([25, 50, 75], ["25", "50", "75"], color="grey", size=7)
                plt.ylim(0, 100)
                
                # Plot data
                ax.plot(angles, values, linewidth=1, linestyle='solid')
                ax.fill(angles, values, 'b', alpha=0.1)
                
                # Save to buffer
                buf = io.BytesIO()
                plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
                plt.close(fig)
                buf.seek(0)
                
                # Embed in PDF
                pdf.image(buf, w=100)
        except Exception as e:
            pdf.set_font("DejaVu", 'I', 8)
            pdf.multi_cell(0, 6, f"Erreur lors de la generation du graphique: {e}")
        pdf.ln(5)

        # Additional Info
        pdf.set_font("DejaVu", 'B', 10)
        pdf.cell(0, 8, "Plus d’informations:", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Métiers
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Top métiers recherchés", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("DejaVu", '', 9)
        
        live_jobs = st_session_state['app_data'].get('live_jobs_data', pd.DataFrame())
        codgeo = str(row.get('codgeo', row.name))
        
        top_metiers = []
        if not live_jobs.empty:
            city_live = live_jobs[live_jobs['commune'] == codgeo]
            if not city_live.empty:
                # Group by label and sort by volume
                top_live = city_live.groupby('romeLibelle')['total_postes'].sum().sort_values(ascending=False).head(10)
                top_metiers = top_live.index.tolist()
        
        if top_metiers:
            pdf.multi_cell(0, 5, "\n".join([f'- {item}' for item in top_metiers]))
        else:
            pdf.multi_cell(0, 5, "Pas de données disponibles.")
        pdf.ln(3)


        # Formations
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Formations proposées", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("DejaVu", '', 9)
        formations = set(row.get('noms_formations', []) or [])
        if formations:
            pdf.multi_cell(0, 5, "\n".join([f'- {item}' for item in sorted(list(formations))]))
        else:
            pdf.multi_cell(0, 5, "Pas de données disponibles.")
        pdf.ln(3)

        # Services d'inclusion
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Services d'inclusion", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("DejaVu", '', 9)
        
        services_df = st_session_state['app_data']['annuaire_inclusion']
        incl_index = st_session_state['app_data'].get('inclusion_services_index', pd.DataFrame())
        
        # Determine Target Slugs for Filtering
        target_slugs = set(cfg.DEFAULT_INC_SERVICES_CORE)
        
        # Add user selected specific needs
        if 'ui_inc_services_add_selection' in st_session_state and st_session_state['ui_inc_services_add_selection']:
                target_slugs.update(st_session_state['ui_inc_services_add_selection'])
        
        communes_to_check = row.get('communes', [row.name])
        
        # Filter services
        # We look for services in any of the communes (if aggregated) or the single commune
        bv_services = services_df[
            (services_df['codgeo'].isin(communes_to_check)) &
            (services_df['categorie'].isin(target_slugs))
        ]

        if not bv_services.empty:
            # Get unique slugs found
            unique_slugs = sorted(bv_services['categorie'].unique())
            
            valid_labels = []
            for slug in unique_slugs:
                # Lookup label
                if not incl_index.empty and slug in incl_index.index:
                    try:
                        label = incl_index.loc[slug, 'label']
                        # If duplicate index
                        if isinstance(label, (pd.Series, pd.DataFrame)):
                            label = label.iloc[0]
                    except:
                        label = slug
                else:
                    label = slug # Fallback
                
                if label:
                        valid_labels.append(label)

            if valid_labels:
                    # Deduplicate labels
                    valid_labels = sorted(list(set(valid_labels)))
                    pdf.multi_cell(0, 5, "\n".join([f'- {label}' for label in valid_labels]))
            else:
                pdf.multi_cell(0, 5, "Aucun service d'inclusion correspondant aux critères trouvé.")
        else:
            pdf.multi_cell(0, 5, "Aucun service d'inclusion correspondant aux critères trouvé.")
        pdf.ln(3)

    return bytes(pdf.output())