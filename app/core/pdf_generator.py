import io
import pandas as pd
from fpdf import FPDF
from fpdf.fonts import FontFace
from fpdf.enums import TextEmphasis, XPos, YPos

import tempfile
import os
import config as cfg
from ui import components as ui
from core.models import SearchCriterias, CommuneResult, SearchResultsData
from core.scoring import ScoringEngine
import base64
import logging
from typing import Dict, Any, List, Optional
import matplotlib.pyplot as plt
import contextily as ctx
import geopandas as gpd
from agents.utils import odis_get_bg_result

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


def _generate_static_map_image(search_results: SearchResultsData) -> bytes:
    """
    Generates a static map image using Matplotlib and Contextily.
    Highlights the results and the current location.
    """
    if not search_results.results:
        return b""

    # 1. Reconstruct results GeoDataFrame internally
    data = []
    for c in search_results.results:
        data.append({
            'codgeo': c.codgeo,
            'libgeo': c.name,
            'weighted_score': c.global_score,
            'geometry': c.geometry
        })
    
    # 🕵️ Debug Logging
    if data:
        sample_geom = data[0]['geometry']
        logging.info(f"🕵️ [PDF-MAP] Sample geometry type: {type(sample_geom)}")
        if hasattr(sample_geom, 'centroid'):
            logging.info(f"🕵️ [PDF-MAP] Sample centroid: ({sample_geom.centroid.x}, {sample_geom.centroid.y})")
    
    # Use the projected CRS from config (EPSG:2154 / Lambert-93)
    # Centroids are now guaranteed to be in metric meters by the fixed pipeline.
    inferred_crs = cfg.PROJECTED_CRS
    
    logging.info(f"🕵️ [PDF-MAP] Inferred CRS: {inferred_crs}")
    results_df = gpd.GeoDataFrame(data, crs=inferred_crs)
    
    # 🕵️ PLM Family Exclusion Logic
    current_code = search_results.current_geo.codgeo if search_results.current_geo else None
    if current_code and not results_df.empty:
        plm_prefix = None
        if current_code in cfg.PLM_MAPPING:
            plm_prefix = cfg.PLM_MAPPING[current_code]
            results_df = results_df[results_df['codgeo'] != current_code]
        else:
            for parent_code, prefix in cfg.PLM_MAPPING.items():
                if str(current_code).startswith(prefix):
                    plm_prefix = prefix
                    results_df = results_df[results_df['codgeo'] != parent_code]
                    results_df = results_df[results_df['codgeo'] != current_code]
                    break
        
        if plm_prefix:
            results_df = results_df[~results_df['codgeo'].astype(str).str.startswith(plm_prefix)]
        else:
            results_df = results_df[results_df['codgeo'] != current_code]

    # 2. Handle Current Location
    current_df = None
    if search_results.current_geo and search_results.current_geo.geometry:
        current_df = gpd.GeoDataFrame([{
            'codgeo': search_results.current_geo.codgeo,
            'libgeo': search_results.current_geo.name,
            'weighted_score': search_results.current_geo.global_score,
            'geometry': search_results.current_geo.geometry
        }], crs=inferred_crs)

    # Project to Web Mercator for Contextily
    gdf_results_plot = results_df.to_crs(epsg=3857)
    
    # Initialize figure
    fig, ax = plt.subplots(figsize=(8, 8))
    
    # Plot results (choropleth)
    gdf_results_plot.plot(
        column='weighted_score',
        cmap='YlGn',
        alpha=0.6,
        edgecolor='grey',
        linewidth=0.5,
        ax=ax,
        legend=True,
        vmin=0.0,
        vmax=1.0,
        legend_kwds={'label': "Score Global", 'orientation': "horizontal", 'shrink': 0.5, 'pad': 0.05}
    )
    
    # Highlight Current Location (Blue dashed outline + light fill)
    if current_df is not None:
        gdf_curr_plot = current_df.to_crs(epsg=3857)
        gdf_curr_plot.plot(
            ax=ax,
            facecolor='#1f77b4',
            alpha=0.3,
            edgecolor='#1f77b4',
            linewidth=3,
            linestyle='--'
        )
    
    # Plot outlines for top results (Red)
    top_5 = gdf_results_plot.head(5)
    top_5.plot(
        ax=ax,
        facecolor='none',
        edgecolor='red',
        linewidth=2
    )
    
    # Add numbered markers for top results
    for idx, row in top_5.iterrows():
        rank = idx + 1
        centroid = row.geometry.centroid
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


