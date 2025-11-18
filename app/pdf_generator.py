import folium
import io
import pandas as pd
from fpdf import FPDF
from fpdf.fonts import FontFace
from fpdf.enums import TextEmphasis
from plotly.express import line_polar
import tempfile
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
import os
import config as cfg
import ui
from config import ScoringConfig


# Basic constants
PDF_TITLE = "Synthèse de votre recherche de territoire"


def _setup_unicode_font(pdf: FPDF):
    """Adds the local Unicode DejaVu fonts to the FPDF instance."""
    font_dir = "assets/fonts/"
    try:
        pdf.add_font("DejaVu", "", os.path.join(font_dir, "DejaVuSans.ttf"))
        pdf.add_font("DejaVu", "B", os.path.join(font_dir, "DejaVuSans-Bold.ttf"))
        pdf.add_font("DejaVu", "I", os.path.join(font_dir, "DejaVuSans-Oblique.ttf"))
        pdf.add_font("DejaVu", "BI", os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf"))
        pdf.set_font("DejaVu", size=12)
    except RuntimeError as e:
        print(f"--- WARNING: Could not load local Unicode font. Falling back to Arial. Error: {e} ---")
        print("--- Please ensure you have downloaded the font files as per the instructions. ---")
        # Fallback to Arial if font setup fails
        pdf.set_font("Arial", size=12)


def _capture_map_as_png(m: folium.Map) -> bytes:
    """Saves a folium map to a temporary HTML file and captures it as a PNG."""
    with tempfile.NamedTemporaryFile(delete=True, suffix=".html") as temp_html:
        m.save(temp_html.name)
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        driver.set_window_size(800, 800)
        driver.get(f"file://{temp_html.name}")
        time.sleep(2)
        map_element = driver.find_element(by="class name", value="folium-map")
        png = map_element.screenshot_as_png
        driver.quit()
        return png


def generate_pdf_report(st_session_state: dict, results_df: pd.DataFrame, folium_map: folium.Map) -> bytes:
    """
    Generates a PDF report with the top 5 results and search criteria using a Unicode font.
    """
    pdf = FPDF()
    _setup_unicode_font(pdf)
    pdf.add_page()

    # --- PAGE 1: HEADER & CRITERIA ---
    # Header
    pdf.image('images/logo_jaccueille_pdf.jpg', x=10, y=8, w=40)
    pdf.ln(50)  # Add space for the logo
    pdf.set_font("DejaVu", 'B', 16)
    pdf.cell(0, 10, PDF_TITLE, 0, 1, 'C')

    # Subtitle with beneficiary's name
    subtitle = f"Pour le projet de vie {ui.get_person_accompanied_str()}"
    pdf.set_font("DejaVu", '', 12)
    pdf.cell(0, 10, subtitle, 0, 1, 'C')
    pdf.ln(10)

    # --- Search Criteria ---
    pdf.set_font("DejaVu", 'B', 12)
    pdf.cell(0, 10, "Vos critères de recherche", 0, 1, 'L')
    pdf.ln(2)
    config = st_session_state.get('config')
    if config:
        # --- Correctly look up names for Jobs & Formations ---
        app_data = st_session_state.get('app_data', {})
        metiers_df = app_data.get('codfap_index')
        formations_df = app_data.get('codformations_index')

        # Get job names from codes
        selected_metier_codes = [code for sublist in config.codes_metiers for code in sublist]
        if selected_metier_codes and metiers_df is not None:
            metiers_df_indexed = metiers_df.set_index('Code FAP 341')
            metier_names = metiers_df_indexed.loc[selected_metier_codes, 'Intitulé FAP 341'].tolist()
            metiers_str = ", ".join(metier_names)
        else:
            metiers_str = "Non spécifié"

        # Get formation names from codes
        selected_formation_codes = [code for sublist in config.codes_formations for code in sublist]
        if selected_formation_codes and formations_df is not None:
            formations_df_indexed = formations_df.set_index('index')
            formation_names = formations_df_indexed.loc[selected_formation_codes, 'libformation'].tolist()
            formations_str = ", ".join(formation_names)
        else:
            formations_str = "Non spécifié"

        # Dynamically build the full criteria list
        criteria = {
            "Lieu de départ": st_session_state.get('ui_commune'),
            "Zone de recherche": cfg.LOC_DISTANCE_OPTIONS.get(config.loc_distance_km,
                                                              str(config.loc_distance_km) + " km"),
            "Métiers recherchés": metiers_str,
            "Formations recherchées": formations_str,
            "Nb. adultes": config.nb_adultes,
            "Nb. enfants": config.nb_enfants,
            "Niveaux scolaires": ", ".join(config.classe_enfants) if config.classe_enfants else "N/A",
            "Hébergement": config.hebergement,
            "Logement à long terme": config.logement,
            "Besoin de santé": config.besoin_sante,
            "Autres besoins": ", ".join(
                [f"{cat}: {', '.join(serv)}" for cat, serv in config.besoins_autres.items()]) if config.besoins_autres else "Aucun",
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
    pdf.cell(0, 10, "Résultats de la recherche", 0, 1, 'C')
    pdf.ln(5)

    # Map Screenshot
    try:
        map_png = _capture_map_as_png(folium_map)
        map_image_stream = io.BytesIO(map_png)
        map_image_stream.seek(0)
        pdf.image(map_image_stream, x=10, w=pdf.w - 20, type='PNG')
    except Exception as e:
        pdf.set_font("DejaVu", 'I', 8)
        pdf.multi_cell(0, 6, f"Erreur lors de la generation de la carte: {e}")
    pdf.ln(5)

    # Top 5 Summary
    pdf.set_font("DejaVu", 'B', 12)
    pdf.cell(0, 10, "Top 5 des résultats", 0, 1, 'L')
    pdf.set_font("DejaVu", '', 9)
    for index, row in results_df.head(5).iterrows():
        score_percent = f"{row['weighted_score'] * 100:.0f}%"
        if st_session_state.get('view_level') == 'Bassins de vie':
            name = f"Bassin de vie de {row.libgeo}"
        else:
            name = row.libgeo + (f" (avec {row.libgeo_binome})" if row.binome else "")
        pdf.cell(0, 5, f"  {index + 1}. {name} - {score_percent}", 0, 1)
    pdf.ln(5)

    # --- INDIVIDUAL RESULT PAGES ---
    for index, row in results_df.head(5).iterrows():
        pdf.add_page()
        # Result Title
        if st_session_state.get('view_level') == 'Bassins de vie':
            title = f"Top {index + 1} | Bassin de vie de {row.libgeo}"
        else:
            title = f"Top {index + 1} | {row.libgeo}" + (f" (avec {row.libgeo_binome})" if row.binome else "")
        pdf.set_font("DejaVu", 'B', 12)
        pdf.cell(0, 8, title, 0, 1)
        pdf.ln(2)

        # Pitch
        pitch = ui._produce_pitch_markdown(row, st_session_state.config, st_session_state.app_data['scores_cat'])
        pdf.set_font("DejaVu", '', 9)
        pdf.multi_cell(0, 5, pitch, markdown=True)
        pdf.ln(5)

        # Radar Chart
        try:
            cat_scores = row[[col for col in row.index if col.endswith('_cat_score')]]
            cat_scores.rename(lambda x: x.split('_')[0].capitalize(), inplace=True)
            fig = line_polar(theta=cat_scores.index, r=cat_scores.values * 100, line_close=True, range_r=[0, 100])
            fig.update_traces(fill='toself')
            fig.update_layout(margin=dict(l=50, r=50, t=50, b=50), width=400, height=300)
            image_bytes = fig.to_image(format="svg")
            with tempfile.NamedTemporaryFile(delete=True, suffix=".svg") as temp_image_file:
                temp_image_file.write(image_bytes)
                temp_image_file.flush()
                pdf.image(temp_image_file.name, w=100)
        except Exception as e:
            pdf.set_font("DejaVu", 'I', 8)
            pdf.multi_cell(0, 6, f"Erreur lors de la generation du graphique: {e}")
        pdf.ln(5)

        # Additional Info
        pdf.set_font("DejaVu", 'B', 10)
        pdf.cell(0, 8, "Plus d’informations:", 0, 1)

        # Métiers
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Top métiers recherchés", 0, 1)
        pdf.set_font("DejaVu", '', 9)
        top_metiers = set(row.get('be_libfap_top', []) or [])
        if top_metiers:
            pdf.multi_cell(0, 5, "\n".join([f'- {item}' for item in sorted(list(top_metiers))]))
        else:
            pdf.multi_cell(0, 5, "Pas de données disponibles.")
        pdf.ln(3)

        # Formations
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Formations proposées", 0, 1)
        pdf.set_font("DejaVu", '', 9)
        formations = set(row.get('noms_formations', []) or [])
        if formations:
            pdf.multi_cell(0, 5, "\n".join([f'- {item}' for item in sorted(list(formations))]))
        else:
            pdf.multi_cell(0, 5, "Pas de données disponibles.")
        pdf.ln(3)

        # Services d'inclusion
        pdf.set_font("DejaVu", 'B', 9)
        pdf.cell(0, 6, "Services d'inclusion", 0, 1)
        pdf.set_font("DejaVu", '', 9)
        services_df = st_session_state.app_data['annuaire_inclusion']
        communes_to_check = row.get('communes', [row.name])
        bv_services = services_df[services_df.codgeo.isin(communes_to_check)]
        if not bv_services.empty and any(bv_services.service != '-'):
            service_text = []
            for cat, group in bv_services.groupby('categorie', observed=True):
                valid_services = group[group.service != '-']
                if not valid_services.empty:
                    services_list_str = ", ".join(
                        valid_services['service'].str.replace('-', ' ').str.capitalize().unique())
                    service_text.append(f"**{cat.replace('-', ' ').capitalize()}**: {services_list_str}")
            pdf.multi_cell(0, 5, "\n".join(service_text), markdown=True)
        else:
            pdf.multi_cell(0, 5, "Pas de services d'inclusion répertoriés.")
        pdf.ln(3)

    return bytes(pdf.output())