def generate_pdf_report(st_session_state: Dict[str, Any], search_results: SearchResultsData, folium_map: Any = None) -> bytes:
    """
    Generates a PDF report with the top 5 results and search criteria using a Unicode font.
    """
    logging.info("📄 [PDF] Starting PDF report generation...")
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

        # Get job names from enriched CriteriaItems
        metier_names = [c.label for sublist in config.codes_metiers for c in sublist]
        metiers_str = ", ".join(metier_names) if metier_names else "Non spécifié"

        # Get formation names from enriched CriteriaItems
        formation_names = [c.label for sublist in config.codes_formations for c in sublist]
        formations_str = ", ".join(formation_names) if formation_names else "Non spécifié"

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
            "Hébergement": ", ".join(config.hebergement_cible) if config.hebergement_cible else "Non spécifié",
            "Logement à long terme": config.logement,
            "Besoin de santé": config.besoin_sante,
            "Autres besoins": ", ".join([c.label for c in config.inc_services_add_selection]) if config.inc_services_add_selection else "Aucun",
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
    logging.info("📄 [PDF] Generating Page 2 (Map & Summary)")
    pdf.add_page()

    # Page 2 Title
    pdf.set_font("DejaVu", 'B', 14)
    pdf.cell(0, 10, "Résultats de la recherche", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(5)

    # Map Generation
    try:
        map_png = _generate_static_map_image(search_results)
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
    for rank, commune in enumerate(search_results.results, start=1):
        score_percent = f"{commune.global_score * 100:.1f}%"
        pdf.cell(0, 5, f"  {rank}. {commune.name} - {score_percent}", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    # --- INDIVIDUAL RESULT PAGES ---
    logging.info(f"📄 [PDF] Generating {len(search_results.results)} individual result pages")
    config = st_session_state.get('config')

    for rank, commune in enumerate(search_results.results, start=1):
        logging.info(f"📄 [PDF] Processing commune: {commune.name}")
        pdf.add_page()
        # Result Title
        title = f"Top {rank} | {commune.name}"
        pdf.set_font("DejaVu", 'B', 12)
        pdf.cell(0, 8, title, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

        # Pitch
        h = config.compute_hash() if config else None
        scorer_res = odis_get_bg_result(h) if h else None
        
        ai_pitch = ""
        codgeo = commune.codgeo
        
        if scorer_res and isinstance(scorer_res, dict) and "pitches" in scorer_res:
            ai_pitch = scorer_res["pitches"].get(codgeo, "")
            
        if ai_pitch:
            pitch = ai_pitch
        else:
            # New signature: (commune, config)
            pitch = ui._produce_pitch_markdown(commune, config)
            
        pdf.set_font("DejaVu", '', 9)
        pdf.multi_cell(0, 5, pitch, markdown=True)
        pdf.ln(5)

        # Radar Chart with Comparison
        try:
            # 1. Target Data
            categories = ['Emploi', 'Logement', 'Education', 'Sante', 'Inclusion', 'Mobilite']
            raw_values = [
                commune.employment.cat_score * 100,
                commune.housing.cat_score * 100,
                commune.education.cat_score * 100,
                commune.health.cat_score * 100,
                commune.inclusion.cat_score * 100,
                commune.mobility.cat_score * 100
            ]
            
            # filter out inactive categories based on config
            active_cats = config.active_categories if config and hasattr(config, 'active_categories') else []
            if active_cats:
                 cat_map = {'emploi': 'Emploi', 'logement': 'Logement', 'education': 'Education', 'sante': 'Sante', 'inclusion': 'Inclusion', 'mobilite': 'Mobilite'}
                 filtered_cats = []
                 filtered_vals = []
                 for i, cat in enumerate(['emploi', 'logement', 'education', 'sante', 'inclusion', 'mobilite']):
                     if cat in active_cats:
                         filtered_cats.append(cat_map.get(cat, cat.capitalize()))
                         filtered_vals.append(raw_values[i])
                 categories = filtered_cats
                 values = filtered_vals
            else:
                 values = raw_values
            
            # 2. Current City Data
            config = st_session_state.get('config')
            current_codgeo = config.commune_actuelle.code if config and hasattr(config.commune_actuelle, 'code') else (config.commune_actuelle if config else None)
            
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
                    current_c.mobility.cat_score * 100
                ]
                if active_cats:
                     filtered_vals_cur = []
                     for i, cat in enumerate(['emploi', 'logement', 'education', 'sante', 'inclusion', 'mobilite']):
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
                angles += angles[:1] # Close the loop
                
                fig, ax = plt.subplots(figsize=(4, 4), subplot_kw=dict(polar=True))
                
                # Draw one axe per variable + add labels
                plt.xticks(angles[:-1], categories, size=8)
                
                # Draw ylabels
                ax.set_rlabel_position(0) # type: ignore
                plt.yticks([25, 50, 75], ["25", "50", "75"], color="grey", size=7)
                plt.ylim(0, 100)
                
                # Plot target city (Green)
                vals_target = values + values[:1]
                ax.plot(angles, vals_target, linewidth=2, linestyle='solid', color='#006268', label=commune.name)
                ax.fill(angles, vals_target, '#006268', alpha=0.3)
                
                # Plot current city (Blue)
                if has_comparison:
                    vals_current = values_current + values_current[:1]
                    label_cur = config.commune_actuelle.label if config and hasattr(config.commune_actuelle, 'label') else "Actuel"
                    ax.plot(angles, vals_current, linewidth=2, linestyle='solid', color='#1f77b4', label=f"Actuel ({label_cur})")
                    ax.fill(angles, vals_current, '#1f77b4', alpha=0.2)
                    plt.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), fontsize=8)
                
                # Save to buffer
                buf = io.BytesIO()
                plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
                plt.close(fig)
                buf.seek(0)
                
                # Embed in PDF
                pdf.image(buf, w=100)
                
                if has_comparison:
                    pdf.set_font("DejaVu", 'I', 8)
                    label_cur = search_results.current_geo.name if search_results.current_geo else "votre commune"
                    pdf.cell(0, 5, f"Comparaison entre {commune.name} (vert) et {label_cur} (bleu).", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
                    pdf.ln(2)

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
        
        top_professions = commune.employment.top_professions
        
        if top_professions:
            pdf.multi_cell(0, 5, "\n".join([f'- {item}' for item in top_professions]))
        else:
            pdf.multi_cell(0, 5, "Pas de données disponibles.")
        pdf.ln(3)


        # Formations
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Formations proposées", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("DejaVu", '', 9)
        training_programs = commune.employment.training_programs
        if training_programs:
            pdf.multi_cell(0, 5, "\n".join([f'- {item}' for item in sorted(list(training_programs))]))
        else:
            pdf.multi_cell(0, 5, "Pas de données disponibles.")
        pdf.ln(3)

        # Services d'inclusion
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Services d'inclusion", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("DejaVu", '', 9)
        
        app_data = st_session_state.get('app_data', {})
        services_df = app_data.get('annuaire_inclusion', pd.DataFrame())
        incl_index = app_data.get('inclusion_services_index', pd.DataFrame())
        
        # Determine Target Slugs for Filtering
        target_slugs = set(cfg.DEFAULT_INC_SERVICES_CORE)
        
        # Add user selected specific needs
        if 'ui_inc_services_add_selection' in st_session_state and st_session_state['ui_inc_services_add_selection']:
                target_slugs.update(st_session_state['ui_inc_services_add_selection'])
        
        communes_to_check = [commune.codgeo]
        
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